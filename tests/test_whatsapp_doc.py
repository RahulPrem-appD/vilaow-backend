"""The templates document says exactly what the code sends.

Meta approves the words in docs/whatsapp-templates.md; the code sends the words
in app/adapters/whatsapp/templates.py. If one changes without the other, the
approved template and the message stop matching, and WhatsApp refuses to send.
"""
from __future__ import annotations

from pathlib import Path

from app.adapters.whatsapp.approval_doc import document

DOC = Path(__file__).resolve().parents[1] / "docs" / "whatsapp-templates.md"


def test_the_document_is_written_from_the_templates():
    assert DOC.read_text(encoding="utf-8") == document(), (
        "Rewrite it: python -m app.adapters.whatsapp.approval_doc > docs/whatsapp-templates.md"
    )
