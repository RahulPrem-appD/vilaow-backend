"""Seed the professions and, optionally, load a contact spreadsheet.

Idempotent: safe to run against a database that already has data. Re-running
must never duplicate a profession or re-add a professional already on file.

    uv run python -m app.seed --owner asaf@vilaow.com --password '...'
    uv run python -m app.seed --contacts /path/to/Vilaow-Contacts.xlsx
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import func, select

from app.db import SessionLocal
from app.importer import normalise_phone, parse_workbook
from app.models import Event, ImportBatch, Profession, Professional, Role, Staff
from app.security import hash_password

# His six, in the order his own site lists them.
PROFESSIONS = [
    ("agent", "Estate agent", "Estate agents", "Find the property"),
    ("lawyer", "Lawyer", "Lawyers", "Legal & contracts"),
    ("engineer", "Civil engineer", "Civil engineers", "Survey & checks"),
    ("architect", "Architect", "Architects", "Design & permits"),
    ("contractor", "Contractor", "Contractors", "Build & renovate"),
    ("accountant", "Tax accountant", "Tax accountants", "Tax & Golden Visa"),
]


def seed_professions(db) -> int:
    added = 0
    for i, (key, label, plural, hint) in enumerate(PROFESSIONS):
        if db.scalar(select(Profession).where(Profession.key == key)):
            continue
        db.add(Profession(key=key, label=label, plural=plural, hint=hint, position=i))
        added += 1
    db.commit()
    return added


def seed_owner(db, email: str, password: str, name: str) -> bool:
    email = email.strip().lower()
    if db.scalar(select(Staff).where(Staff.email == email)):
        return False
    db.add(Staff(name=name, email=email, password_hash=hash_password(password), role=Role.owner))
    db.commit()
    return True


def load_contacts(db, path: str) -> dict:
    result = parse_workbook(path)
    keys = {p.key: p.id for p in db.scalars(select(Profession))}

    # Everything already on file, so a second run of the same sheet adds nothing.
    existing = {
        normalise_phone(p) for p in db.scalars(select(Professional.phone)) if p
    }

    batch = ImportBatch(filename=path.split("/")[-1], rows_seen=result.seen)
    db.add(batch)
    db.flush()

    added = skipped_dupe = 0
    seen_in_file: set[str] = set()
    for row in result.rows:
        digits = normalise_phone(row.phone or "")
        # Both checks matter: against the database, and within this file — his
        # sheet can list the same firm under two cities.
        if not digits or digits in existing or digits in seen_in_file:
            skipped_dupe += 1
            continue
        seen_in_file.add(digits)

        pro = Professional(
            business_name=row.business_name,
            phone=row.phone,
            address=row.address,
            city=row.city,
            region=row.region,
            profession_id=keys.get(row.profession_key),
            rating=row.rating,
            review_count=row.review_count,
            source="Google Maps",
            notes=row.notes,
            batch_id=batch.id,
        )
        db.add(pro)
        db.flush()
        db.add(Event(professional_id=pro.id, kind="imported",
                     actor_label="seed", detail=f"from {batch.filename}"))
        added += 1

    batch.rows_added = added
    batch.rows_skipped = result.skipped_no_phone + result.skipped_unknown_category + skipped_dupe
    db.commit()
    return {
        "seen": result.seen,
        "added": added,
        "no_phone": result.skipped_no_phone,
        "duplicate": skipped_dupe,
        "unknown_category": result.skipped_unknown_category,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner")
    ap.add_argument("--password")
    ap.add_argument("--name", default="Owner")
    ap.add_argument("--contacts")
    args = ap.parse_args()

    with SessionLocal() as db:
        print(f"professions added: {seed_professions(db)}")

        if args.owner:
            if not args.password:
                print("--password is required with --owner", file=sys.stderr)
                return 2
            created = seed_owner(db, args.owner, args.password, args.name)
            print(f"owner {'created' if created else 'already existed'}: {args.owner}")

        if args.contacts:
            print("contacts:", load_contacts(db, args.contacts))

        print("professionals on file:", db.scalar(select(func.count(Professional.id))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
