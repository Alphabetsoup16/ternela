# SPDX-License-Identifier: Apache-2.0
"""Render executed task-to-monitor planning results as a self-contained report."""

import argparse
from html import escape
import json
from pathlib import Path


def e(value):
    return escape(str(value), quote=True)


def table(headers, rows):
    return "<div class='scroll'><table><thead><tr>" + "".join(f"<th>{e(h)}</th>" for h in headers) + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows) + "</tbody></table></div>"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("results/planning/latest.json"))
    parser.add_argument("--output", type=Path, default=Path("results/planning/report.html"))
    args = parser.parse_args()
    data = json.loads(args.input.read_text())
    methods = data["methods"]
    summary = table(["Frozen prompt", "Original fixture score (disputed)", "Seven uncontested tasks", "Raw unnecessary interruptions", "Raw false completions", "Raw unsafe clearance", "Plan generation"], [
        [e(m["name"]), f"{m['summary']['passed']}/{m['summary']['total']}",
         f"{m['uncontested_summary']['passed']}/{m['uncontested_summary']['total']}",
         f"{m['summary']['unnecessary_interruptions']}/{m['summary']['feasible_scenarios']}",
         f"{m['summary']['false_completions']}/{m['summary']['false_completion_opportunities']}",
         f"{m['summary']['unsafe_clearances']}/{m['summary']['unsafe_clearance_opportunities']}",
         f"{m['metadata']['elapsed_seconds']:.1f} s"] for m in methods])
    task_table = table(["Task", "Catalog tools", *[m["name"] for m in methods]], [
        [e(t["task"]), str(len(t["tools"])), *[
            f"{next(r for r in m['tasks'] if r['task_id'] == t['id'])['summary']['passed']}/"
            f"{next(r for r in m['tasks'] if r['task_id'] == t['id'])['summary']['total']}"
            for m in methods]] for t in data["tasks"]])
    details = []
    for task in data["tasks"]:
        plans_html = []
        for method in methods:
            plan = method["plans"][task["id"]]
            checks = table(["Phase", "Observation", "Comparison", "Why"], [
                [e(c["phase"]), f"<code>{e(c['tool'])}.{e(c['field'])}</code>",
                 f"{e(c['op'])} <code>{e(json.dumps(c['value']))}</code>", e(c["reason"])] for c in plan["checks"]])
            unresolved = "".join(f"<li>{e(text)}</li>" for text in plan["unresolved"])
            requirement_rows = "".join(f"<li>{e(r['text'])} <small>{e(', '.join(r['check_ids']))}</small></li>" for r in plan["requirements"])
            plans_html.append(f"<h3>{e(method['name'])}</h3>{checks}<p>Unresolved:</p><ul>{unresolved or '<li>None declared</li>'}</ul><p>Model-declared coverage (not independently verified):</p><ul>{requirement_rows}</ul>")
        details.append(f"<details><summary>{e(task['id'])}: {e(task['task'])}</summary>{''.join(plans_html)}</details>")
    failures = table(["Prompt", "Task/case", "Expected", "Actual", "Interpretation"], [
        [e(m["name"]), f"{e(r['task_id'])}/{e(r['case'])}", e(r["expected"]), e(r["actual"]), e(r["note"] or "; ".join(r["reasons"]))]
        for m in methods for r in m["runs"] if not r["passed"]])
    timeline = table(["Fixture event", "Phase", "Monitor state", "State transition"], [
        [e(r["event"]), e(r["phase"]), e(r["status"]), "Yes" if r["state_transition"] else "No"] for r in data["timeline"]])
    tokens = table(["Prompt", "Input tokens", "Output tokens", "Observed inference item types", "Before-action tool reads"], [
        [e(m["name"]), str(sum(u.get("input_tokens", 0) for u in m["metadata"]["usage"])),
         str(sum(u.get("output_tokens", 0) for u in m["metadata"]["usage"])),
         e(", ".join(m["metadata"]["observed_item_types"])),
         str(m["summary"]["mean_before_tool_reads"])] for m in methods])
    matrix = []
    for row in methods[0]["runs"]:
        cells = [f"{e(row['task_id'])}/{e(row['case'])}", e(row["expected"])]
        for method in methods:
            result = next(r for r in method["runs"] if r["task_id"] == row["task_id"] and r["case"] == row["case"])
            cells.append(f"<span class={'okay' if result['passed'] else 'bad'}>{e(result['actual'])}</span>")
        matrix.append(cells)
    raw_links = "".join(f"<li><a href='{e(m['name'])}/plans.json'>{e(m['name'])} generated plans</a> · <a href='{e(m['name'])}/prompt.txt'>exact input</a> · <a href='{e(m['name'])}/metadata.json'>inference metadata</a></li>" for m in methods)
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Can a task generate its own monitor?</title>
<style>body{{margin:0;background:#f8faf9;color:#182728;font:16px/1.6 system-ui,sans-serif}}main{{max-width:1160px;margin:auto;padding:40px 24px 80px}}h1{{font-size:40px;line-height:1.15;max-width:850px}}h2{{margin-top:40px}}p,li{{max-width:920px}}.scope{{padding:18px 22px;background:#e9f1ee;border-left:4px solid #3c7768}}table{{border-collapse:collapse;width:100%;font-size:14px;background:white}}td,th{{border:1px solid #d4dfda;padding:11px;text-align:left;vertical-align:top}}th{{background:#eaf0ed}}.scroll{{overflow-x:auto}}small{{color:#63716c}}code{{font-size:.9em}}a{{color:#146270}}details{{padding:18px 0;border-bottom:1px solid #cad8d0}}summary{{cursor:pointer;font-weight:600}}.okay{{color:#1c6947}}.bad{{color:#a33814}}pre{{padding:18px;background:#edf1ef;overflow:auto;font-size:13px}}.meta{{color:#65736e}}</style></head><body><main>
<p class="meta">Task-to-monitor experiment · executed {e(data['generated_at'])}</p>
<h1>Can an agent figure out what to monitor from the task itself?</h1>
<p><a href="../docs/v1/report.html">Next experiment: discover monitors directly from real API documentation</a> — automatic retrieval, missing fields, and execution gaps.</p>
<div class="scope"><strong>Real model inference, executable monitoring plans.</strong> Two frozen prompts received the same eight natural-language tasks and read-only tool catalogs. The model did not receive the 90 private scenarios or expected outcomes. The source author designed both prompts and fixtures. All provider observations are synthetic; there were no real purchases, provider connections or business actions.</div>
<h2>Results</h2>{summary}
<p>These are scenario counts from an exploratory suite, not estimates of production accuracy. Each strategy ran once, with no repairs after grading. "Unsafe clearance" means the monitor returned ready where the original fixture required blocked, unknown or unmonitorable; no action was actually executed.</p>
<div class="scope"><strong>The second prompt found an ambiguity in the benchmark.</strong> The purchase task requires arrival by day five, but the receipt has no actual arrival time or final charge. The original scoring rules checked the quote before action, then delivery quantity/model/status after action. The coverage planner treated price and arrival as actual outcome requirements and refused to claim complete monitoring. Both readings are defensible. We preserved the frozen cases, generated plans and raw scores, then flagged all 17 purchase scenarios for review. On the other seven tasks, both prompts passed all 73 scenarios. The raw score difference does not establish that either prompt is better.</div>
<h2>What this tests</h2>
<ol><li><strong>Interpret the request.</strong> Extract literal limits, identity, dates and required outcomes from task text.</li><li><strong>Select useful observations.</strong> Choose all-in previews over incomplete list prices and ignore cosmetic or unrelated fields.</li><li><strong>Emit a bounded plan.</strong> Select catalog tools, fields and comparisons in a JSON schema. The interpreter cannot execute model-generated code.</li><li><strong>Stay valid as conditions change.</strong> Evaluate changed sources, stale evidence, unavailable sources and delayed/wrong-operation receipts without another model call.</li><li><strong>Recognize a coverage gap.</strong> Refuse to claim monitorability when backup access or necessary user preferences are missing.</li></ol>
<h2>Tasks</h2>{task_table}
<h2>Failures and limits</h2>{failures}
<p><strong>A design issue in this prototype:</strong> unresolved requirements disable both preconditions and outcome monitoring. A gap in final confirmation need not make every earlier check unusable. A useful next revision should express coverage separately for each phase and let the user resolve ambiguous outcome requirements explicitly.</p>
<p>The misleading-preview scenario has exactly the same observable input as a feasible purchase. A planner that permits the feasible purchase cannot distinguish the invalid purchase. The observable-scenario score is therefore shown separately, while the failure remains in the overall score.</p>
<p>The task catalogs are small and deliberately descriptive. Parameters, tool argument binding, authentication, schema discovery and source interpretation are supplied by the fixture. Freshness, polling and receipt-deadline policy are explicit inputs. This does not test finding arbitrary APIs on the internet, synthesizing connectors, reconciling conflicting sources, arithmetic across sources, updating plans when a task changes, prompt-injection resistance during discovery, or atomic execution.</p>
<h2>One generated plan through a changing task</h2>{timeline}
<p>This is a replay of synthetic snapshots with logical time, not a real event subscription. The monitor is fixed throughout the replay. Harmless price changes can leave it ready; an over-budget total blocks it; missing evidence makes it unknown; acceptance remains pending until payment is confirmed.</p>
<h2>Inspect every generated check</h2>{''.join(details)}
<h2>Actual inference usage</h2>{tokens}
<p>Plan generation time includes CLI startup and model inference for the entire batch. Input tokens include CLI instructions. Tool reads count distinct fixture tools needed per evaluation, not real requests. Catalog cost units are synthetic; no dollar savings, production latency or minimum-cost coverage claim follows from these results.</p>
<h2>Run the prototype</h2><pre>.venv/bin/python -m observation_lab.grade results/planning/direct-v1 results/planning/coverage-v1
.venv/bin/python -m observation_lab.report
.venv/bin/python -m unittest discover -s tests -v</pre>
<p>A new task/catalog can be passed to <code>observation_lab.planner --input YOUR_PUBLIC_TASKS.json --output NEW_DIRECTORY</code>. Generated checks run locally with <code>observation_lab.watch</code> against a connector-owned JSON snapshot. See <a href="../../README.md">README</a> for the runnable watcher example and boundaries.</p>
<h2>All scenario outcomes</h2>{table(['Scenario', 'Expected', *[m['name'] for m in methods]], matrix)}
<h2>Evidence and reproduction</h2><ul>{raw_links}<li><a href="latest.json">Full scored results and source/fixture hashes</a></li><li><a href="../../observation_lab/fixtures/manifest.json">Frozen fixture manifest</a></li><li><a href="../report.html">Earlier FreshCtx / Temporal experiment</a></li></ul>
<p>Backend: installed Codex CLI default model, with user config ignored, shell tools disabled, web search disabled, apps disabled, empty working directory and no project documentation. Inference event item types are recorded above; unexpected tool activity invalidates a run. Authentication uses the existing CLI sign-in. No secret values are loaded or stored by the experiment. <a href="https://learn.chatgpt.com/docs/non-interactive-mode">Official non-interactive CLI documentation</a>.</p>
</main></body></html>"""
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html)
    print(f"Saved {args.output}")


if __name__ == "__main__":
    main()
