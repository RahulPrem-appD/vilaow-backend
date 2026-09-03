"""The admin API: every router, CORS for the admin frontend, and a health
check Render (or anyone else) can poll.
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from time import sleep

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import errors as api_errors
from app.config import get_settings
from app.db import engine
from app.routers import (
    agreements, assets, auth, imports, introductions, leads, professionals, professions,
    public, staff,
)

settings = get_settings()

# Logging, because there was none. Modules call logging.getLogger("vilaow.…")
# and nothing configured a handler, so every message they wrote went nowhere:
# a storage outage, an unreadable import, a failed send — all invisible in
# production, leaving an operator with a symptom and no way to find the cause.
# The events table remains the audit trail for what the *business* did; this is
# for what the *process* did.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    # Render captures stdout. Unbuffered, so a crash does not eat the last
    # lines — which are the interesting ones.
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("vilaow")


def _check_configuration() -> None:
    """Fail with a readable message, not a forty-line traceback.

    Raising out of the lifespan works, but FastAPI nests its lifespan context
    managers about fifteen deep, so the traceback buries the one line that says
    what is wrong under a wall of `merged_lifespan` frames. On a hosting
    dashboard, where you are reading a log through a small window, that is the
    difference between a five-second fix and a puzzle.

    The message is printed on its own and the process exits. Exit code 1, so
    the platform still reports a failed deploy rather than a crash loop.

    Import-time, so nothing has had a chance to wrap it yet.
    """
    try:
        settings.validate_for_production()
    except RuntimeError as problem:
        print("\n" + "=" * 72, file=sys.stderr)
        print("VILAOW WILL NOT START", file=sys.stderr)
        print("=" * 72, file=sys.stderr)
        print(problem, file=sys.stderr)
        print("=" * 72 + "\n", file=sys.stderr)
        sys.stderr.flush()
        raise SystemExit(1) from None


# Checked here, at import, and deliberately not inside the lifespan. Starlette
# wraps a lifespan failure in its own traceback whatever it is raised as, so
# the one line that says what is wrong ends up under a wall of frames — on a
# hosting dashboard that is the difference between a five-second fix and a
# puzzle. At import there is nothing to wrap it.
_check_configuration()


@asynccontextmanager
async def lifespan(app: FastAPI):

    # Said once, at boot, so an operator can see what this process can actually
    # do rather than inferring it from a failure. `greek_capable` is here
    # because a missing font silently corrupts every Greek name on a signed
    # agreement, and nothing else would ever mention it.
    from app.adapters.pdf.agreement import BODY, greek_capable

    log.info("starting: environment=%s email=%s storage=%s pdf_font=%s",
             settings.environment,
             "smtp" if settings.email_configured else "NOT CONFIGURED",
             settings.firebase_bucket or "local disk",
             BODY if greek_capable() else f"{BODY} (NO GREEK)")
    yield
    log.info("shutting down")


app = FastAPI(title="Vilaow Admin API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Domain refusals become status codes in exactly one place.
api_errors.install(app)

app.include_router(public.router)   # the website reads through this
# The one public *write*: a buyer asking to be introduced. Separate router,
# same rule — nothing here may read across into the admin schemas.
app.include_router(introductions.public_router)
app.include_router(introductions.router)
app.include_router(auth.router)
app.include_router(professionals.router)
app.include_router(imports.router)
app.include_router(leads.router)
app.include_router(agreements.router)
app.include_router(professions.router)
app.include_router(staff.router)
# Photos are public; documents are owner-only. The rule lives in the router.
app.include_router(assets.router)


@app.get("/health")
def health() -> dict:
    """Is this deployment able to do its job?

    Reports whether each dependency is *configured and reachable*, never what it
    is configured with. `email` is the one that has actually bitten us: Render
    blocked outbound SMTP on the old plan, and the only symptom was signing
    invitations silently never arriving. A boolean here is worth far more than
    it costs — it says nothing an attacker can use, and it turns "is mail set
    up on prod?" from a support thread into one HTTP request.
    """
    database = "unreachable"
    # Three tries, not one. This endpoint is `healthCheckPath` in render.yaml
    # and the Dockerfile's HEALTHCHECK, so a single failed `SELECT 1` decided
    # whether a deploy was rolled back and whether a running container was
    # restarted. Render Postgres failover makes a connection fail for a few
    # seconds; a saturated pool can outlast the probe's own timeout. Neither is
    # something restarting this process can fix, and a restart loop against a
    # database that is merely busy is worse than serving the request slowly.
    #
    # Bounded well under the 5s curl timeout the Dockerfile sets, so the probe
    # never times out waiting for us to finish deciding.
    for attempt in range(3):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            database = "ok"
            break
        except Exception:
            if attempt < 2:
                sleep(0.4)

    # 503, not 200-with-a-sad-field. Both probes use `curl -fsS`, which fails
    # on status and reads no body — so a container whose database was gone
    # reported healthy and served 500s. The body still names the dependency.
    if database != "ok":
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {"status": "degraded", "database": database},
        )

    return {
        "status": "ok",
        "database": database,
        "email": "configured" if settings.email_configured else "not configured",
        "storage": "configured" if settings.firebase_configured else "not configured",
    }
