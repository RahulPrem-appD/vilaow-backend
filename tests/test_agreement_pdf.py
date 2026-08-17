"""The signed agreement PDF — the only copy of a binding document.

Two defects lived here undetected until an agent swarm went looking, and both
are the same shape: the existing tests asserted the endpoint returned
`application/pdf`, and nothing looked at what was *on* the page.

  1. **No signed agreement had ever shown a signature.** The pad emits SVG (see
     app/src/components/sign-signature-pad.tsx); the renderer split on a comma
     and base64-decoded, which is the *older* `data:image/png;base64,...`
     contract. An SVG string contains no comma, so the decode produced nothing
     and every professional's copy — and Vilaow's — printed
     "(no drawn signature stored)". Every backend test passed, because the
     fixtures sent a base64 data URL: the one format the real client never
     produces.

  2. **Greek was silently corrupted.** reportlab's built-in Helvetica has no
     Greek. Unaccented letters came through, so `ΑΦΜ` looked fine and the bug
     hid — but every accented vowel became a filled box. `Γιώργος` printed as
     `Γι■ργος` on a legal document for a Greek directory.

So these tests read the PDF's content stream instead of trusting its
content-type. Both classes of bug are invisible to anything that does not.
"""
from __future__ import annotations

import base64
import re
import zlib

import pytest

from app.adapters.pdf import agreement as pdfmod
from app.models import Agreement, Professional, Stage

# Verbatim from the pad, for a signature drawn in a browser. Kept as a literal
# rather than generated, so a change to the pad's output format breaks this
# test instead of quietly agreeing with itself. The frontend has the matching
# half of this contract in app/src/components/sign-signature-pad.test.ts.
PAD_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 150" width="600"'
    ' height="150" fill="none" stroke="#19222c" stroke-width="2.2"'
    ' stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M 72 97.5 L 120 52.5 L 168 105 L 216 45 L 270 102 L 330 52.5'
    ' L 396 108 L 468 60 L 528 90"/></svg>'
)

# What a single tap produces — one point, with a zero-length segment so the
# round cap renders it as a dot.
PAD_SVG_TAP = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 150" width="600"'
    ' height="150" fill="none" stroke="#19222c" stroke-width="2.2"'
    ' stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M 300 75 l 0 0"/></svg>'
)

# A 1x1 PNG, as the pad produced before it was rebuilt on the client's design.
LEGACY_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def content_stream(pdf: bytes) -> str:
    """The PDF's drawing operators, as text.

    reportlab writes page content as ASCII85 then Flate. Decoding it is the
    whole point: asserting on the bytes of a compressed stream, or on the
    content-type, is exactly the blind spot that let both bugs ship.
    """
    parts = []
    for match in re.finditer(rb"stream(.*?)endstream", pdf, re.S):
        raw = match.group(1).strip(b"\r\n").rstrip(b"~>")
        try:
            parts.append(zlib.decompress(base64.a85decode(raw, adobe=False)).decode("latin-1"))
        except Exception:                     # noqa: BLE001 — not a content stream
            continue
    return "\n".join(parts)


def signature_block(pdf: bytes) -> str:
    """Just the ink, isolated from the rules and the raster path.

    The renderer wraps the drawn signature in its own graphics state (`q … Q`),
    which is what makes it separable from the horizontal rules on the same part
    of the page — those also emit `l` operators and would otherwise be counted.
    """
    body = content_stream(pdf)
    after = body[body.find("SIGNATURE"):] if "SIGNATURE" in body else body
    start = after.find("q\n")
    if start < 0:
        return ""
    end = after.find("\nQ", start)
    return after[start:end if end > 0 else len(after)]


def strokes_in(pdf: bytes) -> int:
    """How many path segments were drawn. Zero means no ink on the page."""
    return len(re.findall(r"[-\d.]+ [-\d.]+ l\b", signature_block(pdf)))


@pytest.fixture
def signed(db, professions):
    """A professional with a signed agreement, straight into the database.

    The HTTP path is covered elsewhere; what matters here is what the renderer
    does with a row, and building rows directly keeps each case explicit.
    """
    def make(signature: str | None, **kw) -> tuple[Agreement, Professional]:
        p = Professional(business_name=kw.pop("business_name", "Papadopoulos & Partners"),
                         contact_name=kw.pop("contact_name", "Kostas P"),
                         city="Chania", region="Crete", email="k@example.com",
                         profession_id=professions["lawyer"], stage=Stage.signed)
        db.add(p)
        db.commit()
        a = Agreement(professional_id=p.id, token=f"tok{p.id}",
                      terms_version="2026-01", terms_text="1. Terms as agreed.",
                      signature_image=signature,
                      signed_name=kw.pop("signed_name", "Kostas Papadopoulos"),
                      signed_email="k@example.com",
                      signed_fields=kw.pop("signed_fields", None),
                      **kw)
        db.add(a)
        db.commit()
        return a, p
    return make


# ── the signature actually reaches the page ─────────────────────────────────
def test_a_signature_drawn_in_the_browser_appears_on_the_pdf(signed):
    """The bug: every signed agreement printed "(no drawn signature stored)"."""
    pdf = pdfmod.render(*signed(PAD_SVG))
    body = content_stream(pdf)

    assert "no drawn signature stored" not in body, (
        "The PDF says no signature was stored, but one was — the renderer "
        "could not read the format the pad actually produces."
    )
    assert "signature could not be rendered" not in body
    # Nine points: one moveto and eight linetos.
    assert strokes_in(pdf) == 8


def test_the_stroke_keeps_the_colour_and_round_caps_it_was_drawn_with(signed):
    block = signature_block(pdfmod.render(*signed(PAD_SVG)))
    assert ".098039 .133333 .172549 RG" in block, "not the ink colour #19222c"
    assert "1 J" in block and "1 j" in block, "round cap and join were lost"


def test_a_single_tap_still_puts_a_dot_on_the_page(signed):
    assert strokes_in(pdfmod.render(*signed(PAD_SVG_TAP))) >= 1


def test_the_legacy_base64_signature_still_renders(signed):
    """Early rows are data URLs. They must not regress while fixing SVG."""
    body = content_stream(pdfmod.render(*signed(LEGACY_PNG)))
    assert "no drawn signature stored" not in body
    assert "/Image" in body or " Do" in body, "the raster was not drawn"


# ── and says so honestly when it cannot ─────────────────────────────────────
@pytest.mark.parametrize("stored, why", [
    (None, "nothing stored"),
    ("", "empty string"),
    ("<svg/>", "an empty document — a real row in this database"),
    ("<svg viewBox='0 0 600 150'></svg>", "no paths"),
])
def test_no_ink_says_so_rather_than_pretending(stored, why, signed):
    """Silently drawing *something* on a binding agreement would be worse than
    admitting the ink could not be reproduced."""
    body = content_stream(pdfmod.render(*signed(stored)))
    assert "no drawn signature stored" in body, f"expected the honest note for {why}"


@pytest.mark.parametrize("junk", [
    "<svg><path d=\"M nonsense\"/></svg>",
    "<svg viewBox=\"0 0 0 0\"><path d=\"M 1 1 L 2 2\"/></svg>",
    "not markup at all",
    "data:image/png;base64,!!!not-base64!!!",
    "<svg><path d=\"" + "L 1 1 " * 5000 + "\"/></svg>",
])
def test_a_malformed_signature_never_breaks_the_document(junk, signed):
    """Someone needing their agreement must not meet a 500 because the string
    stored against it is strange."""
    pdf = pdfmod.render(*signed(junk))
    assert pdf.startswith(b"%PDF")


# ── Greek ───────────────────────────────────────────────────────────────────
def test_this_host_can_render_greek():
    """Fails loudly rather than corrupting documents quietly.

    If this fails the PDF still renders — but every accented Greek vowel comes
    out as a box. Install a Unicode font: `fonts-dejavu-core` on Debian (the
    container already does), or point PDF_FONT_FILE at a .ttf.
    """
    assert pdfmod.greek_capable(), (
        f"No font on this host can write Greek, so agreements would print "
        f"Γιώργος as Γι■ργος. Falling back to {pdfmod.BODY}. "
        f"Install fonts-dejavu-core or set PDF_FONT_FILE."
    )


def test_a_greek_name_survives_onto_the_agreement(signed):
    """The accents are the whole test. Unaccented Greek always worked, which is
    exactly why this went unnoticed."""
    name = "Γιώργος Παπαδόπουλος"
    pdf = pdfmod.render(*signed(
        PAD_SVG,
        signed_name=name,
        contact_name=name,
        business_name="Παπαδόπουλος & Συνεργάτες",
        signed_fields={"signed_name": name, "profession": "Δικηγόρος",
                       "licence": "ΔΣΑ-2026-4471", "vat_number": "EL123456789",
                       "email": "k@example.com", "phone": "+30 210 111 1111"},
    ))

    # An embedded font subset carries the glyphs; the name is written with
    # glyph codes rather than literal text, so assert on the subset's coverage.
    body = content_stream(pdf)
    assert "no drawn signature stored" not in body
    assert pdf.startswith(b"%PDF")

    # Every distinct character of the name must exist in the registered face.
    from reportlab.pdfbase import pdfmetrics
    face = pdfmetrics.getFont(pdfmod.BODY).face
    missing = sorted({c for c in name + "Δικηγόρος ΔΣΑ ΑΦΜ" if c.strip()
                      and ord(c) not in getattr(face, "charToGlyph", {})})
    assert not missing, f"the agreement font cannot write: {''.join(missing)}"


def test_the_greek_check_can_actually_fail():
    """A guard nobody has watched fail is a guess.

    Helvetica is what the renderer used before, and what it falls back to. If
    this ever passes, the probe is broken and the Greek test above proves
    nothing.
    """
    from reportlab.pdfbase import pdfmetrics
    helvetica = pdfmetrics.getFont("Helvetica").face
    assert not hasattr(helvetica, "charToGlyph") or \
        any(ord(c) not in helvetica.charToGlyph for c in "Γιώργος"), \
        "Helvetica appears to cover Greek, which would mean the probe is wrong"
