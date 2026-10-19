# SPDX-License-Identifier: Apache-2.0
"""Generate all human-readable experiment documents in one folder."""
import argparse
from datetime import datetime, timezone
import html
import json
from pathlib import Path

from .storage import PROJECT, Store, write_json, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", nargs="+", required=True, help="Explicit run IDs; never select winners by score")
    args = parser.parse_args()
    store = Store(); folder = PROJECT / "documents/generated"; folder.mkdir(parents=True, exist_ok=True)
    entries = []
    for identity in args.runs:
        manifest_path = store.root / "runs" / identity / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        audit = store.get(manifest["audit"]) if "audit" in manifest else None
        attempts = [a for s in manifest["stages"].values() for a in s["attempts"]]
        actual_attempts = [a for s in manifest["stages"].values() if not s.get("reused_from") for a in s["attempts"]]
        usage = [u for a in actual_attempts for u in a.get("usage", [])]
        expansions = [store.get(ref) for ref in manifest.get("expansions", [])]
        entries.append({"id": identity, "arm": manifest["arm"], "suite": manifest["suite"], "status": manifest["status"],
                        "audit": audit, "stages": {k: {"status": v["status"], "reused_from": v.get("reused_from")} for k, v in manifest["stages"].items()},
                        "new_input_tokens": sum(u["input_tokens"] for u in usage), "new_output_tokens": sum(u["output_tokens"] for u in usage),
                        "new_attempt_seconds": sum(a.get("elapsed_seconds", 0) for a in actual_attempts),
                        "logical_stage_seconds": sum(a.get("elapsed_seconds", 0) for a in attempts),
                        "expansions": expansions, "fallback_used": any(e["fallback_used"] for e in expansions), "candidate_counts": manifest.get("candidate_counts", {}),
                        "code_sha256": manifest["code_sha256"], "plans_ref": manifest.get("plans"), "reused_initial_from": manifest.get("reuse_from")})
    unique_refs = {}
    def refs(value):
        if isinstance(value, dict):
            if {"sha256", "bytes", "gzip_bytes", "media_type"} <= value.keys(): unique_refs[value["sha256"]] = value
            else:
                for v in value.values(): refs(v)
        elif isinstance(value, list):
            for v in value: refs(v)
    for p in (store.root / "runs").glob("*/manifest.json"): refs(json.loads(p.read_text()))
    # Verify every stored blob, including all failed development runs.
    for ref in unique_refs.values(): store.get(ref)
    storage = {"unique_blobs": len(unique_refs), "raw_blob_bytes": sum(r["bytes"] for r in unique_refs.values()), "gzip_blob_bytes": sum(r["gzip_bytes"] for r in unique_refs.values()),
               "all_referenced_blobs_verified": True}
    history = store.root / "history/manifest.json"
    if history.exists():
        h = json.loads(history.read_text()); storage["history"] = {k: h[k] for k in ["raw_bytes", "archive_bytes", "verified_all_members"]}
    summary = {"at": datetime.now(timezone.utc).isoformat(), "run_ids_selected_explicitly": args.runs, "entries": entries, "storage": storage,
               "limits": ["Synthetic responses, not live provider integration", "Same author designed tasks and implementation; not blinded", "Same three providers; no unseen-provider claim", "Existing five original tasks have no behavioral probes", "Observed quantity preservation does not provide atomicity against concurrent provider changes", "More model passes are part of the adaptive treatment; no equal-compute claim", "One trial per final arm/suite; no stable latency or population accuracy estimate"]}
    summary_ref = store.put(summary)
    write_json(folder / "expansion-summary.json", {"compressed_summary": summary_ref, "runs": args.runs, "storage": storage})
    esc = html.escape
    def table_rows():
        rows = []
        for e in entries:
            a = e["audit"]
            if not a:
                rows.append(f"<tr><td>{esc(e['id'])}</td><td colspan='9'>{esc(e['status'])}</td></tr>"); continue
            values = [e["arm"], e["suite"], f"{a['frozen']['passed']}/{a['frozen']['total']}", f"{a['corrected']['passed']}/{a['corrected']['total']}",
                      f"{a['quantity']['passed']}/{a['quantity']['total']}" if a["quantity"]["total"] else "included in new tasks" if e["suite"] == "new_fresh" else "—",
                      a["structural_issues"], a["corrected"]["false_ready_or_complete"] + a["quantity"]["false_ready_or_complete"], len(e["stages"]), e["fallback_used"], f"{e['new_input_tokens']:,}"]
            rows.append("<tr>" + "".join(f"<td>{esc(str(v))}</td>" for v in values) + "</tr>")
        return "".join(rows)
    details = []
    for entry in entries:
        audit = entry["audit"]
        if not audit: continue
        failures = [r for r in audit["corrected_scenarios"] + audit["quantity_scenarios"] if not r["passed"]]
        details.append(f"<details><summary>{esc(entry['id'])}: {len(failures)} failed corrected/quantity scenarios</summary><pre>{esc(json.dumps(failures, indent=2))}</pre></details>")
        details.append(f"<details><summary>{esc(entry['id'])}: automatic expansion trace</summary><pre>{esc(json.dumps(entry['expansions'], indent=2))}</pre></details>")
    style = "body{font:16px/1.6 system-ui;max-width:1220px;margin:42px auto;padding:0 24px;color:#172831;background:#f6f8fa}h1{font-size:38px;line-height:1.2}h2{margin-top:36px}table{border-collapse:collapse;width:100%;background:#fff}td,th{padding:10px;border-bottom:1px solid #d9e1e6;text-align:left;font-size:14px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#fff;padding:16px}details{margin:16px 0;padding:12px;background:white}summary{cursor:pointer;font-weight:600}.scroll{overflow-x:auto}a{color:#155f89}"
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Automatic retrieval expansion</title><style>{style}</style></head><body>
    <p><a href="index.html">Observation Lab documents</a></p><h1>Can the agent retrieve its missing evidence?</h1>
    <p>The fixed and adaptive pipelines start with the same BM25 shortlist of 20 official operations per task. Adaptive compilation can request missing evidence, follow resource relationships, open used schema pointers, and fall back once to the complete directory. The full-directory arm is a reference control. All arms use the same preservation policy and guarded runtime.</p>
    <div class="scroll"><table><tr><th>Arm</th><th>Suite</th><th>Frozen scenarios</th><th>Known fixture corrections</th><th>Strict quantity scenarios</th><th>Structural issues</th><th>False ready/complete</th><th>Model stages</th><th>Full fallback</th><th>New input tokens</th></tr>{table_rows()}</table></div>
    <p>Frozen scores retain the old literal expectations and response bodies. Corrected scores add the already documented refund alias and workflow-list adapters, a documented workflow-scoped artifact list alias, and requested proration timestamp metadata omitted by the old preview simulator; they honor SHA filtering. These development adjudications are recorded in expansion_lab/adjudications.json. Strict quantity cases are additional to the old 52. New-fresh has 41 frozen scenarios, including 12 quantity variants; development adds 12 quantity regressions. Five original tasks still have structural audits but no behavioral scenarios.</p>
    <h2>What changed</h2><pre>Task → initial shortlist → schema discovery → draft plan
                                    ↓
                    public evidence gaps + contract validation
                                    ↓
                 focused read queries + related resources + schema branches
                                    ↓
                 recompile → bounded full-directory fallback if needed
                                    ↓
                 validated observations + typed preservation guards</pre>
    <p>Expansion receives planner-declared gaps, partial coverage and public policy validation. It never receives private scenario results. Where a fixed initial run is reused, its exact prompt/output references are retained and the input, instructions and schema are validated; this pairs the initial plan rather than depending on a second lucky generation. Other development attempts are retained and identified separately.</p>
    <h2>Quantity preservation</h2><p>An existing-item price-only Stripe preview must explicitly copy an integer quantity from a completed observation of the correct item and subscription. The guard validates the source identity, source type, dependency order and exact typed binding before the preview request can execute. Missing values, strings, booleans, forbidden reads and wrong identities stop the dependent preview. Zero is preserved. This provider rule is explicit domain policy; it was not discovered automatically from arbitrary legal or API semantics. It preserves the observed value, without locking the provider against concurrent changes.</p>
    <h2>Fresh-task discipline and limits</h2><p>Six new tasks and 41 scenario names were frozen before implementation. They cover PaymentIntents, releases, check runs, Slack user records, a quantity-preserving preview variant, and an impossible future-irreversibility guarantee. Code, prompts and policy are locked before new-fresh inference. The same author designed the fixtures and implementation; this is not blinded external validation. All three providers were seen before. The experiment compiles against real pinned API documents but replays simulated responses; no payment or provider mutation executes.</p>
    <p>Adaptive compilation has a larger model-call budget. Its results do not establish equal-compute superiority or a production speed advantage. Reported new tokens exclude stages reused from another run; those original calls are recorded in their source runs. Operational retries keep byte-identical prompts and preserve failures. Quantity edge cases and private grades are applied only after planning.</p>
    <p>A pre-run source viability check identified a known stress case: the pinned 2020 Slack objs_user definition puts alternative object schemas inside an untyped items array. The standard schema walker cannot establish user-object field pointers through that ambiguous encoding. The new Slack task remains frozen and unchanged; any resulting failures must be distinguished from unsupported API capability or unsafe completion. No vendor schema is silently rewritten.</p>
    <h2>Compressed, portable storage</h2><pre>{esc(json.dumps(storage, indent=2))}</pre><p>Official source snapshots are gzip-compressed and verified against their original pins. Prompts, schemas, inputs, outputs, traces and code snapshots use content-addressed gzip blobs. Small manifests under data/runs contain relative fixture paths and references, making new runs portable. Reports and document exports live under documents/generated. Secrets, model-weight caches and virtual environments are excluded from the historical archive.</p>
    <h2>Detailed evidence</h2>{''.join(details)}<p><a href="expansion-summary.json">Summary reference and selected run IDs</a>. Inspect a run with <code>python3 -m expansion_lab.inspect RUN_ID --kind plans</code>. All failed development runs remain under data/runs.</p></body></html>'''
    (folder / "expansion-report.html").write_text(page)
    historical = sorted((folder / "history").rglob("*.html")) if (folder / "history").exists() else []
    history_links = "".join(f"<li><a href='{esc(str(p.relative_to(folder)))}'>{esc(str(p.relative_to(folder / 'history')))}</a></li>" for p in historical)
    index = f"<!doctype html><html lang='en'><meta charset='utf-8'><title>Observation Lab documents</title><style>{style}</style><h1>Observation Lab</h1><p><a href='expansion-report.html'>Automatic retrieval expansion and quantity-preservation results</a></p><p><a href='architecture.html'>Architecture and reproduction guide</a></p><h2>Historical report exports</h2><p>These preserve earlier reports. Referenced raw files are retained in the verified historical archive; restore the archive for the exact original layout.</p><ul>{history_links}</ul></html>"
    (folder / "index.html").write_text(index)
    architecture = f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Observation Lab architecture</title><style>{style}</style><h1>Architecture and reproduction</h1><p><a href="index.html">All documents</a></p>
    <pre>expansion_lab/          compiler, evidence expansion, guard runtime, grader, storage
tests/                  regression, guard and storage tests
docs_lab/               unchanged v1/v2 reference code and frozen fixtures
qdrant_lab/             previous optional vector-search experiment
data/sources/*.json.gz  pinned official API documents
data/blobs/HH/HASH.gz   deduplicated compressed evidence and code snapshots
data/runs/ID/manifest.json  provenance and stage references
data/locks/             fresh-run code/prompt locks
data/history/           verified archive and per-member SHA-256 manifest
documents/generated/   all generated reports and document exports</pre>
    <p>The current expansion pipeline needs Python's standard library and a signed-in Codex CLI on PATH. Earlier FreshCtx/Temporal and Qdrant experiments have optional pinned dependencies in their own requirements files. No hosted Qdrant account, Stripe/Slack credentials or business API access is needed for these simulations.</p>
    <pre>python3 -m unittest discover -s tests -q
python3 -m expansion_lab.run --arm fixed --suite development --run-id unique-fixed
python3 -m expansion_lab.run --arm adaptive --suite development --run-id unique-adaptive --reuse-from unique-fixed
python3 -m expansion_lab.grade unique-adaptive
python3 -m expansion_lab.inspect unique-adaptive --kind plans
python3 -m expansion_lab.report --runs RUN_ID [RUN_ID ...]</pre>
    <p>Use a new run ID; completed and failed records are preserved. Optional --lock requires exact source/prompt/policy hashes. --reuse-from reuses only a completed stage with identical input, instructions and output schema, preserving its original exact prompt. The full arm supplies every endpoint name. Each adaptive run allows two recompile passes, with at most one rediscovery from the complete directory. This is a bounded prototype, not a production event-monitoring service.</p></html>'''
    (folder / "architecture.html").write_text(architecture)
    print("Generated", folder / "expansion-report.html")
    print(json.dumps(storage))


if __name__ == "__main__":
    main()
