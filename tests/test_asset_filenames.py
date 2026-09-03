"""Serving a file whose name we did not choose — assets and agreement PDFs.

A Greek professional's phone names a photo in Greek. That name is stored, and
it was interpolated straight into `Content-Disposition` — a header value, which
is latin-1. Starlette raised UnicodeEncodeError while encoding the response, so
the asset 500'd on every single request from then on and the photo never
appeared. Nothing in the suite uploaded a non-ASCII name, so nothing caught it.

Checked against the broken version first: with `_content_disposition` replaced
by the old f-string, the Greek and emoji cases raise UnicodeEncodeError here.
"""
import pytest
from starlette.responses import Response

from app.api.headers import content_disposition


def header_for(name):
    """Build the real response, because the failure was in encoding it."""
    value = content_disposition(name)
    response = Response(content=b"x", headers={"Content-Disposition": value})
    return dict(response.raw_headers)[b"content-disposition"].decode("latin-1")


@pytest.mark.parametrize("name", [
    "plain.jpg", "Παπαδάκης.jpg", "Γιώργος-άδεια.pdf", "photo 😀.png",
    "naïve.jpeg", "文件.png", "файл.jpg",
])
def test_any_name_still_encodes_as_a_header(name):
    """The whole bug: this raised before it ever reached the client."""
    assert header_for(name).startswith("inline;")


@pytest.mark.parametrize("name", [None, "", "   ", "\r\n", "\n\n"])
def test_a_missing_or_empty_name_falls_back(name):
    assert 'filename="file"' in header_for(name)


def test_the_real_name_survives_percent_encoded():
    """The ASCII fallback loses the letters; `filename*` must not."""
    from urllib.parse import quote
    assert quote("Παπαδάκης.jpg", safe="") in header_for("Παπαδάκης.jpg")


@pytest.mark.parametrize("name, must_not_contain", [
    ('a" ; download; x="b.jpg', '"a"'),      # quote closed the quoted string
    ('x\r\nSet-Cookie: a=b', "Set-Cookie: a=b\r\n"),
])
def test_a_name_cannot_add_header_syntax_of_its_own(name, must_not_contain):
    value = header_for(name)
    assert "\r" not in value and "\n" not in value
    assert value.count('"') == 2, f"quotes not balanced: {value}"


def test_a_name_cannot_carry_crlf_onto_the_wire():
    """The CR and LF must be gone from the *value*, not merely fail to split.

    This asserted that no `x-evil` header key appeared, which Starlette never
    produces anyway: `init_headers` encodes each value and never splits one
    into two headers. So it passed with the CR/LF strip removed — a
    security-flavoured test that green-lit its own regression. The invariant
    that matters is that the bytes on the wire carry no CR or LF, so that is
    what this checks now.
    """
    raw = dict(
        Response(
            content=b"x",
            headers={"Content-Disposition": content_disposition("a\r\nX-Evil: 1")},
        ).raw_headers
    )[b"content-disposition"]
    assert b"\r" not in raw, raw
    assert b"\n" not in raw, raw


def test_the_agreement_pdf_name_shape_encodes():
    """The helper, on the shape the agreement route hands it.

    This is a unit check of `content_disposition` only. It does NOT prove the
    agreement route uses it — the route could go back to building the header
    itself and this would stay green, which is exactly what happened once.
    That guarantee lives in
    tests/test_api.py::test_the_pdf_route_survives_a_greek_signer, which issues
    the real request.
    """
    for name in ["Γιώργος Παπαδόπουλος", "Ναυτιλιακή Α.Ε.", "Check Firm", ""]:
        value = content_disposition(f"Vilaow agreement - {name}.pdf")
        response = Response(content=b"%PDF", headers={"Content-Disposition": value})
        header = dict(response.raw_headers)[b"content-disposition"].decode("latin-1")
        assert header.startswith("inline;")
        assert "Vilaow agreement" in header
