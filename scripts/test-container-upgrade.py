# SPDX-License-Identifier: Apache-2.0
"""Run by examples.distribution_probe in a disposable Linux container, never on a user's install."""

import hashlib
import io
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, "/opt/runtime")
from local_service import LocalService, save  # noqa: E402


def automatic_check(service, target):
    """Inject only remote evidence/transport; run real byte verification and apply."""
    raw = (Path("/fixtures") / f"ternela-v{target}.tar.gz").read_bytes()
    name = f"ternela-v{target}.tar.gz"
    record = {
        "tag_name": "v" + target,
        "draft": False,
        "prerelease": False,
        "published_at": "2026-10-19T10:00:00Z",
        "assets": [
            {
                "name": name,
                "state": "uploaded",
                "size": len(raw),
                "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "browser_download_url": f"https://github.com/{service.repository}/releases/download/v{target}/{name}",
            }
        ],
    }
    opener = Mock()
    opener.open.return_value = io.BytesIO(raw)
    with (
        patch.object(
            service,
            "probe",
            return_value={
                "decision": "complete",
                "expires_at": time.time() + 300,
                "watch_id": "synthetic-release-evidence",
                "release": record,
            },
        ),
        patch("distribution.build_opener", return_value=opener),
    ):
        return service.check(automatic=True)


def main():
    with tempfile.TemporaryDirectory(prefix="ternela-upgrade-") as directory:
        service = LocalService(Path(directory), Path("/opt/bootstrap.tar.gz"))
        service.initialize()
        initial = service.current()["version"]
        versions = json.loads(Path("/fixtures/probe.json").read_text())
        assert initial == versions["initial"], "The image must match the checkout's current version"
        good, bad, commit = (versions[name] for name in ("good", "bad", "commit"))
        service.start_app(initial)
        try:
            assert service.healthy(initial)
            sys.path.insert(0, str(service.app(initial)))
            from ternela import Client
            from workbench.demo import snapshots

            client = Client()
            watch = client.watch("n02", ttl=3600)
            responses = snapshots(watch["task"], watch["plan"], "success", "after")
            client.submit(watch["id"], "probe-success", 1, responses)
            sentinel = service.state / "data/probe-sentinel"
            sentinel.write_text("preserve me")
            save(service.state / "settings.json", {"automatic_updates": True})
            assert automatic_check(service, good)["status"] == "updated"
            assert service.current()["version"] == good
            assert Client().get(watch["id"])["receipt"]["result"]["status"] == "complete"
            assert sentinel.read_text() == "preserve me"
            failure = automatic_check(service, bad)
            assert failure["status"] == "error" and "Candidate" in failure["reason"]
            assert automatic_check(service, bad)["status"] == "skipped_failed"
            assert service.current()["version"] == good
            assert sentinel.read_text() == "preserve me", "Candidate writes escaped rollback"
            assert Client().get(watch["id"])["receipt"]["result"]["status"] == "complete"

            # Emulate an interruption after the pre-update backup and before commit.
            service.stop_app()
            service.backup()
            save(service.state / "transaction.json", {"old": good, "target": bad})
            sentinel.write_text("uncommitted interrupted write")
            service.initialize()
            service.start_app(service.current()["version"])
            assert service.healthy(good)
            assert sentinel.read_text() == "preserve me"
            assert Client().get(watch["id"])["receipt"]["result"]["status"] == "complete"

            # A newer image with a broken bundled app must keep its recovered app
            # serving rather than attempting the broken version on every restart.
            service.stop_app()
            (service.state / "failed-version.json").unlink(missing_ok=True)
            service.bootstrap = Path("/fixtures") / f"ternela-v{bad}.tar.gz"
            service.initialize()
            assert service.healthy(good)
            assert sentinel.read_text() == "preserve me"
            backup = (service.state / "pre-update.tar.gz").read_bytes()
            service.stop_app()
            service.initialize()
            assert service.child is None
            assert (service.state / "pre-update.tar.gz").read_bytes() == backup
            service.start_app(good)
            assert service.healthy(good)

            def uncertain_save(path, value):
                save(path, value)
                if Path(path).name == "current.json":
                    sentinel.write_text("new committed data")
                    raise OSError("Injected failure after publishing commit")

            with patch("local_service.save", side_effect=uncertain_save):
                try:
                    automatic_check(service, commit)
                except OSError:
                    pass
                else:
                    raise AssertionError("Commit fault did not propagate to recovery")
            assert service.current()["version"] == commit
            assert sentinel.read_text() == "new committed data"
            assert (service.state / "transaction.json").exists()
            service.stop_app()
            service.initialize()
            service.start_app(commit)
            assert service.healthy(commit)
            assert sentinel.read_text() == "new committed data"
            assert Client().get(watch["id"])["receipt"]["result"]["status"] == "complete"
            print(
                json.dumps(
                    {
                        "successful_upgrade": True,
                        "startup_rollback": True,
                        "interrupted_recovery": True,
                        "watch_preserved": True,
                        "candidate_data_write_rolled_back": True,
                        "automatic_check_download_verify_apply": True,
                        "failed_bundled_upgrade_no_restart_loop": True,
                        "uncertain_commit_recovery": True,
                    }
                )
            )
        finally:
            service.stop_app()


if __name__ == "__main__":
    main()
