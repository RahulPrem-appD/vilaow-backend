"""The signed agreement, rendered as a PDF from the stored row.

Built on demand rather than saved as a file at signing time. That is only safe
because the agreement row freezes the clauses verbatim: this renderer reproduces
`terms_text` as stored, never today's version of the constant, so a copy
produced years later still shows the words that person actually agreed to.

Everything on the page comes from the row — the drawn signature, the field
values as submitted, the timestamp, the IP, and the confirmed address. Nothing
is re-derived from the professional record, which may have been edited since.
"""
from __future__ import annotations

import base64
import binascii
import os
import re
from dataclasses import dataclass
from io import BytesIO

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas

from app.models import Agreement, Professional

# His palette, so a printed agreement looks like the thing that was signed.
AEGEAN = HexColor("#0b3a6b")
INK = HexColor("#19222c")
MUTED = HexColor("#666f78")
LINE = HexColor("#e3e2d6")

PAGE_W, PAGE_H = A4
MARGIN = 20 * mm

# ── fonts ───────────────────────────────────────────────────────────────────
# Helvetica is one of reportlab's built-in Type 1 faces, and it has no Greek.
# On a Greek directory that is not cosmetic: an unaccented name came through,
# but every accented vowel — ώ in Γιώργος, ό in Παπαδόπουλος — was replaced by
# a filled box, silently, on the only copy of a binding agreement. Names
# without an accent are the minority.
#
# The font is resolved from the host rather than committed here. The container
# installs fonts-dejavu-core (see Dockerfile); a developer's machine has its own
# Unicode face. PDF_FONT_FILE overrides both. If nothing is found the document
# still renders in Helvetica — a readable agreement with mangled accents beats
# no agreement at all — and `greek_capable()` reports the truth so a caller can
# see it rather than discover it from a professional's complaint.
_FONT_CANDIDATES = (
    # Debian/Ubuntu, which is what the container is.
    ("DejaVuSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("NotoSans", "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
     "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
    # macOS, for local work. One weight only, so bold reuses it.
    ("ArialUnicode", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", None),
)

# A name that exercises the failure: unaccented Greek always worked.
_GREEK_PROBE = "Γιώργος"

BODY = "Helvetica"
BODY_BOLD = "Helvetica-Bold"
BODY_ITALIC = "Helvetica-Oblique"


def _register_unicode_font() -> tuple[str, str, str]:
    """Register the first font on this host that can actually write Greek.

    Returns the (regular, bold, italic) face names to use. Falls back to the
    built-in Helvetica trio, which is why nothing here raises.
    """
    override = os.environ.get("PDF_FONT_FILE")
    candidates = ((("Custom", override, os.environ.get("PDF_FONT_FILE_BOLD")),) if override else ()) \
        + _FONT_CANDIDATES

    for name, regular, bold in candidates:
        if not regular or not os.path.exists(regular):
            continue
        try:
            face = TTFont(name, regular)
            if any(ord(c) not in face.face.charToGlyph for c in _GREEK_PROBE):
                continue                      # present, but no Greek — keep looking
            pdfmetrics.registerFont(face)
            bold_name = name
            if bold and os.path.exists(bold):
                bold_name = f"{name}-Bold"
                pdfmetrics.registerFont(TTFont(bold_name, bold))
            # Italic is only used for one parenthetical note; reuse regular
            # rather than fake a slant on a legal document.
            return name, bold_name, name
        except Exception:                     # noqa: BLE001 — a bad font must not break the PDF
            continue

    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"


BODY, BODY_BOLD, BODY_ITALIC = _register_unicode_font()


def greek_capable() -> bool:
    """Whether this process can render Greek. Asserted by the tests, and worth
    checking on a host before blaming the data."""
    return BODY != "Helvetica"


def _decode_signature(data_url: str | None) -> ImageReader | None:
    """A raster signature stored as `data:image/png;base64,...`.

    Only the historical format. The pad has emitted SVG since it was rebuilt on
    the client's design; see `_signature_strokes`. Kept because a handful of
    early rows are data URLs, and because refusing to render a document someone
    needs is worse than carrying twenty lines.

    Anything malformed returns None rather than raising: a PDF without the ink
    is far more useful than an endpoint that 500s when someone wants their copy.
    """
    if not data_url or not data_url.startswith("data:"):
        return None
    try:
        _, _, encoded = data_url.partition(",")
        if not encoded:
            return None
        return ImageReader(BytesIO(base64.b64decode(encoded)))
    except (binascii.Error, ValueError, OSError):
        return None


# What the pad emits, and nothing more: `M x y L x y L …`, with a trailing
# `l 0 0` for a single tap so a dot still has a segment to round-cap.
_PATH_D = re.compile(r'\sd="([^"]*)"')
_VIEWBOX = re.compile(r'\sviewBox="([\d.\s+-]+)"')
_STROKE = re.compile(r'\sstroke="(#[0-9a-fA-F]{3,8})"')
_STROKE_WIDTH = re.compile(r'\sstroke-width="([\d.]+)"')
_COMMAND = re.compile(r"([MmLl])\s*(-?[\d.]+)[\s,]+(-?[\d.]+)")


@dataclass(frozen=True)
class _Signature:
    """A drawn signature in its own viewBox coordinates."""
    strokes: list[list[tuple[float, float]]]
    width: float
    height: float
    colour: str
    line_width: float


def _signature_strokes(svg: str | None) -> _Signature | None:
    """Parse the SVG the signature pad produces.

    This deliberately understands *our own* pad's output and not SVG in
    general. The pad writes one `<path>` per stroke, absolute `M`/`L` with a
    relative `l 0 0` for a tap, in a fixed 600x150 viewBox — see
    app/src/components/sign-signature-pad.tsx. A real SVG renderer would be a
    dependency and an attack surface for a string that arrives from a public,
    unauthenticated endpoint.

    Anything this cannot parse yields None, and the document says so rather
    than pretending. That matters more than usual here: this is the only copy
    of a signature on a binding agreement, so silently drawing *something*
    would be worse than admitting the ink could not be reproduced.
    """
    if not svg or "<svg" not in svg:
        return None

    box = _VIEWBOX.search(svg)
    if box:
        parts = box.group(1).replace(",", " ").split()
        width, height = (float(parts[2]), float(parts[3])) if len(parts) == 4 else (600.0, 150.0)
    else:
        width, height = 600.0, 150.0
    if width <= 0 or height <= 0:
        return None

    strokes: list[list[tuple[float, float]]] = []
    for d in _PATH_D.findall(svg):
        points: list[tuple[float, float]] = []
        x = y = 0.0
        for command, raw_x, raw_y in _COMMAND.findall(d):
            dx, dy = float(raw_x), float(raw_y)
            # Lowercase is relative; the pad only uses it for a tap's `l 0 0`.
            x, y = (x + dx, y + dy) if command.islower() else (dx, dy)
            points.append((x, y))
        if points:
            strokes.append(points)

    if not strokes:
        # An empty `<svg/>` is a real row in this database — a dev artifact from
        # before the pad refused to submit nothing. It has no ink to draw.
        return None

    colour = _STROKE.search(svg)
    line_width = _STROKE_WIDTH.search(svg)
    return _Signature(
        strokes=strokes,
        width=width,
        height=height,
        colour=colour.group(1) if colour else "#19222c",
        line_width=float(line_width.group(1)) if line_width else 2.2,
    )


def _draw_signature(c, signature: _Signature, x: float, y: float, box_height: float) -> None:
    """Stroke the parsed signature into a box, preserving its aspect ratio.

    Drawn as vector rather than embedded as an image, which is what the stored
    SVG already is: it stays sharp at any print size, and the PDF carries the
    same geometry the person actually drew.
    """
    scale = box_height / signature.height
    c.saveState()
    try:
        c.setStrokeColor(HexColor(signature.colour))
        c.setLineWidth(max(signature.line_width * scale, 0.4))
        c.setLineCap(1)                       # round, as on the pad
        c.setLineJoin(1)
        for stroke in signature.strokes:
            path = c.beginPath()
            # SVG's y grows downward; PDF's grows upward.
            path.moveTo(x + stroke[0][0] * scale, y + (signature.height - stroke[0][1]) * scale)
            for px, py in stroke[1:]:
                path.lineTo(x + px * scale, y + (signature.height - py) * scale)
            if len(stroke) == 1:
                # A tap: a zero-length segment renders as a dot under a round cap.
                path.lineTo(x + stroke[0][0] * scale, y + (signature.height - stroke[0][1]) * scale)
            c.drawPath(path, stroke=1, fill=0)
    finally:
        c.restoreState()


class _Sheet:
    """A cursor over one or more pages, so nothing silently runs off the end."""

    def __init__(self, c: pdfcanvas.Canvas) -> None:
        self.c = c
        self.y = PAGE_H - MARGIN

    def space(self, needed: float) -> None:
        if self.y - needed < MARGIN:
            self.c.showPage()
            self.y = PAGE_H - MARGIN

    def text(self, s: str, *, size: float = 10, colour=INK, font: str | None = None,
             leading: float = 5.2, indent: float = 0) -> None:
        font = font or BODY
        self.c.setFont(font, size)
        self.c.setFillColor(colour)
        width = PAGE_W - 2 * MARGIN - indent
        for line in _wrap(s, font, size, width, self.c):
            self.space(size + leading)
            self.c.setFont(font, size)
            self.c.setFillColor(colour)
            self.c.drawString(MARGIN + indent, self.y, line)
            self.y -= size + leading

    def gap(self, amount: float) -> None:
        self.y -= amount

    def rule(self) -> None:
        self.space(6)
        self.c.setStrokeColor(LINE)
        self.c.setLineWidth(0.6)
        self.c.line(MARGIN, self.y, PAGE_W - MARGIN, self.y)
        self.y -= 6


def _wrap(s: str, font: str, size: float, width: float, c: pdfcanvas.Canvas) -> list[str]:
    out: list[str] = []
    for paragraph in (s or "").split("\n"):
        if not paragraph.strip():
            out.append("")
            continue
        line = ""
        for word in paragraph.split():
            trial = f"{line} {word}".strip()
            if c.stringWidth(trial, font, size) <= width:
                line = trial
            else:
                if line:
                    out.append(line)
                line = word
        if line:
            out.append(line)
    return out


def render(agreement: Agreement, professional: Professional) -> bytes:
    buf = BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4, pageCompression=1)
    c.setTitle(f"Vilaow listing agreement — {agreement.signed_name or professional.business_name}")
    sheet = _Sheet(c)

    # ── header ──────────────────────────────────────────────────────────────
    c.setFont(BODY_BOLD, 17)
    c.setFillColor(AEGEAN)
    c.drawString(MARGIN, sheet.y, "vilaow")
    c.setFont(BODY, 9)
    c.setFillColor(MUTED)
    c.drawRightString(PAGE_W - MARGIN, sheet.y, "Free listing")
    sheet.y -= 22

    sheet.text("Professional Listing Agreement", size=16, font=BODY_BOLD)
    sheet.gap(2)
    sheet.text(f"Terms version {agreement.terms_version}", size=9, colour=MUTED)
    sheet.gap(8)
    sheet.rule()
    sheet.gap(6)

    # ── who signed ──────────────────────────────────────────────────────────
    # From the frozen submission, not from the record: the professional may
    # have been edited since, and this document is evidence of that moment.
    signed = agreement.signed_fields or {}
    rows = [
        ("Name", signed.get("signed_name") or agreement.signed_name),
        ("Profession", signed.get("profession")
            or (professional.profession.label if professional.profession else None)),
        ("Licence no.", signed.get("licence") or professional.license),
        ("VAT no. (ΑΦΜ)", signed.get("vat_number") or professional.vat_number),
        ("Email", agreement.signed_email or signed.get("email")),
        ("Phone", signed.get("phone") or professional.phone),
    ]
    for label, value in rows:
        sheet.space(14)
        c.setFont(BODY, 9)
        c.setFillColor(MUTED)
        c.drawString(MARGIN, sheet.y, label)
        c.setFont(BODY_BOLD, 10)
        c.setFillColor(INK)
        c.drawString(MARGIN + 95, sheet.y, str(value or "—"))
        sheet.y -= 14

    sheet.gap(6)
    sheet.rule()
    sheet.gap(6)

    # ── the terms, verbatim ─────────────────────────────────────────────────
    sheet.text("THE TERMS", size=9, font=BODY_BOLD, colour=AEGEAN)
    sheet.gap(4)
    sheet.text(
        agreement.terms_text or "(the agreed terms were not recorded on this agreement)",
        size=9.5, leading=4.6,
    )
    sheet.gap(6)

    consent = signed.get("consent_statement")
    if consent:
        sheet.text(f"Agreed: {consent}", size=8.5, colour=MUTED, leading=3.8)
        sheet.gap(6)

    # ── the signature ───────────────────────────────────────────────────────
    sheet.space(72)
    sheet.rule()
    sheet.gap(4)
    sheet.text("SIGNATURE", size=9, font=BODY_BOLD, colour=AEGEAN)
    sheet.gap(4)

    # Two formats, because two have been stored. The pad emits SVG; a handful
    # of early rows are base64 rasters. Neither may crash the document.
    drawn = _signature_strokes(agreement.signature_image)
    ink = None if drawn else _decode_signature(agreement.signature_image)

    if drawn is not None:
        sheet.space(46)
        try:
            _draw_signature(c, drawn, MARGIN, sheet.y - 40, box_height=40)
        except Exception:                     # noqa: BLE001 — never fail the document
            c.setFont(BODY_ITALIC, 9)
            c.setFillColor(MUTED)
            c.drawString(MARGIN, sheet.y - 20, "(signature could not be rendered)")
        sheet.y -= 46
    elif ink is not None:
        sheet.space(46)
        try:
            c.drawImage(ink, MARGIN, sheet.y - 40, width=70 * mm, height=40,
                        mask="auto", preserveAspectRatio=True, anchor="sw")
        except Exception:                     # noqa: BLE001 — never fail the document
            c.setFont(BODY_ITALIC, 9)
            c.setFillColor(MUTED)
            c.drawString(MARGIN, sheet.y - 20, "(signature could not be rendered)")
        sheet.y -= 46
    else:
        sheet.text("(no drawn signature stored)", size=9, colour=MUTED)

    sheet.gap(4)
    sheet.rule()
    sheet.gap(4)

    # ── the audit trail ─────────────────────────────────────────────────────
    when = agreement.signed_at.strftime("%d %B %Y at %H:%M UTC") if agreement.signed_at else "—"
    verified = (agreement.email_verified_at.strftime("%d %B %Y at %H:%M UTC")
                if agreement.email_verified_at else None)

    sheet.text(f"Signed electronically on {when}", size=8.5, colour=MUTED, leading=3.4)
    if verified:
        sheet.text(
            f"Email address {agreement.signed_email} confirmed by emailed code on {verified}",
            size=8.5, colour=MUTED, leading=3.4,
        )
    else:
        sheet.text("Email address was not confirmed.", size=8.5, colour=MUTED, leading=3.4)
    if agreement.signed_ip:
        sheet.text(f"Submitted from {agreement.signed_ip}", size=8.5, colour=MUTED, leading=3.4)
    sheet.gap(4)
    sheet.text("Vilaow · Trusted property professionals in Greece", size=8, colour=MUTED)

    c.showPage()
    c.save()
    return buf.getvalue()
