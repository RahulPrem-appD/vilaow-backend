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

if TYPE_CHECKING:  # imported lazily below to keep the graph acyclic
    from app.services.agreements import AgreementService
    from app.services.assets import AssetService
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
def _firebase(bucket: str, credentials: str | None) -> StorageBackend:
    return FirebaseStorage(bucket, credentials)


def get_storage(settings: SettingsDep) -> StorageBackend:
    if settings.firebase_bucket:
        return _firebase(settings.firebase_bucket, settings.firebase_credentials_file or None)
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


def get_introduction_service(
    db: DbDep, clock: ClockDep, email: EmailDep, urls: UrlsDep,
) -> "IntroductionService":
    from app.services.introductions import IntroductionService

    return IntroductionService(db, clock=clock, email=email, urls=urls)


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

StaffDep = Annotated[Staff, Depends(current_staff)]
