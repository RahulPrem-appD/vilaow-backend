"""Who is actually on the other end of a request.

`request.client.host` is the peer that opened the TCP connection. Behind
Render's load balancer that is always the proxy, so every visitor on the
internet shared one rate-limit bucket: the per-IP cap on the introduction form
became a site-wide cap of a handful per hour, which one person could exhaust to
lock the form for everybody. The same value is written to `Introduction.ip` and
printed on the signed agreement PDF as "Submitted from …", where it identified
nobody.

The forwarded header is only trusted when the app is deployed behind a proxy
that sets it, because anyone can send it. `Settings.is_production` is that
switch: in development the peer address is already the real one, so nothing is
trusted that does not need to be.
"""
from __future__ import annotations

import ipaddress

from fastapi import Request

from app.config import Settings

# Render, Vercel, Cloudflare and friends all prepend; the leftmost entry is
# the original client, with each hop appended after it.
_FORWARDED = "x-forwarded-for"


def _usable(value: str) -> bool:
    """A routable address, so a spoofed private hop cannot become the key."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not (address.is_private or address.is_loopback or
                address.is_reserved or address.is_unspecified)


def client_ip(request: Request, settings: Settings) -> str | None:
    """The caller's address, or None when nothing trustworthy is available.

    None is a real answer, not a failure: the rate limiter falls back to the
    per-email cap rather than lumping unknown callers into one shared bucket,
    which is the bug this exists to avoid repeating.
    """
    peer = request.client.host if request.client else None

    if not settings.is_production:
        return peer

    forwarded = request.headers.get(_FORWARDED, "")
    for candidate in (part.strip() for part in forwarded.split(",")):
        if candidate and _usable(candidate):
            return candidate

    # Behind a proxy with no usable forwarded address, the peer is the proxy.
    # Returning it would rebuild the shared bucket, so say so instead.
    return peer if peer and _usable(peer) else None
