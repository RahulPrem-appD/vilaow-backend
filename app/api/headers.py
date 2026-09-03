"""Header values built from data we did not choose.

A filename comes from a professional's phone or from the client's contact
sheet. It is not ours, it is not ASCII, and it goes into an HTTP header — where
values are latin-1 and a Greek letter raises UnicodeEncodeError while the
response is being encoded. The row keeps the name, so the failure is not
transient: that document 500s on every request from then on.

This lived inside routers/assets.py and the agreement PDF route grew its own
version two files over, which "sanitised" with `str.isalnum()`. That is
Unicode-aware — `"Γιώργος".isalnum()` is True — so it kept every Greek letter
and broke in exactly the same way, on the binding legal document. One
implementation, imported by both, is the point of this module.
"""
from __future__ import annotations

from urllib.parse import quote


def content_disposition(filename: str | None) -> str:
    """`Content-Disposition` for a name we did not choose.

    Two things go wrong if the stored name is interpolated raw, and both were
    live. HTTP header values are latin-1, so a Greek filename — the normal case
    in this directory, and exactly what a Greek professional's phone produces —
    raised UnicodeEncodeError while Starlette encoded the response. Not once:
    the row keeps the name, so that document 500'd on every request forever. A
    quote in the name was the other half — it closed the quoted string and let
    the rest of the filename add parameters of its own.

    RFC 6266 is the answer to both: an ASCII-only `filename` that any client can
    read, and a `filename*` carrying the real UTF-8 name percent-encoded. Non-
    ASCII is replaced rather than dropped so the fallback keeps its shape.
    """
    name = (filename or "file").replace("\r", "").replace("\n", "").strip() or "file"
    ascii_fallback = name.encode("ascii", "replace").decode("ascii")
    ascii_fallback = ascii_fallback.replace("\\", "_").replace('"', "_")
    return f"inline; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(name, safe='')}"
