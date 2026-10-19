# SPDX-License-Identifier: Apache-2.0
"""Compare development rerun and a single frozen fresh-task run."""

import argparse
from html import escape
import json
from pathlib import Path

from observation_lab.report import table


def e(value):return escape(str(value),quote=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development",type=Path,default=Path("results/docs/v2-development-4"))
    parser.add_argument("--fresh",type=Path,default=Path("results/docs/v2-fresh"))
    parser.add_argument("--output",type=Path,default=Path("results/docs/v2-report.html"))
    args=parser.parse_args()
    suites=[]
    for folder in (args.development,args.fresh):
        experiment=json.loads((folder/"experiment.json").read_text())
        audit=json.loads((folder/"audit.json").read_text())
        plans=json.loads((folder/"plans.json").read_text())
        tasks=json.loads((Path(experiment["fixture_directory"])/"public.json").read_text())["tasks"]
        suites.append((folder,experiment,audit,plans,tasks))
    dev,fresh=suites
    overview=table(["Evaluation","Observed behavior","What the number means"],[
        ["Original fourteen probes",f"{dev[2]['original_probe_passed']}/{dev[2]['original_probe_total']} (v1: 0/14 full-plan states)","Exact original test function and expected outcomes; v2 executes pagination with captured headers."],
        ["Added billing probes",f"{sum(r['passed'] for r in dev[2]['scenarios'] if r['task_id']=='r01')}/6",f"Exact action bindings separately verified: {dev[2]['preview_contract']['passed']}. These probes were added during development."],
        ["Fresh tasks",f"{fresh[2]['summary']['passed']}/{fresh[2]['summary']['total']}","Six new tasks; first five use new resource families, sixth requests unavailable bank-credit proof. Frozen before v2 implementation; no model-output repairs."],
        ["Detected structural reference issues",f"Development: {dev[2]['summary']['structural_issues']}; fresh: {fresh[2]['summary']['structural_issues']}","Operation/parameter/response-field name audit. Not full schema, policy, authorization or semantic verification."]])
    task_rows=[];details=[]
    for folder,experiment,audit,batch,tasks in suites:
        plans={p["task_id"]:p for p in batch["plans"]}
        for task in tasks:
            identity=task["id"];summary=audit["task_summaries"][identity]
            plan=plans[identity]
            assessment=f"{summary['passed']}/{summary['total']} probes" if summary['total'] else "Coverage review; no behavior probes"
            gaps=[r["text"]+": "+r["limitation"] for r in plan["requirements"] if r["coverage"]!="observable"]
            task_rows.append([e(experiment["suite"]),e(identity),e(task["task"]),e(assessment),e("; ".join(gaps))])
            cases=[r for r in audit["scenarios"] if r["task_id"]==identity]
            case_table=table(["Probe","Expected","Actual","Matches","Traversal"],[
                [e(r["case"]),e(r["expected"]),e(r["status"]),str(r["passed"]),e(json.dumps(r.get("traversal",{})))] for r in cases])
            details.append(f"<details><summary>{e(experiment['suite'])}/{e(identity)} — {e(assessment)}</summary>{case_table}<h3>Untouched model plan</h3><pre>{e(json.dumps(plan,indent=2))}</pre></details>")
    live=dev[2]["live"]
    http_probe=json.loads(Path("results/docs/v2-http-pagination.json").read_text())
    usage_rows=[]
    for folder,experiment,audit,batch,tasks in suites:
        for number in (1,2):
            m=json.loads((folder/f"round-{number}/metadata.json").read_text())
            usage_rows.append([e(experiment["suite"]),str(number),str(sum(u.get("input_tokens",0) for u in m["usage"])),str(sum(u.get("output_tokens",0) for u in m["usage"])),str(m["elapsed_seconds"])])
    paths=[(folder.name,folder) for folder,*_ in suites]
    links="".join(f"<li>{e(name)}: <a href='{e(name)}/plans.json'>plans</a> · <a href='{e(name)}/audit.json'>audit and probes</a> · <a href='{e(name)}/experiment.json'>code/source hashes</a> · <a href='{e(name)}/round-2/input.json'>exact planning input</a></li>" for name,folder in paths)
    html=f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Retrieval and pagination: rerun and fresh tasks</title><style>body{{margin:0;background:#f8faf9;color:#192829;font:16px/1.6 system-ui,sans-serif}}main{{max-width:1160px;margin:auto;padding:40px 24px 80px}}h1{{font-size:38px;line-height:1.2}}h2{{margin-top:40px}}p,li{{max-width:940px}}.scope{{padding:18px;background:#eaf1ee;border-left:4px solid #3d7463}}table{{border-collapse:collapse;width:100%;font-size:14px;background:white}}td,th{{border:1px solid #d5dfd9;padding:11px;text-align:left;vertical-align:top}}th{{background:#eaf0ed}}.scroll{{overflow-x:auto}}small{{color:#5d6e64}}code{{overflow-wrap:anywhere}}a{{color:#146270}}details{{padding:18px 0;border-bottom:1px solid #cad8d0}}summary{{cursor:pointer;font-weight:600}}pre{{padding:18px;background:#edf1ef;overflow:auto;font-size:12px;max-height:650px}}</style></head><body><main>
<p>Development rerun and frozen fresh-task evaluation</p><h1>Retrieve the right operation, then finish observing it</h1>
<div class="scope">The original task text and private criteria remain unchanged. The revised planner sees the complete directory of 2,028 official operations, selects operations and inline schema branches, then emits executable pagination descriptors. The fresh set was frozen before v2 implementation; code/prompts/guides were locked before fresh inference. Both sets share providers and the same experiment author. This is a small development/generalization experiment, not an independent production benchmark.</div>
<h2>Results</h2>{overview}
<h2>Changes under test</h2><ol><li><strong>Endpoint discovery is separate from schema reading.</strong> Search ranking is a hint, never a hard exclusion. At this corpus size, the model receives every operation name and a short title. This directly removes the earlier top-two recall cutoff; it does not establish a scalable retrieval architecture.</li><li><strong>Read exact schema branches.</strong> Every child field name is retained at depth boundaries, with a cursor for further reading. Inline request bodies can be addressed directly. Broad root reads stay shallow rather than recursively expanding every linked resource.</li><li><strong>Pagination executes.</strong> HTTP Link, explicit cursor and has-more/last-item cursors run through an injected transport. Missing evidence, repeated continuation, cross-origin links, HTTP errors and page caps keep observations incomplete.</li><li><strong>Absence requires complete evidence.</strong> Complete empty collections can establish count zero. A partially observed empty page cannot. These are protocol-traversal claims, not atomic snapshots or guarantees about a changing world.</li></ol>
<h2>Task-level results and unresolved coverage</h2>{table(['Suite','ID','Task','Behavior probes','Declared gaps'],task_rows)}
<h2>Generated public GET</h2><p>Executed: {e(live.get('executed'))}. Planner-generated: {e(live.get('planner_generated'))}. Result: <strong>{e(live.get('result',{}).get('status'))}</strong>. <a href='{e(dev[0].name)}/live-response.json'>Recorded response</a>. Only the exact original public Actions-run GET was eligible for live execution; all billing, private Slack and other response changes were fixtures.</p>
<p>An additional independent live pagination probe traversed {len(http_probe['result']['trace'])} public GitHub pages and correctly reported incomplete evidence at its two-page cap. Probe passed: {e(http_probe['passed'])}. It was not generated by the planner and is excluded from task scores. <a href="v2-http-pagination.json">Raw HTTP pagination evidence</a>.</p>
<h2>Inspect every probe and generated plan</h2>{''.join(details)}
<h2>Measured inference usage</h2>{table(['Suite','Stage','Input tokens','Output tokens','Seconds'],usage_rows)}
<p>The development discovery was reused from a completed attempt, not called again. Earlier development attempts are preserved: one failed task-count validation; one exceeded the CLI input-size limit; a completed planning run then exposed missing response-to-request bindings. A general ordered data-binding primitive was added before the final development plan and before fresh inference. One added billing fixture was corrected to honor server-side subscription filters. The original fourteen probes and expected outcomes were unchanged. No private probe result was sent to a model. The fresh run uses the locked pipeline once; any fresh failures remain visible.</p>
<h2>Qdrant</h2><p>Qdrant was evaluated as an architectural option, not installed or benchmarked. The fix tested here changes discovery granularity, schema navigation and pagination. A vector-search backend can later be compared against this baseline; no Qdrant retrieval improvement is claimed. <a href="https://qdrant.tech/documentation/search/hybrid-queries/">Official hybrid-query documentation</a>.</p>
<h2>Scope</h2><p>The fresh tasks use new endpoint families within the same three providers; this does not demonstrate transfer to a new provider. The author saw the fresh task definitions while building the general runtime, so this is not a blinded independent evaluation. API definitions and pagination guides are real; response perturbations are synthetic. One generation per stage is insufficient to estimate reliability or statistical significance. Source age, policy, authentication, rate entitlement, physical-world outcomes and cross-read atomicity remain separate concerns.</p>
<h2>Reproduce</h2><pre>.venv/bin/python -m docs_lab.v2.grade --directory {e(dev[0])}
.venv/bin/python -m docs_lab.v2.grade --directory {e(fresh[0])}
.venv/bin/python -m docs_lab.v2.report
.venv/bin/python -m unittest discover -s tests -v</pre><p>These commands replay saved plans offline; live execution requires <code>--live</code> on the development audit.</p>
<h2>Evidence</h2><ul>{links}<li><a href="v2-lock.json">Pre-fresh code/prompt/guide lock</a></li><li><a href="v2-development/round-1/metadata.json">Rejected incomplete development inference</a></li><li><a href="v2-development-2/round-2/metadata.json">Oversize development input failure</a></li><li><a href="v1/report.html">Original real-document experiment</a></li><li><a href="../../README.md">README</a></li></ul></main></body></html>"""
    args.output.write_text(html);print("Saved",args.output)


if __name__=="__main__":main()
