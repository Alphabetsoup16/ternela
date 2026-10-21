# SPDX-License-Identifier: Apache-2.0
"""Inspect GitHub Actions readiness through the local service without an account."""

from ternela import Client


def main():
    client = Client()
    watch = client.recipe("github_status", {"component_id": "br0l2tvcx85d"}, ttl=900)
    client.refresh(watch["id"])
    current = client.status(watch["id"])
    print(current["receipt"]["result"]["status"], current["explanation"]["summary"])
    print(f"{client.base}/?watch={watch['id']}")
    # For an unattended wait, opt in explicitly:
    # client.monitor(watch["id"], interval=300, duration=1800)
    # try:
    #     current = client.wait(watch["id"], timeout=120)
    # finally:
    #     client.pause(watch["id"])


if __name__ == "__main__":
    main()
