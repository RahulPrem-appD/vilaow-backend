"""Map the sub-roles callers typed onto each trade's list.

`professionals.subrole` used to take whatever a caller wrote — "Real estate
Lawyer", "Luxury real estate agent", "Property Lawyer · Foreign Buyer
Transactions". It now takes one title from `professions.subroles`. This walks
what is already stored and moves it across where the two line up.

It reports rather than assumes. A value that matches nothing is printed, and
--apply writes every record's before and after to a JSON file first, because a
line somebody typed on a call is a fact about a real professional and should
be recoverable rather than only regrettable. Nothing is written without --apply.

    python -m scripts.map_subroles            # dry run, prints what it would do
    python -m scripts.map_subroles --apply    # writes the matches

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
    """A form two spellings of the same title both collapse to: lowercased,
    ampersands spelled out, everything that is not a letter or digit gone."""
    lowered = text.strip().lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "", lowered)


# Titles that mean one of his entries without sharing its words. Kept short
# and obvious on purpose; anything less certain is left for the report.
SYNONYMS: dict[str, str] = {
    "propertylawyer": "Real Estate Lawyer",
    "realestatelawyer": "Real Estate Lawyer",
    "conveyancinglawyer": "Real Estate Lawyer",
    "goldenvisa": "Golden Visa / Investment Visa",
    "restoration": "Historic Preservation",
}


def candidates(entry: str, trade_label: str) -> list[str]:
    """The entry, its parts if it was written as "Title · Detail", and each
    of those with the trade's own name taken off — "Luxury real estate agent"
    is "Luxury" once the trade is removed."""
    parts = [entry]
    for dash in ("·", "—", " - ", "–", ":", "/"):
        if dash in entry:
            parts.extend(p for p in entry.split(dash) if p.strip())
    out: list[str] = []
    for part in parts:
        out.append(part)
        stripped = re.sub(re.escape(trade_label), "", part, flags=re.I).strip(" -·—–")
        if stripped and stripped != part:
            out.append(stripped)
    return out


def main(apply: bool) -> int:
    db = SessionLocal()
    matched = unmatched = untouched = 0
    ledger: list[dict] = []
    try:
        trades = {t.id: t for t in db.scalars(select(Profession)).all()}
        rows = db.scalars(
            select(Professional).where(Professional.subrole.isnot(None))
        ).all()

        for pro in rows:
            entry = (pro.subrole or "").strip()
            if not entry:
                continue
            trade = trades.get(pro.profession_id) if pro.profession_id else None
            if not trade or not trade.subroles:
                untouched += 1
                continue

            lookup = {normalise(o): o for o in trade.subroles}
            hit = None
            for candidate in candidates(entry, trade.label):
                norm = normalise(candidate)
                if norm in lookup:
                    hit = lookup[norm]
                    break
                if norm in SYNONYMS and SYNONYMS[norm] in trade.subroles:
                    hit = SYNONYMS[norm]
                    break

            print(f"\n#{pro.id} {pro.slug or pro.business_name} ({trade.key})")
            print(f"    was: {entry[:90]}")
            print(f"    now: {hit or '(nothing matched — will be cleared)'}")

            if hit is None:
                unmatched += 1
            if hit != entry:
                matched += 1
                ledger.append({
                    "id": pro.id, "slug": pro.slug, "profession": trade.key,
                    "before": entry, "after": hit,
                })
                if apply:
                    # Cleared when nothing matched, for the same reason the
                    # services script clears: the API now refuses a title the
                    # trade does not list, so a record holding one could not
                    # be saved through its own form again. The ledger is where
                    # the typed line survives.
                    pro.subrole = hit

        if apply:
            path = os.path.join(os.getcwd(), "subroles-before-mapping.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(ledger, fh, ensure_ascii=False, indent=2)
            print(f"\nWrote {path} — every before and after.")
            db.commit()

        print(
            f"\n{len(rows)} records with a sub-role. "
            f"{matched} would change, {unmatched} matched nothing, "
            f"{untouched} in a trade with no list and left alone."
        )
        if not apply:
            print("Dry run. Re-run with --apply to write the matches.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(apply="--apply" in sys.argv[1:]))
