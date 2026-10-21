# SPDX-License-Identifier: Apache-2.0
"""Watch exact registry metadata from a running local service; no accounts needed."""

from ternela import Client


def main():
    client = Client()
    for provider, package, version in (("pypi", "httpx", "0.28.1"), ("npm", "lodash", "4.17.21")):
        watch = client.release(provider, package, version)
        client.refresh(watch["id"])
        current = client.status(watch["id"])
        print(provider, package, version, "->", current["receipt"]["result"]["status"])
        print(f"{client.base}/?watch={watch['id']}")
    # Opt in when waiting for a future release:
    # client.monitor(watch["id"], interval=300, duration=1800)
    # try:
    #     current = client.wait(watch["id"], timeout=120)
    # finally:
    #     client.pause(watch["id"])


if __name__ == "__main__":
    main()
