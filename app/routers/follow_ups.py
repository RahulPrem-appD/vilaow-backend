"""HTTP for the WhatsApp follow-up of introductions (change request 12).

Three audiences, so three routers:

  * staff, and the scheduler standing in for them — read the settings, and run
    whatever steps are due. The scheduler has no session, so it sends a shared
    secret in X-Jobs-Key instead; without one, the run needs a signed-in person.
  * Twilio or Meta — tells us about replies and deliveries. These addresses
    are on the open internet, so nothing is read until the provider's
    signature checks out, and they do not exist at all unless WhatsApp is
    switched on for real through that provider.

The rules live in app/services/follow_ups.py; this file reads requests and
shapes responses.
"""
from __future__ import annotations

import hmac
import json

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from starlette.concurrency import run_in_threadpool

from app.adapters.whatsapp import meta
from app.adapters.whatsapp.twilio import signed_by_twilio
from app.api.deps import (
    DbDep,
    FollowUpServiceDep,
    MetaWebhookDep,
    SettingsDep,
    StaffDep,
    TwilioWebhook,
    TwilioWebhookDep,
)
from app.schemas import FollowUpSettingsOut
from app.security import current_staff

router = APIRouter(prefix="/api/follow-ups", tags=["follow-ups"])
webhook_router = APIRouter(prefix="/api/whatsapp/twilio", tags=["whatsapp"])
meta_router = APIRouter(prefix="/api/whatsapp/meta", tags=["whatsapp"])

# What Twilio expects back from a message webhook: TwiML that says nothing.
EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


@router.get("/settings", response_model=FollowUpSettingsOut)
def follow_up_settings(service: FollowUpServiceDep, _staff: StaffDep) -> FollowUpSettingsOut:
    return FollowUpSettingsOut.model_validate(service.settings())


def _staff_or_scheduler(request: Request, db: DbDep, settings: SettingsDep) -> None:
    key = request.headers.get("x-jobs-key")
    if key is not None:
        # Compared in constant time, and refused outright when no secret is
        # set: an empty key must never match an empty setting.
        if not (settings.jobs_secret
                and hmac.compare_digest(key.encode(), settings.jobs_secret.encode())):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Wrong jobs key")
    else:
        current_staff(request, db)


@router.post("/run")
def run_due_steps(
    request: Request, service: FollowUpServiceDep, db: DbDep, settings: SettingsDep,
) -> dict:
    _staff_or_scheduler(request, db, settings)
    return service.run_due()


@router.post("/meta-setup")
def meta_setup(request: Request, db: DbDep, settings: SettingsDep) -> dict:
    """Connect Meta's side once: subscribe the app to the WhatsApp account,
    submit the templates Meta does not have yet, and report each one's status
    and the account's phone numbers. Safe to run again: it only adds what is
    missing. Needs only the token and the account id, so it runs while the
    follow-up is still off — the templates are approved before anything sends.
    """
    _staff_or_scheduler(request, db, settings)
    if not settings.meta_whatsapp_token or not settings.meta_waba_id:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Set META_WHATSAPP_TOKEN and META_WABA_ID first")
    return meta.setup(token=settings.meta_whatsapp_token, waba_id=settings.meta_waba_id,
                      messaging_id=settings.meta_messaging_account_id,
                      version=settings.meta_graph_version)


async def _signed_form(request: Request, hook: TwilioWebhook | None) -> dict[str, str]:
    """The posted fields, once Twilio's signature over them checks out."""
    if hook is None:
        # WhatsApp is not connected: as far as the internet knows, there is
        # nothing here.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    form = await request.form()
    params: dict[str, list[str]] = {}
    for name, value in form.multi_items():
        params.setdefault(name, []).append(str(value))
    # The address Twilio was told to call, not the one this request reports:
    # behind Render's proxy the scheme and host it sees are not the public ones.
    url = hook.base + request.url.path + (f"?{request.url.query}" if request.url.query else "")
    if not signed_by_twilio(hook.auth_token, url, params, request.headers.get("x-twilio-signature")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not signed by Twilio")
    return {name: values[0] for name, values in params.items()}


@webhook_router.post("/inbound")
async def twilio_inbound(
    request: Request, service: FollowUpServiceDep, hook: TwilioWebhookDep,
) -> Response:
    """Somebody wrote to Vilaow's WhatsApp number, or tapped Yes or No."""
    fields = await _signed_form(request, hook)
    number = fields.get("From", "").removeprefix("whatsapp:")
    if number:
        await run_in_threadpool(
            service.record_reply,
            number=number,
            text=fields.get("Body"),
            button=fields.get("ButtonPayload") or fields.get("ButtonText"),
            replied_to=fields.get("OriginalRepliedMessageSid"),
            provider_id=fields.get("MessageSid"),
        )
    return Response(EMPTY_TWIML, media_type="text/xml")


@webhook_router.post("/status", status_code=status.HTTP_204_NO_CONTENT)
async def twilio_status(
    request: Request, service: FollowUpServiceDep, hook: TwilioWebhookDep,
) -> Response:
    """A message we sent was delivered, read, or could not be delivered."""
    fields = await _signed_form(request, hook)
    if fields.get("MessageSid") and fields.get("MessageStatus"):
        await run_in_threadpool(
            service.record_delivery,
            provider_id=fields["MessageSid"],
            status=fields["MessageStatus"],
            error_code=fields.get("ErrorCode"),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Meta ────────────────────────────────────────────────────────────────────
@meta_router.get("/webhook")
def meta_verify(
    hook: MetaWebhookDep,
    mode: str | None = Query(None, alias="hub.mode"),
    token: str | None = Query(None, alias="hub.verify_token"),
    challenge: str | None = Query(None, alias="hub.challenge"),
) -> Response:
    """Meta checking this address is ours, when it is typed into the app."""
    if hook is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if not (mode == "subscribe" and token and challenge
            and hmac.compare_digest(token.encode(), hook.verify_token.encode())):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Wrong verify token")
    return Response(challenge, media_type="text/plain")


@meta_router.post("/webhook")
async def meta_events(
    request: Request, service: FollowUpServiceDep, hook: MetaWebhookDep,
) -> Response:
    """Replies to Vilaow's number, and reports on messages it sent."""
    if hook is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    body = await request.body()
    if not meta.signed_by_meta(hook.app_secret, body, request.headers.get("x-hub-signature-256")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not signed by Meta")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not JSON") from None
    for reply in meta.replies(payload):
        await run_in_threadpool(service.record_reply, transport="meta", **reply)
    for report in meta.deliveries(payload):
        await run_in_threadpool(service.record_delivery, provider="Meta", **report)
    return Response(status_code=status.HTTP_200_OK)
