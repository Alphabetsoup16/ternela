# SPDX-License-Identifier: Apache-2.0
"""Dogfood the existing watch/evidence runtime before installing a GitHub release."""

import json
import sys
import uuid
from copy import deepcopy

from ternela.acquisition import acquire as collect
from ternela.branding import BRAND
from ternela.contracts import Acquisition
from ternela.distribution import version
from ternela.paths import resource
from ternela.storage import Store, write_json
from ternela.watches import Watches


def probe(current):
    version(current)
    template = next(
        t
        for t in json.loads(resource("examples/catalog.json").read_text())
        if t["task"]["id"] == "live-github-demo-1"
    )
    task, plan = deepcopy(template["task"]), deepcopy(template["plan"])
    owner, repo = BRAND["repository"].split("/")
    task.update(
        id="ternela-update-" + current,
        context={"owner": owner, "repo": repo},
        task=f"Wait for a published stable release of {owner}/{repo} with a tag different from v{current}. Release metadata alone does not authorize execution.",
        _origin_run="local-release-updater",
        _mode="live public GET",
    )
    plan["task_id"] = task["id"]
    plan["requests"][0]["purpose"] = (
        f"Observe latest published release metadata for {owner}/{repo}."
    )
    for requirement in plan["requirements"]:
        requirement["text"] = requirement["text"].replace("cli/cli", f"{owner}/{repo}")
    plan["checks"].append(
        dict(
            plan["checks"][2],
            id="different_version",
            op="ne",
            value_json=json.dumps("v" + current),
            reason="The published tag must differ from the installed version; the updater separately requires a strictly newer stable version.",
        )
    )
    plan["requirements"].append(
        {
            "id": "different_version",
            "phase": "after",
            "text": "Require a different tag from the installed version.",
            "coverage": "observable",
            "check_ids": ["different_version"],
            "limitation": "A different tag is not necessarily a newer compatible version or a trusted executable.",
        }
    )
    store = Store()
    engine = Watches(store.root / "workbench/watches.sqlite3", store)
    record_path = store.root / "local-update-watch.json"
    record = json.loads(record_path.read_text()) if record_path.exists() else {}
    if record.get("version") != current:
        watch = engine.create(task, plan, "after", ttl=300)
        record = {"version": current, "id": watch["id"]}
        write_json(record_path, record)
    watch = engine.get(record["id"])
    acquired = collect(watch["plan"], watch["task"]["context"], "after", "github")
    if isinstance(acquired, Acquisition):
        responses, observed_at = acquired.snapshots.value(), acquired.observed_at
    else:
        responses, observed_at = acquired
    result = engine.submit(
        watch["id"],
        uuid.uuid4().hex,
        watch["revision"] + 1,
        acquired if isinstance(acquired, Acquisition) else responses,
        observed_at=observed_at,
        mode="live public GitHub release update check",
    )
    receipt = result.get("receipt")
    if not result.get("accepted") or receipt is None:
        return {"watch_id": watch["id"], "decision": "unknown", "release": None}
    decision = receipt["result"]["status"]
    pages = responses.get("latest_release", [])
    return {
        "watch_id": watch["id"],
        "decision": decision,
        "expires_at": receipt["expires_at"],
        "release": pages[0].get("body") if len(pages) == 1 else None,
    }


if __name__ == "__main__":
    print(json.dumps(probe(sys.argv[1]), allow_nan=False))
