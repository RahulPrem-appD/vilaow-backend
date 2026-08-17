"""What must be true before this site is shown to real buyers.

An agent review found the live statistics on the homepage — "1 Purchase
supported", "★ 5.0 verified rating", "5 vetted professionals" — computed
honestly from demo data: a test listing, an introduction whose buyer was the
operator's own address, and a verified review stitched from the review form's
placeholder text. The numbers were not fabricated by the code; the code was
faithfully reporting fabricated rows.

Nothing in the app can tell a demo row from a real one, and adding a flag for
that is a product decision. This is the cheaper honest answer: a command that
looks at what is actually in the database and at the copy on the site, and says
what would be published if the doors opened now.

    uv run python -m scripts.check_launch_ready

Exits non-zero if anything is outstanding, so it can gate a deploy.
"""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Introduction, Professional, Review, ReviewKind

REPO = Path(__file__).resolve().parents[2]

# Addresses that mean "this row came from testing", not from a buyer.
TEST_ADDRESS = re.compile(r"@(example\.(com|org|net)|test\.|localhost)|rpremkumar\.dev@", re.I)


def _problems() -> list[str]:
    found: list[str] = []

    with SessionLocal() as db:
        published = db.scalars(select(Professional).where(Professional.published.is_(True))).all()

        for p in published:
            if p.email and TEST_ADDRESS.search(p.email):
                found.append(
                    f"Professional #{p.id} '{p.business_name}' is PUBLISHED with a test "
                    f"address ({p.email}) — it counts toward the public 'vetted "
                    f"professionals' figure."
                )

        for intro in db.scalars(select(Introduction)).all():
            if TEST_ADDRESS.search(intro.buyer_email or ""):
                found.append(
                    f"Introduction #{intro.id} is from a test address "
                    f"({intro.buyer_email}) — a closed one counts toward "
                    f"'purchases supported'."
                )

        verified = db.scalars(
            select(Review).where(Review.kind == ReviewKind.vilaow_verified)
        ).all()
        for review in verified:
            source = (db.get(Introduction, review.introduction_id)
                      if review.introduction_id else None)
            if source is not None and TEST_ADDRESS.search(source.buyer_email or ""):
                found.append(
                    f"Review #{review.id} by '{review.author}' is marked verified but "
                    f"came from a test introduction — it is a fabricated consumer "
                    f"review on a live commercial site (EU UCPD)."
                )

        google = db.scalar(
            select(func.count()).select_from(Review).where(Review.kind == ReviewKind.google)
        ) or 0
        if google:
            # No code path creates these; they were inserted by hand from the
            # client's own sample content. He flagged it himself in
            # client-reference/handover/README.md, item 5.
            found.append(
                f"{google} review(s) are attributed to Google and no code creates "
                f"them — they came from sample content. Attributing invented text to "
                f"Google is a UCPD offence however well intentioned. Import them "
                f"properly or remove them."
            )

    # Copy the client still has to supply. These render visibly on the page, so
    # they are not hidden — but they must not go live.
    for relative, marker, what in [
        ("app/src/app/(site)/privacy/page.tsx", "[your legal entity name]",
         "the data controller's legal name — GDPR Art. 13 requires it"),
        ("app/src/app/(site)/privacy/page.tsx", "[add date on launch]",
         "the policy's effective date"),
        ("app/src/app/(site)/about/page.tsx", "Add your personal story here",
         "the founder's story placeholder"),
    ]:
        path = REPO / relative
        if path.exists() and marker in path.read_text():
            found.append(f"{relative} still contains {marker!r} — {what}.")

    return found


def main() -> int:
    problems = _problems()
    if not problems:
        print("Launch checks pass: nothing demo-shaped is published and no "
              "placeholder copy remains.")
        return 0

    print(f"{len(problems)} thing(s) to resolve before real buyers see this:\n")
    for problem in problems:
        print(f"  - {problem}")
    print("\nThese are decisions, not bugs — most need the client. See the "
          "'Before launch' section of backend/README.md.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
