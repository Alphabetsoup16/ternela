# SPDX-License-Identifier: Apache-2.0
"""Small local control client, run through docker exec by both host launchers."""

import argparse
import json
import os
import time
import uuid
from pathlib import Path

try:
    from .local_service import read, save
except ImportError:
    from local_service import read, save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "update", "updates"])
    parser.add_argument("choice", nargs="?", choices=["on", "off"])
    args = parser.parse_args()
    state = Path(os.environ.get("TERNELA_STATE", "/state"))
    if args.command == "updates":
        if args.choice is None:
            parser.error("updates requires on or off")
        save(state / "settings.json", {"automatic_updates": args.choice == "on"})
    elif args.command == "update":
        identity = uuid.uuid4().hex + ".json"
        save(state / "commands" / identity, {"action": "update"})
        end = time.monotonic() + 240
        while time.monotonic() < end:
            result = read(state / "results" / identity)
            if result:
                print(json.dumps(result, indent=2))
                raise SystemExit(1 if result["status"] in {"error", "unavailable"} else 0)
            time.sleep(0.5)
        raise SystemExit("Update request remains queued; inspect status and logs before retrying")
    print(
        json.dumps(
            {
                "current": read(state / "current.json"),
                "settings": read(state / "settings.json"),
                "update": read(state / "update-status.json"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
