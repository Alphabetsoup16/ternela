# SPDX-License-Identifier: Apache-2.0
"""Known synthetic regression probes; never fed back into task compilation."""

import copy

from docs_lab.source import resolve, response_schema
from docs_lab.v2 import grade as legacy
from safety_lab.runtime import run_plan


def quantity_probes(task, plan):
    names = [
        "quantity_1",
        "quantity_3",
        "quantity_11",
        "quantity_0",
        "quantity_missing",
        "quantity_string",
        "quantity_boolean",
        "wrong_item",
        "wrong_subscription",
        "forbidden",
        "over_budget",
        "wrong_currency",
    ]
    rows = []
    ctx = task["context"]
    for name in names:
        value = (
            int(name.split("_")[1])
            if name in ["quantity_1", "quantity_3", "quantity_11", "quantity_0"]
            else "5"
            if name == "quantity_string"
            else True
            if name == "quantity_boolean"
            else 5
        )
        invalid = name in [
            "quantity_missing",
            "quantity_string",
            "quantity_boolean",
            "wrong_item",
            "wrong_subscription",
            "forbidden",
        ]
        expected = (
            "unknown"
            if invalid
            else "blocked"
            if name in ["over_budget", "wrong_currency"]
            else "ready"
        )
        calls = []
        body = {
            "id": ctx["subscription_item_id"],
            "subscription": ctx["subscription_id"],
            "quantity": value,
            "price": {"id": "price_old"},
        }
        if name == "quantity_missing":
            body.pop("quantity")
        if name == "wrong_item":
            body["id"] = "si_other"
        if name == "wrong_subscription":
            body["subscription"] = "sub_other"

        def transport(call):
            calls.append(copy.deepcopy(call))
            if call["operation"] == "POST /v1/invoices/create_preview":
                return legacy.envelope(
                    {
                        "id": "upcoming_preview",
                        "customer": ctx["customer_id"],
                        "parent": {
                            "type": "subscription_details",
                            "subscription_details": {
                                "subscription": ctx["subscription_id"],
                                "subscription_proration_date": ctx["proration_date"],
                            },
                        },
                        "currency": "eur" if name == "wrong_currency" else "usd",
                        "amount_due": (
                            ctx["budget_cents"] + 1
                            if name == "over_budget"
                            else ctx["budget_cents"] - 1
                        ),
                    }
                )
            if name == "forbidden":
                return legacy.envelope({}, status=403)
            if call["operation"] == "GET /v1/subscription_items/{item}":
                if call["bindings"].get("path", {}).get("item") != ctx["subscription_item_id"]:
                    return legacy.envelope({}, status=404)
                return legacy.envelope(body)
            if call["operation"] == "GET /v1/subscription_items":
                return legacy.envelope(
                    {
                        "data": [
                            {
                                "id": "si_other",
                                "subscription": ctx["subscription_id"],
                                "quantity": 99,
                            },
                            body,
                        ],
                        "has_more": False,
                    }
                )
            if call["operation"].startswith("GET /v1/subscriptions/"):
                return legacy.envelope(
                    {
                        "id": ctx["subscription_id"],
                        "customer": ctx["customer_id"],
                        "items": {"data": [{"id": "si_other", "quantity": 99}, body]},
                    }
                )
            return legacy.envelope(None, status=404)

        result, observed = run_plan(plan, ctx, transport, "before")
        previews = [c for c in calls if c["operation"] == "POST /v1/invoices/create_preview"]
        sent = [
            i.get("quantity")
            for c in previews
            for i in c["bindings"].get("body", {}).get("subscription_details", {}).get("items", [])
            if i.get("id") == ctx["subscription_item_id"]
        ]
        copy_ok = len(sent) == 1 and type(sent[0]) is int and sent[0] == value
        # An invalid source must stop the dependent preview, regardless of status.
        contract_ok = not previews if invalid else copy_ok
        rows.append(
            {
                "task_id": task["id"],
                "case": name,
                "expected": expected,
                **result,
                "passed": result["status"] == expected and contract_ok,
                "preview_calls": len(previews),
                "sent_quantities": sent,
                "preservation_contract_ok": contract_ok,
            }
        )
    return rows


def new_probes(tasks, plans, private, indexes):
    rows = []
    for identity, names in private["probes"].items():
        task, plan = tasks[identity], plans[identity]
        if identity == "n01":
            rows += quantity_probes(task, plan)
            continue
        ctx = task["context"]
        for name in names:
            expected = (
                "unverifiable"
                if identity == "n06"
                else (
                    "unknown"
                    if name in ["forbidden", "missing_response"]
                    else "complete"
                    if name == "success"
                    else "pending"
                )
            )
            if identity in ["n02", "n06"]:
                body = {
                    "id": ctx["payment_intent_id"],
                    "status": "succeeded",
                    "currency": "usd",
                    "amount_received": ctx.get("amount_cents", 4200),
                    "customer": "cus_new",
                }
                body.update(
                    {
                        "processing": {"status": "processing"},
                        "wrong_id": {"id": "pi_other"},
                        "wrong_currency": {"currency": "eur"},
                        "wrong_amount": {"amount_received": 4199},
                        "wrong_customer": {"customer": "cus_other"},
                    }.get(name, {})
                )
                resource = "#/components/schemas/payment_intent"
            elif identity == "n03":
                body = {
                    "id": 202,
                    "tag_name": ctx["tag"],
                    "draft": False,
                    "prerelease": False,
                    "target_commitish": ctx["sha"],
                }
                body.update(
                    {
                        "draft": {"draft": True},
                        "prerelease": {"prerelease": True},
                        "wrong_tag": {"tag_name": "v0"},
                        "wrong_sha": {"target_commitish": "0" * 40},
                        "wrong_id": {"id": 203},
                    }.get(name, {})
                )
                resource = "#/components/schemas/release"
            elif identity == "n04":
                body = {
                    "id": 501,
                    "head_sha": ctx["head_sha"],
                    "status": "completed",
                    "conclusion": "success",
                }
                body.update(
                    {
                        "queued": {"status": "queued", "conclusion": None},
                        "failure": {"conclusion": "failure"},
                        "wrong_sha": {"head_sha": "0" * 40},
                        "wrong_id": {"id": 502},
                    }.get(name, {})
                )
                if name == "missing_conclusion":
                    body.pop("conclusion")
                # Missing required evidence is unknown, rather than a known failure.
                if name == "missing_conclusion":
                    expected = "unknown"
                resource = "#/components/schemas/check-run"
            else:
                body = {
                    "id": ctx["user_id"],
                    "team_id": ctx["team_id"],
                    "deleted": False,
                    "is_bot": False,
                }
                body.update(
                    {
                        "deleted": {"deleted": True},
                        "bot": {"is_bot": True},
                        "wrong_id": {"id": "U_OTHER"},
                        "wrong_team": {"team_id": "T_OTHER"},
                    }.get(name, {})
                )
                resource = None
            unsupported = []

            def transport(call):
                if name == "forbidden":
                    return legacy.envelope({}, status=403)
                if name == "missing_response":
                    return legacy.envelope(None, status=0)
                index = indexes[task["provider"]]
                entry = index.by_key.get(call["operation"])
                if entry and entry["method"] == "get":
                    bound = call["bindings"].get("path", {})
                    if any(key in ctx and bound.get(key) != ctx[key] for key in ["owner", "repo"]):
                        return legacy.envelope({}, status=404)
                    expected_id = ctx.get(
                        "payment_intent_id", ctx.get("release_id", ctx.get("check_run_id"))
                    )
                    last = entry["path"].rsplit("/", 1)[-1]
                    if last.startswith("{"):
                        parameter = last[1:-1]
                        if parameter not in ["owner", "repo"] and bound.get(parameter) not in [
                            expected_id,
                            ctx.get("tag"),
                        ]:
                            return legacy.envelope({}, status=404)
                    schema = response_schema(index.spec, entry["operation"])
                    if resource and schema.get("$ref") == resource:
                        return legacy.envelope(body)
                    resolved = resolve(index.spec, schema)
                    if resource and resolved.get("items", {}).get("$ref") == resource:
                        return legacy.envelope([body])
                    for key, child in resolved.get("properties", {}).items():
                        child = resolve(index.spec, child)
                        if resource and child.get("items", {}).get("$ref") == resource:
                            result = {key: [body]}
                            if task["provider"] == "stripe":
                                result["has_more"] = False
                            else:
                                result["total_count"] = 1
                            return legacy.envelope(result)
                    if task["provider"] == "slack" and call["operation"] == "GET /users.info":
                        if call["bindings"].get("query", {}).get("user") != ctx["user_id"]:
                            return legacy.envelope({"ok": False, "error": "user_not_found"})
                        return legacy.envelope({"ok": True, "user": body})
                    if task["provider"] == "slack" and call["operation"] == "GET /users.list":
                        return legacy.envelope(
                            {
                                "ok": True,
                                "members": [body],
                                "response_metadata": {"next_cursor": ""},
                            }
                        )
                unsupported.append(call["operation"])
                return legacy.envelope(None, status=404)

            result, responses = run_plan(plan, ctx, transport)
            rows.append(
                {
                    "task_id": identity,
                    "case": name,
                    "expected": expected,
                    **result,
                    "passed": result["status"] == expected,
                    "unsupported_fixture_operations": unsupported,
                }
            )
    return rows


def score(rows):
    return {
        "passed": sum(r["passed"] for r in rows),
        "total": len(rows),
        "false_ready_or_complete": sum(
            not r["passed"]
            and r["status"] in ["ready", "complete"]
            and r["status"] != r["expected"]
            for r in rows
        ),
    }
