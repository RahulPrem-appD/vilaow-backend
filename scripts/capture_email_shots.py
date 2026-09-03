"""Screenshots of every email Vilaow sends.

The bodies are produced by the real template functions in
app/adapters/email/templates.py — the same code that built the messages
actually delivered — so what is pictured is what a recipient receives, not a
mock-up. Each one is framed with the From / To / Subject lines a mail client
shows above it.

Rendered rather than photographed from an inbox on purpose: a screenshot of a
real Gmail account carries that person's unread counts, labels and other
people's mail down the side, none of which belongs in a guide handed to a
client.

    python3 -m scripts.capture_email_shots
"""
from __future__ import annotations

import pathlib

from playwright.sync_api import sync_playwright

from app.adapters.email import templates
from app.adapters.urls import PublicUrls

OUT = pathlib.Path(__file__).resolve().parents[2] / "docs" / "guide" / "shots"
SITE = "https://vilaow.com"
urls = PublicUrls(SITE)

FRAME = """
<!doctype html><meta charset="utf-8">
<style>
  body {{ margin: 0; background: #eef0f4; font-family: system-ui, -apple-system, sans-serif; }}
  .wrap {{ max-width: 720px; margin: 22px auto; background: #fff; border-radius: 12px;
          overflow: hidden; box-shadow: 0 1px 3px rgba(16,24,40,.14); }}
  .head {{ padding: 18px 24px 16px; border-bottom: 1px solid #e6e8ee; }}
  .subject {{ font-size: 19px; font-weight: 600; color: #14181f; margin: 0 0 12px; }}
  .row {{ display: flex; gap: 10px; font-size: 13px; line-height: 1.6; }}
  .k {{ color: #6b7280; width: 58px; flex: none; }}
  .v {{ color: #14181f; }}
  .avatar {{ width: 34px; height: 34px; border-radius: 50%; background: #0b3a6b; color: #fff;
            display: inline-grid; place-items: center; font-weight: 700; font-size: 14px;
            margin-right: 12px; float: left; }}
  .body {{ padding: 22px 24px 30px; }}
</style>
<div class="wrap">
  <div class="head">
    <p class="subject">{subject}</p>
    <div class="avatar">V</div>
    <div>
      <div class="row"><span class="k">From</span><span class="v"><b>Vilaow</b> &lt;hello@vilaow.com&gt;</span></div>
      <div class="row"><span class="k">To</span><span class="v">{to}</span></div>
    </div>
  </div>
  <div class="body">{html}</div>
</div>
"""

MESSAGES = [
    ("email-1-agreement-invitation", templates.agreement_invitation(
        to="nikos@andreou-surveying.gr", name="Nikos Andreou",
        link=urls.agreement("Xk9mP2qR7vT4wY8nB3cF6hJ1lZ0aS5dG"), ttl_days=30)),
    ("email-2-confirmation-code", templates.signing_code(
        to="nikos@andreou-surveying.gr", name="Nikos Andreou", code="697251")),
    ("email-3-introduction-to-professional", templates.introduction_to_professional(
        to="kostas@papadopoulos-law.gr", name="Kostas Papadopoulos",
        buyer_name="Sarah Mitchell", buyer_email="sarah.mitchell@example.co.uk",
        buyer_phone="+44 7700 900123",
        message="We are buying a stone house near Chania in the spring and need "
                "a title check before we commit.")),
    ("email-4-buyer-confirmation", templates.introduction_confirmation(
        to="sarah.mitchell@example.co.uk", buyer_name="Sarah Mitchell",
        professional_name="Kostas Papadopoulos", professional_role="Lawyer")),
    ("email-5-review-request", templates.review_request(
        to="sarah.mitchell@example.co.uk", buyer_name="Sarah Mitchell",
        professional_name="Kostas Papadopoulos",
        link=urls.review("Qw3eR5tY7uI9oP1aS2dF4gH6jK8lZ0xC"))),
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 800, "height": 700},
                                device_scale_factor=2)
        for name, message in MESSAGES:
            page.set_content(FRAME.format(subject=message.subject, to=message.to,
                                          html=message.html))
            page.wait_for_timeout(300)
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
            print(f"  {name}.png  —  {message.subject}")
        browser.close()
    print(f"\nwritten to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
