#!/usr/bin/env python3
"""Run the real offline demo and fail if any presenter artifact or evidence hook drifts.

Two stages, both against a real run of the C1 services over the in-memory local stack:

1. **rendered** - every static console page, checked through the ``data-*`` evidence hooks.
2. **served** - the presenter-controlled demo server, walked end to end over real HTTP.

The checks deliberately do not pattern-match on prose or CSS classes. Earlier revisions
asserted that the string ``"human review required"`` and the substring ``"MAS"`` appeared
somewhere in the page, which passes just as happily when a panel has silently lost its
contents, and breaks the moment a caption is reworded. Every figure asserted below is
instead **recomputed from the payload and compared to what the page published**, so a
hard-coded or stale number in the renderer is a failure rather than a passing constant.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

import compliance_demo
import compliance_demo_server as demo_server
import render_compliance_ui as renderer

# The panel each presenter step must actually render, keyed by the step key the
# control bar publishes. Ties the served walkthrough to the real console panels.
STEP_PANEL = {
    "answer": "grounded-answer",
    "checklist": "control-checklist",
    "testcases": "test-cases",
    "regulator_questions": "regulator-questions",
    "audit": "audit-trail",
}


class _HookCollector(HTMLParser):
    """Collect every element carrying at least one ``data-*`` attribute."""

    def __init__(self) -> None:
        super().__init__()
        self.elements: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {k: (v or "") for k, v in attrs if k.startswith("data-")}
        if data:
            self.elements.append(data)


def hooks(page: str) -> list[dict[str, str]]:
    parser = _HookCollector()
    parser.feed(page)
    return parser.elements


def one(page: str, key: str, value: str) -> dict[str, str]:
    """The single element whose ``key`` attribute equals ``value``."""
    found = [e for e in hooks(page) if e.get(key) == value]
    assert len(found) == 1, f"expected exactly one [{key}={value}], found {len(found)}"
    return found[0]


def values(page: str, key: str) -> list[str]:
    """Every value of ``key``, in document order."""
    return [e[key] for e in hooks(page) if key in e]


def check_rendered(payload: dict) -> None:
    """Stage 1: the static console pages, cross-checked against the payload."""
    artifacts = payload["artifacts"]
    pages = {name: render(payload) for name, render in renderer.RENDERERS.items()}
    assert set(pages) == set(renderer.RENDERERS)

    # Every page states the run context, and states it identically.
    for name, page in pages.items():
        head = one(page, "data-panel", "header")
        assert head["data-profile"] == payload["profile"], name
        assert head["data-region"] == payload["region"], name
        assert head["data-regulator-filter"] == payload["regulator_filter"], name
        # The maker-checker banner is present exactly when the header says it is.
        banner = [e for e in hooks(page) if "data-review-banner" in e]
        assert len(banner) == (1 if head["data-review-required"] == "true" else 0), name

    # -- answer ------------------------------------------------------------- #
    answer = artifacts["answer"]
    page = pages["answer.html"]
    panel = one(page, "data-panel", "grounded-answer")
    assert int(panel["data-answer-citations"]) == len(answer["citations"])
    assert int(panel["data-answer-caveats"]) == len(answer.get("caveats", []) or [])
    assert panel["data-answer-review"] == str(bool(answer["requires_human_review"])).lower()
    assert panel["data-answer-review"] == "true", "the answer must stay maker-checker gated"
    citations = one(page, "data-panel", "citations")
    assert int(citations["data-panel-citations"]) == len(answer["citations"])
    # The chips are the evidence: one rendered chip per citation, sources in order.
    assert values(page, "data-citation-source") == [
        str(c.get("source_id") or "") for c in answer["citations"]
    ]
    assert answer["citations"], "an uncited answer is never acceptable"

    # -- the three cited artifact lists -------------------------------------- #
    checklist = artifacts["checklist"]
    page = pages["checklist.html"]
    panel = one(page, "data-panel", "control-checklist")
    _assert_list_panel(panel, checklist["items"], "checklist")
    assert panel["data-checklist-review"] == str(bool(checklist["requires_human_review"])).lower()
    assert panel["data-checklist-review"] == "true"
    assert values(page, "data-control-id") == [i["control_id"] for i in checklist["items"]]

    testcases = artifacts["testcases"]
    page = pages["testcases.html"]
    panel = one(page, "data-panel", "test-cases")
    _assert_list_panel(panel, testcases, "testcases")
    assert values(page, "data-testcase-id") == [t["id"] for t in testcases]
    # Every test case must verify a control the checklist actually raised.
    control_ids = {i["control_id"] for i in checklist["items"]}
    assert set(values(page, "data-testcase-control")) <= control_ids

    questions = artifacts["regulator_questions"]
    page = pages["regulator-questions.html"]
    panel = one(page, "data-panel", "regulator-questions")
    _assert_list_panel(panel, questions, "regulator_questions")
    assert values(page, "data-question-regulator") == [q["regulator"] for q in questions]

    # -- audit trail --------------------------------------------------------- #
    trail = payload["audit_trail"]
    page = pages["audit.html"]
    panel = one(page, "data-panel", "audit-trail")
    assert int(panel["data-audit-count"]) == len(trail)
    assert values(page, "data-audit-action") == [e["action"] for e in trail]
    assert len(trail) >= 4
    assert all(event.get("redacted_prompt") for event in trail)

    # The pages are writable as the demo ships them.
    with TemporaryDirectory(prefix="rsk1-demo-") as directory:
        out = Path(directory)
        for name, page in pages.items():
            (out / name).write_text(page, encoding="utf-8")
        assert {p.name for p in out.glob("*.html")} == set(renderer.RENDERERS)


def _assert_list_panel(panel: dict[str, str], items: list[dict], label: str) -> None:
    """A list panel must publish its real item and citation counts, and cite everything."""
    assert items, f"{label} produced nothing"
    assert int(panel["data-item-count"]) == len(items), label
    cited = sum(len(i.get("citations") or []) for i in items)
    assert int(panel["data-panel-citations"]) == cited, label
    for item in items:
        assert item.get("citations"), f"uncited {label} item: {item}"


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - fixed localhost
        return response.read().decode("utf-8")


def _post(url: str) -> str:
    request = urllib.request.Request(url, data=b"", method="POST")  # noqa: S310
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return response.read().decode("utf-8")


def check_served(base: str) -> None:
    """Stage 2: walk the presenter server over HTTP, step by step."""
    steps = [s["key"] for s in demo_server.STEPS]
    seen = []
    for position, key in enumerate(steps):
        page = _get(f"{base}/")
        bar = one(page, "data-demo", "presenter-step")
        assert int(bar["data-step"]) == position, f"step {position}: bar says {bar['data-step']}"
        assert int(bar["data-step-count"]) == len(steps)
        assert bar["data-step-key"] == key
        assert bar["data-at-end"] == str(position == len(steps) - 1).lower()
        # The served page is the real console: its step's panel must be present,
        # and the header hooks must survive the control-bar injection.
        assert one(page, "data-panel", "header")
        assert one(page, "data-panel", STEP_PANEL[key]), f"step {key} rendered no panel"
        # The server's own state endpoint must agree with what it published.
        assert json.loads(_get(f"{base}/state"))["step"] == position
        seen.append(bar["data-step-key"])
        if position < len(steps) - 1:
            _post(f"{base}/advance")
    assert seen == steps, f"presenter walk visited {seen}"

    # Advancing past the last step is a no-op, not a crash or a wrap-around.
    _post(f"{base}/advance")
    bar = one(_get(f"{base}/"), "data-demo", "presenter-step")
    assert int(bar["data-step"]) == len(steps) - 1
    assert bar["data-at-end"] == "true"

    # Restart returns the presenter to the first step.
    _post(f"{base}/restart")
    bar = one(_get(f"{base}/"), "data-demo", "presenter-step")
    assert int(bar["data-step"]) == 0
    assert bar["data-step-key"] == steps[0]


def _serve() -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    """Boot the real demo server on an ephemeral port, exactly as ``make demo-server`` does."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), demo_server.Handler)
    server.session = demo_server.DemoSession()  # type: ignore[attr-defined]
    server.lock = threading.Lock()  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}"


def main() -> None:
    payload = compliance_demo.run()
    assert payload["profile"] == "local"
    check_rendered(payload)
    print("demo self-test: rendered pages PASS")

    server, thread, base = _serve()
    try:
        check_served(base)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    print("demo self-test: served presenter walkthrough PASS")

    print("demo self-test: PASS")


if __name__ == "__main__":
    main()
