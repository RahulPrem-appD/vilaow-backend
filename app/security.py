"""Staff authentication.

This replaces what Supabase was doing. Two previous versions of this admin
shipped a login screen that checked the fields were non-empty and then let
anyone through, so the rules are written down here rather than assumed:

  * a password is never stored, only a bcrypt hash
  * an inactive account is refused even with the right password
  * the session cookie is signed, so its contents cannot be edited by the holder
  * role is read from the database on every request, never from the cookie —
    a cookie that carries "role=owner" is a cookie the holder can forge
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, Response, status
import base64
import hashlib

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Role, Staff

settings = get_settings()
_signer = URLSafeTimedSerializer(settings.secret_key, salt="vilaow-session")


def _prepare(raw: str) -> bytes:
    """SHA-256 then base64, before bcrypt sees it.

    bcrypt takes at most 72 bytes and silently ignores the rest, so without
    this a long passphrase is only as strong as its first 72 bytes. Hashing
    first gives a fixed 44-byte input, which also sidesteps passlib's broken
    length check against bcrypt 5 — a 15-character password was being rejected
    as "longer than 72 bytes", which is why passlib is no longer used here.
    """
    return base64.b64encode(hashlib.sha256(raw.encode("utf-8")).digest())


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(_prepare(raw), bcrypt.gensalt()).decode()


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare(raw), hashed.encode())
    except (ValueError, TypeError):
        return False


def issue_session(response: Response, staff: Staff) -> None:
    """Only the id goes in the cookie. Everything else is looked up."""
    token = _signer.dumps({"sid": staff.id})
    response.set_cookie(
        settings.session_cookie,
        token,
        max_age=settings.session_max_age,
        httponly=True,                      # not readable from JavaScript
        # See Settings.session_samesite. A browser ignores SameSite=None
        # unless Secure is also set, so the two travel together — otherwise
        # switching to "none" would appear to work and silently do nothing.
        samesite=settings.session_samesite,
        secure=settings.is_production or settings.session_samesite == "none",
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(settings.session_cookie, path="/")


def current_staff(request: Request, db: Session = Depends(get_db)) -> Staff:
    raw = request.cookies.get(settings.session_cookie)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    try:
        data = _signer.loads(raw, max_age=settings.session_max_age)
    except SignatureExpired:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    except BadSignature:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session")

    staff = db.get(Staff, data.get("sid"))
    if staff is None or not staff.active:
        # Deactivating someone must lock them out immediately, even though
        # their cookie is still cryptographically valid.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account is not active")

    staff.last_active_at = datetime.now(timezone.utc)
    db.commit()
    return staff


def require_owner(staff: Staff = Depends(current_staff)) -> Staff:
    if staff.role != Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Owners only")
    return staff


def authenticate(db: Session, email: str, password: str) -> Staff | None:
    staff = db.scalar(select(Staff).where(Staff.email == email.strip().lower()))
    if staff is None:
        # Hash anyway so a missing account and a wrong password take about the
        # same time; otherwise the response time enumerates valid addresses.
        hash_password("timing-equaliser")
        return None
    if not staff.active or not verify_password(password, staff.password_hash):
        return None
    return staff
