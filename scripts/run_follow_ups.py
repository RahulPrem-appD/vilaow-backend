"""Send the WhatsApp follow-up's due steps: the reminders to a professional who
has not confirmed contact, and the check with the buyer two days on.

An introduction's first two messages go out the moment it is made; everything
after waits for a run, and without this nothing runs it but the admin's "Run
due steps now" button.

A command for Render's cron, every 10 minutes, for the same reason
send_review_requests is one. Unlike that one it asks the API to do the work,
through the endpoint the button uses, with the jobs key in place of a staff
session: so the job needs JOBS_SECRET, the same value as the API's, rather
than its own copy of the database address and Meta's keys.

    JOBS_SECRET=... uv run python -m scripts.run_follow_ups

API_URL points it at another API; the live one is the default. Safe to run as
often as you like: each step is taken once, under a row lock.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

LIVE_API = "https://vilaow-backend.onrender.com"


def main() -> int:
    key = os.environ.get("JOBS_SECRET", "")
    if not key:
        print("JOBS_SECRET is not set: the API would refuse the run.", file=sys.stderr)
        return 1
    api = os.environ.get("API_URL", LIVE_API).rstrip("/")
    request = urllib.request.Request(f"{api}/api/follow-ups/run", method="POST",
                                     headers={"X-Jobs-Key": key})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            print(json.dumps(json.load(response)))
    except urllib.error.HTTPError as error:
        reason = error.read().decode(errors="replace")[:300]
        print(f"The API refused the run: {error.code} {reason}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"The API did not answer: {getattr(error, 'reason', error)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
