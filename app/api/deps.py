"""Composition. The one place that decides which implementation is in play.

Everything below the web layer depends on a port — a Clock, an EmailSender, a
StorageBackend — and nothing below the web layer chooses which one. That choice
is made here, from settings, and can be replaced wholesale in a test with
FastAPI's `dependency_overrides`.

That is the practical payoff of the ports: the test suite no longer has to
monkeypatch a module global to stop the app emailing real people. It hands the
app a different sender.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Annotated, TYPE_CHECKING

from fastapi import Depends
from sqlalchemy.orm import Session

from app.adapters.email.senders import NullEmailSender, SmtpEmailSender
from app.adapters.storage.firebase import FirebaseStorage
from app.adapters.storage.local import LocalStorage
from app.adapters.urls import PublicUrls
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Staff
from app.security import current_staff
from app.domain.errors import StorageFailure
from app.ports.clock import Clock, SystemClock
from app.ports.email import EmailSender
from app.ports.storage import StorageBackend
from app.ports.whatsapp import WhatsAppSender

if TYPE_CHECKING:  # imported lazily below to keep the graph acyclic
    import anthropic

    from app.services.agreements import AgreementService
    from app.services.assistant import Assistant
    from app.services.assets import AssetService
    from app.services.follow_ups import FollowUpConfig, FollowUpService
    from app.services.introductions import IntroductionService, VerifiedReviewService
    from app.services.professionals import ProfessionalService
    from app.services.professions import ProfessionService

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[Session, Depends(get_db)]


def get_clock() -> Clock:
    return SystemClock()


ClockDep = Annotated[Clock, Depends(get_clock)]


@lru_cache
def _smtp_sender(host: str, port: int, user: str, password: str,
                 from_address: str, from_name: str) -> EmailSender:
    return SmtpEmailSender(host=host, port=port, user=user, password=password,
                           from_address=from_address, from_name=from_name)


def get_email_sender(settings: SettingsDep) -> EmailSender:
    if not settings.email_configured:
        # Reports the failure rather than hiding it: a pipeline that stalls
        # with no explanation is worse than one that fails loudly.
        return NullEmailSender()
    return _smtp_sender(settings.smtp_host, settings.smtp_port, settings.smtp_user,
                        settings.smtp_password, settings.mail_from, settings.smtp_from_name)


EmailDep = Annotated[EmailSender, Depends(get_email_sender)]


def get_urls(settings: SettingsDep) -> PublicUrls:
    return PublicUrls(settings.public_site_url)


UrlsDep = Annotated[PublicUrls, Depends(get_urls)]


@lru_cache
def _firebase(bucket: str, credentials_file: str | None,
              credentials_json: str | None) -> StorageBackend:
    """Cached, because building a client opens a connection and reads a key.

    The key is part of the cache argument rather than fetched inside, so
    changing it in the environment produces a new client instead of quietly
    reusing one built from the old value.
    """
    return FirebaseStorage(bucket, credentials_file, credentials_json)


def get_storage(settings: SettingsDep) -> StorageBackend:
    if settings.firebase_bucket:
        return _firebase(
            settings.firebase_bucket,
            settings.firebase_credentials_file or None,
            settings.firebase_credentials_json or None,
        )
    if settings.is_production:
        # A local disk in production is silent data loss on the next deploy.
        raise StorageFailure(
            "FIREBASE_BUCKET is unset in production. Refusing to fall back to local "
            "disk: Render's filesystem is ephemeral, so every uploaded photo and "
            "licence would vanish on the next deploy."
        )
    return LocalStorage(Path(__file__).resolve().parents[2] / ".uploads")


StorageDep = Annotated[StorageBackend, Depends(get_storage)]


# ── services ────────────────────────────────────────────────────────────────
# Each is assembled from ports, never from concrete adapters, so a router asks
# for a use case and has no idea what is behind it.

def get_agreement_service(
    db: DbDep, clock: ClockDep, email: EmailDep, urls: UrlsDep, settings: SettingsDep,
) -> "AgreementService":
    from app.services.agreements import AgreementService

    return AgreementService(
        db, clock=clock, email=email, urls=urls,
        terms_version=settings.terms_version,
        ttl_days=settings.agreement_ttl_days,
    )


AgreementServiceDep = Annotated["AgreementService", Depends(get_agreement_service)]


# ── the WhatsApp follow-up (change request 12) ─────────────────────────────

def get_follow_up_config(settings: SettingsDep) -> "FollowUpConfig":
    from app.services.follow_ups import MODES, FollowUpConfig, FollowUpPolicy

    def hours(value: float, default: float) -> timedelta:
        # A zero or negative gap would send every reminder at once.
        return timedelta(hours=value if value > 0 else default)

    return FollowUpConfig(
        mode=settings.whatsapp_mode if settings.whatsapp_mode in MODES else "off",
        pro_language=settings.whatsapp_pro_language,
        policy=FollowUpPolicy(
            first_reminder=hours(settings.followup_first_reminder_hours, 4),
            reminder_every=hours(settings.followup_reminder_every_hours, 24),
            max_reminders=max(0, min(10, settings.followup_max_reminders)),
            buyer_check=hours(settings.followup_buyer_check_hours, 48),
        ),
    )


FollowUpConfigDep = Annotated["FollowUpConfig", Depends(get_follow_up_config)]


@lru_cache
def _twilio(account_sid: str, auth_token: str, from_number: str, content_sids: str,
            status_callback: str | None) -> WhatsAppSender:
    from app.adapters.whatsapp.twilio import TwilioWhatsAppSender

    try:
        sids = json.loads(content_sids or "{}")
    except ValueError:
        sids = {}   # each send then fails with "no approved template", visibly
    return TwilioWhatsAppSender(
        account_sid=account_sid, auth_token=auth_token, from_number=from_number,
        content_sids=sids if isinstance(sids, dict) else {},
        status_callback=status_callback,
    )


def get_whatsapp_sender(settings: SettingsDep) -> WhatsAppSender | None:
    """None while WhatsApp is off, or switched on without its credentials:
    the follow-up then plans nothing, rather than planning messages that
    cannot leave."""
    if settings.whatsapp_mode == "pretend":
        from app.adapters.whatsapp.pretend import PretendWhatsAppSender

        return PretendWhatsAppSender()
    if (settings.whatsapp_mode == "twilio" and settings.twilio_account_sid
            and settings.twilio_auth_token and settings.twilio_whatsapp_from):
        base = settings.twilio_webhook_base.rstrip("/")
        return _twilio(settings.twilio_account_sid, settings.twilio_auth_token,
                       settings.twilio_whatsapp_from, settings.twilio_content_sids,
                       f"{base}/api/whatsapp/twilio/status" if base else None)
    return None


WhatsAppDep = Annotated[WhatsAppSender | None, Depends(get_whatsapp_sender)]


def get_follow_up_service(
    db: DbDep, clock: ClockDep, sender: WhatsAppDep, config: FollowUpConfigDep,
) -> "FollowUpService":
    from app.services.follow_ups import FollowUpService

    return FollowUpService(db, clock=clock, sender=sender, config=config)


FollowUpServiceDep = Annotated["FollowUpService", Depends(get_follow_up_service)]


@dataclass(frozen=True)
class TwilioWebhook:
    """What checking a callback needs: the secret it is signed with, and the
    address Twilio was told to call."""
    auth_token: str
    base: str


def get_twilio_webhook(settings: SettingsDep) -> TwilioWebhook | None:
    if (settings.whatsapp_mode != "twilio" or not settings.twilio_auth_token
            or not settings.twilio_webhook_base):
        return None
    return TwilioWebhook(settings.twilio_auth_token, settings.twilio_webhook_base.rstrip("/"))


TwilioWebhookDep = Annotated[TwilioWebhook | None, Depends(get_twilio_webhook)]


def get_introduction_service(
    db: DbDep, clock: ClockDep, email: EmailDep, urls: UrlsDep,
    follow_ups: FollowUpServiceDep,
) -> "IntroductionService":
    from app.services.introductions import IntroductionService

    return IntroductionService(db, clock=clock, email=email, urls=urls, follow_ups=follow_ups)


IntroductionServiceDep = Annotated["IntroductionService", Depends(get_introduction_service)]


def get_verified_review_service(db: DbDep, clock: ClockDep) -> "VerifiedReviewService":
    from app.services.introductions import VerifiedReviewService

    return VerifiedReviewService(db, clock=clock)


VerifiedReviewServiceDep = Annotated["VerifiedReviewService", Depends(get_verified_review_service)]

# Every staff-only route needs the same annotation; naming it once keeps the
# routers from repeating `Depends(current_staff)` in every signature.
def get_asset_service(db: DbDep, storage: StorageDep, clock: ClockDep) -> "AssetService":
    from app.services.assets import AssetService

    return AssetService(db, storage=storage, clock=clock)


AssetServiceDep = Annotated["AssetService", Depends(get_asset_service)]

def get_professional_service(db: DbDep, clock: ClockDep) -> "ProfessionalService":
    from app.services.professionals import ProfessionalService

    return ProfessionalService(db, clock=clock)


ProfessionalServiceDep = Annotated["ProfessionalService", Depends(get_professional_service)]


def get_profession_service(db: DbDep) -> "ProfessionService":
    from app.services.professions import ProfessionService

    return ProfessionService(db)


ProfessionServiceDep = Annotated["ProfessionService", Depends(get_profession_service)]


@lru_cache
def _anthropic(api_key: str, base_url: str | None = None) -> "anthropic.Anthropic":
    """One client per key and endpoint, for the same reason as the Firebase
    one above.

    Sixty seconds of silence ends a request: an answer streams in well under
    that, and a visitor should hear "try again" rather than wait on a hung
    connection.
    """
    import anthropic

    return anthropic.Anthropic(api_key=api_key, base_url=base_url,
                               timeout=anthropic.Timeout(60.0, connect=10.0))


def get_assistant_client(settings: SettingsDep) -> "anthropic.Anthropic":
    from fastapi import HTTPException, status

    from app.services.assistant import GLM_BASE_URL

    # Refused before a client is built: there is no key to build one with.
    if not settings.assistant_configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "The assistant is not switched on.")
    if settings.assistant_provider == "glm":
        return _anthropic(settings.glm_api_key, GLM_BASE_URL)
    return _anthropic(settings.anthropic_api_key)


AssistantClientDep = Annotated["anthropic.Anthropic", Depends(get_assistant_client)]


def get_assistant(settings: SettingsDep, client: AssistantClientDep) -> "Assistant":
    from app.db import SessionLocal
    from app.services.assistant import CLAUDE_MODEL, GLM_MODEL, Assistant

    claude = settings.assistant_provider == "claude"
    return Assistant(client, SessionLocal,
                     model=settings.assistant_model or (CLAUDE_MODEL if claude else GLM_MODEL),
                     effort=settings.assistant_effort, claude=claude)


AssistantDep = Annotated["Assistant", Depends(get_assistant)]

StaffDep = Annotated[Staff, Depends(current_staff)]
