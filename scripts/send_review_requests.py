"""Ask buyers who went ahead to leave a review.

The whole verified-review loop hung on this and nothing called it. The service
method existed, an endpoint existed, it was covered by tests — and there was no
cron job, no scheduler and no button, so it had never run outside a test except
when someone drove it by hand. Verified reviews are the product's
differentiator, and they were silently never going to accumulate.

Deliberately a command rather than a background thread. A thread that quietly
stopped would put us straight back here, and Render's own cron reports a failed
run; a thread reports nothing. It is also why the endpoint alone was not enough:
it requires a staff session, which a scheduler does not have.

    uv run python -m scripts.send_review_requests
    uv run python -m scripts.send_review_requests --dry-run

Safe to run as often as you like: `review_requested_at` is stamped only after a
send succeeds, so a failure retries on the next run and a success is never
repeated.
"""
from __future__ import annotations

import argparse
import sys

from app.adapters.email.senders import InMemoryEmailSender
from app.api.deps import get_email_sender, get_urls
from app.config import get_settings
from app.db import SessionLocal
from app.ports.clock import SystemClock
from app.services.introductions import IntroductionService


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what is due without sending anything")
    args = parser.parse_args()

    settings = get_settings()
    settings.validate_for_production()

    # Wired through the app's own composition root, so this cannot drift from
    # how the API sends mail. Unconfigured SMTP yields a sender that reports
    # failure rather than pretending, which leaves the rows unstamped for the
    # next run.
    sender = InMemoryEmailSender() if args.dry_run else get_email_sender(settings)
    if not args.dry_run and not settings.email_configured:
        print("SMTP is not configured — nothing will actually be delivered.",
              file=sys.stderr)

    with SessionLocal() as db:
        service = IntroductionService(
            db, email=sender, clock=SystemClock(), urls=get_urls(settings),
        )
        result = service.send_due_review_requests()

    due, sent = result.get("due", 0), result.get("sent", 0)
    print(f"due={due} sent={sent}" + (" (dry run — nothing delivered)" if args.dry_run else ""))

    # The interesting case, and the one worth shouting about: work was waiting
    # and none of it went out. A silent zero here is how this stayed broken.
    if due and not sent and not args.dry_run:
        print(f"{due} review request(s) were due and none were sent. "
              f"Check SMTP settings and the app's event log.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
