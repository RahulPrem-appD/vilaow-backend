"""WhatsApp through Twilio: sending a template, and knowing a callback is Twilio's.

Sending is one POST to Twilio's Messages API with the numbers as
"whatsapp:+…", the approved template's ContentSid, and ContentVariables — a
JSON object of the blanks keyed "1", "2", … (Twilio's docs, "Send templates
created with the Content Template Builder").

Twilio calls back twice: when somebody replies, and when a message we sent is
delivered or read. Both arrive on the open internet, so each is checked
against Twilio's signature before it can touch an introduction (Twilio's docs,
"Webhooks security"): the exact address it called, then every posted field
sorted by name with its value appended, signed with HMAC-SHA1 under the
account's auth token and Base64-encoded, in the X-Twilio-Signature header.

The standard library does the HTTP rather than Twilio's SDK, which would be a
dependency for one request.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence

from app.ports.whatsapp import WhatsAppMessage, WhatsAppResult

MESSAGES_API = "https://api.twilio.com/2010-04-01/Accounts/{account}/Messages.json"

# (address, form fields, Authorization header) -> (HTTP status, response body)
Post = Callable[[str, dict[str, str], str], tuple[int, str]]


def _post(url: str, form: dict[str, str], authorization: str) -> tuple[int, str]:
    request = urllib.request.Request(
        url, data=urllib.parse.urlencode(form).encode(), method="POST",
        headers={"Authorization": authorization,
                 "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        # A refusal is an answer, not an outage: it carries Twilio's reason.
        return error.code, error.read().decode("utf-8", "replace")


def _json(body: str) -> dict:
    try:
        data = json.loads(body)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


class TwilioWhatsAppSender:
    transport = "twilio"

    def __init__(self, *, account_sid: str, auth_token: str, from_number: str,
                 content_sids: Mapping[str, str], status_callback: str | None = None,
                 post: Post = _post) -> None:
        self._account = account_sid
        self._from = from_number
        self._sids = dict(content_sids)
        self._status_callback = status_callback
        self._post = post
        token = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
        self._authorization = f"Basic {token}"

    def content_sid(self, template: str, language: str) -> str | None:
        """The approved template for this language, or the English one."""
        return self._sids.get(f"{template}.{language}") or self._sids.get(f"{template}.en")

    def send(self, message: WhatsAppMessage) -> WhatsAppResult:
        sid = self.content_sid(message.template, message.language)
        if not sid:
            return WhatsAppResult(
                False, f"No approved template for {message.template} yet "
                       f"(add it to TWILIO_CONTENT_SIDS)")
        form = {
            "To": f"whatsapp:{message.to}",
            "From": f"whatsapp:{self._from}",
            "ContentSid": sid,
            "ContentVariables": json.dumps(
                {str(n): value for n, value in enumerate(message.variables, start=1)},
                ensure_ascii=False),
        }
        if self._status_callback:
            form["StatusCallback"] = self._status_callback
        try:
            status, body = self._post(MESSAGES_API.format(account=self._account), form,
                                      self._authorization)
        except OSError as error:   # unreachable, or timed out: worth another try
            return WhatsAppResult(False, f"Could not reach Twilio ({type(error).__name__})",
                                  retry=True)
        data = _json(body)
        if 200 <= status < 300 and data.get("sid"):
            return WhatsAppResult(True, f"Accepted by Twilio ({data.get('status', 'queued')})",
                                  provider_id=str(data["sid"]))
        reason = f"Twilio refused it ({status}, error {data.get('code', '?')})"
        if data.get("message"):
            reason += f": {data['message']}"
        # Too many requests, or Twilio's own trouble, may pass. Anything else
        # — a bad number, an unapproved template — will not, so it stops here.
        return WhatsAppResult(False, reason, retry=status == 429 or status >= 500)


def signature(auth_token: str, url: str, params: Mapping[str, Sequence[str]]) -> str:
    payload = url + "".join(
        name + value for name in sorted(params) for value in sorted(set(params[name]))
    )
    mac = hmac.new(auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode("ascii")


def signed_by_twilio(auth_token: str, url: str, params: Mapping[str, Sequence[str]],
                     header: str | None) -> bool:
    if not auth_token or not header:
        return False
    expected = signature(auth_token, url, params)
    return hmac.compare_digest(expected.encode("utf-8"), header.encode("utf-8"))
