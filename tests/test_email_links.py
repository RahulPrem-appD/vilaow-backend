"""Every link we email must land on a page that exists.

This is here because it did not. The review-request email pointed at
`/review/{token}`, the API endpoint behind it was built and tested, the mail
was sending — and the page had never been written, so every buyer who followed
that link got a 404. The whole verified-review loop had no way to finish, and
nothing in the suite noticed, because the backend was entirely correct.

The gap is structural: the backend owns the URL and the frontend owns the page,
and no test spanned the two. This one does, by checking the Next app router
directory for a route matching each URL the backend can emit.

It is deliberately filesystem-based rather than an HTTP request, so it works in
CI with no dev server running.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.urls import PublicUrls

# backend/tests/ -> backend/ -> repo root -> app/src/app
APP_ROUTER = Path(__file__).resolve().parents[2] / "app" / "src" / "app"


def route_exists(path: str) -> bool:
    """Does the Next app router have a page for this path?

    `/sign/abc123` matches `sign/[token]/page.tsx`: a literal segment matches
    itself, and any `[param]` directory matches one segment.
    """
    segments = [s for s in path.strip("/").split("/") if s]
    candidates = [APP_ROUTER]

    for segment in segments:
        nxt: list[Path] = []
        for base in candidates:
            exact = base / segment
            if exact.is_dir():
                nxt.append(exact)
            # A dynamic segment, and route groups like (site) which add no path.
            for child in base.iterdir() if base.is_dir() else []:
                if child.is_dir() and child.name.startswith("[") and child.name.endswith("]"):
                    nxt.append(child)
                elif child.is_dir() and child.name.startswith("(") and child.name.endswith(")"):
                    if (child / segment).is_dir():
                        nxt.append(child / segment)
        candidates = nxt
        if not candidates:
            return False

    return any((c / "page.tsx").exists() or (c / "page.ts").exists() for c in candidates)


@pytest.mark.parametrize(
    "name, url",
    [
        ("signing link", PublicUrls("http://x").agreement("TOKEN123")),
        ("review link", PublicUrls("http://x").review("TOKEN123")),
    ],
)
def test_every_emailed_link_has_a_page(name: str, url: str) -> None:
    path = url.replace("http://x", "")
    assert route_exists(path), (
        f"The {name} points at {path}, but no page in app/src/app serves it. "
        f"An email that links to a 404 is worse than no email."
    )


def test_the_route_check_can_actually_fail() -> None:
    """A guard that cannot fail is not a guard.

    Without this, a bug in `route_exists` that made it always return True would
    leave the tests above passing while protecting nothing.
    """
    assert not route_exists("/definitely-not-a-route/xyz")
