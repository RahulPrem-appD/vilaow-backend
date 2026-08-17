"""Session login/logout/whoami. See app/security.py for the actual rules —
this router just wires them to HTTP."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.clients import client_ip
from app.api.deps import SettingsDep
from app.api.throttle import login_attempts
from app.db import get_db
from app.models import Staff
from app.schemas import LoginRequest, StaffOut
from app.security import authenticate, clear_session, current_staff, issue_session

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=StaffOut)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    settings: SettingsDep,
    db: Session = Depends(get_db),
) -> StaffOut:
    # Throttled on the address *and* the account. This endpoint is on the open
    # internet and had no limit at all, so an attacker could run unlimited
    # parallel guesses — and because every guess costs a bcrypt hash, the same
    # requests are a cheap way to exhaust a small instance's CPU.
    #
    # Both keys matter: per-address stops one host grinding through passwords,
    # per-account stops a distributed attempt at one known email. 429 rather
    # than 401 tells an honest person who has mistyped what is actually
    # happening, and tells an attacker nothing they cannot already measure.
    caller = client_ip(request, settings)
    account = payload.email.strip().lower()
    if not login_attempts.check(caller) or not login_attempts.check(f"account:{account}"):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many sign-in attempts. Wait a minute and try again.",
        )

    staff = authenticate(db, payload.email, payload.password)
    if staff is None:
        # Wrong password and an inactive account look identical here on
        # purpose — authenticate() already refuses inactive staff even with
        # the right password, so there is nothing more specific to say.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    # Getting in clears the count, so one mistyped password does not follow
    # someone around for a minute after they succeed.
    login_attempts.clear(caller)
    login_attempts.clear(f"account:{account}")
    issue_session(response, staff)
    return StaffOut.model_validate(staff)


@router.post("/logout")
def logout(response: Response) -> dict:
    clear_session(response)
    return {"status": "ok"}


@router.get("/me", response_model=StaffOut)
def me(staff: Staff = Depends(current_staff)) -> StaffOut:
    return StaffOut.model_validate(staff)
