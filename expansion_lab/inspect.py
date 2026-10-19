# SPDX-License-Identifier: Apache-2.0
"""Read compressed run artifacts without creating permanent uncompressed copies."""
import argparse
import json
from .storage import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("run_id")
    parser.add_argument("--kind", choices=["manifest", "plans", "audit", "input", "output", "prompt"], default="manifest")
    parser.add_argument("--stage", default="compile-0"); args = parser.parse_args()
    store = Store(); manifest = json.loads((store.root / "runs" / args.run_id / "manifest.json").read_text())
    if args.kind == "manifest": value = manifest
    elif args.kind in ["plans", "audit"]: value = store.get(manifest[args.kind])
    else: value = store.get(manifest["stages"][args.stage][args.kind])
    print(value if isinstance(value, str) else json.dumps(value, indent=2))


if __name__ == "__main__":
    main()
