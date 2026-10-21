# SPDX-License-Identifier: Apache-2.0
"""Start a public wait, then resume its named consumer in another process."""

import argparse
import json

from ternela import Client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--watch", help="Resume an existing watch ID")
    parser.add_argument("--consumer", default="example-agent")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    client = Client(args.base)
    if not args.watch:
        watch = client.recipe("github_status", {"component_id": "br0l2tvcx85d"})
        client.monitor(watch["id"])
        print("Saved watch:", watch["id"], flush=True)
        print(
            f"Resume: python3 -m examples.resumable_wait --base {args.base} --watch {watch['id']} --consumer {args.consumer}",
            flush=True,
        )
        return
    try:
        delivery = client.wait_delivery(args.watch, args.consumer, timeout=args.timeout)
    except TimeoutError as exc:
        parser.exit(1, str(exc) + "\n")
    # Printing is this example's entire action. External actions need their own
    # idempotency guarantee using the stable watch/message identity below.
    print(
        json.dumps(
            {"idempotency_key": f"{args.watch}:{delivery['message']['id']}", **delivery}, indent=2
        ),
        flush=True,
    )
    client.acknowledge_delivery(args.watch, args.consumer, delivery["message"]["id"])
    print("Printed and acknowledged for", args.consumer)


if __name__ == "__main__":
    main()
