# SPDX-License-Identifier: Apache-2.0
"""Replay original probes unchanged; evaluate fresh fixtures through the same runtime."""

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import quote, urlsplit, parse_qs

import docs_lab.audit as old_audit
from docs_lab.source import Index, ROOT as SOURCE_ROOT, get_json, parameters, response_schema
from observation_lab.schema import validate_shape
from .runtime import bind, bind_observations, evaluate, execute
from .schema import BATCH

ROOT = Path(__file__).parent


def envelope(body, *, status=200, next_page=None):
    return {"http_status": status, "body": copy.deepcopy(body), "headers_complete": True,
            "headers": {"link": f'<https://api.example.test/resource?page={next_page}>; rel="next"'} if next_page else {},
            "url": "https://api.example.test/resource"}


def run_plan(plan, context, transport, phase="after"):
    responses = {}
    for request in plan["requests"]:
        try:
            responses[request["id"]] = execute(request, context, transport, responses)
        except (ValueError, KeyError, TypeError) as exc:
            responses[request["id"]] = {"http_status": 0, "body": None, "collection_complete": False, "traversal_reason": str(exc),"trace":[]}
    return evaluate(plan, responses, context, phase), responses


def original_probes(tasks, plans):
    # Reuse the actual v1 test function and its literal expected outcomes. The
    # adapter supplies captured headers and executes v2 procedures on those same bodies.
    original_evaluator = old_audit.evaluate
    def adapter(plan, raw, context):
        responses = {}
        for request in plan["requests"]:
            source = raw.get(request["id"], {"http_status": 0, "body": None})
            if "/actions/runs/{run_id}/attempts/{attempt_number}" in request["operation"] and request["id"] not in raw:
                root_id=next((r["id"] for r in plan["requests"] if r["operation"]=="GET /repos/{owner}/{repo}/actions/runs/{run_id}"),None)
                source=raw.get(root_id,{"http_status":0,"body":None})
            def transport(call, source=source):
                body=copy.deepcopy(source.get("body"))
                if "/attempts/{attempt_number}" in call["operation"] and isinstance(body,dict):
                    attempt=call["bindings"].get("path",{}).get("attempt_number")
                    if attempt!=body.get("run_attempt"):
                        body.update(status="completed",conclusion="success",run_attempt=attempt)
                return envelope(body, status=source.get("http_status", 0))
            try:responses[request["id"]] = execute(request, context, transport, responses)
            except (KeyError,ValueError,TypeError):responses[request["id"]]={"http_status":0,"body":None,"collection_complete":False}
        return evaluate(plan, responses, context)
    try:
        old_audit.evaluate = adapter
        return old_audit.scenario_tests(tasks, plans)
    finally:
        old_audit.evaluate = original_evaluator


def preview_contract(plan, context):
    observations = []
    observed={r["id"]:{"collection_complete":True,"body":{"id":"si_goal","subscription":"sub_goal","quantity":5}}
              for r in plan["requests"] if r["operation"]=="GET /v1/subscription_items/{item}"}
    for request in plan["requests"]:
        if request["operation"] != "POST /v1/invoices/create_preview":
            continue
        try:binding = bind_observations(json.loads(request["bindings_json"]), context, observed)
        except (KeyError,ValueError,TypeError) as exc:
            observations.append({"request_id":request["id"],"matches_requested_change":False,"error":str(exc)})
            continue
        body = binding.get("body", {})
        detail = body.get("subscription_details", {})
        matches = (body.get("subscription") == context["subscription_id"]
                   and detail.get("proration_date") == context["proration_date"]
                   and any(item.get("id") == context["subscription_item_id"] and item.get("price") == context["new_price_id"]
                           for item in detail.get("items", [])) and body.get("preview_mode", "next") == "next")
        observations.append({"request_id": request["id"], "matches_requested_change": matches,
                             "preserves_observed_quantity":any(item.get("quantity")==5 for item in detail.get("items",[]))})
    return {"passed": bool(observations) and all(x["matches_requested_change"] for x in observations), "requests": observations}


def audit_pagination(index, request):
    issues=[];warnings=[];pagination=request["pagination"]
    entry=index.by_key.get(request["operation"])
    if not entry:return {"id":request["id"],"issues":[],"warnings":[]}
    schema=response_schema(index.spec,entry["operation"])
    if pagination["mode"]!="none":
        nodes=old_audit.nodes_at(index.spec,schema,old_audit.split_pointer(pagination["items_pointer"]))
        if not any(n.get("type")=="array" or "items" in n for n in nodes):issues.append("Pagination items pointer is not a documented array")
    if pagination["mode"]=="cursor":
        params=parameters(index.spec,entry["item"],entry["operation"])
        if not any(p.get("in")=="query" and p.get("name")==pagination["cursor_parameter"] for p in params):
            issues.append("Cursor query parameter is not documented")
        for name in ("next_cursor_pointer","has_more_pointer"):
            path=pagination[name]
            if path and not old_audit.nodes_at(index.spec,schema,old_audit.split_pointer(path)):
                warnings.append("Pagination signal needs narrative evidence: "+path)
    return {"id":request["id"],"issues":issues,"warnings":warnings}


def billing_probes(task, plan):
    ctx = task["context"]
    invoice = {"id": "in_goal", "object": "invoice", "status": "paid", "currency": "usd", "amount_due": 90000,
               "customer": "cus_goal", "parent": {"type": "subscription_details", "subscription_details": {"subscription": "sub_goal"}}}
    rows = []
    for name, phase, patch, expected in (("preview_ok", "before", {}, "ready"), ("preview_over", "before", {"amount_due":100001}, "blocked"),
                                        ("preview_currency", "before", {"currency":"eur"}, "blocked"), ("paid_invoice", "after", {}, "complete"),
                                        ("open_invoice", "after", {"status":"open"}, "pending"),
                                        ("wrong_subscription", "after", {"parent":{"type":"subscription_details","subscription_details":{"subscription":"sub_other"}}}, "pending")):
        def transport(call):
            op = call["operation"]
            body = copy.deepcopy(invoice) | patch
            if op == "POST /v1/invoices/create_preview":
                body["id"] = "upcoming_in_preview"
                return envelope(body)
            if op == "GET /v1/invoices/{invoice}":
                return envelope(body)
            if op == "GET /v1/invoices":
                query=call["bindings"].get("query",{})
                subscription=body.get("parent",{}).get("subscription_details",{}).get("subscription")
                matches=(query.get("subscription",subscription)==subscription and query.get("customer",body.get("customer"))==body.get("customer")
                         and query.get("status",body.get("status"))==body.get("status"))
                return envelope({"data":[body] if matches else [],"has_more":False})
            if op == "GET /v1/subscription_items/{item}":
                return envelope({"id":"si_goal","subscription":"sub_goal","quantity":5,"price":{"id":"price_old"}})
            if op.startswith("GET /v1/subscriptions/"):
                return envelope({"id":"sub_goal","customer":"cus_goal","latest_invoice":"in_goal","items":{"data":[{"id":"si_goal","price":{"id":"price_annual"}}]}})
            return envelope(None,status=404)
        result,responses=run_plan(plan,ctx,transport,phase)
        rows.append({"task_id":"r01","case":name,"phase":phase,"expected":expected,**result,"passed":result["status"]==expected,
                     "traversal":{k:{"complete":v["collection_complete"],"pages":len(v["trace"])} for k,v in responses.items()}})
    return rows


def fresh_probes(tasks, plans, private):
    rows=[]
    for identity,names in private["probes"].items():
        task,plan=tasks[identity],plans[identity];ctx=task["context"]
        for name in names:
            if name=="forbidden": expected="unknown"
            elif name in ("missing_cursor","page_budget","loop"):expected="unknown"
            elif identity=="f06":expected="unverifiable"
            elif name in ("success","empty_complete","empty_first_with_next","member_on_second_page","member_on_first_page"):expected="complete"
            else:expected="pending"
            call_counts={}
            def transport(call):
                op=call["operation"];call_counts[op]=call_counts.get(op,0)+1;n=call_counts[op]
                if name=="forbidden":return envelope({},status=403)
                if identity in ("f01","f06") and op.startswith("GET /v1/refunds"):
                    body={"id":"re_goal","status":"succeeded","currency":"usd","amount":2500,"charge":"ch_goal"}
                    changes={"wrong_id":{"id":"re_other"},"pending":{"status":"pending"},"wrong_currency":{"currency":"eur"},"wrong_amount":{"amount":2501},"wrong_charge":{"charge":"ch_other"}}
                    body.update(changes.get(name,{}))
                    query=call["bindings"].get("query",{})
                    listed=[body] if query.get("charge",body["charge"])==body["charge"] else []
                    return envelope({"data":listed,"has_more":False} if op=="GET /v1/refunds" else body)
                if identity=="f02" and op in ("GET /repos/{owner}/{repo}/pulls/{pull_number}","GET /repos/{owner}/{repo}/pulls"):
                    body={"number":42,"merged":True,"state":"closed","base":{"ref":"main"},"head":{"sha":ctx["head_sha"]}}
                    changes={"closed_unmerged":{"merged":False},"wrong_base":{"base":{"ref":"dev"}},"wrong_sha":{"head":{"sha":"e"*40}},"wrong_number":{"number":43}}
                    body.update(changes.get(name,{}))
                    if op.endswith("/pulls"):
                        state=call["bindings"].get("query",{}).get("state","open")
                        return envelope([body] if state in ("all",body["state"]) else [])
                    return envelope(body)
                if identity=="f03" and op=="GET /repos/{owner}/{repo}/milestones":
                    if call["bindings"].get("query",{}).get("state","open")!="open":return envelope({},status=400)
                    if name in ("page_budget","loop"):
                        return envelope([],next_page=2 if name=="loop" else n+1)
                    if name in ("open_on_second_page","empty_first_with_next") and n==1:return envelope([],next_page=2)
                    body=[{"id":2,"number":2,"state":"open","title":"Incomplete"}] if name=="open_on_second_page" else []
                    return envelope(body)
                if identity=="f04" and op=="GET /conversations.members":
                    if call["bindings"].get("query",{}).get("channel")!=ctx["channel_id"]:return envelope({"ok":False,"error":"channel_not_found"})
                    if name=="page_budget":return envelope({"ok":True,"members":["U_OTHER"],"response_metadata":{"next_cursor":f"token{n}"}})
                    if name=="missing_cursor":return envelope({"ok":True,"members":[],"response_metadata":{}})
                    second=name in ("member_on_second_page","absent_complete")
                    if second and n==1:return envelope({"ok":True,"members":["U_OTHER"],"response_metadata":{"next_cursor":"token2"}})
                    if second and call["bindings"].get("query",{}).get("cursor")!="token2":return envelope({"ok":False,"error":"invalid_cursor"})
                    members=[ctx["user_id"]] if name.startswith("member_on") else ["U_OTHER"]
                    return envelope({"ok":True,"members":members,"response_metadata":{"next_cursor":""}})
                if identity=="f05" and op in ("GET /repos/{owner}/{repo}/actions/artifacts/{artifact_id}","GET /repos/{owner}/{repo}/actions/artifacts"):
                    body={"id":77,"expired":False,"digest":ctx["digest"],"workflow_run":{"id":123}}
                    body.update({"expired":{"expired":True},"wrong_digest":{"digest":"sha256:"+"0"*64},"wrong_run":{"workflow_run":{"id":124}},"wrong_id":{"id":78}}.get(name,{}))
                    return envelope({"total_count":1,"artifacts":[body]} if op.endswith("/artifacts") else body)
                return envelope(None,status=404)
            result,responses=run_plan(plan,ctx,transport)
            rows.append({"task_id":identity,"case":name,"expected":expected,**result,"passed":result["status"]==expected,
                         "traversal":{key:{"complete":v["collection_complete"],"pages":len(v["trace"]),"reason":v["traversal_reason"]} for key,v in responses.items()}})
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory",type=Path,required=True)
    parser.add_argument("--live",action="store_true")
    args=parser.parse_args();folder=args.directory
    experiment=json.loads((folder/"experiment.json").read_text())
    for source in experiment["sources"]:
        if hashlib.sha256((SOURCE_ROOT/"sources"/f"{source['provider']}.json").read_bytes()).hexdigest()!=source["sha256"]:
            raise ValueError("API source snapshot changed")
    fixtures=Path(experiment["fixture_directory"])
    manifest=experiment["fixture_manifest"]
    for name in ("public.json","private.json"):
        if hashlib.sha256((fixtures/name).read_bytes()).hexdigest()!=manifest[name]:raise ValueError("Fixture changed")
    tasks={t["id"]:t for t in json.loads((fixtures/"public.json").read_text())["tasks"]}
    batch=json.loads((folder/"plans.json").read_text());validate_shape(batch,BATCH)
    for number in (1,2):
        stage=folder/f"round-{number}"
        metadata=json.loads((stage/"metadata.json").read_text())
        if metadata["status"]!="completed" or hashlib.sha256((stage/"plans.json").read_bytes()).hexdigest()!=metadata["plans_sha256"]:
            raise ValueError("Incomplete or modified inference")
    if batch!=json.loads((folder/"round-2/plans.json").read_text()):raise ValueError("Model output was changed")
    plans={p["task_id"]:p for p in batch["plans"]}
    if set(plans)!=set(tasks):raise ValueError("Missing task")
    documents=json.loads((folder/"round-2/input.json").read_text())["selected_schema_cards"]
    indexes={p:Index(p) for p in {t["provider"] for t in tasks.values()}}
    structural=[]
    for identity,plan in plans.items():
        index=indexes[tasks[identity]["provider"]]
        req={r["id"]:r for r in plan["requests"]}
        requests=[old_audit.audit_request(index,r,tasks[identity],documents) for r in plan["requests"]]
        checks=[old_audit.audit_check(index,c,req.get(c["request_id"])) for c in plan["checks"]]
        pagination=[audit_pagination(index,r) for r in plan["requests"]]
        structural.append({"task_id":identity,"requests":requests,"checks":checks,"pagination":pagination,"issue_count":sum(len(r["issues"]) for r in requests+checks+pagination)})
    if experiment["suite"]=="development":
        original=original_probes(tasks,plans);extra=billing_probes(tasks["r01"],plans["r01"])
        contract=preview_contract(plans["r01"],tasks["r01"]["context"])
        rows=original+extra
    else:
        original=[];extra=[];contract=None
        rows=fresh_probes(tasks,plans,json.loads((fixtures/"private.json").read_text()))
    old_result=json.loads((folder/"audit.json").read_text()) if (folder/"audit.json").exists() else {}
    live=old_result.get("live",{"executed":False})
    if args.live and experiment["suite"]=="development":
        task,plan=tasks["r05"],plans["r05"]
        permitted="GET /repos/{owner}/{repo}/actions/runs/{run_id}"
        request=next((r for r in plan["requests"] if r["operation"]==permitted),None)
        if request:
            binding=bind(json.loads(request["bindings_json"]),task["context"])
            expected={k:task["context"][k] for k in ("owner","repo","run_id")}
            if binding.get("path")!=expected or request["pagination"]["mode"]!="none":raise ValueError("Live request not exactly permitted")
            url=f"https://api.github.com/repos/cli/cli/actions/runs/{int(expected['run_id'])}"
            body,raw,_=get_json(url);(folder/"live-response.json").write_bytes(raw)
            def transport(call):
                if call["operation"]!=permitted:return envelope(None,status=404)
                return envelope(body)
            result,_=run_plan(plan,task["context"],transport)
            live={"executed":True,"planner_generated":True,"url":url,"authenticated":False,"at":datetime.now(timezone.utc).isoformat(),
                  "response_sha256":hashlib.sha256(raw).hexdigest(),"result":result}
    output={"suite":experiment["suite"],"at":datetime.now(timezone.utc).isoformat(),"structural":structural,"scenarios":rows,
            "original_probe_passed":sum(r["passed"] for r in original),"original_probe_total":len(original),
            "preview_contract":contract,"live":live,
            "summary":{"passed":sum(r["passed"] for r in rows),"total":len(rows),"structural_issues":sum(r["issue_count"] for r in structural)},
            "task_summaries":{identity:{"passed":sum(r["passed"] for r in rows if r["task_id"]==identity),"total":sum(r["task_id"]==identity for r in rows)} for identity in tasks},
            "grader_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (folder/"audit.json").write_text(json.dumps(output,indent=2)+"\n")
    print(json.dumps(output["summary"]));print("Original probes:",output["original_probe_passed"],"/",output["original_probe_total"])
    if contract:print("Preview binds exact action:",contract["passed"])
    for r in rows:
        if not r["passed"]:print("FAIL",r["task_id"],r["case"],r["expected"],r["status"])


if __name__=="__main__":main()
