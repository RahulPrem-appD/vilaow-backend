"""The admin API: every router, CORS for the admin frontend, and a health
check Render (or anyone else) can poll.
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Refuses to start in production with the published dev secret key, and
    # refuses to start a development process against the production database.
    # See Settings.validate_for_production for why each matters.
    settings.validate_for_production()

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
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        database = "unreachable"
    return {"status": "ok", "database": database}
