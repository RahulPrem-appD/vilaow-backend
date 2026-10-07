"""The WhatsApp templates as a document for the people who submit them.

Written from app/adapters/whatsapp/templates.py, so what Meta approves is
word for word what the code sends. docs/whatsapp-templates.md is this
module's output; tests/test_whatsapp_doc.py fails when the two drift apart.

    python -m app.adapters.whatsapp.approval_doc > docs/whatsapp-templates.md
"""
from __future__ import annotations

from app.adapters.whatsapp.templates import TEMPLATES

LANGUAGE_NAMES = {"en": "English", "el": "Greek", "fr": "French"}
WHO = {"buyer": "the buyer", "professional": "the professional"}


def document() -> str:
    lines = [
        "# Vilaow's WhatsApp templates",
        "",
        "<!-- Written by app/adapters/whatsapp/approval_doc.py from templates.py. "
        "Do not edit by hand. -->",
        "",
        "WhatsApp lets a business start a conversation only with a message template that "
        "Meta has approved. Vilaow's follow-up of an introduction uses the four below.",
        "",
        "## How to create them",
        "",
        "In Twilio: **Messaging → Content Template Builder → Create new**, once per template "
        "and language.",
        "",
        "1. **Template name**: as given below.",
        "2. **Language**: as given below. Each language is its own template in Twilio.",
        "3. **Content type**: *Text* for a template without buttons; *Quick reply* for one "
        "with buttons, using the button IDs `yes` and `no` exactly as written.",
        "4. **Body**: paste the text exactly, keeping {{1}}, {{2}} and so on.",
        "5. **Sample values**: the examples listed under each template.",
        "6. **Category**: Utility. Submit for WhatsApp approval.",
        "",
        "For the professionals' two templates, Greek is used when it is approved and "
        "English otherwise, so English alone is enough to start.",
        "",
        "## What to send back",
        "",
        "Each approved template's **Content SID** (it starts with HX), with its name and "
        "language. They go into the server's settings, not into chat.",
        "",
    ]
    for number, template in enumerate(TEMPLATES.values(), start=1):
        languages = ", ".join(f"{LANGUAGE_NAMES[code]} (`{code}`)" for code in template.texts)
        lines += [
            f"## {number}. `{template.meta_name}` — to {WHO[template.recipient]}",
            "",
            template.purpose,
            "",
            f"- **Languages**: {languages}",
            f"- **Content type**: {'Quick reply' if template.buttons else 'Text'}",
            f"- **Our key**: `{template.key}` (for TWILIO_CONTENT_SIDS: "
            f"`{template.key}.<language>`)",
            "",
            "| Blank | Holds | Sample value |",
            "| --- | --- | --- |",
        ]
        lines += [f"| {{{{{n}}}}} | {holds} | {sample} |"
                  for n, (holds, sample) in enumerate(template.samples, start=1)]
        lines.append("")
        for code, text in template.texts.items():
            lines += [f"**{LANGUAGE_NAMES[code]}**", "", f"> {text}", ""]
            if template.buttons:
                labels = " · ".join(f"`{label[code]}` (ID `{payload}`)"
                                    for payload, label in template.buttons)
                lines += [f"Buttons: {labels}", ""]
    lines += [
        "## For the developer: switching it on",
        "",
        "On Render, in the API's environment (never in chat or in the code):",
        "",
        "- `WHATSAPP_MODE=twilio`",
        "- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_WHATSAPP_FROM` "
        "(the approved WhatsApp number, as +30…)",
        '- `TWILIO_CONTENT_SIDS` as JSON, for example `{"thank_buyer.en": "HX…", '
        '"remind_pro.el": "HX…"}`',
        "- `TWILIO_WEBHOOK_BASE=https://vilaow-backend.onrender.com`",
        "- `JOBS_SECRET`: 32 characters or more, random",
        "",
        "In Twilio, the WhatsApp sender's incoming-message webhook: "
        "`https://vilaow-backend.onrender.com/api/whatsapp/twilio/inbound` (POST).",
        "",
        "A scheduler calls `POST https://vilaow-backend.onrender.com/api/follow-ups/run` "
        "with the header `X-Jobs-Key: <JOBS_SECRET>` every ten minutes. Without it nothing "
        "after the first two messages goes out, and the admin says so.",
        "",
        "The timings are settings too: `FOLLOWUP_FIRST_REMINDER_HOURS` (4), "
        "`FOLLOWUP_REMINDER_EVERY_HOURS` (24), `FOLLOWUP_MAX_REMINDERS` (3), "
        "`FOLLOWUP_BUYER_CHECK_HOURS` (48), and `WHATSAPP_PRO_LANGUAGE` (el).",
        "",
        "Before switching on, the request form's consent line and its thank-you message "
        "must mention WhatsApp: today they promise an email confirmation, and with WhatsApp "
        "on, no email is sent.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(document(), end="")
