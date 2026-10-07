"""The words of every WhatsApp message (change request 12).

WhatsApp only lets a business start a conversation with a template Meta has
approved, so these four are submitted for approval once — the client does that
in Twilio, from docs/whatsapp-templates.md, which is written from this file —
and from then on a message is a template plus the values for its blanks.

Meta's rules shape the wording, and a template that breaks one is rejected:
no blank at the very start or end, never two blanks side by side, blanks
numbered {{1}}, {{2}}, … in order, and one paragraph with no line breaks.
Values have rules too — no line breaks, no tabs, never empty — which is what
`clean` is for.

The buyer's messages come in the language of the page they asked from. The
professional's come in Greek or English (WHATSAPP_PRO_LANGUAGE). A language
whose template is not approved yet falls back to English.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import Introduction, Professional


@dataclass(frozen=True)
class Template:
    key: str            # ours, and the step kind that sends it
    meta_name: str      # the name it is submitted to Meta under
    recipient: str      # "buyer" or "professional"
    purpose: str        # one line for the approval document
    texts: dict[str, str]
    # Quick-reply buttons: (the payload a tap sends back, its label by language).
    buttons: tuple[tuple[str, dict[str, str]], ...] = field(default=())
    # What each blank holds, and an example, for the approval form.
    samples: tuple[tuple[str, str], ...] = field(default=())


YES_NO = (
    ("yes", {"en": "Yes", "el": "Ναι", "fr": "Oui"}),
    ("no", {"en": "No", "el": "Όχι", "fr": "Non"}),
)

TEMPLATES: dict[str, Template] = {t.key: t for t in (
    Template(
        key="thank_buyer",
        meta_name="vilaow_request_received",
        recipient="buyer",
        purpose="Sent to the buyer the moment they ask to be put in touch with a professional.",
        texts={
            "en": "Hi {{1}}, thank you for your request on Vilaow. We have passed your "
                  "details to {{2}}, who will contact you soon.",
            "el": "Γεια σας {{1}}, σας ευχαριστούμε για το αίτημά σας στο Vilaow. "
                  "Στείλαμε τα στοιχεία σας στον επαγγελματία που επιλέξατε ({{2}}), "
                  "ο οποίος θα επικοινωνήσει μαζί σας σύντομα.",
            "fr": "Bonjour {{1}}, merci pour votre demande sur Vilaow. Nous avons transmis "
                  "vos coordonnées à {{2}}, qui vous contactera très prochainement.",
        },
        samples=(("the buyer's first name", "Sarah"),
                 ("the professional's name", "Kostas Papadopoulos")),
    ),
    Template(
        key="request_to_pro",
        meta_name="vilaow_new_client_request",
        recipient="professional",
        purpose="Sent to the professional the moment a buyer asks to be put in touch with them.",
        texts={
            "en": "Hi {{1}}, you have a new client request from Vilaow. Name: {{2}}. "
                  "Phone: {{3}}. Email: {{4}}. Their message: {{5}} Please contact the "
                  "client as soon as you can.",
            "el": "Γεια σας {{1}}, έχετε ένα νέο αίτημα πελάτη από το Vilaow. Όνομα: {{2}}. "
                  "Τηλέφωνο: {{3}}. Email: {{4}}. Μήνυμα: {{5}} Παρακαλούμε επικοινωνήστε "
                  "με τον πελάτη το συντομότερο δυνατό.",
        },
        samples=(("the professional's first name", "Kostas"),
                 ("the buyer's full name", "Sarah Mitchell"),
                 ("the buyer's phone", "+44 7700 900123"),
                 ("the buyer's email", "sarah@example.com"),
                 ("the buyer's message, or a dash", "Buying a house near Chania in the spring.")),
    ),
    Template(
        key="remind_pro",
        meta_name="vilaow_contact_reminder",
        recipient="professional",
        purpose="Sent to the professional some hours after a request, and again each day, "
                "until they answer Yes (at most three times).",
        texts={
            "en": "Hi {{1}}, a reminder from Vilaow: {{2}} (phone {{3}}) asked to be "
                  "introduced to you. Did you contact the client?",
            "el": "Γεια σας {{1}}, υπενθύμιση από το Vilaow: ο πελάτης {{2}} (τηλ. {{3}}) "
                  "ζήτησε να έρθει σε επαφή μαζί σας. Επικοινωνήσατε με τον πελάτη;",
        },
        buttons=YES_NO,
        samples=(("the professional's first name", "Kostas"),
                 ("the buyer's full name", "Sarah Mitchell"),
                 ("the buyer's phone", "+44 7700 900123")),
    ),
    Template(
        key="check_buyer",
        meta_name="vilaow_contact_check",
        recipient="buyer",
        purpose="Sent to the buyer two days after their request, to check the professional "
                "contacted them.",
        texts={
            "en": "Hi {{1}}, a quick check from Vilaow about your request. Have you heard "
                  "from {{2}} yet?",
            "el": "Γεια σας {{1}}, ένας σύντομος έλεγχος από το Vilaow για το αίτημά σας. "
                  "Έχει επικοινωνήσει ήδη μαζί σας ο επαγγελματίας που επιλέξατε ({{2}});",
            "fr": "Bonjour {{1}}, petite vérification de la part de Vilaow au sujet de votre "
                  "demande. Avez-vous déjà eu des nouvelles de {{2}} ?",
        },
        buttons=YES_NO,
        samples=(("the buyer's first name", "Sarah"),
                 ("the professional's name", "Kostas Papadopoulos")),
    ),
)}

_BLANK = re.compile(r"\{\{(\d+)\}\}")
_SPACE = re.compile(r"\s+")


def clean(value: str | None, limit: int = 200) -> str:
    """A value WhatsApp will accept in a blank: one line, not too long, never empty."""
    text = _SPACE.sub(" ", value or "").strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text or "—"


def sentence(value: str | None, limit: int = 300) -> str:
    """A value that ends its own sentence, for a blank the template does not
    follow with a full stop: "…in the spring." rather than "…in the spring.."."""
    text = clean(value, limit)
    return text if text == "—" or text[-1] in ".!?…;" else text + "."


def first_name(name: str | None) -> str:
    parts = (name or "").split()
    return parts[0] if parts else "there"


def render(key: str, language: str, variables: tuple[str, ...]) -> str:
    """The message as the recipient reads it. For the pretend sender and the tests."""
    text = TEMPLATES[key].texts.get(language) or TEMPLATES[key].texts["en"]
    return _BLANK.sub(lambda m: variables[int(m.group(1)) - 1], text)


def variables(key: str, intro: "Introduction", professional: "Professional | None") -> tuple[str, ...]:
    """The values for one template's blanks, from the introduction."""
    pro_name = clean(intro.professional_name
                     or (professional and (professional.contact_name or professional.business_name)))
    # "Hi Kostas" for a person; a business with no contact name keeps its name.
    pro_first = clean(first_name(professional.contact_name)
                      if professional and professional.contact_name else pro_name)
    buyer_first = clean(first_name(intro.buyer_name), 60)
    if key == "thank_buyer":
        return (buyer_first, pro_name)
    if key == "request_to_pro":
        return (pro_first, clean(intro.buyer_name), clean(intro.buyer_phone, 60),
                clean(intro.buyer_email), sentence(intro.message))
    if key == "remind_pro":
        return (pro_first, clean(intro.buyer_name), clean(intro.buyer_phone, 60))
    if key == "check_buyer":
        return (buyer_first, pro_name)
    raise KeyError(key)
