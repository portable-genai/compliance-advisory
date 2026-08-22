"""Live, presenter-controlled demo server for the C1 four-artifact flow (stdlib only).

Holds a real set of C1 services over the in-memory ``local`` stack and reveals the four
cited artifacts one step per click — Answer -> Control checklist -> Test cases ->
Regulator questions -> WORM audit trail — rendering the audit-first console at each step.
No Google Cloud, no API key, no extra dependencies (the rendering is reused from
``render_compliance_ui``).

    PYTHONPATH=src:tests python scripts/compliance_demo_server.py [--port 8088]

Then open http://localhost:8088 and click "Next ▶", or drive it with
``scripts/compliance_demo_playwright.py`` for a presenter-controlled walkthrough.

The demo port (8088) is deliberately distinct from the FastAPI API port (8080) and the
Next.js console port (3000) so all three can run side by side.
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import compliance_demo as demo  # sibling script: the synthetic scenario + real run
import render_compliance_ui as r  # sibling script: reuse the exact audit-first rendering

# The scripted reveal. Each "Next" exposes one more artifact the run already computed.
STEPS = [
    {
        "key": "answer",
        "label": "Answer — grounded, page-cited reply to the compliance question",
        "next": "Reveal the control checklist this use case requires",
        "render": "answer.html",
    },
    {
        "key": "checklist",
        "label": "Control checklist — controls with cited rationale",
        "next": "Reveal the test cases that verify each control",
        "render": "checklist.html",
    },
    {
        "key": "testcases",
        "label": "Test cases — automated checks per control",
        "next": "Reveal the regulator / CRO questions, answered and cited",
        "render": "testcases.html",
    },
    {
        "key": "regulator_questions",
        "label": "Regulator questions — anticipated and answered",
        "next": "Show the WORM audit trail recorded for this run",
        "render": "regulator-questions.html",
    },
    {
        "key": "audit",
        "label": "WORM audit trail — every interaction, PII-redacted",
        "next": None,
        "render": "audit.html",
    },
]

_CONTROL_CSS = """
.democtl{position:sticky;top:0;z-index:10;display:flex;align-items:center;gap:12px;
  margin:-24px -18px 16px;padding:12px 18px;background:#0b101a;color:#fff}
.democtl .lbl{font-size:13px}.democtl .lbl b{color:#90b2ff}
.democtl .spacer{flex:1}
.democtl form{margin:0}
.democtl button{font:inherit;font-size:13px;font-weight:600;border:0;border-radius:7px;
  padding:7px 14px;cursor:pointer}
.democtl .next{background:#3a60f0;color:#fff}.democtl .next:disabled{opacity:.4;cursor:default}
.democtl .restart{background:transparent;color:#a6b6cc;border:1px solid #33445b}
"""


class DemoSession:
    """Computes the real C1 run once, then reveals one artifact per click."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        # demo.run() exercises the real services over the in-memory local stack.
        self.data = demo.run()
        self.idx = 0

    @property
    def at_end(self) -> bool:
        return self.idx >= len(STEPS) - 1

    def advance(self) -> None:
        if not self.at_end:
            self.idx += 1

    # -- rendering --------------------------------------------------------- #
    def render(self) -> str:
        step = STEPS[self.idx]
        fn = r.RENDERERS[step["render"]]
        return self._inject_controls(fn(self.data))

    def _inject_controls(self, page_html: str) -> str:
        step = STEPS[self.idx]
        nxt = step["next"]
        next_btn = (
            f"<form method='post' action='/advance'><button class='next' type='submit'>"
            f"Next ▶ &nbsp;·&nbsp; {r.esc(nxt)}</button></form>"
            if nxt
            else "<button class='next' disabled>Demo complete ✓</button>"
        )
        # The bar carries stable, styling-independent evidence hooks so the served-path
        # self-test and the headless-browser walkthrough can address the presenter control
        # without pattern-matching on prose or CSS classes.
        bar = (
            "<div class='democtl' data-demo='presenter-step'"
            f" data-step='{self.idx}' data-step-count='{len(STEPS)}'"
            f" data-step-key='{r.esc(step['key'])}'"
            f" data-at-end='{str(self.at_end).lower()}'>"
            f"<span class='lbl'>Step {self.idx + 1}/{len(STEPS)} — <b>{r.esc(step['label'])}</b></span>"
            f"<span class='spacer'></span>{next_btn}"
            "<form method='post' action='/restart'><button class='restart' type='submit'>Restart</button></form>"
            "</div>"
        )
        page_html = page_html.replace("</style>", _CONTROL_CSS + "</style>", 1)
        return page_html.replace("<div class='wrap'>", "<div class='wrap'>" + bar, 1)


class Handler(BaseHTTPRequestHandler):
    session: DemoSession  # set on the server instance below

    def _send(self, body: str, status: int = 200, ctype: str = "text/html; charset=utf-8") -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _redirect(self, to: str = "/") -> None:
        self.send_response(303)
        self.send_header("Location", to)
        self.end_headers()

    @property
    def _sess(self) -> DemoSession:
        return self.server.session  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        with self.server.lock:  # type: ignore[attr-defined]
            if path == "/":
                self._send(self._sess.render())
            elif path == "/state":
                self._send(json.dumps({"step": self._sess.idx}), ctype="application/json")
            elif path == "/restart":
                # Allowed over GET so the walkthrough can reset with a plain navigation.
                self._sess.reset()
                self._redirect("/")
            else:
                self._send("<h1>404</h1>", 404)

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        with self.server.lock:  # type: ignore[attr-defined]
            if path == "/advance":
                self._sess.advance()
            elif path == "/restart":
                self._sess.reset()
        self._redirect("/")

    def log_message(self, *args: object) -> None:  # quiet console
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Live C1 compliance demo server")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.session = DemoSession()  # type: ignore[attr-defined]
    server.lock = threading.Lock()  # type: ignore[attr-defined]
    print(f"C1 demo server on http://{args.host}:{args.port}  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
