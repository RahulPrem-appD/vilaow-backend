"""WhatsApp from a typed phone number to Twilio's answer (change request 12).

Most of the rules tested here are not ours to decide: Meta rejects a template
that breaks its wording rules, Twilio refuses a badly shaped request, a
callback without the right signature is just a stranger posting, and a
number without a country code reaches nobody — or worse, someone else. So
these tests hold each provider's rules letter by letter, with no database,
leaving the introduction tests free to be about introductions.
"""
from __future__ import annotations

import base64
import json
import re
from types import SimpleNamespace

from app.adapters.whatsapp import templates
from app.adapters.whatsapp.twilio import (TwilioWhatsAppSender, signature,
                                          signed_by_twilio)
from app.config import Settings
from app.domain.phones import buyer_whatsapp, professional_whatsapp
from app.ports.whatsapp import WhatsAppMessage
from app.services.follow_ups import parse_answer


# ── phone numbers ───────────────────────────────────────────────────────────
def test_a_buyers_number_takes_many_spellings_but_needs_a_country_code():
    assert buyer_whatsapp("+44 7700 900123").number == "+447700900123"
    assert buyer_whatsapp("0044 7700 900123").number == "+447700900123"
    # The national 0 in brackets is dropped, not kept as "+4407700…".
    assert buyer_whatsapp("+44 (0) 7700 900123").number == "+447700900123"
    assert buyer_whatsapp("+44(0)7700 900123").number == "+447700900123"

    national = buyer_whatsapp("07700 900123")
    assert national.number is None
    assert "country code" in national.problem

    empty = buyer_whatsapp("")
    assert (empty.number, empty.problem) == (None, "No phone number")

    assert buyer_whatsapp("+0123 456 789").number is None


def test_a_professionals_number_is_read_as_greek_when_undecorated():
    assert professional_whatsapp("691 234 5678").number == "+306912345678"
    assert professional_whatsapp("306912345678").number == "+306912345678"
    assert professional_whatsapp("+30 691 234 5678").number == "+306912345678"
    assert professional_whatsapp("+30 (0) 691 234 5678").number == "+306912345678"
    assert professional_whatsapp("+44 7700 900123").number == "+447700900123"

    landline = professional_whatsapp("(+30) 210-123 4567")
    assert landline.number is None
    assert "landline" in landline.problem

    short = professional_whatsapp("12345")
    assert short.number is None
    assert "not a full number" in short.problem

    nothing = professional_whatsapp(None)
    assert nothing.number is None
    assert "No phone number" in nothing.problem


# ── the templates Meta has to approve ───────────────────────────────────────
def test_every_template_in_every_language_keeps_metas_rules():
    for template in templates.TEMPLATES.values():
        assert re.fullmatch(r"[a-z0-9_]+", template.meta_name)
        english = {int(n) for n in re.findall(r"\{\{(\d+)\}\}", template.texts["en"])}
        for language, text in template.texts.items():
            assert "\n" not in text and "\t" not in text
            stripped = text.strip()
            assert not stripped.startswith("{{") and not stripped.endswith("}}")
            blanks = {int(n) for n in re.findall(r"\{\{(\d+)\}\}", text)}
            assert sorted(blanks) == list(range(1, len(blanks) + 1))
            assert re.search(r"\}\}\s*\{\{", text) is None
            assert blanks == english
        assert len(template.samples) == len(english)


def test_quick_replies_only_where_a_question_is_asked():
    for key in ("remind_pro", "check_buyer"):
        template = templates.TEMPLATES[key]
        assert [payload for payload, _ in template.buttons] == ["yes", "no"]
        for _, labels in template.buttons:
            for language in template.texts:
                assert labels[language]
    assert templates.TEMPLATES["thank_buyer"].buttons == ()
    assert templates.TEMPLATES["request_to_pro"].buttons == ()


# ── values and wording ──────────────────────────────────────────────────────
def test_clean_makes_one_short_line_and_never_nothing():
    assert templates.clean("a\nb\t c   d") == "a b c d"
    assert templates.clean("") == "—"
    assert templates.clean(None) == "—"
    clipped = templates.clean("x" * 50, limit=10)
    assert len(clipped) == 10
    assert clipped.endswith("…")


def test_render_fills_the_blanks_and_falls_back_to_english():
    expected = ("Hi Sarah, thank you for your request on Vilaow. We have passed "
                "your details to Kostas, who will contact you soon.")
    assert templates.render("thank_buyer", "en", ("Sarah", "Kostas")) == expected
    assert templates.render("thank_buyer", "de", ("Sarah", "Kostas")) == expected


def _intro(**changes):
    values = dict(buyer_name="Sarah Mitchell", buyer_phone="+44 7700 900123",
                  buyer_email="sarah@example.com", message="Line one\nline two",
                  professional_name="Kostas Papadopoulos")
    values.update(changes)
    return SimpleNamespace(**values)


_PROFESSIONAL = SimpleNamespace(contact_name="Kostas Papadopoulos",
                                business_name="Papadopoulos & Partners")


def test_the_values_for_a_templates_blanks_come_from_the_introduction():
    # The message ends its own sentence: the template has no full stop after it.
    assert templates.variables("request_to_pro", _intro(), _PROFESSIONAL) == (
        "Kostas", "Sarah Mitchell", "+44 7700 900123",
        "sarah@example.com", "Line one line two.")
    asked = templates.variables("request_to_pro", _intro(message="Is March too late?"),
                                _PROFESSIONAL)
    assert asked[-1] == "Is March too late?"
    no_message = templates.variables("request_to_pro", _intro(message=None),
                                     _PROFESSIONAL)
    assert no_message[-1] == "—"
    assert templates.variables("thank_buyer", _intro(), _PROFESSIONAL) == (
        "Sarah", "Kostas Papadopoulos")


def test_a_business_without_a_contact_name_is_addressed_by_its_name():
    business = SimpleNamespace(contact_name=None, business_name=None)
    reminded = templates.variables("remind_pro",
                                   _intro(professional_name="Atlas Properties"),
                                   business)
    assert reminded[0] == "Atlas Properties"


# ── Twilio ──────────────────────────────────────────────────────────────────
def _sender(status=201, body='{"sid": "SM1", "status": "queued"}', error=None):
    """A Twilio that always answers the same way, remembering what was asked."""
    calls = []

    def post(url, form, authorization):
        calls.append((url, form, authorization))
        if error is not None:
            raise error
        return status, body

    sender = TwilioWhatsAppSender(account_sid="AC123", auth_token="token",
                                  from_number="+302100000000",
                                  content_sids={"thank_buyer.en": "HXen",
                                                "thank_buyer.fr": "HXfr"},
                                  status_callback="https://x.test/status", post=post)
    return sender, calls


_THANKS = dict(to="+447700900123", template="thank_buyer",
               variables=("Sarah", "Kostas"))


def test_sending_is_one_post_to_the_messages_api():
    sender, calls = _sender()
    result = sender.send(WhatsAppMessage(language="fr", **_THANKS))
    url, form, authorization = calls[0]

    assert url == "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
    assert form["To"] == "whatsapp:+447700900123"
    assert form["From"] == "whatsapp:+302100000000"
    assert form["ContentSid"] == "HXfr"
    assert json.loads(form["ContentVariables"]) == {"1": "Sarah", "2": "Kostas"}
    assert form["StatusCallback"] == "https://x.test/status"
    assert authorization == "Basic " + base64.b64encode(b"AC123:token").decode()
    assert result.ok is True
    assert result.provider_id == "SM1"


def test_a_language_without_an_approved_template_uses_the_english_one():
    sender, calls = _sender()
    sender.send(WhatsAppMessage(language="el", **_THANKS))
    assert calls[0][1]["ContentSid"] == "HXen"


def test_every_way_twilio_can_answer_is_sorted_into_retry_or_stop():
    message = WhatsAppMessage(language="fr", **_THANKS)

    accepted = _sender()[0].send(message)
    assert accepted.ok and accepted.provider_id == "SM1"

    refused = _sender(400, '{"code": 21211, "message": "Invalid To"}')[0].send(message)
    assert not refused.ok and not refused.retry
    assert "21211" in refused.detail

    assert _sender(503, "")[0].send(message).retry is True
    assert _sender(429, "{}")[0].send(message).retry is True

    unreachable = _sender(error=OSError("no route to host"))[0].send(message)
    assert not unreachable.ok and unreachable.retry


def test_a_template_with_no_content_sid_is_refused_before_any_request():
    sender, calls = _sender()
    result = sender.send(WhatsAppMessage(
        to="+306912345678", template="remind_pro", language="el",
        variables=("Kostas", "Sarah Mitchell", "+44 7700 900123")))
    assert not result.ok
    assert "TWILIO_CONTENT_SIDS" in result.detail
    assert calls == []


def test_a_signature_is_twilios_own_documented_example():
    url = "https://example.com/myapp.php?foo=1&bar=2"
    params = {"CallSid": ["CA1234567890ABCDE"], "Caller": ["+14158675310"],
              "Digits": ["1234"], "From": ["+14158675310"],
              "To": ["+18005551212"]}
    header = "L/OH5YylLD5NRKLltdqwSvS0BnU="

    assert signature("12345", url, params) == header
    assert signed_by_twilio("12345", url, params, header)

    params["Digits"] = ["1235"]        # one value tampered with
    assert not signed_by_twilio("12345", url, params, header)
    assert not signed_by_twilio("12345", url, params, None)
    assert not signed_by_twilio("", url, params, header)


# ── settings and answers ────────────────────────────────────────────────────
def _settings(**changes):
    """Every WhatsApp setting given explicitly, so a local .env cannot leak in."""
    values = dict(whatsapp_mode="off", jobs_secret="", twilio_account_sid="",
                  twilio_auth_token="", twilio_whatsapp_from="",
                  twilio_content_sids="", twilio_webhook_base="",
                  meta_whatsapp_token="", meta_waba_id="", meta_phone_number_id="",
                  meta_app_secret="", meta_verify_token="")
    values.update(changes)
    return Settings(**values)


_READY = dict(whatsapp_mode="twilio", twilio_account_sid="AC1", twilio_auth_token="t",
              twilio_whatsapp_from="+302100000000",
              twilio_content_sids='{"thank_buyer.en": "HX1"}',
              twilio_webhook_base="https://x.onrender.com", jobs_secret="s" * 32)


def test_whatsapp_problems_name_the_setting_that_blocks_real_sending():
    assert _settings()._whatsapp_problems() == []

    loud = _settings(whatsapp_mode="loud")._whatsapp_problems()
    assert len(loud) == 1
    assert "WHATSAPP_MODE" in loud[0]

    short_secret = _settings(**{**_READY, "jobs_secret": "short"})
    assert any("JOBS_SECRET" in p for p in short_secret._whatsapp_problems())

    refused = _settings(whatsapp_mode="twilio", jobs_secret="s" * 32)._whatsapp_problems()
    assert any("TWILIO_ACCOUNT_SID" in p for p in refused)
    assert any("TWILIO_AUTH_TOKEN" in p for p in refused)
    assert any("TWILIO_WHATSAPP_FROM" in p for p in refused)

    broken = _settings(**{**_READY, "twilio_content_sids": "not json"})
    assert any("TWILIO_CONTENT_SIDS" in p for p in broken._whatsapp_problems())

    plain_http = _settings(**{**_READY, "twilio_webhook_base": "http://x"})
    assert any("TWILIO_WEBHOOK_BASE" in p for p in plain_http._whatsapp_problems())

    assert _settings(**_READY)._whatsapp_problems() == []


def test_a_pros_answer_is_read_in_greek_english_and_french():
    for text in ("yes", "Yes", "YES.", "Ναι", "ναι", "oui"):
        assert parse_answer(text) == "yes"
    for text in ("no", "Όχι", "οχι", "non", "Not yet"):
        assert parse_answer(text) == "no"
    assert parse_answer("Called her on Tuesday") == "other"
    assert parse_answer(None, "yes") == "yes"
    assert parse_answer("no", "yes") == "no"


# ── Meta's Cloud API (12 October: in place of Twilio) ───────────────────────
from app.adapters.whatsapp import meta  # noqa: E402


def _meta_sender(*answers):
    """A Meta sender whose requests are kept, and answered in turn."""
    calls = []
    queue = list(answers) or [(200, '{"messages": [{"id": "wamid.1"}]}')]

    def call(method, url, body, token):
        calls.append((method, url, body, token))
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return meta.MetaWhatsAppSender(token="tok", phone_number_id="555", call=call), calls


def test_meta_sending_is_one_post_with_the_template_and_its_blanks():
    sender, calls = _meta_sender()
    result = sender.send(WhatsAppMessage(language="fr", **_THANKS))
    method, url, body, token = calls[0]

    assert (method, url, token) == ("POST", "https://graph.facebook.com/v25.0/555/messages", "tok")
    assert body["to"] == "447700900123"
    assert body["template"]["name"] == "vilaow_request_received"
    assert body["template"]["language"] == {"code": "fr"}
    assert body["template"]["components"] == [{"type": "body", "parameters": [
        {"type": "text", "text": "Sarah"}, {"type": "text", "text": "Kostas"}]}]
    assert result.ok and result.provider_id == "wamid.1"


def test_meta_quick_replies_carry_yes_and_no_whatever_the_language():
    sender, calls = _meta_sender()
    sender.send(WhatsAppMessage(to="+306912345678", template="remind_pro", language="el",
                                variables=("Kostas", "Sarah Mitchell", "+44 7700 900123")))
    buttons = [c for c in calls[0][2]["template"]["components"] if c["type"] == "button"]
    assert [(b["index"], b["parameters"][0]["payload"]) for b in buttons] == [("0", "yes"), ("1", "no")]


def test_meta_falls_back_to_english_when_the_language_is_not_approved():
    sender, calls = _meta_sender(
        (404, '{"error": {"code": 132001, "message": "Template name does not exist in the translation"}}'),
        (200, '{"messages": [{"id": "wamid.en"}]}'),
    )
    result = sender.send(WhatsAppMessage(language="el", **_THANKS))
    assert [c[2]["template"]["language"]["code"] for c in calls] == ["el", "en"]
    assert result.ok and result.provider_id == "wamid.en"


def test_every_way_meta_can_answer_is_sorted_into_retry_or_stop():
    message = WhatsAppMessage(language="en", **_THANKS)
    refused = _meta_sender((400, '{"error": {"code": 131026, "message": "Undeliverable"}}'))[0].send(message)
    assert not refused.ok and not refused.retry and "131026" in refused.detail
    assert _meta_sender((503, ""))[0].send(message).retry is True
    assert _meta_sender((429, "{}"))[0].send(message).retry is True
    unreachable = _meta_sender(OSError("no route"))[0].send(message)
    assert not unreachable.ok and unreachable.retry


def test_a_meta_signature_is_sha256_of_the_raw_body_under_the_app_secret():
    import hashlib
    import hmac as hmac_

    body = b'{"entry": []}'
    good = "sha256=" + hmac_.new(b"secret", body, hashlib.sha256).hexdigest()
    assert meta.signed_by_meta("secret", body, good)
    assert not meta.signed_by_meta("secret", body + b" ", good)       # tampered with
    assert not meta.signed_by_meta("other", body, good)
    assert not meta.signed_by_meta("secret", body, good.removeprefix("sha256="))
    assert not meta.signed_by_meta("secret", body, None)
    assert not meta.signed_by_meta("", body, good)


def test_meta_callbacks_are_read_into_replies_and_delivery_reports():
    payload = {"entry": [{"changes": [{"field": "messages", "value": {
        "messages": [
            {"from": "306912345678", "id": "wamid.r1", "type": "button",
             "button": {"payload": "yes", "text": "Ναι"}, "context": {"id": "wamid.q1"}},
            {"from": "447700900123", "id": "wamid.r2", "type": "text", "text": {"body": "not yet"}},
            {"from": "447700900123", "id": "wamid.r3", "type": "image"},
        ],
        "statuses": [
            {"id": "wamid.q1", "status": "read"},
            {"id": "wamid.q2", "status": "failed", "errors": [{"code": 131026}]},
        ],
    }}]}]}
    assert list(meta.replies(payload)) == [
        {"number": "+306912345678", "text": "Ναι", "button": "yes",
         "replied_to": "wamid.q1", "provider_id": "wamid.r1"},
        {"number": "+447700900123", "text": "not yet", "button": None,
         "replied_to": None, "provider_id": "wamid.r2"},
        {"number": "+447700900123", "text": None, "button": None,
         "replied_to": None, "provider_id": "wamid.r3"},
    ]
    assert list(meta.deliveries(payload)) == [
        {"provider_id": "wamid.q1", "status": "read", "error_code": None},
        {"provider_id": "wamid.q2", "status": "failed", "error_code": "131026"},
    ]
    assert list(meta.replies({"object": "page"})) == []


def test_every_template_is_submitted_as_meta_takes_it():
    request = meta.template_request(templates.TEMPLATES["check_buyer"], "el")
    assert request["name"] == "vilaow_contact_check"
    assert (request["language"], request["category"]) == ("el", "UTILITY")
    body, buttons = request["components"]
    assert body["text"] == templates.TEMPLATES["check_buyer"].texts["el"]
    assert body["example"] == {"body_text": [["Sarah", "Kostas Papadopoulos"]]}
    assert [b["text"] for b in buttons["buttons"]] == ["Ναι", "Όχι"]
    assert len(meta.template_request(templates.TEMPLATES["thank_buyer"], "en")["components"]) == 1


def test_setup_subscribes_submits_only_what_is_missing_and_reports():
    calls = []

    def call(method, url, body, token):
        calls.append((method, url.removeprefix("https://graph.facebook.com/v25.0/waba"), body))
        if url.endswith("/subscribed_apps"):
            return 200, '{"success": true}'
        if "/phone_numbers" in url:
            return 200, ('{"data": [{"id": "555", "display_phone_number": "+30 697 674 6506", '
                         '"verified_name": "Vilaow", "status": "CONNECTED"}]}')
        if method == "GET":
            return 200, ('{"data": [{"name": "vilaow_request_received", "language": "en", '
                         '"status": "APPROVED"}]}')
        return 200, '{"id": "t1", "status": "PENDING"}'

    result = meta.setup(token="tok", waba_id="waba", call=call)

    assert result["subscribed"] is True
    assert result["phone_numbers"][0]["id"] == "555"
    every = sum(len(t.texts) for t in templates.TEMPLATES.values())
    assert len(result["templates"]) == every
    submitted = [c for c in calls if c[0] == "POST" and c[1] == "/message_templates"]
    assert len(submitted) == every - 1          # the approved one is left alone
    first = result["templates"][0]
    assert first == {"name": "vilaow_request_received", "language": "en", "status": "APPROVED"}


def test_meta_mode_names_each_setting_that_is_missing():
    missing = _settings(whatsapp_mode="meta")._whatsapp_problems()
    for name in ("META_WHATSAPP_TOKEN", "META_WABA_ID", "META_PHONE_NUMBER_ID",
                 "META_APP_SECRET", "META_VERIFY_TOKEN"):
        assert any(name in p for p in missing), name
    ready = _settings(whatsapp_mode="meta", meta_whatsapp_token="t", meta_waba_id="w",
                      meta_phone_number_id="p", meta_app_secret="s",
                      meta_verify_token="v" * 16)
    assert ready._whatsapp_problems() == []


def test_a_meta_refusal_names_its_real_reason_not_just_invalid_parameter():
    code, reason = meta._error({"error": {
        "message": "Invalid parameter", "code": 100, "error_subcode": 2388024,
        "error_user_title": "Content in this language already exists",
        "error_user_msg": "Your template already has content in English."}})
    assert code == 100
    assert reason == ("Content in this language already exists: Your template already "
                      "has content in English. (subcode 2388024)")
    assert meta._error({"error": {"message": "Invalid parameter"}})[1] == "Invalid parameter"
