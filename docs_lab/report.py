# SPDX-License-Identifier: Apache-2.0
"""Render real-document retrieval results, separating facts from interpretation."""

import argparse
from html import escape
import json
from pathlib import Path

from observation_lab.report import table
from .source import ROOT


def e(value):
    return escape(str(value), quote=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("results/docs/v1"))
    args = parser.parse_args()
    folder = args.directory
    experiment = json.loads((folder / "experiment.json").read_text())
    review = json.loads((folder / "review.json").read_text())
    audit = json.loads((folder / "audit.json").read_text())
    diagnostics = json.loads((folder / "diagnostics.json").read_text())
    timestamp = json.loads((folder / "timestamp-diagnostic.json").read_text())
    plans = {p["task_id"]: p for p in json.loads((folder / "plans.json").read_text())["plans"]}
    tasks = {t["id"]: t for t in json.loads((ROOT / "fixtures/public.json").read_text())["tasks"]}
    rounds = [json.loads((folder / f"round-{n}/metadata.json").read_text()) for n in (1, 2)]
    operation_count = sum(s["operations"] for s in experiment["sources"])
    source_rows = table(["Official source", "Operations", "Source commit date", "API version", "Content SHA-256"], [
        [f"<a href='{e(s['url'])}'>{e(s['provider'])}</a>", str(s["operations"]), e(s["source_commit_date"]),
         e(s["info"].get("version")), f"<code>{e(s['sha256'][:20])}…</code>"] for s in experiment["sources"]])
    review_rows = table(["Task", "Assessment", "What happened"], [
        [f"<a href='#task-{e(r['task_id'])}'>{e(r['task_id'])}</a>", e(r["assessment"]), e(r["finding"])] for r in review["tasks"]])
    rank_rows = table(["Missing operation", "Rank on initial query", "Ranks on follow-ups"], [
        [f"<code>{e(r['operation'])}</code>", str(r["query_ranks"][0]["rank"]),
         e(", ".join(str(q["rank"]) for q in r["query_ranks"][1:]))] for r in diagnostics["retrieval_ranks"]])
    omitted_rows = table(["Stripe preview request field", "Exists in full specification", "Present in model's excerpt"], [
        [f"<code>{e(r['pointer'])}</code>", str(r["exists_in_full_spec"]), str(r["present_in_excerpt"])] for r in diagnostics["omitted_fields"]])
    scenario_rows = table(["Task/probe", "Expected if fully supported", "Full plan state", "Checks alone", "Interpretation"], [
        [f"{e(r['task_id'])}/{e(r['case'])}", e(r["expected"]), e(r["status"]), e(r["checks_only_status"]),
         "Check logic matches this probe" if r["checks_only_passed"] else "Required check logic missing"] for r in audit["scenarios"]])
    details = []
    for identity, task in tasks.items():
        plan = plans[identity]
        requests = table(["Observation request", "Bindings from task context", "Polling", "Access notes"], [
            [f"<code>{e(r['operation'])}</code>", f"<code>{e(r['bindings_json'])}</code>",
             str(r["poll_interval_seconds"]) + " s", e(r["auth_notes"])] for r in plan["requests"]])
        checks = table(["Check", "Phase", "Response selection", "Condition"], [
            [e(c["id"]), e(c["phase"]), f"<code>{e(c['request_id'])}{e(c['collection_pointer'])}</code><br>{e(c['select_mode'])} / {e(c['order_by'])}<br><code>{e(c['selector_json'])}</code>",
             f"<code>{e(c['field_pointer'])} {e(c['op'])} {e(c['value_json'])}</code>"] for c in plan["checks"]])
        requirements = "".join(f"<li><strong>{e(r['phase'])} · {e(r['coverage'])}</strong>: {e(r['text'])}<br><small>{e(r['limitation'])}</small></li>" for r in plan["requirements"])
        limitations = "".join(f"<li>{e(s)}</li>" for s in plan["limitations"])
        details.append(f"<details id='task-{e(identity)}'><summary>{e(identity)} · {e(task['provider'])}: {e(task['task'])}</summary><h3>Requests</h3>{requests}<h3>Checks</h3>{checks}<h3>Declared coverage</h3><ul>{requirements}</ul><h3>Limitations</h3><ul>{limitations}</ul></details>")
    usage = table(["Model call", "Input tokens", "Output tokens", "Wall time", "Inference item types"], [
        [str(r["round"]), str(sum(u.get("input_tokens", 0) for u in r["usage"])), str(sum(u.get("output_tokens", 0) for u in r["usage"])),
         f"{r['elapsed_seconds']:.1f} s", e(", ".join(r["observed_item_types"]))] for r in rounds])
    links = sorted({url for r in review["tasks"] for url in r["sources"]})
    source_links = "".join(f"<li><a href='{e(url)}'>{e(url)}</a></li>" for url in links)
    control = audit["control"]
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Real API docs: where task-to-monitor planning breaks</title>
<style>body{{margin:0;background:#f8faf9;color:#192829;font:16px/1.6 system-ui,sans-serif}}main{{max-width:1160px;margin:auto;padding:40px 24px 80px}}h1{{font-size:40px;line-height:1.15;max-width:900px}}h2{{margin-top:40px}}p,li{{max-width:950px}}.scope{{padding:18px 22px;background:#eaf1ee;border-left:4px solid #3d7463}}.finding{{padding:16px 22px;background:#fff2e5;border-left:4px solid #b77330}}table{{border-collapse:collapse;width:100%;font-size:14px;background:white}}td,th{{border:1px solid #d5dfd9;padding:11px;text-align:left;vertical-align:top}}th{{background:#eaf0ed}}.scroll{{overflow-x:auto}}small{{color:#5d6e64}}code{{font-size:.88em;overflow-wrap:anywhere}}a{{color:#146270}}details{{padding:18px 0;border-bottom:1px solid #cad8d0}}summary{{cursor:pointer;font-weight:600}}pre{{padding:18px;background:#edf1ef;overflow:auto;font-size:13px}}.meta{{color:#627168}}</style></head><body><main>
<p class="meta">Real-document experiment · two automatic retrieval rounds · eight frozen tasks</p>
<h1>Real API docs exposed gaps before execution</h1>
<div class="scope"><strong>No hand-authored observation catalogs.</strong> The program downloaded complete official Stripe, GitHub and Slack specifications, indexed all {operation_count:,} operations, retrieved excerpts automatically, and let a tools-disabled model request another search round. Task requirements and context were authored; endpoints and fields were not selected for the planner by hand. Private audit requirements were withheld.</div>
<h2>The result</h2>
<div class="finding"><strong>None of the three intended positive workflows became a complete executable monitor.</strong> Stripe's exact-change preview remained incomplete; the deployment checks needed unimplemented pagination; the workflow-run endpoint was missed by retrieval. The model generally exposed those gaps rather than inventing evidence.</div>
<p>The structural auditor found <strong>{audit['summary']['structural_issues']} operation/parameter-name/response-field-reference issues</strong> across {audit['summary']['requests']} proposed requests and {audit['summary']['checks']} checks. That narrow result is compatible with a useless monitor: schema-valid fragments do not establish adequate observation coverage or execution completeness.</p>
{review_rows}
<h2>Where the pipeline broke</h2>
<ol><li><strong>Retrieval missed an existing, working endpoint.</strong> The exact-run GitHub operation ranked 112th initially and 8th/6th on follow-up searches; follow-ups retained two results. A separately labeled public GET control succeeded. The planner did not receive that control or a manual repair.</li>
<li><strong>Automatic summarization removed necessary request fields.</strong> Stripe's preview operation was retrieved, but projection/character budgets removed all four fields below. The model correctly declined to construct an undocumented exact-change body. The full source had the answer.</li>
<li><strong>Important conditions stayed in prose.</strong> The deployment plan explicitly required complete pagination and ambiguity handling. The generated request/check language could not orchestrate pagination. Such a note cannot be counted as an implemented guarantee.</li>
<li><strong>Official documentation can be stale.</strong> The downloaded Slack specification was last committed in 2020. The planner noticed that it could not validate the relevant policy. Current <a href="https://docs.slack.dev/reference/methods/conversations.history/">Slack history documentation</a> limits new commercially distributed non-Marketplace apps to one request per minute, preventing the requested sustained 30-second polling.</li></ol>
{rank_rows}{omitted_rows}
<h2>One actual public API control</h2>
<p><strong>Executed: {e(control.get('executed'))}; generated by planner: {e(control.get('planner_generated', False))}.</strong> The independent control used an unauthenticated GET to <a href='{e(control.get('url', '#'))}'>{e(control.get('url', ''))}</a>. It returned run status <code>{e(control.get('status'))}</code>, conclusion <code>{e(control.get('conclusion'))}</code>, and matched the run ID/head SHA. This demonstrates a retrieval failure; it does not count as successful generated execution.</p>
<p>No generated business request ran. No Stripe or Slack account was used, no deployment was created, and no message was sent. Public workflow identity discovery and this control were the only business-resource reads; source repository commits and API definitions were also read.</p>
<h2>Behavior under changed responses</h2>{scenario_rows}
<p>All full-plan states remain unverifiable because the model explicitly declared necessary coverage gaps. Check-only replay is a diagnostic: it temporarily removes those gap declarations to isolate whether selected comparisons work. It must never be used to authorize an action. All six deployment check-only probes work, including newer failure after old success and unavailable evidence; the workflow plan contains no checks. These fourteen synthetic probes are exploratory diagnostics, not a model accuracy estimate.</p>
<h2>A runtime defect surfaced too</h2>
<p>After reading the generated plan's timestamp warning, we added a separate adversarial diagnostic: success and failure have identical latest timestamps. The original selector picked the first record and returned check-only <code>{e(timestamp['before_fix']['status'])}</code>. A generic interpreter fix now returns <code>{e(timestamp['after_fix']['status'])}</code> when tied latest records disagree on the condition. The full plan was already unverifiable, so this did not produce a false overall completion. The diagnostic was authored after inference, retained separately, and was never fed to the model.</p>
<h2>Source snapshots</h2>{source_rows}
<p>These are pinned official OpenAPI/Swagger sources. The planner saw automatically projected excerpts, including source dates and explicit truncation notices. It did not browse unrestricted website documentation. The current narrative pages below were consulted by the researcher for post-run review only. The same researcher authored tasks, retrieval code and review; this is not independent held-out validation.</p>
<h2>Inspect the plans</h2>{''.join(details)}
<h2>Measured inference usage</h2>{usage}
<p>The two calls consumed {sum(sum(u.get('input_tokens', 0) for u in r['usage']) for r in rounds):,} input tokens including CLI instructions. No inference was used during response replay. Token counts and wall time are actual; this experiment makes no dollar or production latency claim.</p>
<h2>What to change next</h2>
<ol><li>Separate endpoint discovery from full schema reading, so long task text cannot bury the required operation below a tiny search cutoff.</li><li>Navigate exact request/response schema branches on demand instead of discarding tail fields from large flattened schemas.</li><li>Make pagination, collection completeness, identity binding and ambiguous revisions executable parts of the monitoring language.</li><li>Retrieve current narrative policy documentation as well as machine-readable schemas, carrying source versions and unresolved policy gaps into the plan.</li><li>Repeat the frozen tasks as a disclosed development rerun, then evaluate fresh tasks before claiming improvement.</li></ol>
<h2>Reproduce without more model calls</h2><pre>.venv/bin/python -m docs_lab.audit
.venv/bin/python -m docs_lab.diagnose
.venv/bin/python -m docs_lab.report
.venv/bin/python -m unittest discover -s tests -v</pre>
<p>The offline audit preserves and hash-checks the recorded live control. A fresh live control requires <code>--live-control</code>. See <a href="../../../README.md">README</a> for source acquisition and fresh inference commands.</p>
<h2>Evidence</h2><ul><li><a href="plans.json">Final untouched generated plans</a></li><li><a href="experiment.json">Source pins, retrieval limits and code hashes</a></li><li><a href="round-1/input.json">Initial retrieved inputs</a> · <a href="round-1/metadata.json">first inference metadata</a></li><li><a href="round-2/input.json">Automatic follow-up retrieval and previous proposals</a> · <a href="round-2/metadata.json">second inference metadata</a></li><li><a href="audit.json">Structural audit and response probes</a> · <a href="diagnostics.json">retrieval/projection diagnostics</a></li><li><a href="review.json">Post-inference semantic review</a></li><li><a href="timestamp-diagnostic.json">Timestamp counterexample before and after interpreter fix</a></li><li><a href="control-response.json">Actual public control response</a></li><li><a href="../../planning/report.html">Earlier prepared-catalog experiment</a></li></ul><ul>{source_links}</ul>
</main></body></html>"""
    (folder / "report.html").write_text(html)
    print(f"Saved {folder / 'report.html'}")


if __name__ == "__main__":
    main()
