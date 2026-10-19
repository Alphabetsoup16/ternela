# SPDX-License-Identifier: Apache-2.0
"""Post-run diagnostics; target assertions are audit data, never planner hints."""

import argparse
import json
from pathlib import Path

from .audit import nodes_at
from .source import Index, request_schema


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("results/docs/v1"))
    args = parser.parse_args()
    data = json.loads((args.directory / "round-2/input.json").read_text())
    result = {"authored_after_inference": True, "fed_back_to_planner": False, "retrieval_ranks": [], "omitted_fields": []}
    for identity, provider, operation in (("r05", "github", "GET /repos/{owner}/{repo}/actions/runs/{run_id}"),
                                           ("r01", "stripe", "POST /v1/invoices/create_preview")):
        index = Index(provider)
        record = data["retrieval"][identity]
        queries = [record["initial_query"]] + [s["query"] for s in record.get("followup_searches", [])]
        ranks = [next(n for n, hit in enumerate(index.search(query, len(index.entries)), 1) if hit["operation"] == operation) for query in queries]
        result["retrieval_ranks"].append({"task_id": identity, "operation": operation, "exists_in_full_spec": operation in index.by_key,
                                           "query_ranks": [{"query": q, "rank": rank} for q, rank in zip(queries, ranks)]})
        if identity == "r01":
            schema = request_schema(index.spec, index.by_key[operation]["operation"])
            excerpt = data["documents"][provider + "::" + operation]
            seen = {f.get("pointer") for f in excerpt.get("request_fields", [])}
            for field in ("/subscription", "/subscription_details/items/*/id", "/subscription_details/items/*/price", "/subscription_details/proration_date"):
                result["omitted_fields"].append({"pointer": field,
                    "exists_in_full_spec": bool(nodes_at(index.spec, schema, field[1:].split("/"))), "present_in_excerpt": field in seen})
            result["preview_excerpt_truncation"] = excerpt.get("budget_truncation")
    (args.directory / "diagnostics.json").write_text(json.dumps(result, indent=2) + "\n")
    print("Saved post-run retrieval and truncation diagnostics")


if __name__ == "__main__":
    main()
