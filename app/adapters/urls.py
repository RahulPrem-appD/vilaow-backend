"""Public URLs the backend has to put inside emails.

The API and the website are different hosts, so a link in an email cannot be
built from the request. It comes from configuration — and it is built here
rather than inside a template, so the templates stay pure functions of their
arguments and can be tested without settings.
"""
from __future__ import annotations


class PublicUrls:
    def __init__(self, site_base_url: str) -> None:
        self._base = site_base_url.rstrip("/")

    def agreement(self, token: str) -> str:
        return f"{self._base}/sign/{token}"

    def review(self, token: str) -> str:
        return f"{self._base}/review/{token}"
