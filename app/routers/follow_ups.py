"""HTTP for the WhatsApp follow-up of introductions (change request 12).

Three audiences, so two routers:

  * staff, and the scheduler standing in for them — read the settings, and run
    whatever steps are due. The scheduler has no session, so it sends a shared
    secret in X-Jobs-Key instead; without one, the run needs a signed-in person.
  * Twilio — tells us about replies and deliveries. These two addresses are on
    the open internet, so nothing is read until Twilio's signature checks out,
    and they do not exist at all unless WhatsApp is switched on for real.

The rules live in app/services/follow_ups.py; this file reads requests and
shapes responses.
"""
from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request, Response, status
from starlette.concurrency import run_in_threadpool

from app.adapters.whatsapp.twilio import signed_by_twilio
from app.api.deps import (
    DbDep,
    FollowUpServiceDep,
    SettingsDep,
    StaffDep,
    TwilioWebhook,
    TwilioWebhookDep,
)
from app.schemas import FollowUpSettingsOut
from app.security import current_staff

router = APIRouter(prefix="/api/follow-ups", tags=["follow-ups"])
webhook_router = APIRouter(prefix="/api/whatsapp/twilio", tags=["whatsapp"])

# What Twilio expects back from a message webhook: TwiML that says nothing.
EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


@router.get("/settings", response_model=FollowUpSettingsOut)
def follow_up_settings(service: FollowUpServiceDep, _staff: StaffDep) -> FollowUpSettingsOut:
    return FollowUpSettingsOut.model_validate(service.settings())


@router.post("/run")
def run_due_steps(
    request: Request, service: FollowUpServiceDep, db: DbDep, settings: SettingsDep,
) -> dict:
    key = request.headers.get("x-jobs-key")
    if key is not None:
        # Compared in constant time, and refused outright when no secret is
        # set: an empty key must never match an empty setting.
        if not (settings.jobs_secret
                and hmac.compare_digest(key.encode(), settings.jobs_secret.encode())):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Wrong jobs key")
    else:
        current_staff(request, db)
    return service.run_due()


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
