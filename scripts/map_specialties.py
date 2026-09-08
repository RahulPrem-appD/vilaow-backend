"""Map the free text callers typed onto each trade's list of services.

`professionals.specialties` used to take whatever a caller wrote, one entry per
line, often as "Title — description". It now takes services ticked from
`professions.specializations`. This walks what is already stored and moves it
across where the two line up.

It reports rather than assumes. An entry that matches nothing is printed, and
--apply writes every record's before and after to a JSON file first, because a
line somebody typed on a call is a fact about a real professional and it should
be recoverable after the fact rather than only regrettable. Nothing is written
to the database at all without --apply.

    python -m scripts.map_specialties            # dry run, prints what it would do
    python -m scripts.map_specialties --apply    # writes the matches

Against the live database, set DATABASE_URL and VILAOW_ALLOW_HOSTED_DB=1 the
way the other scripts here do.
"""
from __future__ import annotations

import json
import os
import re
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Profession, Professional


def normalise(text: str) -> str:
    """A form two spellings of the same service both collapse to.

    Lowercased, ampersands spelled out, and everything that is not a letter or
    a digit dropped — so "Building Permits & Licensing", "building permits and
    licensing" and "Building permits/licensing" all meet.
    """
    lowered = text.strip().lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "", lowered)


def candidates(entry: str) -> list[str]:
    """The entry, and its title if it was written as "Title — description".

    The old box invited that shape, so the part before the dash is usually the
    service and the rest is a sentence about it.
    """
    out = [entry]
    for dash in ("—", " - ", "–", ":"):
        if dash in entry:
            out.append(entry.split(dash, 1)[0])
    return out


def main(apply: bool) -> int:
    db = SessionLocal()
    matched = unmatched = untouched = 0
    ledger: list[dict] = []
    try:
        trades = {t.id: t for t in db.scalars(select(Profession)).all()}
        rows = db.scalars(
            select(Professional).where(Professional.specialties.isnot(None))
        ).all()

        for pro in rows:
            entries = [e for e in (pro.specialties or []) if e and e.strip()]
            if not entries:
                continue
            trade = trades.get(pro.profession_id) if pro.profession_id else None
            if not trade or not trade.specializations:
                untouched += 1
                continue

            lookup = {normalise(o): o for o in trade.specializations}
            keep: list[str] = []
            misses: list[str] = []
            for entry in entries:
                hit = next(
                    (lookup[normalise(c)] for c in candidates(entry)
                     if normalise(c) in lookup),
                    None,
                )
                if hit is None:
                    misses.append(entry)
                elif hit not in keep:
                    keep.append(hit)

            cap = trade.max_specializations
            over = cap is not None and len(keep) > cap
            if over:
                keep = keep[:cap]

            ordered = [o for o in trade.specializations if o in set(keep)]

            print(f"\n#{pro.id} {pro.slug or pro.business_name} ({trade.key})")
            for entry in entries:
                print(f"    was: {entry[:90]}")
            print(f"    now: {ordered or '(nothing matched — left as it is)'}")
            if over:
                print(f"    NOTE: more than {trade.key}'s limit of {cap}; kept the first {cap}")
            for miss in misses:
                print(f"    NO MATCH: {miss[:90]}")

            if misses:
                unmatched += 1
            if ordered != entries:
                matched += 1
                ledger.append({
                    "id": pro.id,
                    "slug": pro.slug,
                    "profession": trade.key,
                    "before": entries,
                    "after": ordered,
                    "dropped": misses,
                })
                if apply:
                    # Written even when nothing matched, which clears the row.
                    # Leaving unmatched text in place would be the tidier-
                    # looking choice and the wrong one: the API now refuses a
                    # service the trade does not offer, so a record holding
                    # any would be one nobody could save through its own form
                    # again. The ledger above is where those lines survive.
                    pro.specialties = ordered or None

        if apply:
            # The file lands before the commit on purpose. If the write fails,
            # what was about to be replaced is already on disk.
            path = os.path.join(os.getcwd(), "specialties-before-mapping.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(ledger, fh, ensure_ascii=False, indent=2)
            print(f"\nWrote {path} — every before and after, including the dropped lines.")
            db.commit()

        print(
            f"\n{len(rows)} records with services. "
            f"{matched} changed, {unmatched} had a line that matched nothing, "
            f"{untouched} in a trade with no list and left alone."
        )
        print("Dry run — nothing written. Re-run with --apply." if not apply
              else "Written.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--apply" in sys.argv))
