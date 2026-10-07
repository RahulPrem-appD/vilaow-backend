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
                  twilio_content_sids="", twilio_webhook_base="")
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
