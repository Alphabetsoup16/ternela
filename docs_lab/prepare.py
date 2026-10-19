# SPDX-License-Identifier: Apache-2.0
"""Freeze natural-language tasks and separate semantic audit criteria.

No endpoint catalog is authored here. Expected operation/parameter assertions
belong to the private grader and are not planner inputs.
"""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parent


def main():
    run = json.loads((ROOT / "sources/public-workflow-list.json").read_text())["workflow_runs"][0]
    tasks = [
        {"id": "r01", "provider": "stripe",
         "task": "Before changing the existing subscription item's price, preview the next upcoming invoice for exactly that proposed change, using the specified proration timestamp. Require amount due no more than USD 1,000. This is a preview check, not a guarantee of the final charge. After execution, confirm that invoice in_goal is paid, in USD, amount due at most USD 1,000, and belongs to sub_goal and cus_goal. Do not change anything in this experiment.",
         "context": {"customer_id": "cus_goal", "subscription_id": "sub_goal", "subscription_item_id": "si_goal", "new_price_id": "price_annual", "proration_date": 1793491200, "invoice_id": "in_goal", "budget_cents": 100000}},
        {"id": "r02", "provider": "stripe",
         "task": "Using only an invoice preview, guarantee before updating sub_goal that the actual future charge will never exceed USD 1,000, including any currency conversion changes. A current estimate alone is insufficient. Determine whether this can be monitored with certainty; do not perform the change.",
         "context": {"customer_id": "cus_goal", "subscription_id": "sub_goal", "budget_cents": 100000}},
        {"id": "r03", "provider": "github",
         "task": "Monitor GitHub's recorded deployment state for deployment 1001 in cli/cli. Complete only if the deployment's recorded SHA is the specified SHA, environment is production, and its most recent deployment status is success. An older success followed by failure must not count. This requirement is about GitHub's recorded state, not independently measured server health.",
         "context": {"owner": "cli", "repo": "cli", "deployment_id": 1001, "sha": "a" * 40, "environment": "production"}},
        {"id": "r04", "provider": "github",
         "task": "Use only GitHub's deployment APIs to prove that production servers are currently serving the requested artifact with less than 1 percent errors. A deployment success status reported by an integration is not sufficient evidence of actual server health.",
         "context": {"owner": "cli", "repo": "cli", "deployment_id": 1001, "sha": "a" * 40}},
        {"id": "r05", "provider": "github",
         "task": "Watch the exact public GitHub Actions run in the context. Complete only when its latest attempt has status completed and conclusion success, its run ID matches, and its head SHA is the specified SHA. An in-progress attempt or completed failure does not count.",
         "context": {"owner": "cli", "repo": "cli", "run_id": run["id"], "head_sha": run["head_sha"]}},
        {"id": "r06", "provider": "slack",
         "task": "Monitor whether the exact message timestamp still exists in the specified private conversation, and detect deletion within 30 seconds using polling through Slack's standard Web API. Our commercially distributed app was created in June 2026 and is not approved for the Slack Marketplace. Identify any restriction that makes this requested monitoring interval infeasible. Message existence is sufficient; do not infer that anyone read it.",
         "context": {"channel_id": "G0123456789", "message_ts": "1793491200.000001", "poll_seconds": 30}},
        {"id": "r07", "provider": "slack",
         "task": "Using Slack's standard Web API, confirm that every human member of this private channel has read this exact message. Successful posting, existence in history, reactions, and the calling user's last-read marker do not establish that everyone read it. If this is not observable, say so.",
         "context": {"channel_id": "G0123456789", "message_ts": "1793491200.000001"}},
        {"id": "r08", "provider": "stripe",
         "task": "After deleting customer cus_goal, prove that all personal data for that customer has been erased from all historical invoices, charges, and backups. A deleted-customer tombstone alone is insufficient. Use only Stripe's standard API documentation and identify gaps; do not delete anything.",
         "context": {"customer_id": "cus_goal"}},
    ]
    audits = {
        "frozen_before_inference": True,
        "r01": ["Preview must simulate the specified existing item price change and proration timestamp, not just retrieve an unchanged upcoming invoice.",
                "Use next upcoming invoice and amount_due <= 100000 with currency usd; recurring-only estimates do not satisfy the request.",
                "Invoice confirmation must check status paid, actual amount_due/currency, customer and subscription correlation using real documented fields."],
        "r02": ["Do not claim a preview guarantees the actual future charge or exchange rate."],
        "r03": ["Identify exact deployment and repository; check SHA and environment.",
                "Select the most recent status, not any historical success; exercise old success followed by newer failure."],
        "r04": ["GitHub recorded deployment success cannot establish actual server error rate or deployed bytes."],
        "r05": ["Generate an executable GET request for the exact public workflow run and check run ID, head_sha, status completed and conclusion success.",
                "Exercise queued/in_progress, success, failed completion and wrong SHA. Live GET is allowed only for this public run."],
        "r06": ["Find exact message with correct conversation, timestamp boundaries and inclusive semantics; a missing first page is not proof of deletion.",
                "Current Slack documentation imposes restrictions on history polling for this distribution class; do not claim guaranteed 30-second deletion detection.",
                "Membership, scopes, pagination and rate limiting matter; absence under an API error is unknown, not deletion."],
        "r07": ["Do not infer every human read the message from calling user's last_read, posting success, reactions or message existence."],
        "r08": ["Do not infer universal historical erasure from a deleted customer response."]
    }
    destination = ROOT / "fixtures"
    destination.mkdir(exist_ok=True)
    for name, content in (("public.json", {"tasks": tasks}), ("private.json", audits)):
        path = destination / name
        if path.exists():
            raise RuntimeError("Frozen tasks already exist; create a new experiment version instead")
        path.write_text(json.dumps(content, indent=2) + "\n")
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (destination / "public.json", destination / "private.json")}
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Frozen eight documentation tasks and private audit requirements; no tool catalogs")


if __name__ == "__main__":
    main()
