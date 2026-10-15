"""WhatsApp through Meta's Cloud API directly, in place of Twilio (12 October).

Sending is one POST to the Graph API's messages endpoint for Vilaow's number:
the approved template by name and language, its blanks as body parameters,
and a payload for each quick-reply button, so a tap comes back as "yes" or
"no" whatever language its label was in (Meta's docs, "Send message
templates"). Free text is the same POST with a text body, and Meta accepts it
only within 24 hours of the person's last message.

Meta calls back on one address for everything — replies and delivery reports
alike — and checks it first with a GET carrying a token we chose. Every POST is
signed: HMAC-SHA256 of the raw body under the app secret, hex, in
X-Hub-Signature-256 as "sha256=…" (Meta's docs, "Webhooks: event
notifications"). Nothing is read before that checks out.

`setup` does once, from the server, what would otherwise be a dozen forms in
WhatsApp Manager: it subscribes the app to the WhatsApp account, so replies
reach the webhook at all; submits every template in templates.py that Meta
does not have yet; and reports each one's status and the account's phone
numbers with their ids. The templates belong to the Messaging account, not
the WhatsApp account: Meta keeps them and the billing there, and refuses a
template sent to the WhatsApp account (subcode 2388339, October 2026).

The standard library does the HTTP, as for Twilio.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from datetime import datetime, timezone

from app.adapters.whatsapp.templates import TEMPLATES, Template
from app.ports.whatsapp import WhatsAppMessage, WhatsAppResult

GRAPH = "https://graph.facebook.com/{version}/"
# "Template name does not exist in the translation": that language is not
# approved (yet), so the English one is sent instead.
NO_TRANSLATION = 132001

# (method, address, JSON body or None, token) -> (HTTP status, response body)
Call = Callable[[str, str, dict | None, str], tuple[int, str]]


def _call(method: str, url: str, body: dict | None, token: str) -> tuple[int, str]:
    request = urllib.request.Request(
        url, method=method,
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        # A refusal is an answer, not an outage: it carries Meta's reason.
        return error.code, error.read().decode("utf-8", "replace")


def _json(body: str) -> dict:
    try:
        data = json.loads(body)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _error(data: dict) -> tuple[int | None, str]:
    """Meta's code, and its reason in the most specific words it gave: its
    "message" alone is often just "Invalid parameter"."""
    error = data.get("error") if isinstance(data.get("error"), dict) else {}
    details = (error.get("error_data") or {}).get("details") if isinstance(
        error.get("error_data"), dict) else None
    parts = [error.get("error_user_title"), error.get("error_user_msg") or details]
    reason = ": ".join(str(p) for p in parts if p) or error.get("message") or ""
    if error.get("error_subcode"):
        reason += f" (subcode {error['error_subcode']})"
    return error.get("code"), reason


class MetaWhatsAppSender:
    transport = "meta"

    def __init__(self, *, token: str, phone_number_id: str, version: str = "v25.0",
                 call: Call = _call) -> None:
        self._token = token
        self._url = GRAPH.format(version=version) + f"{phone_number_id}/messages"
        self._call = call

    def _payload(self, message: WhatsAppMessage, language: str) -> dict:
        template = TEMPLATES[message.template]
        components: list[dict] = [{
            "type": "body",
            "parameters": [{"type": "text", "text": value} for value in message.variables],
        }]
        for index, (payload, _labels) in enumerate(template.buttons):
            components.append({
                "type": "button", "sub_type": "quick_reply", "index": str(index),
                "parameters": [{"type": "payload", "payload": payload}],
            })
        return {
            "messaging_product": "whatsapp",
            "to": message.to.lstrip("+"),
            "type": "template",
            "template": {"name": template.meta_name, "language": {"code": language},
                         "components": components},
        }

    def send(self, message: WhatsAppMessage) -> WhatsAppResult:
        result, code = self._post(self._payload(message, message.language))
        if code == NO_TRANSLATION and message.language != "en":
            result, _ = self._post(self._payload(message, "en"))
        return result

    def send_text(self, to: str, text: str) -> WhatsAppResult:
        """Free text. Meta refuses it outside the 24 hours, with its reason."""
        return self._post({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to.lstrip("+"),
            "type": "text",
            # A link to a guide shows its preview; nothing else needs one.
            "text": {"body": text, "preview_url": "https://" in text},
        })[0]

    def _post(self, payload: dict) -> tuple[WhatsAppResult, int | None]:
        try:
            status, body = self._call("POST", self._url, payload, self._token)
        except OSError as error:   # unreachable, or timed out: worth another try
            return WhatsAppResult(False, f"Could not reach Meta ({type(error).__name__})",
                                  retry=True), None
        data = _json(body)
        messages = data.get("messages")
        if 200 <= status < 300 and isinstance(messages, list) and messages and messages[0].get("id"):
            return WhatsAppResult(True, "Accepted by Meta",
                                  provider_id=str(messages[0]["id"])), None
        code, reason = _error(data)
        detail = f"Meta refused it ({status}, error {code or '?'})"
        if reason:
            detail += f": {reason}"
        # Too many requests, or Meta's own trouble, may pass. Anything else
        # — a bad number, an unapproved template — will not, so it stops here.
        return WhatsAppResult(False, detail, retry=status == 429 or status >= 500), code


# ── the webhook ─────────────────────────────────────────────────────────────
def signed_by_meta(app_secret: str, body: bytes, header: str | None) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode("ascii"),
                               header.removeprefix("sha256=").encode("ascii", "replace"))


# Media that can carry a caption, as Meta names them.
_CAPTIONED = ("image", "video", "document")


def messages(payload: dict) -> Iterator[dict]:
    """Each message somebody sent Vilaow's number, with all the inbox keeps.

    `text` is the words: what was typed, a button's label, a caption, an
    emoji reaction. `at` is WhatsApp's time for it, when it gave one.
    """
    for value in _values(payload):
        names = {str(c.get("wa_id") or ""): (c.get("profile") or {}).get("name")
                 for c in value.get("contacts") or [] if isinstance(c, dict)}
        for message in value.get("messages") or []:
            sender = str(message.get("from") or "")
            if not sender:
                continue
            kind = str(message.get("type") or "unknown")
            part = message.get(kind) if isinstance(message.get(kind), dict) else {}
            text = button = None
            if kind == "text":
                text = part.get("body")
            elif kind == "button":   # a template's quick reply
                button, text = part.get("payload"), part.get("text")
            elif kind == "interactive":
                reply = part.get("button_reply") or part.get("list_reply") or {}
                button, text = reply.get("id"), reply.get("title")
            elif kind in _CAPTIONED:
                text = part.get("caption")
            elif kind == "reaction":
                text = part.get("emoji")
            elif kind == "location":
                text = ", ".join(str(part[k]) for k in ("name", "address") if part.get(k)) or None
            yield {
                "number": "+" + sender.lstrip("+"),
                "kind": kind[:16],
                "text": text,
                "button": button,
                "replied_to": (message.get("context") or {}).get("id"),
                "provider_id": message.get("id"),
                "profile_name": names.get(sender.lstrip("+")),
                "at": _time(message.get("timestamp")),
            }


def replies(payload: dict) -> Iterator[dict]:
    """Each message somebody sent Vilaow's number, as record_reply takes it."""
    for message in messages(payload):
        yield {key: message[key] for key in
               ("number", "text", "button", "replied_to", "provider_id")}


def _time(stamp: object) -> datetime | None:
    """Meta's seconds since 1970, as a time; None for anything else."""
    try:
        return datetime.fromtimestamp(int(str(stamp)), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def deliveries(payload: dict) -> Iterator[dict]:
    """Each delivery report on a message we sent, as record_delivery takes it."""
    for value in _values(payload):
        for report in value.get("statuses") or []:
            if not report.get("id") or not report.get("status"):
                continue
            errors = report.get("errors") or [{}]
            error = errors[0] if isinstance(errors[0], dict) else {}
            code = error.get("code")
            details = (error.get("error_data") or {}).get("details") if isinstance(
                error.get("error_data"), dict) else None
            yield {
                "provider_id": str(report["id"]),
                "status": str(report["status"]),
                "error_code": str(code) if code is not None else None,
                # Meta's own words, for the inbox: "Re-engagement message", …
                "error_title": details or error.get("title") or error.get("message"),
            }


def _values(payload: dict) -> Iterator[dict]:
    if not isinstance(payload, dict):
        return
    for entry in payload.get("entry") or []:
        for change in (entry or {}).get("changes") or []:
            if change.get("field") == "messages" and isinstance(change.get("value"), dict):
                yield change["value"]


# ── setting up ──────────────────────────────────────────────────────────────
def template_request(template: Template, language: str) -> dict:
    """One template in one language, as Meta's template endpoint takes it."""
    components: list[dict] = [{
        "type": "BODY",
        "text": template.texts[language],
        "example": {"body_text": [[sample for _what, sample in template.samples]]},
    }]
    if template.buttons:
        components.append({"type": "BUTTONS", "buttons": [
            {"type": "QUICK_REPLY", "text": labels[language]} for _payload, labels in template.buttons
        ]})
    return {"name": template.meta_name, "language": language, "category": "UTILITY",
            "components": components}


def _apps(status: int, data: dict) -> list[dict] | str | int:
    """The apps subscribed to an account, each with the address that replaces
    its own for this account's messages, if one does."""
    if status != 200:
        return _error(data)[1] or status
    return [{"id": (item.get("whatsapp_business_api_data") or {}).get("id"),
             "name": (item.get("whatsapp_business_api_data") or {}).get("name"),
             "override": item.get("override_callback_uri")}
            for item in data.get("data") or [] if isinstance(item, dict)]


def setup(*, token: str, waba_id: str, messaging_id: str = "", version: str = "v25.0",
          call: Call = _call) -> dict:
    """Subscribe the app, submit the missing templates, and say where each
    stands, and where Meta sends the account's messages."""
    base = GRAPH.format(version=version) + waba_id
    templates_base = GRAPH.format(version=version) + (messaging_id or waba_id)

    def get(path: str, root: str = base) -> tuple[int, dict]:
        status, body = call("GET", root + path, None, token)
        return status, _json(body)

    status, body = call("POST", base + "/subscribed_apps", None, token)
    subscribed = 200 <= status < 300 and _json(body).get("success") is True
    result: dict = {"subscribed": subscribed if subscribed else _error(_json(body))[1] or status}

    status, numbers = get("/phone_numbers?fields=id,display_phone_number,verified_name,status")
    result["phone_numbers"] = [
        {key: number.get(key) for key in ("id", "display_phone_number", "verified_name", "status")}
        for number in numbers.get("data") or []
    ] if status == 200 else _error(numbers)[1] or status

    # Where Meta sends the messages, for when none arrive: the app the token
    # belongs to (the one subscribed above), every app subscribed to the
    # accounts, and each number's own settings.
    graph = GRAPH.format(version=version)
    status, app = get("", graph + "app")
    result["token_app"] = ({key: app.get(key) for key in ("id", "name")} if status == 200
                           else _error(app)[1] or status)
    result["apps"] = _apps(*get("/subscribed_apps"))
    if messaging_id:
        result["messaging_account_apps"] = _apps(*get("/subscribed_apps", graph + messaging_id))
    for number in result["phone_numbers"] if isinstance(result["phone_numbers"], list) else []:
        status, data = get("?fields=platform_type,account_mode,webhook_configuration",
                           graph + str(number["id"]))
        number["webhooks"] = ({key: data.get(key) for key in
                               ("platform_type", "account_mode", "webhook_configuration")}
                              if status == 200 else _error(data)[1] or status)

    # Which account the templates went to, so a refusal can be read against it.
    result["templates_account"] = (f"Messaging account {messaging_id}" if messaging_id
                                   else f"WhatsApp account {waba_id} (no Messaging account set)")
    status, existing = get("/message_templates?fields=name,language,status,rejected_reason&limit=200",
                           templates_base)
    if status != 200:
        result["templates"] = _error(existing)[1] or status
        return result
    known = {(t.get("name"), t.get("language")): t for t in existing.get("data") or []}

    templates = []
    for template in TEMPLATES.values():
        for language in template.texts:
            found = known.get((template.meta_name, language))
            if found:
                state = found.get("status")
                if found.get("rejected_reason") not in (None, "", "NONE"):
                    state = f"{state} ({found['rejected_reason']})"
            else:
                status, body = call("POST", templates_base + "/message_templates",
                                    template_request(template, language), token)
                data = _json(body)
                state = (f"submitted, {data.get('status', 'PENDING')}"
                         if 200 <= status < 300 and data.get("id")
                         else f"refused: {_error(data)[1] or status}")
            templates.append({"name": template.meta_name, "language": language, "status": state})
    result["templates"] = templates
    return result
