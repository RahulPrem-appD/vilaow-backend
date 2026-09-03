"""Screenshots for the user guide.

Drives the local site with Playwright and writes PNGs. Local rather than
production because production is empty: 327 professionals, five published
profiles and four signed agreements make a guide that shows the product doing
its job, and an empty directory does not.

    python3 -m scripts.capture_guide_shots

Nothing here changes data except signing in, so it is safe to re-run.
"""
from __future__ import annotations

import pathlib
import sys

from playwright.sync_api import Page, sync_playwright

SITE = "http://localhost:3000"
OUT = pathlib.Path(__file__).resolve().parents[2] / "docs" / "guide" / "shots"

OWNER = ("asaf@vilaow.com", "dev-owner-pw")
CALLER = ("elena@vilaow.com", "dev-caller-pw")

WIDE = {"width": 1280, "height": 860}
PHONE = {"width": 420, "height": 880}


# The dev-server badge sits in the bottom-left corner, on top of Sign out.
# It is not part of the product and should not appear in a guide.
HIDE_DEV_BADGE = """
  nextjs-portal, [data-nextjs-toast], #__next-build-watcher,
  [data-nextjs-dev-tools-button] { display: none !important; }
"""


def shot(page: Page, name: str, *, full: bool = False) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    page.add_style_tag(content=HIDE_DEV_BADGE)
    page.wait_for_timeout(700)
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
    print(f"  {name}.png")


def sign_in(page: Page, who: tuple[str, str]) -> None:
    """Sign in and wait until the session is actually established.

    `wait_for_load_state` is not enough: signing in is a fetch followed by a
    client-side route change, so it returns before the cookie is set and every
    page after it redirects back here. Six identical screenshots of the
    sign-in form is how that shows up.
    """
    page.goto(f"{SITE}/admin", wait_until="networkidle")
    page.fill('input[type=email]', who[0])
    page.fill('input[type=password]', who[1])
    page.click('button[type=submit]')
    page.wait_for_url(lambda url: "/admin/dashboard" in url, timeout=15000)
    page.wait_for_load_state("networkidle")
    if "Not signed in" in page.inner_text("body"):
        raise RuntimeError(f"sign-in failed for {who[0]}")


def buyer(page: Page) -> None:
    print("buyer")
    page.goto(SITE, wait_until="networkidle")
    shot(page, "buyer-01-home")

    # The directory is the homepage's #results section, as in his index.html.
    page.goto(f"{SITE}/#results", wait_until="networkidle")
    shot(page, "buyer-02-directory")

    page.goto(f"{SITE}/?region=Crete&role=lawyer#results", wait_until="networkidle")
    shot(page, "buyer-03-filtered")

    page.goto(f"{SITE}/p/kostas-papadopoulos", wait_until="networkidle")
    shot(page, "buyer-04-profile")
    shot(page, "buyer-05-profile-full", full=True)

    # The form is behind "Ask for an introduction", as it is for a real buyer.
    page.get_by_text("Ask for an introduction").first.click()
    page.wait_for_timeout(1200)
    form = page.locator("form").filter(has=page.locator("input[type=email]")).first
    form.scroll_into_view_if_needed()
    shot(page, "buyer-06-introduction-form")


def professional(page: Page, fresh: str, signed: str) -> None:
    """Two tokens, because one agreement cannot be in both states.

    `fresh` is an unsigned link — what the professional actually opens.
    `signed` is one already completed, for the finished state.
    """
    print("professional")
    page.goto(f"{SITE}/sign/{fresh}", wait_until="networkidle")
    shot(page, "pro-01-agreement-top")
    shot(page, "pro-02-agreement-full", full=True)

    page.goto(f"{SITE}/sign/{signed}", wait_until="networkidle")
    shot(page, "pro-03-signed-and-confirmed")


def caller(page: Page) -> None:
    print("caller")
    sign_in(page, CALLER)
    shot(page, "caller-01-dashboard")

    page.goto(f"{SITE}/admin/worklist", wait_until="networkidle")
    shot(page, "caller-02-worklist")
    shot(page, "caller-03-worklist-full", full=True)

    page.goto(f"{SITE}/admin/call/1", wait_until="networkidle")
    shot(page, "caller-04-call-form")
    shot(page, "caller-05-call-form-full", full=True)

    page.goto(f"{SITE}/admin/introductions", wait_until="networkidle")
    shot(page, "caller-06-introductions")

    page.goto(f"{SITE}/admin/leads", wait_until="networkidle")
    shot(page, "caller-07-leads")


def owner(page: Page) -> None:
    print("owner")
    page.goto(f"{SITE}/admin", wait_until="networkidle")
    shot(page, "owner-00-sign-in")

    sign_in(page, OWNER)
    page.goto(f"{SITE}/admin/dashboard", wait_until="networkidle")
    shot(page, "owner-01-dashboard")

    page.goto(f"{SITE}/admin/professions", wait_until="networkidle")
    shot(page, "owner-02-professions")

    page.goto(f"{SITE}/admin/professions/2/fields", wait_until="networkidle")
    shot(page, "owner-03-form-builder")
    shot(page, "owner-04-form-builder-full", full=True)

    page.goto(f"{SITE}/admin/professional/6", wait_until="networkidle")
    shot(page, "owner-05-record-and-readiness", full=True)

    page.goto(f"{SITE}/admin/staff", wait_until="networkidle")
    shot(page, "owner-06-staff")


def main() -> int:
    fresh = sys.argv[1] if len(sys.argv) > 1 else None
    signed = sys.argv[2] if len(sys.argv) > 2 else None
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            # A context per role, because they are different people. Sharing one
            # left the owner signed in as the caller and skipped the login page.
            for job in (lambda pg: buyer(pg),
                        (lambda pg: professional(pg, fresh, signed)) if fresh else None,
                        lambda pg: caller(pg),
                        lambda pg: owner(pg)):
                if job is None:
                    continue
                context = browser.new_context(viewport=WIDE, device_scale_factor=2)
                try:
                    job(context.new_page())
                finally:
                    context.close()
        finally:
            browser.close()
    print(f"\nwritten to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
