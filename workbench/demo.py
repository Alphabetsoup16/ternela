# SPDX-License-Identifier: Apache-2.0
"""Explicit simulated provider snapshots for the already-seen regression tasks."""
import json
from expansion_lab.storage import PROJECT, Store
from .engine import phase_plan

NAMES = {"n01":"Change a subscription price", "n02":"Wait for a payment", "n03":"Verify a release", "n04":"Wait for a check run", "n05":"Check workspace membership", "n06":"Ask for an impossible guarantee"}
CASES = {
 "n01":["success","quantity_11","quantity_0","quantity_string","wrong_item","wrong_subscription","over_budget","wrong_currency","forbidden"],
 "n02":["success","processing","wrong_amount","wrong_customer","wrong_currency","forbidden","missing_response"],
 "n03":["success","draft","prerelease","wrong_sha","wrong_tag","forbidden"],
 "n04":["success","queued","failure","wrong_sha","missing_conclusion","forbidden"],
 "n05":["success","deleted","bot","wrong_team","missing_field","forbidden"],
 "n06":["success","forbidden"]}


def catalog():
    store = Store()
    result=json.loads((PROJECT/"examples/catalog.json").read_text())
    # Completed user compilations remain discoverable after service/page restarts.
    for path in sorted((PROJECT/"data/runs").glob("task-*/manifest.json"),key=lambda p:p.stat().st_mtime,reverse=True)[:20]:
        manifest=json.loads(path.read_text())
        if manifest.get("status")!="completed": continue
        task=json.loads((PROJECT/manifest["fixtures"]/"public.json").read_text())["tasks"][0]
        plan=store.get(manifest["plans"])["plans"][0]
        phases={c["phase"] for c in plan["checks"]}
        result.append({"task":task,"plan":plan,"name":"Your task: "+task["task"].split(".")[0][:45],"cases":[],"phase":"before" if phases=={"before"} else "after","run":manifest["id"],"mode":"caller-supplied snapshot"})
    return result


def snapshots(task, plan, case, phase):
    identity, ctx = task["id"], task["context"]
    if case not in CASES.get(identity,[]): raise ValueError("Unknown replay case")
    if identity == "n01":
        quantity = 11 if case=="quantity_11" else 0 if case=="quantity_0" else "5" if case=="quantity_string" else 3
        item = {"id":"si_other" if case=="wrong_item" else ctx["subscription_item_id"], "subscription":"sub_other" if case=="wrong_subscription" else ctx["subscription_id"],"quantity":quantity}
        preview = {"id":"upcoming_preview","customer":ctx["customer_id"],"parent":{"type":"subscription_details","subscription_details":{"subscription":ctx["subscription_id"],"subscription_proration_date":ctx["proration_date"]}},"currency":"eur" if case=="wrong_currency" else "usd","amount_due":ctx["budget_cents"]+1 if case=="over_budget" else ctx["budget_cents"]-1}
    elif identity in ["n02","n06"]:
        body = {"id":ctx["payment_intent_id"],"status":"processing" if case=="processing" else "succeeded","currency":"eur" if case=="wrong_currency" else "usd","amount_received":4199 if case=="wrong_amount" else ctx.get("amount_cents",4200),"customer":"cus_other" if case=="wrong_customer" else ctx.get("customer_id","cus_new")}
    elif identity=="n03":
        body = {"id":ctx["release_id"],"tag_name":"v0" if case=="wrong_tag" else ctx["tag"],"draft":case=="draft","prerelease":case=="prerelease","target_commitish":"0"*40 if case=="wrong_sha" else ctx["sha"]}
    elif identity=="n04":
        body = {"id":ctx["check_run_id"],"head_sha":"0"*40 if case=="wrong_sha" else ctx["head_sha"],"status":"queued" if case=="queued" else "completed","conclusion":"failure" if case=="failure" else "success"}
        if case=="missing_conclusion": body.pop("conclusion")
    else:
        user={"id":ctx["user_id"],"team_id":"T_OTHER" if case=="wrong_team" else ctx["team_id"],"deleted":case=="deleted","is_bot":case=="bot"}
        if case=="missing_field": user.pop("is_bot")
        body={"ok":True,"user":user}
    responses = {}
    for request in phase_plan(plan,phase)["requests"]:
        if identity=="n01":
            op=request["operation"]
            value = preview if op=="POST /v1/invoices/create_preview" else {"id":ctx["subscription_id"],"items":{"data":[item]}} if op.startswith("GET /v1/subscriptions/") else {"data":[item],"has_more":False} if op=="GET /v1/subscription_items" else item
        else: value=body
        responses[request["id"]]={"http_status":403 if case=="forbidden" else 0 if case=="missing_response" else 200,"body":{} if case=="forbidden" else None if case=="missing_response" else value,"headers_complete":True,"headers":{}}
    return responses
