"""Presenter-controlled Playwright walkthrough of the live C1 compliance demo.

Drives a headed browser through the four-artifact flow served by
``scripts/compliance_demo_server.py``. It is **paced by the presenter**: before each step
it prints what is about to happen and waits for you to press Enter, then performs the
action (click "Next ▶") and highlights the panel to look at. You stay in control.

Usage (two terminals)::

    # terminal 1 — the live demo server
    PYTHONPATH=src:tests python scripts/compliance_demo_server.py

    # terminal 2 — the guided walkthrough (a real Chrome window opens)
    pip install playwright && playwright install chromium     # one-time
    python scripts/compliance_demo_playwright.py

Point it at the real Next.js console instead with ``DEMO_URL=http://localhost:3000``
(then it just opens the console for the presenter; the Next/Restart buttons are specific
to the demo server, so against the live console use it as a guided narration overlay).

Environment overrides:
    DEMO_URL    server base URL (default http://127.0.0.1:8088)
    HEADLESS=1  run headless (used for the self-test; no window)
    DEMO_AUTO=1 don't wait for Enter — advance automatically (self-test / recording)
    SLOWMO_MS   per-action slow-motion in ms (default 250 headed, 0 headless)
    CHROME_PATH explicit Chromium/Chrome binary (else Playwright's own)
"""

from __future__ import annotations

import contextlib
import os
import sys
import time

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DEMO_URL", "http://127.0.0.1:8088")
HEADLESS = os.environ.get("HEADLESS") == "1"
AUTO = os.environ.get("DEMO_AUTO") == "1"
SLOWMO = int(os.environ.get("SLOWMO_MS", "0" if HEADLESS else "250"))
CHROME_PATH = os.environ.get("CHROME_PATH") or None

# (narration shown in the terminal, whether this step clicks "Next", panel to spotlight)
STEPS = [
    (
        "Answer. The analyst asks a MAS cloud-outsourcing question. The assistant returns "
        "a grounded answer with a confidence meter, page-level citations to the MAS TRM "
        "Guidelines, and a HUMAN REVIEW REQUIRED banner (maker-checker, P-06).",
        False,
        ".panel",
    ),
    (
        "Control checklist. From the same use case the assistant derives the controls it "
        "requires — each with a severity, cited rationale, and regulator + page citation "
        "chips. Always gated for a second reviewer.",
        True,
        ".panel",
    ),
    (
        "Test cases. Each control gets an automated verification test — steps, expected "
        "result, and an executable check — again cited to the source page.",
        True,
        ".item",
    ),
    (
        "Regulator questions. The questions a regulator / CRO will ask, each with why it "
        "is asked and a cited model answer — so the team can rehearse the exam.",
        True,
        ".item",
    ),
    (
        "WORM audit trail. Every interaction in the run — ask / checklist / testcases / "
        "regulator-questions — is written PII-redacted to the append-only audit store, "
        "with its decision, confidence and citation count.",
        True,
        ".tl",
    ),
]


def _pause(prompt: str) -> None:
    if AUTO:
        time.sleep(1.2)
        return
    try:
        input(prompt)
    except EOFError:  # non-interactive stdin
        time.sleep(1.0)


def _spotlight(page, selector: str | None) -> None:
    if not selector:
        return
    with contextlib.suppress(Exception):  # cosmetic only
        page.eval_on_selector_all(
            selector,
            "els => els.forEach((e,i)=>{ if(i<6){ e.style.transition='box-shadow .3s';"
            " e.style.boxShadow='0 0 0 3px #3a60f0'; setTimeout(()=>e.style.boxShadow='',1600);} })",
        )


def _reachable() -> bool:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(BASE + "/state", timeout=2):
            return True
    except (urllib.error.URLError, OSError):
        return False


def main() -> int:
    if not _reachable():
        print(f"Cannot reach the demo server at {BASE}.")
        print("Start it first:  PYTHONPATH=src:tests python scripts/compliance_demo_server.py")
        return 2

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=SLOWMO, executable_path=CHROME_PATH)
        page = browser.new_context(viewport={"width": 1100, "height": 900}).new_page()

        print("\n=== C1 compliance live demo — press Enter to advance each step ===\n")
        page.goto(BASE + "/restart", wait_until="load")  # always start clean
        page.goto(BASE + "/", wait_until="load")

        for i, (say, click, spotlight) in enumerate(STEPS):
            print(f"[{i + 1}/{len(STEPS)}] {say}")
            _pause("        ⏎  press Enter to run this step… ")
            if click:
                btn = page.locator(".democtl button.next")
                if btn.count() and btn.is_enabled():
                    btn.click()
                    page.wait_for_load_state("load")
            page.wait_for_timeout(200)
            _spotlight(page, spotlight)
            page.wait_for_timeout(700)
            print()

        print("Demo complete. The browser stays open for questions.")
        _pause("        ⏎  press Enter to close the browser… ")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
