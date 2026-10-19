# SPDX-License-Identifier: Apache-2.0
"""Current safety results, with the failed locked fresh run kept visible."""
import html
import json
from pathlib import Path
from expansion_lab.storage import PROJECT, Store, write_json


def main():
    store = Store(); folder = PROJECT / "documents/generated"; folder.mkdir(parents=True, exist_ok=True)
    rows, raw = [], []
    identities = ["adaptive-development-paired-1", "adaptive-previous-fresh-1", "adaptive-new-fresh-1", "safety-development-1", "safety-previous-fresh-1", "safety-new-fresh-regression-1"]
    for identity in identities:
        manifest = json.loads((store.root / "runs" / identity / "manifest.json").read_text())
        audit = store.get(manifest["audit"])
        phase = "post-fresh regression" if manifest.get("post_fresh_regression") else "locked fresh test" if manifest["suite"] == "new_fresh" else "pre-repair regression"
        raw.append({"id": identity, "phase": phase, "frozen": audit["frozen"], "corrected": audit["corrected"], "quantity": audit["quantity"], "structural_issues": audit["structural_issues"], "task_summaries": audit["task_summaries"]})
        values = [identity, phase, f"{audit['frozen']['passed']}/{audit['frozen']['total']}", f"{audit['corrected']['passed']}/{audit['corrected']['total']}",
                  f"{audit['quantity']['passed']}/{audit['quantity']['total']}" if audit["quantity"]["total"] else "included in new tasks" if manifest["suite"] == "new_fresh" else "—",
                  audit["corrected"]["false_ready_or_complete"] + audit["quantity"]["false_ready_or_complete"], audit["structural_issues"]]
        rows.append("<tr>" + "".join("<td>" + html.escape(str(v)) + "</td>" for v in values) + "</tr>")
    repaired = json.loads((store.root / "runs/safety-new-fresh-regression-1/manifest.json").read_text())
    audit = store.get(repaired["audit"])
    failures = [r for r in audit["corrected_scenarios"] if not r["passed"]]
    quantity_new = [r for r in audit["corrected_scenarios"] if r["task_id"] == "n01"]
    summary = {"results": raw, "new_quantity_cases_passed": sum(r["passed"] for r in quantity_new), "new_quantity_cases_total": len(quantity_new), "remaining_failures": failures,
               "conclusion": "Retrieval expansion recovered missing evidence. A held-out phase bug required a separate safety repair; its rerun is regression evidence. The ambiguous pinned Slack user schema remains unresolved. No new task-generalization claim is made from the repaired rerun."}
    write_json(folder / "safety-summary.json", summary)
    style = "body{font:16px/1.6 system-ui;max-width:1180px;margin:42px auto;padding:0 24px;background:#f6f8fa;color:#172831}h1{font-size:38px;line-height:1.2}table{border-collapse:collapse;width:100%;background:white}td,th{padding:10px;text-align:left;border-bottom:1px solid #dbe2e8;font-size:14px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:16px}.scroll{overflow:auto}a{color:#155f89}"
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Current safety and retrieval results</title><style>{style}</style></head><body><p><a href="index.html">All documents</a> · <a href="expansion-report.html">Original locked comparison</a></p>
    <h1>Automatic retrieval expansion: current result</h1><p>Expansion recovered the evidence needed to pass all 52 existing scenarios under the documented simulator corrections and all 12 added original-task quantity tests. The six new tasks exposed a business-phase bug that passing totals alone hid. We preserved that fresh result, repaired the runtime and compiler contract separately, and reran it as regression testing.</p>
    <div class="scroll"><table><tr><th>Run</th><th>Evidence status</th><th>Frozen scenarios</th><th>Corrected replay</th><th>Extra original quantity</th><th>False ready/complete</th><th>Structural issues</th></tr>{''.join(rows)}</table></div>
    <h2>Safety repair</h2><p>The original fresh plans placed some preview budget and currency checks AFTER the business change. BEFORE readiness could therefore be satisfied without those checks. Failed typed guards also did not independently gate their business phase. The hardened runtime now makes a failed preservation guard force that phase to unknown, validates check/requirement phase consistency, and rejects existing-item price-preview checks assigned AFTER. The compiler receives this public contract feedback from the start.</p>
    <p>After repair, all 24 quantity scenarios pass: 12 original-task variants and 12 new-task variants. Values 0, 1, 3 and 11 are explicitly preserved; missing values, strings, booleans, wrong item/subscription identities and failed reads stop the dependent preview. Over-budget and wrong-currency previews block readiness. The repaired new-task suite scores 35/41 with zero false ready/complete results and zero structural issues. This is a post-fresh regression result, not new held-out validation.</p>
    <h2>What still fails</h2><p>The remaining six cases are the Slack directory task. The pinned 2020 specification encodes user alternatives in an ambiguous untyped items array. The model now reports insufficient schema evidence instead of asserting unsupported user-field checks. The API capability may exist; our ingestion cannot establish it from this snapshot. No source schema or expected result was silently rewritten.</p>
    <h2>Use the hardened entry point</h2><pre>python3 -m safety_lab.run --arm adaptive --suite development --run-id UNIQUE_RUN_ID
python3 -m safety_lab.grade UNIQUE_RUN_ID
python3 -m expansion_lab.inspect UNIQUE_RUN_ID --kind plans
python3 -m unittest discover -s tests -q
python3 -m unittest discover -s safety_lab/tests -q</pre><p>expansion_lab is retained as the locked experimental baseline. safety_lab layers the repaired public contract and runtime on top. The current run compiler can still request missing operations/schema branches and fall back once to the complete endpoint directory. More inference passes are part of the improvement; no speed or equal-compute advantage is established.</p>
    <h2>Data and provenance</h2><p>Code and fixtures live in the Repos project. Generated documents are together under documents/generated. Source snapshots and content-addressed run blobs are gzip-compressed; all referenced run blobs are verified. Historical files were archived from 34.5 MB to 3.8 MB with every member hash checked. The original workspace remains a compatibility backup, with no dependency environment or model cache copied to the new project.</p>
    <p><a href="safety-summary.json">Safety summary and remaining cases</a> · <a href="architecture.html">Storage and reproduction guide</a>. The locked expansion code remains unchanged. The same author designed tasks and implementation; there are no unseen-provider or production-reliability claims. Preserving an observed quantity does not make reads and previews atomic.</p></body></html>'''
    (folder / "safety-report.html").write_text(page)
    index = folder / "index.html"
    content = index.read_text()
    marker = '<p><strong>Current result:</strong> <a href="safety-report.html">Retrieval expansion and phase-safety repair</a></p>'
    if marker not in content: content = content.replace("<h1>Observation Lab</h1>", "<h1>Observation Lab</h1>" + marker)
    index.write_text(content)
    print("Generated", folder / "safety-report.html")


if __name__ == "__main__":
    main()
