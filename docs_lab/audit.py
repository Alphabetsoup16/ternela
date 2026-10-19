# SPDX-License-Identifier: Apache-2.0
"""Mechanical OpenAPI audit plus preregistered behavioral probes.

This does not grade arbitrary semantic claims as true from schema validity.
Policy/meaning claims need a separately recorded human review against docs.
"""

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote, urlencode

from observation_lab.schema import validate_shape
from .runtime import bind, evaluate
from .schema import BATCH
from .source import Index, ROOT, get_json, parameters, pointer, request_schema, resolve, response_schema


def nodes_at(spec, schema, path, seen=()):
    """Walk a response/request JSON pointer through OpenAPI refs and unions."""
    if not isinstance(schema, dict):
        return []
    ref = schema.get("$ref")
    marker = (ref, path)
    if ref and marker in seen:
        return []
    schema = resolve(spec, schema)
    seen = seen + (marker,)
    if not path:
        return [schema]
    token, *rest = path
    remaining = rest
    nodes = []
    for union in ("oneOf", "anyOf", "allOf"):
        for branch in schema.get(union, []):
            nodes.extend(nodes_at(spec, branch, path, seen))
    if token in schema.get("properties", {}):
        nodes.extend(nodes_at(spec, schema["properties"][token], remaining, seen))
    if (token == "*" or token.isdigit()) and "items" in schema:
        items = schema["items"]
        for item in items if isinstance(items, list) else [items]:
            nodes.extend(nodes_at(spec, item, remaining, seen))
    return nodes


def split_pointer(path):
    if path == "":
        return []
    if not path.startswith("/"):
        raise ValueError("Not a JSON pointer")
    return [p.replace("~1", "/").replace("~0", "~") for p in path[1:].split("/")]


def validate_body(spec, schema, value, prefix=""):
    issues = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = prefix + "/" + key.replace("~", "~0").replace("/", "~1")
            nodes = nodes_at(spec, schema, split_pointer(path))
            if not nodes:
                issues.append("Undocumented request field " + path)
            issues += validate_body(spec, schema, child, path)
    elif isinstance(value, list):
        for item in value:
            issues += validate_body(spec, schema, item, prefix + "/*")
    return issues


def audit_request(index, request, task, available_documents):
    issues, warnings = [], []
    key = request["operation"]
    if key not in index.by_key:
        return {"id": request["id"], "operation": key, "issues": ["Operation does not exist in official specification"], "warnings": []}
    entry = index.by_key[key]
    operation = entry["operation"]
    if task["provider"] + "::" + key not in available_documents:
        warnings.append("Operation exists but its excerpt was not retrieved for the model")
    try:
        binding = bind(json.loads(request["bindings_json"]), task["context"])
        if not isinstance(binding, dict) or set(binding) - {"path", "query", "body", "header"}:
            raise ValueError("Bindings must use path/query/body/header objects")
        docs = parameters(index.spec, entry["item"], operation)
        documented = {(p.get("in"), p["name"]): p for p in docs}
        for location in ("path", "query", "header"):
            supplied = binding.get(location, {})
            if not isinstance(supplied, dict):
                raise ValueError("Binding location must be an object")
            for name in supplied:
                if (location, name) not in documented:
                    # Standard auth/version/content headers aren't operation parameters.
                    if location == "header" and name.lower() in {"accept", "authorization", "content-type", "stripe-version", "x-github-api-version"}:
                        continue
                    issues.append(f"Undocumented {location} parameter {name}")
        for p in docs:
            if p.get("required") and p.get("in") in ("path", "query") and p["name"] not in binding.get(p["in"], {}):
                # Swagger's required token is auth infrastructure, not a literal planner secret.
                if p["name"] != "token":
                    issues.append(f"Missing required {p['in']} parameter {p['name']}")
        expected_path_names = set(re.findall(r"\{([^}]+)\}", entry["path"]))
        if not expected_path_names <= binding.get("path", {}).keys():
            issues.append("Not every URL path placeholder is bound")
        if "body" in binding:
            body_schema = request_schema(index.spec, operation)
            if body_schema:
                issues += validate_body(index.spec, body_schema, binding["body"])
                for required in resolve(index.spec, body_schema).get("required", []):
                    if required not in binding["body"]:
                        issues.append("Missing required request body field " + required)
            else:
                for key in binding["body"]:
                    if ("formData", key) not in documented and not any(p.get("in") == "body" for p in docs):
                        issues.append("Undocumented body/form parameter " + key)
        if entry["method"] != "get":
            warnings.append("Non-GET proposed observation: semantic read-only review required; never executed by this lab")
        if not request["auth_notes"]:
            warnings.append("No authentication/access notes supplied")
    except (KeyError, TypeError, ValueError) as exc:
        issues.append("Invalid binding: " + str(exc))
        binding = None
    return {"id": request["id"], "operation": request["operation"], "issues": issues,
            "warnings": warnings, "resolved_bindings": binding}


def audit_check(index, check, request):
    issues = []
    if request is None or request["operation"] not in index.by_key:
        return {"id": check["id"], "issues": ["Check references missing/unknown operation"]}
    entry = index.by_key[request["operation"]]
    schema = response_schema(index.spec, entry["operation"])
    try:
        collection_path = split_pointer(check["collection_pointer"])
        collection_nodes = nodes_at(index.spec, schema, collection_path)
        if not collection_nodes:
            issues.append("Undocumented response collection " + check["collection_pointer"])
        is_array = any("items" in node or node.get("type") == "array" for node in collection_nodes)
        base = collection_path + (["*"] if is_array else [])
        candidates = {"field": check["field_pointer"]}
        selector = json.loads(check["selector_json"])
        if not isinstance(selector, dict):
            raise ValueError("Selector must be an object")
        candidates.update({"selector " + key: key for key in selector})
        if check["select_mode"] == "latest":
            candidates["order_by"] = check["order_by"]
            if not check["order_by"]:
                issues.append("Latest selection requires an order_by field")
        for label, path in candidates.items():
            if not nodes_at(index.spec, schema, base + split_pointer(path)):
                issues.append("Undocumented response " + label + " " + path)
        json.loads(check["value_json"])
    except (KeyError, TypeError, ValueError) as exc:
        issues.append("Invalid check expression: " + str(exc))
    return {"id": check["id"], "issues": issues}


def scenario_tests(tasks, plans):
    output = []
    for identity in ("r03", "r05"):
        task, plan = tasks[identity], plans[identity]
        context = task["context"]
        scenarios = []
        if identity == "r05":
            good = {"id": context["run_id"], "head_sha": context["head_sha"], "status": "completed", "conclusion": "success", "run_attempt": 3}
            for name, patch, expected in (("success", {}, "complete"), ("running", {"status": "in_progress", "conclusion": None}, "pending"),
                                           ("failed", {"conclusion": "failure"}, "pending"), ("wrong_sha", {"head_sha": "b" * 40}, "pending"),
                                           ("wrong_run", {"id": 999}, "pending"), ("no_conclusion", {"conclusion": None}, "pending")):
                scenarios.append((name, good | patch, expected))
        else:
            good = {"id": 1001, "sha": context["sha"], "environment": "production"}
            scenarios = [("latest_success", "success", "complete"), ("old_success_new_failure", "failure", "pending"),
                         ("wrong_sha", "sha", "pending"), ("wrong_environment", "environment", "pending")]
        for name, state, expected in scenarios:
            responses = {}
            for request in plan["requests"]:
                operation = request["operation"]
                if identity == "r05" and "/actions/runs/{run_id}" in operation and "/attempts/" not in operation:
                    body = state
                elif identity == "r03" and operation.endswith("/statuses"):
                    body = [{"id": 11, "state": "success", "created_at": "2026-10-18T10:00:00Z"},
                            {"id": 12, "state": "failure" if state == "failure" else "success", "created_at": "2026-10-18T11:00:00Z"}]
                elif identity == "r03" and operation.endswith("/deployments/{deployment_id}"):
                    body = good | ({"sha": "b" * 40} if state == "sha" else {"environment": "staging"} if state == "environment" else {})
                elif identity == "r03" and operation.endswith("/deployments"):
                    body = [good | ({"sha": "b" * 40} if state == "sha" else {"environment": "staging"} if state == "environment" else {})]
                else:
                    continue
                responses[request["id"]] = {"http_status": 200, "body": body}
            result = evaluate(plan, responses, context)
            checks_only = evaluate(dict(plan, requirements=[]), responses, context)
            output.append({"task_id": identity, "case": name, "expected": expected, **result, "passed": result["status"] == expected,
                           "checks_only_status": checks_only["status"], "checks_only_passed": checks_only["status"] == expected})
        for name, responses in (("no_response", {}), ("forbidden", {r["id"]: {"http_status": 403, "body": {"message": "Forbidden"}} for r in plan["requests"]})):
            result = evaluate(plan, responses, context)
            checks_only = evaluate(dict(plan, requirements=[]), responses, context)
            output.append({"task_id": identity, "case": name, "expected": "unknown", **result, "passed": result["status"] == "unknown",
                           "checks_only_status": checks_only["status"], "checks_only_passed": checks_only["status"] == "unknown"})
    return output


def live_probe(task, plan, index, directory):
    """One public GET, narrowly bound to the preselected Actions run. No secrets."""
    context = task["context"]
    expected_path = f"/repos/cli/cli/actions/runs/{context['run_id']}"
    for request in plan["requests"]:
        if request["operation"] != "GET /repos/{owner}/{repo}/actions/runs/{run_id}":
            continue
        audit = audit_request(index, request, task, {"github::" + request["operation"]})
        if audit["issues"]:
            return {"executed": False, "reason": "Generated request failed structural validation", "audit": audit}
        binding = audit["resolved_bindings"]
        path = request["operation"].split(" ", 1)[1]
        for key, value in binding.get("path", {}).items():
            path = path.replace("{" + key + "}", quote(str(value), safe=""))
        if path != expected_path or binding.get("body"):
            return {"executed": False, "reason": "Generated request was not exactly the permitted public GET"}
        query = urlencode(binding.get("query", {}))
        url = "https://api.github.com" + path + ("?" + query if query else "")
        body, raw, _ = get_json(url)
        (directory / "live-response.json").write_bytes(raw)
        result = evaluate(plan, {request["id"]: {"http_status": 200, "body": body}}, context)
        return {"executed": True, "url": url, "method": "GET", "authenticated": False,
                "at": datetime.now(timezone.utc).isoformat(), "response_sha256": hashlib.sha256(raw).hexdigest(),
                "actual_run_status": body.get("status"), "actual_run_conclusion": body.get("conclusion"),
                "result": result}
    return {"executed": False, "reason": "Planner did not produce the required exact-run GET operation"}


def live_control(task, index, directory):
    """Explicit researcher control, never fed back to the planner."""
    operation = "GET /repos/{owner}/{repo}/actions/runs/{run_id}"
    if operation not in index.by_key:
        return {"executed": False, "reason": "Control endpoint absent from full specification"}
    context = task["context"]
    url = f"https://api.github.com/repos/cli/cli/actions/runs/{int(context['run_id'])}"
    body, raw, _ = get_json(url)
    (directory / "control-response.json").write_bytes(raw)
    return {"executed": True, "planner_generated": False, "fed_back_to_planner": False,
            "method": "GET", "url": url, "authenticated": False,
            "at": datetime.now(timezone.utc).isoformat(), "response_sha256": hashlib.sha256(raw).hexdigest(),
            "matches_run_id": body.get("id") == context["run_id"], "matches_head_sha": body.get("head_sha") == context["head_sha"],
            "status": body.get("status"), "conclusion": body.get("conclusion"),
            "purpose": "Verify that the missed endpoint really exists and is callable; this is not a generated-plan success"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("results/docs/v1"))
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--live-control", action="store_true")
    args = parser.parse_args()
    previous_path = args.directory / "audit.json"
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
    public = json.loads((ROOT / "fixtures/public.json").read_text())
    tasks = {t["id"]: t for t in public["tasks"]}
    manifest = json.loads((ROOT / "fixtures/manifest.json").read_text())
    for filename, expected in manifest.items():
        if hashlib.sha256((ROOT / "fixtures" / filename).read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen task/audit fixture changed")
    batch = json.loads((args.directory / "plans.json").read_text())
    validate_shape(batch, BATCH)
    original = json.loads((args.directory / "round-2/plans.json").read_text())
    if batch != original:
        raise ValueError("Final plans differ from original model output")
    for round_number in (1, 2):
        folder = args.directory / f"round-{round_number}"
        metadata = json.loads((folder / "metadata.json").read_text())
        if metadata["status"] != "completed" or hashlib.sha256((folder / "plans.json").read_bytes()).hexdigest() != metadata["plans_sha256"]:
            raise ValueError("Inference did not complete or output changed")
    plans = {p["task_id"]: p for p in batch["plans"]}
    indexes = {p: Index(p) for p in {t["provider"] for t in public["tasks"]}}
    experiment = json.loads((args.directory / "experiment.json").read_text())
    for source in experiment["sources"]:
        if hashlib.sha256((ROOT / "sources" / f"{source['provider']}.json").read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError("Official source snapshot changed since inference")
    docs = json.loads((args.directory / "round-2/input.json").read_text())["documents"]
    rows = []
    for identity, plan in plans.items():
        task, index = tasks[identity], indexes[tasks[identity]["provider"]]
        requests = {r["id"]: r for r in plan["requests"]}
        request_rows = [audit_request(index, r, task, docs) for r in plan["requests"]]
        checks = [audit_check(index, c, requests.get(c["request_id"])) for c in plan["checks"]]
        check_ids = {c["id"] for c in plan["checks"]}
        refs = [r["id"] for r in plan["requirements"] if not set(r["check_ids"]) <= check_ids]
        rows.append({"task_id": identity, "requests": request_rows, "checks": checks,
                     "bad_requirement_references": refs,
                     "structural_issue_count": sum(len(r["issues"]) for r in request_rows + checks) + len(refs)})
    scenarios = scenario_tests(tasks, plans)
    result = {"at": datetime.now(timezone.utc).isoformat(), "fixture_manifest": manifest,
              "tasks": rows, "scenarios": scenarios,
              "audit_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "execution_code_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                        for name in ("runtime.py", "source.py", "schema.py")},
              "summary": {"tasks": len(rows), "requests": sum(len(r["requests"]) for r in rows),
                          "checks": sum(len(r["checks"]) for r in rows),
                          "structural_issues": sum(r["structural_issue_count"] for r in rows),
                          "behavioral_passed": sum(r["passed"] for r in scenarios), "behavioral_total": len(scenarios),
                          "checks_only_passed": sum(r["checks_only_passed"] for r in scenarios)},
              "live": live_probe(tasks["r05"], plans["r05"], indexes["github"], args.directory) if args.live else previous.get("live", {"executed": False, "reason": "Not requested"}),
              "control": live_control(tasks["r05"], indexes["github"], args.directory) if args.live_control else previous.get("control", {"executed": False, "reason": "Not requested"})}
    if result["control"].get("executed"):
        if hashlib.sha256((args.directory / "control-response.json").read_bytes()).hexdigest() != result["control"]["response_sha256"]:
            raise ValueError("Stored live control response changed")
    (args.directory / "audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["summary"]))
    print("Live probe:", json.dumps(result["live"]))
    print("Independent control:", json.dumps(result["control"]))
    for row in rows:
        for item in row["requests"] + row["checks"]:
            if item["issues"]:
                print(row["task_id"], item["id"], item["issues"])


if __name__ == "__main__":
    main()
