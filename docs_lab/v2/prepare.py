# SPDX-License-Identifier: Apache-2.0
"""Freeze new resource-family tasks before the revised planner is implemented/run."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def main():
    folder = Path(__file__).parent / "fresh"
    if folder.exists():
        raise RuntimeError("Fresh suite already frozen")
    tasks = [
        {"id": "f01", "provider": "stripe", "task": "Monitor refund re_goal. Complete when Stripe records that exact refund as succeeded, in USD, for exactly 2500 cents and associated with charge ch_goal. This is Stripe's recorded refund state, not proof of the customer's bank balance.",
         "context": {"refund_id": "re_goal", "charge_id": "ch_goal", "amount_cents": 2500}},
        {"id": "f02", "provider": "github", "task": "Monitor pull request 42 in cli/cli. Complete only if GitHub records it as merged, its base branch is main, its head SHA matches the specified SHA, and the PR number matches. A closed but unmerged PR is insufficient.",
         "context": {"owner": "cli", "repo": "cli", "pull_number": 42, "head_sha": "c" * 40}},
        {"id": "f03", "provider": "github", "task": "Complete when cli/cli has no open milestones according to GitHub's REST API. Inspect every page; an empty or incomplete first page is not by itself proof of absence. HTTP errors or a traversal that hits its page budget must leave the result unknown. Do not close or change any milestone.",
         "context": {"owner": "cli", "repo": "cli"}},
        {"id": "f04", "provider": "slack", "task": "Using the standard Web API, monitor whether user U0123456789 is currently listed as a member of private conversation G0123456789. Complete on a matching member ID. Search all cursor pages; if the user is absent after a complete traversal, remain pending. Failed access or an incomplete traversal is unknown. Assume an authorized token with the required conversation access will be provided by the caller.",
         "context": {"user_id": "U0123456789", "channel_id": "G0123456789"}},
        {"id": "f05", "provider": "github", "task": "Using artifact metadata only, complete when artifact 77 in cli/cli has id 77, expired is false, its recorded digest equals the specified digest, and its workflow_run id is 123. This request is about recorded metadata, not downloading or independently hashing archive bytes.",
         "context": {"owner": "cli", "repo": "cli", "artifact_id": 77, "digest": "sha256:" + "d" * 64, "run_id": 123}},
        {"id": "f06", "provider": "stripe", "task": "Using only Stripe's standard refund API, prove that the cardholder's bank has actually posted refund re_goal to the customer's account. Stripe's succeeded refund state or a bank reference alone is insufficient; require independent confirmation of the actual bank account credit. Do not create a refund.",
         "context": {"refund_id": "re_goal"}},
    ]
    private = {
        "written_before_v2_model_runs": True,
        "positive_tasks": ["f01", "f02", "f03", "f04", "f05"], "gap_tasks": ["f06"],
        "criteria": {
            "f01": ["exact id", "status succeeded", "currency usd", "amount equals 2500", "charge correlation"],
            "f02": ["number equals 42", "merged true", "base ref main", "head sha matches"],
            "f03": ["open-state filter", "all pages traversed", "complete empty collection can prove no open milestones", "incomplete/error traversal cannot authorize completion"],
            "f04": ["correct channel binding", "target membership can be on second cursor page", "absent after exhausted pages is pending", "missing next-page evidence is unknown"],
            "f05": ["exact artifact id", "expired false", "recorded digest matches", "workflow run id matches"],
            "f06": ["do not infer bank posting from Stripe's refund state or reference"]},
        "probes": {
            "f01": ["success", "wrong_id", "pending", "wrong_currency", "wrong_amount", "wrong_charge", "forbidden"],
            "f02": ["success", "closed_unmerged", "wrong_base", "wrong_sha", "wrong_number", "forbidden"],
            "f03": ["empty_complete", "open_on_second_page", "empty_first_with_next", "page_budget", "loop", "forbidden"],
            "f04": ["member_on_second_page", "member_on_first_page", "absent_complete", "missing_cursor", "page_budget", "forbidden"],
            "f05": ["success", "expired", "wrong_digest", "wrong_run", "wrong_id", "forbidden"],
            "f06": ["stripe_succeeded_not_bank_proof"]}}
    folder.mkdir(parents=True)
    for filename, data in (("public.json", {"tasks": tasks}), ("private.json", private)):
        (folder / filename).write_text(json.dumps(data, indent=2) + "\n")
    manifest = {filename: hashlib.sha256((folder / filename).read_bytes()).hexdigest() for filename in ("public.json", "private.json")}
    manifest["frozen_at"] = datetime.now(timezone.utc).isoformat()
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Frozen six fresh tasks and 32 private probe names before v2 implementation")


if __name__ == "__main__":
    main()
