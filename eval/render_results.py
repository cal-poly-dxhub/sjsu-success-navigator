#!/usr/bin/env python3
"""Render an eval transcript against the ground truth as one self-contained HTML page.

Presentation only, no scoring: each question gets the golden expectation on the left
(straight out of ground-truth.yaml) and the system's real answer on the right (straight out
of the transcript), so a human - or Claude reading the JSON - decides accuracy. The only
mechanical annotations are the behavior badge (which response shape actually fired), bolding
of expected source URLs the response actually cited, and, when a judgments file exists,
each pair's recorded verdict chip.

Judgments file (optional): eval/judgments/<transcript stem>.yaml, either
    route-tutor: pass
or
    route-tutor: {verdict: fail, note: "gave the 5910 number"}

Usage:
    python3 render_results.py                       # newest transcript in results/
    python3 render_results.py --transcript path.json [--judgments path.yaml] [--out page.html]
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

import yaml

BADGE_COLORS = {
    "cards": "#1a7f37",
    "prose-only": "#9a6700",
    "safety": "#8250df",
    "error": "#cf222e",
}
VERDICT_COLORS = {"pass": "#1a7f37", "fail": "#cf222e", "unsure": "#9a6700"}

CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { font-family: system-ui, -apple-system, sans-serif; margin: 0; background: #f6f8fa;
       color: #1f2328; line-height: 1.45; }
.wrap { max-width: 1180px; margin: 0 auto; padding: 24px 20px 80px; }
h1 { font-size: 1.35rem; margin: 0 0 4px; }
.meta { color: #59636e; font-size: 0.85rem; margin-bottom: 18px; }
.strip { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 22px; }
.stat { background: #fff; border: 1px solid #d1d9e0; border-radius: 8px; padding: 8px 14px;
        font-size: 0.85rem; }
.stat b { display: block; font-size: 1.15rem; }
.toc { font-size: 0.8rem; margin-bottom: 26px; column-width: 260px; column-gap: 24px; }
.toc a { color: #0969da; text-decoration: none; display: block; padding: 1px 0; }
.pair { background: #fff; border: 1px solid #d1d9e0; border-radius: 10px; margin-bottom: 18px;
        padding: 14px 18px; }
.pair-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.pair-head code { font-size: 0.8rem; color: #59636e; }
.q { font-size: 1.05rem; font-weight: 600; margin: 6px 0 12px; }
.badge, .chip { display: inline-block; border-radius: 999px; padding: 1px 10px; color: #fff;
        font-size: 0.72rem; font-weight: 600; letter-spacing: 0.02em; }
.lat { color: #59636e; font-size: 0.78rem; }
.cols { display: grid; grid-template-columns: minmax(0,5fr) minmax(0,7fr); gap: 18px; }
@media (max-width: 900px) { .cols { grid-template-columns: 1fr; } }
.col h3 { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em;
          color: #59636e; margin: 0 0 8px; }
.kv { font-size: 0.85rem; margin: 0 0 6px; }
.kv b { color: #59636e; font-weight: 600; }
.facts { margin: 4px 0 8px 18px; padding: 0; font-size: 0.85rem; }
.srcs { margin: 4px 0 8px 18px; padding: 0; font-size: 0.8rem; word-break: break-all; }
.srcs .cited { font-weight: 700; }
.srcs .cited::after { content: "  <- cited"; color: #1a7f37; font-weight: 600; }
.note { font-size: 0.78rem; color: #59636e; border-left: 3px solid #d1d9e0;
        padding-left: 8px; margin-top: 8px; }
.prose { white-space: pre-wrap; font-size: 0.9rem; background: #f6f8fa; border-radius: 8px;
         padding: 10px 12px; }
.card { border: 1px solid #d1d9e0; border-left: 4px solid #1a7f37; border-radius: 8px;
        padding: 8px 12px; margin-top: 8px; font-size: 0.85rem; }
.card .t { font-weight: 700; }
.card a { color: #0969da; font-size: 0.78rem; word-break: break-all; }
.card .fu { color: #59636e; font-size: 0.78rem; margin-top: 4px; }
.safety { border: 1px solid #8250df; border-left: 6px solid #8250df; border-radius: 8px;
          padding: 10px 12px; margin-top: 8px; font-size: 0.88rem; background: #fbf7ff; }
.err { border: 1px solid #cf222e; border-radius: 8px; padding: 10px 12px; margin-top: 8px;
       font-size: 0.82rem; background: #fff5f5; white-space: pre-wrap; word-break: break-all; }
.judge-note { font-size: 0.8rem; color: #59636e; font-style: italic; }
"""


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""))


def badge(kind: str) -> str:
    color = BADGE_COLORS.get(kind, "#59636e")
    return f'<span class="badge" style="background:{color}">{esc(kind.upper())}</span>'


def cited_urls(response: dict | None) -> set:
    urls = set()
    for batch in (response or {}).get("statementBatches") or []:
        for card in batch.get("cards") or []:
            if card.get("sourceUrl"):
                urls.add(card["sourceUrl"])
    return urls


def golden_col(pair: dict, cited: set) -> str:
    parts = ['<div class="col"><h3>Golden</h3>']
    parts.append(f'<p class="kv"><b>behavior:</b> {esc(pair["expected_behavior"])}'
                 + (" &middot; sensitive" if pair.get("sensitive") else "")
                 + (" &middot; volatile" if pair.get("volatile") else "") + "</p>")
    parts.append(f'<p class="kv"><b>office:</b> {esc(pair["expected_office"])}</p>')
    facts = pair.get("expected_facts") or []
    if facts:
        parts.append('<ul class="facts">' + "".join(f"<li>{esc(f)}</li>" for f in facts) + "</ul>")
    urls = pair.get("expected_source_urls") or []
    if urls:
        items = "".join(
            f'<li class="{"cited" if u in cited else ""}">{esc(u)}</li>' for u in urls
        )
        parts.append(f'<ul class="srcs">{items}</ul>')
    parts.append(f'<p class="kv"><b>provenance:</b> {esc(pair.get("provenance"))}<br>'
                 f'<b>verified:</b> {esc(pair.get("verified"))}</p>')
    if pair.get("notes"):
        parts.append(f'<div class="note">{esc(pair["notes"])}</div>')
    parts.append("</div>")
    return "".join(parts)


def actual_col(result: dict) -> str:
    parts = ['<div class="col"><h3>System</h3>']
    response = result.get("response")
    if result.get("error") or response is None:
        parts.append(f'<div class="err">HTTP {esc(result.get("status"))}\n'
                     f'{esc(result.get("error"))}</div></div>')
        return "".join(parts)
    if response.get("conversationalText"):
        parts.append(f'<div class="prose">{esc(response["conversationalText"])}</div>')
    handoff = response.get("safetyHandoff")
    if handoff:
        contacts = "".join(
            f'<div><b>{esc(c.get("label"))}</b>: {esc(c.get("detail"))}</div>'
            for c in handoff.get("contacts") or []
        )
        parts.append(f'<div class="safety"><b>{esc(handoff.get("headline"))}</b>'
                     f'<div>{esc(handoff.get("body"))}</div>{contacts}</div>')
    for batch in response.get("statementBatches") or []:
        for card in batch.get("cards") or []:
            followups = "".join(
                f'<div class="fu">Tell me more &rarr; &ldquo;{esc(a.get("prompt"))}&rdquo;</div>'
                for a in card.get("actions") or [] if a.get("type") == "followup"
            )
            url = card.get("sourceUrl") or ""
            link = f'<a href="{esc(url)}">{esc(url)}</a>' if url else ""
            parts.append(f'<div class="card"><div class="t">{esc(card.get("title"))}</div>'
                         f'<div>{esc(card.get("body"))}</div>{link}{followups}</div>')
    parts.append("</div>")
    return "".join(parts)


def verdict_chip(judgment) -> str:
    if judgment is None:
        return ""
    if isinstance(judgment, str):
        verdict, note = judgment, None
    else:
        verdict, note = judgment.get("verdict"), judgment.get("note")
    color = VERDICT_COLORS.get(verdict, "#59636e")
    chip = f'<span class="chip" style="background:{color}">{esc(verdict).upper()}</span>'
    if note:
        chip += f' <span class="judge-note">{esc(note)}</span>'
    return chip


def build_html(transcript: dict, pairs_by_id: dict, judgments: dict) -> str:
    run = transcript["run"]
    results = transcript["results"]
    fired: dict = {}
    for r in results:
        fired[r["behavior_fired"]] = fired.get(r["behavior_fired"], 0) + 1

    stats = [f"<div class='stat'><b>{len(results)}</b>questions</div>"]
    for kind in ("cards", "prose-only", "safety", "error"):
        if kind in fired:
            stats.append(f"<div class='stat'><b>{fired[kind]}</b>{esc(kind)}</div>")
    if run.get("median_latency_s") is not None:
        stats.append(f"<div class='stat'><b>{run['median_latency_s']}s</b>median latency</div>")
    if judgments:
        counts: dict = {}
        for j in judgments.values():
            v = j if isinstance(j, str) else j.get("verdict")
            counts[v] = counts.get(v, 0) + 1
        for v, n in sorted(counts.items()):
            stats.append(f"<div class='stat'><b>{n}</b>judged {esc(v)}</div>")

    toc, blocks = [], []
    for result in results:
        pair = pairs_by_id.get(result["id"])
        if pair is None:
            continue
        cited = cited_urls(result.get("response"))
        toc.append(f'<a href="#{esc(result["id"])}">{badge(result["behavior_fired"])} '
                   f'{esc(result["id"])}</a>')
        lat = f'<span class="lat">{result["latency_s"]}s</span>' if result.get("latency_s") else ""
        blocks.append(
            f'<div class="pair" id="{esc(result["id"])}">'
            f'<div class="pair-head"><code>{esc(result["id"])}</code>'
            f'<span class="lat">{esc(pair["type"])} &middot; {esc(pair["category"])}</span>'
            f'{badge(result["behavior_fired"])}{lat}{verdict_chip(judgments.get(result["id"]))}</div>'
            f'<div class="q">&ldquo;{esc(result["question"])}&rdquo;</div>'
            f'<div class="cols">{golden_col(pair, cited)}{actual_col(result)}</div></div>'
        )

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Eval {esc(run['timestamp_utc'])}</title><style>{CSS}</style></head><body>"
        f"<div class='wrap'><h1>Ground-truth eval run {esc(run['timestamp_utc'])}</h1>"
        f"<div class='meta'>{esc(run['api_url'])} &middot; commit {esc(run.get('git_commit'))}"
        f" &middot; {esc(run.get('duration_s'))}s total</div>"
        f"<div class='strip'>{''.join(stats)}</div>"
        f"<div class='toc'>{''.join(toc)}</div>"
        f"{''.join(blocks)}</div></body></html>"
    )


def render(transcript_path: Path, ground_truth_path: Path,
           judgments_path: Path | None, out_path: Path) -> Path:
    transcript = json.loads(Path(transcript_path).read_text())
    doc = yaml.safe_load(Path(ground_truth_path).read_text())
    pairs_by_id = {p["id"]: p for p in doc["pairs"]}

    if judgments_path is None:
        candidate = Path(transcript_path).parent.parent / "judgments" / (
            Path(transcript_path).stem + ".yaml")
        judgments_path = candidate if candidate.exists() else None
    judgments = yaml.safe_load(Path(judgments_path).read_text()) if judgments_path else {}

    out_path.write_text(build_html(transcript, pairs_by_id, judgments or {}))
    return out_path


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--transcript", default=None,
                        help="default: newest eval-*.json under results/")
    parser.add_argument("--ground-truth", default=str(here / "ground-truth.yaml"))
    parser.add_argument("--judgments", default=None)
    parser.add_argument("--out", default=None, help="default: transcript path with .html")
    args = parser.parse_args()

    if args.transcript:
        transcript_path = Path(args.transcript)
    else:
        candidates = sorted((here / "results").glob("eval-*.json"))
        if not candidates:
            raise SystemExit("no transcripts in results/; run run_eval.py first")
        transcript_path = candidates[-1]

    out_path = Path(args.out) if args.out else transcript_path.with_suffix(".html")
    judgments_path = Path(args.judgments) if args.judgments else None
    render(transcript_path, Path(args.ground_truth), judgments_path, out_path)
    print(out_path)


if __name__ == "__main__":
    main()
