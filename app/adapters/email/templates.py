"""The wording of every email Vilaow sends.

Pure functions: facts in, `Message` out. No SMTP, no settings, no session — a
template cannot accidentally send anything, and a test can assert on the exact
words without a mail server or a monkeypatch.

Adding an email is a new function here. It never touches transport, which is
the point of keeping the two apart.

Tone is his: plain, no marketing, no chasing. These go to professionals who
agreed on a call to expect them, and to buyers who just filled in a form.
"""
from __future__ import annotations

from html import escape
from urllib.parse import quote

from app.ports.email import Message


def e(value: object) -> str:
    """Escape a value for the HTML half of a message.

    Every one of these bodies interpolates something a stranger typed: a
    buyer's name and free-text message on the introduction form, the name a
    professional types while signing. Unescaped, `</table><p>Please confirm
    your bank details at …` closed the layout and continued in Vilaow's own
    voice, inside an email Vilaow sent, under Vilaow's name. Mail clients strip
    scripts; they do not stop that.

    The plain-text half needs no escaping — it is not markup.
    """
    return escape(str(value or ""), quote=True)


def link_attr(value: str) -> str:
    """A user-supplied value going inside href="mailto:…" or href="tel:…".

    Escaping alone is not enough in a URL: a quote or a space would break out
    of the attribute or split the target.
    """
    return escape(quote(str(value or ""), safe="@.+-_"), quote=True)


def _first_name(name: str | None) -> str:
    return (name or "").strip().split(" ")[0] or "there"


def _wrap(inner: str) -> str:
    return (
        f'<div style="font-family:system-ui,-apple-system,sans-serif;font-size:15px;'
        f'line-height:1.6;color:#19222c">{inner}<p>Vilaow</p></div>'
    )


def agreement_invitation(*, to: str, name: str, link: str, ttl_days: int) -> Message:
    """The one-time link to the listing agreement."""
    first = _first_name(name)

    text = (
        f"Hello {first},\n\n"
        f"Thank you for speaking with us. To confirm your listing on Vilaow, "
        f"please review and sign the agreement here:\n\n"
        f"{link}\n\n"
        f"The link is for you alone and can be used once. It expires in "
        f"{ttl_days} days.\n\n"
        f"If anything in your details is wrong, reply to this email and we will "
        f"correct it before you sign.\n\n"
        f"Vilaow\n"
    )
    html = _wrap(
        f"<p>Hello {e(first)},</p>"
        f"<p>Thank you for speaking with us. To confirm your listing on Vilaow, "
        f"please review and sign the agreement:</p>"
        f'<p><a href="{link}" style="display:inline-block;background:#0b3a6b;'
        f'color:#fff;text-decoration:none;padding:12px 20px;border-radius:8px;'
        f'font-weight:600">Review and sign</a></p>'
        f'<p style="color:#656e77;font-size:13px">The link is for you alone and '
        f"can be used once. It expires in {ttl_days} days.</p>"
        f'<p style="color:#656e77;font-size:13px">If anything in your details is '
        f"wrong, reply to this email and we will correct it before you sign.</p>"
    )
    return Message(to=to, subject="Confirm your Vilaow listing", text=text, html=html)


def signing_code(*, to: str, name: str, code: str) -> Message:
    """The code that confirms the address, sent straight after signing.

    Note what this is and is not. It goes to the same inbox as the signing
    link, so it is not a second factor — it confirms the address is real and
    reachable. The wording says "confirm your email", never "for your
    security", so nobody downstream mistakes it for something stronger.
    """
    first = _first_name(name)

    text = (
        f"Hello {first},\n\n"
        f"Your confirmation code is {code}\n\n"
        f"Enter it on the signing page to confirm this email address and "
        f"complete your Vilaow listing. The code expires in 10 minutes.\n\n"
        f"If you did not sign anything, you can ignore this email.\n\n"
        f"Vilaow\n"
    )
    html = _wrap(
        f"<p>Hello {e(first)},</p>"
        f"<p>Your confirmation code is:</p>"
        f'<p style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;'
        f'font-size:30px;font-weight:700;letter-spacing:6px;color:#0b3a6b;'
        f'background:#eef2f8;border-radius:10px;padding:14px 20px;'
        f'display:inline-block">{code}</p>'
        f"<p>Enter it on the signing page to confirm this email address and "
        f"complete your Vilaow listing.</p>"
        f'<p style="color:#656e77;font-size:13px">The code expires in 10 minutes. '
        f"If you did not sign anything, you can ignore this email.</p>"
    )
    return Message(to=to, subject=f"{code} is your Vilaow confirmation code", text=text, html=html)


def introduction_to_professional(
    *,
    to: str,
    name: str,
    buyer_name: str,
    buyer_email: str,
    buyer_phone: str | None,
    message: str | None,
) -> Message:
    """The one email that carries a buyer's contact details to a third party.

    The buyer has been told this professional will be in touch, so the ask is
    explicit rather than implied: ring them. Everything they consented to
    share is here, and nothing else.
    """
    first = _first_name(name)
    phone_line = f"Phone: {buyer_phone}\n" if buyer_phone else ""
    note = f"\nWhat they said:\n{message}\n" if message else ""

    text = (
        f"Hello {first},\n\n"
        f"{buyer_name} found you on Vilaow and would like you to contact them.\n\n"
        f"Name:  {buyer_name}\n"
        f"Email: {buyer_email}\n"
        f"{phone_line}"
        f"{note}\n"
        f"They have been told to expect your call, so please get in touch as "
        f"soon as you can. Anything you agree is directly between you and them.\n\n"
        f"Vilaow\n"
    )
    html = _wrap(
        f"<p>Hello {e(first)},</p>"
        f"<p><b>{e(buyer_name)}</b> found you on Vilaow and would like you to contact them.</p>"
        f'<table style="border-collapse:collapse;font-size:14px">'
        f'<tr><td style="padding:3px 14px 3px 0;color:#656e77">Name</td><td>{e(buyer_name)}</td></tr>'
        f'<tr><td style="padding:3px 14px 3px 0;color:#656e77">Email</td>'
        f'<td><a href="mailto:{link_attr(buyer_email)}">{e(buyer_email)}</a></td></tr>'
        + (
            f'<tr><td style="padding:3px 14px 3px 0;color:#656e77">Phone</td>'
            f'<td><a href="tel:{link_attr(buyer_phone)}">{e(buyer_phone)}</a></td></tr>'
            if buyer_phone
            else ""
        )
        + "</table>"
        + (
            f'<p style="background:#f6f6f2;border-radius:10px;padding:12px 16px">{e(message)}</p>'
            if message
            else ""
        )
        + "<p>They have been told to expect your call, so please get in touch as soon "
          "as you can. Anything you agree is directly between you and them.</p>"
    )
    return Message(
        to=to, subject=f"{buyer_name} would like you to contact them", text=text, html=html
    )


def introduction_confirmation(
    *, to: str, buyer_name: str, professional_name: str, professional_role: str | None
) -> Message:
    """The promise. If it is not kept, it is Vilaow that broke it."""
    first = _first_name(buyer_name)
    who = f"{professional_role} {professional_name}" if professional_role else professional_name

    text = (
        f"Hello {first},\n\n"
        f"Thank you — we have passed your details to {who}, who will reach you "
        f"shortly.\n\n"
        f"If you have not heard anything within a couple of days, reply to this "
        f"email and we will chase it for you.\n\n"
        f"Vilaow\n"
    )
    html = _wrap(
        f"<p>Hello {e(first)},</p>"
        f"<p>Thank you — we have passed your details to <b>{e(who)}</b>, who will "
        f"reach you shortly.</p>"
        f'<p style="color:#656e77;font-size:13px">If you have not heard anything '
        f"within a couple of days, reply to this email and we will chase it for you.</p>"
    )
    return Message(to=to, subject=f"{professional_name} will be in touch", text=text, html=html)


def review_request(*, to: str, buyer_name: str, professional_name: str, link: str) -> Message:
    """Only sent to someone we can show we introduced, which is what makes the
    resulting review a verified one rather than an anonymous submission."""
    first = _first_name(buyer_name)

    text = (
        f"Hello {first},\n\n"
        f"You asked us to introduce you to {professional_name}. How did it go?\n\n"
        f"{link}\n\n"
        f"It takes a minute, and it helps the next buyer choose. We publish "
        f"reviews as they are written — we do not edit or remove them on request.\n\n"
        f"Vilaow\n"
    )
    html = _wrap(
        f"<p>Hello {e(first)},</p>"
        f"<p>You asked us to introduce you to <b>{e(professional_name)}</b>. How did it go?</p>"
        f'<p><a href="{link}" style="display:inline-block;background:#0b3a6b;color:#fff;'
        f'text-decoration:none;padding:12px 20px;border-radius:8px;font-weight:600">'
        f"Leave a review</a></p>"
        f'<p style="color:#656e77;font-size:13px">It takes a minute, and it helps the '
        f"next buyer choose. We publish reviews as they are written — we do not edit "
        f"or remove them on request.</p>"
    )
    return Message(to=to, subject=f"How did it go with {professional_name}?", text=text, html=html)
