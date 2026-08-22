"""Render the C1 audit-first console from the demo JSON into static HTML pages.

Server-side, dependency-free rendering of the four cited artifacts (Answer,
ControlChecklist, TestCase[], RegulatorQuestion[]) plus the WORM audit trail, faithful
to the Next.js console palette (ink / regblue, panels, regulator + page citation chips,
the maker-checker "human review required" banner). Used to produce screenshots for slides
with the synthetic, fictional corpus.

    PYTHONPATH=src:tests python scripts/render_compliance_ui.py compliance_demo.json out_dir/

Writes one page per artifact (answer / checklist / testcases / regulator-questions),
plus an audit-trail page.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

REG_LABEL = {
    "MAS": "MAS · Singapore",
    "HKMA": "HKMA · Hong Kong",
    "APRA": "APRA · Australia",
    "FSA": "FSA · Japan",
    "CROSS": "Cross-jurisdiction",
}
SEV_COLOR = {
    "low": ("#eef2f7", "#546b8b"),
    "medium": ("#fef3c7", "#92400e"),
    "high": ("#ffedd5", "#c2410c"),
    "critical": ("#fee2e2", "#b91c1c"),
}
DECISION_COLOR = {
    "allowed": ("#ecfdf5", "#059669"),
    "escalated": ("#fffbeb", "#d97706"),
    "blocked": ("#fee2e2", "#b91c1c"),
}

CSS = """
:root{--ink-50:#f5f7fa;--ink-100:#e6ebf2;--ink-200:#cdd7e4;--ink-300:#a6b6cc;
--ink-400:#7790ae;--ink-500:#546b8b;--ink-600:#3f5470;--ink-700:#33445b;--ink-800:#1f2a3a;
--regblue-50:#eef4ff;--regblue-100:#dbe7ff;--regblue-600:#2945d6;--regblue-700:#2237ad;
--ok:#059669;--ok-bg:#ecfdf5;--warn:#d97706;--warn-bg:#fffbeb;
--shadow:0 1px 2px rgba(11,16,26,.06),0 8px 24px rgba(11,16,26,.06);}
*{box-sizing:border-box}
body{margin:0;background:var(--ink-50);color:var(--ink-800);
font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;
font-size:14px;line-height:1.5;padding:24px 18px}
.wrap{max-width:900px;margin:0 auto}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
h1{font-size:18px;margin:0 0 2px}
.sub{color:var(--ink-500);font-size:13px;margin:0 0 16px}
.sub b{color:var(--ink-800)}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 16px}
.tab{font-size:12px;padding:4px 10px;border-radius:999px;border:1px solid var(--ink-200);background:#fff;color:var(--ink-500);text-decoration:none}
.tab.cur{background:var(--regblue-600);border-color:var(--regblue-600);color:#fff;font-weight:600}
.tab.done{background:var(--regblue-50);border-color:var(--regblue-100);color:var(--regblue-700)}
.panel{border:1px solid var(--ink-200);background:#fff;border-radius:10px;box-shadow:var(--shadow);margin-bottom:16px}
.panel>h2{border-bottom:1px solid var(--ink-100);padding:11px 16px;margin:0;font-size:13px;font-weight:600;color:var(--ink-800);
display:flex;justify-content:space-between;align-items:center}
.panel>.body{padding:16px}
.statuspill{font-size:11px;font-weight:600;padding:2px 9px;border-radius:999px}
.review{border:1px solid #fcd34d;background:var(--warn-bg);color:#92400e;border-radius:8px;padding:8px 12px;font-size:12px;font-weight:600;margin-bottom:14px}
.qbox{background:var(--ink-50);border:1px solid var(--ink-100);border-radius:8px;padding:10px 12px;font-size:13px;color:var(--ink-700);margin-bottom:12px}
.answer{font-size:14px;line-height:1.6}
.conf{display:flex;align-items:center;gap:10px;margin:12px 0}
.conf .bar{flex:1;max-width:240px;height:10px;border-radius:6px;background:var(--ink-100);border:1px solid var(--ink-200);overflow:hidden}
.conf .bar>span{display:block;height:100%;background:linear-gradient(90deg,#3a60f0,#2945d6)}
.conf .pct{font-variant-numeric:tabular-nums;font-size:12px;color:var(--ink-500)}
.muted{color:var(--ink-400);font-size:12px}
/* citation chips */
.cites{margin-top:10px;display:flex;flex-direction:column;gap:6px}
.cite{display:flex;gap:8px;align-items:baseline;border:1px solid var(--ink-200);background:var(--ink-50);border-radius:7px;padding:7px 10px}
.cite .reg{font-family:ui-monospace,Menlo,monospace;font-size:11px;font-weight:600;color:var(--regblue-700);background:var(--regblue-50);border:1px solid var(--regblue-100);border-radius:5px;padding:1px 6px;white-space:nowrap}
.cite .title{font-size:12px;color:var(--ink-700)}
.cite .pg{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--ink-500);margin-left:auto;white-space:nowrap}
.cite a{color:var(--regblue-600);text-decoration:none;font-size:11px}
.cite .snip{display:block;color:var(--ink-400);font-size:11px;margin-top:3px}
/* items (controls / testcases / questions) */
.item{padding:12px 0;border-bottom:1px solid var(--ink-100)}
.item:last-child{border-bottom:0}
.item .hd{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.sev{font-size:10px;font-weight:700;text-transform:uppercase;padding:2px 7px;border-radius:5px}
.id{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--ink-500)}
.item .name{font-weight:600;font-size:13px}
.item .d{color:var(--ink-500);font-size:12px;margin:4px 0 0}
.steps{margin:6px 0 0;padding-left:18px;font-size:12px;color:var(--ink-600)}
.code{font-family:ui-monospace,Menlo,monospace;font-size:11px;background:#0b101a;color:#cdd7e4;border-radius:6px;padding:6px 8px;margin-top:6px;overflow:auto}
.empty{color:var(--ok);font-size:13px;background:var(--ok-bg);border:1px solid #a7f3d0;border-radius:8px;padding:10px 12px}
.foot{font-size:11px;color:var(--ink-400);text-align:center;margin-top:18px}
/* audit table */
table.tl{width:100%;border-collapse:collapse;font-size:13px}
table.tl th,table.tl td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--ink-100)}
table.tl th{font-size:11px;text-transform:uppercase;color:var(--ink-400);font-weight:600}
"""

# (key, label, filename) for the tab bar.
TABS = [
    ("answer", "Answer", "answer.html"),
    ("checklist", "Checklist", "checklist.html"),
    ("testcases", "Test Cases", "testcases.html"),
    ("regulator_questions", "Regulator Questions", "regulator-questions.html"),
    ("audit", "Audit Trail", "audit.html"),
]


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def sev_pill(sev: str) -> str:
    bg, fg = SEV_COLOR.get(sev, SEV_COLOR["medium"])
    return f'<span class="sev" style="background:{bg};color:{fg}">{esc(sev)}</span>'


def cite(c: dict) -> str:
    reg = REG_LABEL.get(c.get("regulator", ""), c.get("regulator", ""))
    ver = c.get("version")
    ver_s = f" {esc(ver)}" if ver and ver != "unknown" else ""
    page = f"p.{c['page']}" if c.get("page") is not None else ""
    url = c.get("url", "")
    snip = c.get("snippet") or ""
    snip = snip if len(snip) <= 200 else snip[:197] + "…"
    return (
        '<div class="cite"'
        f' data-citation-source="{esc(c.get("source_id"))}"'
        f' data-citation-regulator="{esc(c.get("regulator"))}"'
        f' data-citation-page="{esc(c["page"]) if c.get("page") is not None else ""}">'
        f'<span class="reg">{esc(reg)}{ver_s}</span>'
        f'<span class="title">{esc(c.get("title"))}'
        f'<span class="snip">{esc(snip)}</span></span>'
        f'<span class="pg">{esc(page)} · <a href="{esc(url)}">source</a></span>'
        "</div>"
    )


def cites_block(citations: list[dict]) -> str:
    """The citation chips, carrying a machine-readable count of what was cited."""
    if not citations:
        return '<p class="muted" data-citation-count="0">(no citations)</p>'
    return (
        f'<div class="cites" data-citation-count="{len(citations)}">'
        + "".join(cite(c) for c in citations)
        + "</div>"
    )


def tabs_bar(cur: str) -> str:
    order = [t[0] for t in TABS]
    cur_i = order.index(cur) if cur in order else 0
    out = []
    for i, (key, label, fname) in enumerate(TABS):
        cls = "tab"
        if key == cur:
            cls += " cur"
        elif i < cur_i:
            cls += " done"
        out.append(f'<a class="{cls}" href="{esc(fname)}">{esc(label)}</a>')
    return '<div class="tabs">' + "".join(out) + "</div>"


def review_banner() -> str:
    return (
        '<div class="review" data-review-banner="1">HUMAN REVIEW REQUIRED — maker-checker gate (P-06). '
        "The assistant proposes; a qualified checker disposes before any reliance.</div>"
    )


def header(data: dict, cur: str, *, review: bool) -> str:
    return (
        "<header data-panel='header'"
        f" data-view='{esc(cur)}'"
        f" data-profile='{esc(data['profile'])}'"
        f" data-region='{esc(data['region'])}'"
        f" data-regulator-filter='{esc(data['regulator_filter'])}'"
        f" data-review-required='{str(bool(review)).lower()}'>"
        f"<h1>C1 Compliance Assistant — {esc(REG_LABEL.get(data['regulator_filter'], data['regulator_filter']))}</h1>"
        f"<p class='sub'>Use case <b>{esc(data['use_case'])}</b> · profile "
        f"<b class='mono'>{esc(data['profile'])}</b> · region <b>{esc(data['region'])}</b></p>"
        + tabs_bar(cur)
        + (review_banner() if review else "")
        + "</header>"
    )


def page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(title)}</title>"
        f"<style>{CSS}</style></head><body><div class='wrap'>{body}</div></body></html>"
    )


def render_answer(data: dict) -> str:
    a = data["artifacts"]["answer"]
    conf = round(float(a.get("confidence", 0)) * 100)
    caveats = "".join(f"<li>{esc(c)}</li>" for c in a.get("caveats", []))
    body = (
        header(data, "answer", review=bool(a.get("requires_human_review")))
        + f"<div class='qbox'><b>Q:</b> {esc(a.get('question'))}</div>"
        + '<section class="panel" data-panel="grounded-answer"'
        + f' data-answer-confidence="{conf}"'
        + f' data-answer-citations="{len(a.get("citations", []))}"'
        + f' data-answer-review="{str(bool(a.get("requires_human_review"))).lower()}"'
        + f' data-answer-caveats="{len(a.get("caveats", []))}"><h2>Grounded answer'
        + f'<span class="statuspill" style="background:#dbeafe;color:#1e40af">confidence {conf}%</span></h2>'
        + f"<div class='body'><div class='answer'>{esc(a.get('answer'))}</div>"
        + "<div class='conf'><span class='pct'>confidence</span><div class='bar'>"
        + f"<span style='width:{conf}%'></span></div><span class='pct'>{conf}%</span></div>"
        + (f"<ul class='steps'>{caveats}</ul>" if caveats else "")
        + "</div></section>"
        + '<section class="panel" data-panel="citations"'
        + f' data-panel-citations="{len(a.get("citations", []))}">'
        + "<h2>Citations — page-level provenance</h2>"
        + f"<div class='body'>{cites_block(a.get('citations', []))}</div></section>"
        + "<p class='foot'>Audit-first compliance console · synthetic fictional corpus · "
        + "deterministic grounded RAG (local profile)</p>"
    )
    return page("C1 — Answer", body)


def _render_items(
    data: dict,
    cur: str,
    title: str,
    items: list[dict],
    render_item,
    *,
    panel: str,
    extra_attrs: str = "",
) -> str:
    """One artifact list page.

    ``panel`` is the stable ``data-panel`` slug the anti-rot checks and the browser
    walkthrough address the section by; it never changes when the styling does.
    """
    rows = "".join(render_item(it) for it in items)
    cited = sum(len(it.get("citations", []) or []) for it in items)
    body = (
        header(data, cur, review=True)
        + f"<div class='qbox'><b>Use case:</b> {esc(data['use_case'])}</div>"
        + f'<section class="panel" data-panel="{esc(panel)}" data-item-count="{len(items)}"'
        + f' data-panel-citations="{cited}"{extra_attrs}>'
        + f'<h2>{esc(title)} <span class="muted">{len(items)}</span></h2>'
        + f"<div class='body'>{rows or '<p class=muted>(none produced)</p>'}</div></section>"
        + "<p class='foot'>Audit-first compliance console · synthetic fictional corpus · "
        + "every control / test / answer cited to a regulator source and page</p>"
    )
    return page(f"C1 — {title}", body)


def render_checklist(data: dict) -> str:
    ck = data["artifacts"]["checklist"]

    def item(it: dict) -> str:
        return (
            f'<div class="item" data-control-id="{esc(it.get("control_id"))}"'
            f' data-control-severity="{esc(it.get("severity", "medium"))}"'
            f' data-item-citations="{len(it.get("citations", []) or [])}"><div class="hd">'
            f"{sev_pill(it.get('severity', 'medium'))}"
            f'<span class="id">{esc(it.get("control_id"))}</span>'
            f'<span class="name">{esc(it.get("control"))}</span></div>'
            f'<div class="d">{esc(it.get("rationale"))}</div>'
            f"{cites_block(it.get('citations', []))}</div>"
        )

    return _render_items(
        data,
        "checklist",
        "Control checklist",
        ck.get("items", []),
        item,
        panel="control-checklist",
        extra_attrs=(
            f' data-checklist-review="{str(bool(ck.get("requires_human_review"))).lower()}"'
        ),
    )


def render_testcases(data: dict) -> str:
    tcs = data["artifacts"]["testcases"]

    def item(tc: dict) -> str:
        steps = "".join(f"<li>{esc(s)}</li>" for s in tc.get("steps", []))
        check = (
            f"<div class='code'>{esc(tc['automated_check'])}</div>"
            if tc.get("automated_check")
            else ""
        )
        return (
            f'<div class="item" data-testcase-id="{esc(tc.get("id"))}"'
            f' data-testcase-control="{esc(tc.get("control_id"))}"'
            f' data-item-citations="{len(tc.get("citations", []) or [])}"><div class="hd">'
            f'<span class="id">{esc(tc.get("id"))}</span>'
            f'<span class="name">{esc(tc.get("title"))}</span>'
            f'<span class="muted">verifies {esc(tc.get("control_id"))}</span></div>'
            f"<ol class='steps'>{steps}</ol>"
            f"<div class='d'>expected: {esc(tc.get('expected_result'))}</div>"
            f"{check}{cites_block(tc.get('citations', []))}</div>"
        )

    return _render_items(
        data, "testcases", "Verification test cases", tcs, item, panel="test-cases"
    )


def render_regulator_questions(data: dict) -> str:
    rqs = data["artifacts"]["regulator_questions"]

    def item(q: dict) -> str:
        return (
            f'<div class="item" data-question-regulator="{esc(q.get("regulator"))}"'
            f' data-item-citations="{len(q.get("citations", []) or [])}"><div class="hd">'
            f'<span class="reg" style="font-size:11px">{esc(REG_LABEL.get(q.get("regulator"), q.get("regulator")))}</span>'
            f'<span class="name">{esc(q.get("question"))}</span></div>'
            f"<div class='d'>why asked: {esc(q.get('why_asked'))}</div>"
            f"<div class='d'>model answer: {esc(q.get('model_answer'))}</div>"
            f"{cites_block(q.get('citations', []))}</div>"
        )

    return _render_items(
        data,
        "regulator_questions",
        "Anticipated regulator / CRO questions",
        rqs,
        item,
        panel="regulator-questions",
    )


def render_audit(data: dict) -> str:
    rows = []
    for ev in data.get("audit_trail", []):
        dec = ev.get("decision", "")
        bg, fg = DECISION_COLOR.get(dec, ("#eef2f7", "#546b8b"))
        meta = ev.get("metadata", {}) or {}
        rows.append(
            f'<tr data-audit-action="{esc(ev.get("action"))}"'
            f' data-audit-decision="{esc(dec)}"'
            f' data-audit-citations="{len(ev.get("citations", []) or [])}">'
            f"<td class='mono'>{esc(ev.get('action'))}</td>"
            f"<td><span class='statuspill' style='background:{bg};color:{fg}'>{esc(dec)}</span></td>"
            f"<td>{esc(ev.get('actor'))}</td>"
            f"<td class='mono'>{esc(meta.get('confidence', '—'))}</td>"
            f"<td class='mono'>{esc(meta.get('n_citations', len(ev.get('citations', []))))}</td>"
            f"<td>{esc(meta.get('requires_human_review', '—'))}</td>"
            "</tr>"
        )
    body = (
        header(data, "audit", review=False)
        + '<section class="panel" data-panel="audit-trail"'
        + f' data-audit-count="{len(data.get("audit_trail", []))}">'
        + "<h2>WORM audit trail — every interaction, PII-redacted</h2>"
        + "<div class='body'><table class='tl'>"
        + "<tr><th>Action</th><th>Decision</th><th>Actor</th><th>Confidence</th>"
        + "<th>Citations</th><th>Human review</th></tr>"
        + "".join(rows)
        + "</table>"
        + "<p class='muted' style='margin-top:10px'>Prompts and responses are redacted at the "
        + "boundary (P-04) before they are ever written to the append-only audit store.</p>"
        + "</div></section>"
        + "<p class='foot'>Audit-first compliance console · synthetic fictional corpus</p>"
    )
    return page("C1 — Audit trail", body)


RENDERERS = {
    "answer.html": render_answer,
    "checklist.html": render_checklist,
    "testcases.html": render_testcases,
    "regulator-questions.html": render_regulator_questions,
    "audit.html": render_audit,
}


def main(json_path: str, out_dir: str) -> None:
    data = json.loads(Path(json_path).read_text())
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for fname, fn in RENDERERS.items():
        p = out / fname
        p.write_text(fn(data))
        print("wrote", p)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
