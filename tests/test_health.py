"""/health — what the platform actually keys on.

Render's `healthCheckPath` and the Dockerfile's HEALTHCHECK both call it with
`curl -fsS`, which fails on HTTP status and never reads the body. The endpoint
returned 200 with `"database": "unreachable"` in the body, so a container whose
database had gone reported healthy, kept its place in the rotation, and served
500s to real traffic. The body was right and nothing was listening to it.
"""
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


def test_healthy_is_200_and_names_each_dependency(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    # Booleans only. A health endpoint that leaked a host or a user would be a
    # worse bug than the one this file exists for.
    assert body["email"] in {"configured", "not configured"}
    assert body["storage"] in {"configured", "not configured"}
    assert "smtp" not in r.text.lower() and "@" not in r.text


def test_a_dead_database_fails_the_status_line_not_just_the_body():
    """The whole point: `curl -fsS` has to fail, so the status must be non-2xx."""
    with patch("app.main.engine") as engine:
        engine.connect.side_effect = OSError("connection refused")
        r = TestClient(app, raise_server_exceptions=False).get("/health")
    assert r.status_code == 503
    assert r.json()["detail"]["database"] == "unreachable"


def test_health_needs_no_session(client):
    """It is called by the platform, which has no cookie."""
    assert client.get("/health").status_code == 200


def test_a_momentary_blip_does_not_flip_the_status_line():
    """One failed SELECT must not restart the container.

    This path is `healthCheckPath` in render.yaml and the Dockerfile's
    HEALTHCHECK, so a single failure decided whether a deploy rolled back and
    whether a running service was restarted. Render Postgres failover makes a
    connection fail for a few seconds — and restarting this process cannot fix
    a database, so a restart loop is strictly worse than answering slowly.
    """
    attempts = {"n": 0}

    class Conn:
        def execute(self, *_a):
            return None

    class Ctx:
        def __enter__(self):
            return Conn()

        def __exit__(self, *_a):
            return False

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise OSError("failover")
        return Ctx()

    with patch("app.main.engine") as engine:
        engine.connect.side_effect = lambda: flaky()
        r = TestClient(app, raise_server_exceptions=False).get("/health")

    assert r.status_code == 200, "a single blip must not report unhealthy"
    assert attempts["n"] == 2, "it should have retried exactly once here"


def test_a_real_outage_still_fails_and_answers_inside_the_probe_timeout():
    """Retrying must not turn the probe into a timeout of its own.

    The Dockerfile gives curl 5 seconds. If deciding took longer than that the
    probe would fail on timeout instead of status, which is the same outcome by
    a worse route — and would hide the body that names the dependency.
    """
    import time

    with patch("app.main.engine") as engine:
        engine.connect.side_effect = OSError("connection refused")
        started = time.monotonic()
        r = TestClient(app, raise_server_exceptions=False).get("/health")
        elapsed = time.monotonic() - started

    assert r.status_code == 503
    assert elapsed < 3.0, f"took {elapsed:.1f}s; the probe allows 5s"
