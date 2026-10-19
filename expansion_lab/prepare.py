# SPDX-License-Identifier: Apache-2.0
"""Freeze public tasks, hidden scenarios and comparison policy before implementation."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def main():
    folder = Path(__file__).parent / "fresh"
    if folder.exists():
        raise ValueError("Fresh tasks already frozen")
    tasks = [
        {"id": "n01", "provider": "stripe", "task": "Before changing only the price of existing subscription item si_new in subscription sub_new to price_new, preview the next invoice using proration timestamp 1800000000. Preserve the item's current quantity exactly; it is not supplied in context and must be observed. Require USD amount due at most 7500 cents. This is a preview, not a guarantee of a future charge. Do not make any change.", "context": {"customer_id": "cus_new", "subscription_id": "sub_new", "subscription_item_id": "si_new", "new_price_id": "price_new", "proration_date": 1800000000, "budget_cents": 7500}},
        {"id": "n02", "provider": "stripe", "task": "Monitor PaymentIntent pi_new. Complete only when Stripe records the exact ID as succeeded, currency usd, amount_received exactly 4200 cents, and customer cus_new. A processing intent is insufficient. This concerns recorded API state, not independent bank settlement.", "context": {"payment_intent_id": "pi_new", "customer_id": "cus_new", "amount_cents": 4200}},
        {"id": "n03", "provider": "github", "task": "Monitor release 202 in cli/cli. Complete only when its recorded ID is 202, tag_name is v3.2.1, draft is false, prerelease is false, and target_commitish equals the supplied SHA exactly. This is release metadata, not independent verification of deployed software or resolving a branch name to a commit.", "context": {"owner": "cli", "repo": "cli", "release_id": 202, "tag": "v3.2.1", "sha": "e" * 40}},
        {"id": "n04", "provider": "github", "task": "Monitor check run 501 in cli/cli. Complete only for ID 501, head_sha matching the supplied SHA, status completed and conclusion success. A queued check or another check run's success is insufficient. Do not create, rerun or modify a check.", "context": {"owner": "cli", "repo": "cli", "check_run_id": 501, "head_sha": "f" * 40}},
        {"id": "n05", "provider": "slack", "task": "Using an authorized standard Slack Web API token, monitor directory record U1234567890. Complete only if its exact ID matches, deleted is false, is_bot is false and team_id is T1234567890. This is Slack directory state, not proof that a person is currently working or reading messages. Do not change the user profile.", "context": {"user_id": "U1234567890", "team_id": "T1234567890"}},
        {"id": "n06", "provider": "stripe", "task": "Using only Stripe's standard PaymentIntent API, prove that a succeeded PaymentIntent pi_new can never be disputed, refunded, reversed or otherwise lose its economic value in the future. An API status snapshot alone is insufficient. Do not make a payment or change anything.", "context": {"payment_intent_id": "pi_new"}},
    ]
    private = {"probes": {
        "n01": ["quantity_1", "quantity_3", "quantity_11", "quantity_0", "quantity_missing", "quantity_string", "quantity_boolean", "wrong_item", "wrong_subscription", "forbidden", "over_budget", "wrong_currency"],
        "n02": ["success", "processing", "wrong_id", "wrong_currency", "wrong_amount", "wrong_customer", "forbidden", "missing_response"],
        "n03": ["success", "draft", "prerelease", "wrong_tag", "wrong_sha", "wrong_id", "forbidden"],
        "n04": ["success", "queued", "failure", "wrong_sha", "wrong_id", "forbidden", "missing_conclusion"],
        "n05": ["success", "deleted", "bot", "wrong_id", "wrong_team", "forbidden"],
        "n06": ["succeeded_is_not_future_irreversibility"]},
        "limits": "Same author designs the system and scenarios. Fresh means frozen before implementation and model execution; not blinded external validation. Same three providers; four new endpoint families plus a quantity variant and an impossible guarantee."}
    folder.mkdir(parents=True)
    for name, value in [("public.json", {"tasks": tasks}), ("private.json", private)]:
        (folder / name).write_text(json.dumps(value, indent=2) + "\n")
    manifest = {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in ["public.json", "private.json"]}
    manifest["frozen_at"] = datetime.now(timezone.utc).isoformat()
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Frozen", len(tasks), "tasks and", sum(map(len, private["probes"].values())), "new scenarios")


if __name__ == "__main__":
    main()
