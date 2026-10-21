# SPDX-License-Identifier: Apache-2.0
"""Run with python3 -m examples.public_wait while the local workbench is running."""

from ternela import Client


def main():
    client = Client()
    watch = client.watch("live-github-demo-1", ttl=900)
    print(f"Watch: {client.base}/?watch={watch['id']}", flush=True)
    client.monitor(watch["id"], interval=300, duration=1800)
    try:
        current = client.wait(watch["id"], timeout=120)
        print(current["explanation"]["summary"])
        print("Receipt event:", current["receipt"]["event_id"])
    except TimeoutError as exc:
        print(str(exc))
        print("Inspect the watch's observations for the current result.")
    finally:
        client.pause(watch["id"])
        print("Automatic checks paused; the watch and receipts remain available.")


if __name__ == "__main__":
    main()
