"""The cron command that runs the WhatsApp follow-up (scripts/run_follow_ups.py)."""
from __future__ import annotations

import io
import json
import urllib.error

from scripts import run_follow_ups


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def test_it_asks_the_api_to_run_the_due_steps_with_the_jobs_key(monkeypatch, capsys):
    sent = []

    def urlopen(request, timeout):
        sent.append(request)
        return _Response(json.dumps({"processed": 2, "sent": 2}).encode())

    monkeypatch.setattr(run_follow_ups.urllib.request, "urlopen", urlopen)
    monkeypatch.setenv("JOBS_SECRET", "k" * 32)
    monkeypatch.setenv("API_URL", "https://api.vilaow.test/")

    assert run_follow_ups.main() == 0
    (request,) = sent
    assert (request.method, request.full_url) == (
        "POST", "https://api.vilaow.test/api/follow-ups/run")
    assert request.get_header("X-jobs-key") == "k" * 32
    assert json.loads(capsys.readouterr().out)["sent"] == 2


def test_it_fails_loudly_without_the_key_or_when_refused(monkeypatch, capsys):
    monkeypatch.delenv("JOBS_SECRET", raising=False)
    assert run_follow_ups.main() == 1

    def refuse(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {},
                                     io.BytesIO(b'{"detail": "Wrong jobs key"}'))

    monkeypatch.setattr(run_follow_ups.urllib.request, "urlopen", refuse)
    monkeypatch.setenv("JOBS_SECRET", "wrong")
    assert run_follow_ups.main() == 1
    assert "403" in capsys.readouterr().err
