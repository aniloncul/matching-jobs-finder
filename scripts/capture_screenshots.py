"""Capture marketing screenshots from a running local JobScout.

Usage: python3 scripts/capture_screenshots.py [base_url] [out_dir]
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "site/assets")
OUT.mkdir(parents=True, exist_ok=True)


def post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def main() -> None:
    # Kick off the deterministic fixture run so the feed and graph are alive.
    try:
        post("/api/runs", {"sources": ["linkedin", "indeed"], "cached": True})
    except Exception as exc:  # noqa: BLE001 - a screenshot is still useful
        print(f"run not started: {exc}")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as play:
        browser = play.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            device_scale_factor=1,
            color_scheme="dark",
        )
        page = context.new_page()
        page.goto(BASE + "/", wait_until="load")
        page.wait_for_timeout(9000)
        page.screenshot(path=str(OUT / "control-room.png"))
        print("wrote", OUT / "control-room.png")

        page.goto(BASE + "/#job-offers", wait_until="load")
        page.wait_for_timeout(2500)
        page.screenshot(path=str(OUT / "job-offers.png"))
        print("wrote", OUT / "job-offers.png")

        page.goto(BASE + "/#setup", wait_until="load")
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "setup.png"))
        print("wrote", OUT / "setup.png")

        browser.close()

    # Let the fixture run finish before the caller shuts the server down.
    time.sleep(1)


if __name__ == "__main__":
    main()
