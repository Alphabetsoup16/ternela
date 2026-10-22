# SPDX-License-Identifier: Apache-2.0
import gzip
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from expansion_lab.storage import Store
from workbench.client import ObservationClient
from workbench.demo import snapshots
from workbench.engine import Watches
from workbench.monitor import Monitors
from workbench.server import Handler, Service


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        root = Path(cls.temporary.name)
        cls.engine = Watches(root / "watches.sqlite3", Store(root / "data"))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # Source/compiler records are read-only; watch receipts remain in the isolated test store.
        service = Service.__new__(Service)
        service.engine, service.token, service.store, service.jobs = (
            cls.engine,
            "test-session",
            Store(),
            {},
        )
        service.monitors = Monitors(cls.engine)
        cls.server.service = service
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()
        cls.temporary.cleanup()

    def test_client_round_trip_and_duplicate_delivery(self):
        client = ObservationClient(self.base)
        watch = client.watch("n02")
        body = snapshots(watch["task"], watch["plan"], "success", "after")
        result = client.submit(watch["id"], "same-event", 1, body)
        self.assertEqual(result["receipt"]["result"]["status"], "complete")
        self.assertTrue(client.submit(watch["id"], "same-event", 1, body)["duplicate"])
        messages = client.wakeups(watch["id"])
        self.assertEqual(len(messages), 1)
        client.acknowledge(watch["id"], messages[0]["id"])
        self.assertIsNotNone(client.wakeups(watch["id"])[0]["acked"])

    def test_directory_descriptors_usage_and_durable_consumer_sdk(self):
        client = ObservationClient(self.base)
        self.assertIn("pypi", {c["provider"] for c in client.connectors()})
        watch = client.watch("n02")
        identity = watch["id"]
        body = snapshots(watch["task"], watch["plan"], "success", "after")
        client.submit(identity, "handoff", 1, body)
        cursor, found = "", set()
        while True:
            page = client.watches(cursor, limit=2)
            found.update(i["id"] for i in page["items"])
            if not page["next"]:
                break
            cursor = page["next"]
        self.assertIn(identity, found)
        self.assertEqual(client.usage(identity)["observations"], 1)
        message = client.deliveries(identity, "sdk-agent")[0]
        again = ObservationClient(self.base)
        self.assertEqual(again.deliveries(identity, "sdk-agent")[0]["id"], message["id"])
        again.acknowledge_delivery(identity, "sdk-agent", message["id"])
        self.assertEqual(ObservationClient(self.base).deliveries(identity, "sdk-agent"), [])
        self.assertTrue(client.deliveries(identity, "other-agent"))
        for path in (
            "/api/watches?limit=101",
            "/api/watches?after=invalid",
            f"/api/watches/{identity}/deliveries?consumer=bad%20name",
        ):
            with self.assertRaises(HTTPError) as error:
                client._request(path)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()

    def test_live_http_refresh_evaluates_once_and_does_not_trust_caller_decisions(self):
        from copy import deepcopy

        from ternela import observations
        from ternela.storage import PROJECT

        with gzip.open(PROJECT / "evals/github_status_v1.json.gz", "rt") as stream:
            envelope = json.load(stream)["cases"][0]["envelope"]
        client = ObservationClient(self.base)
        watch = client.recipe("github_status", {"component_id": "br0l2tvcx85d"})
        with (
            patch("ternela.connectors.read_json", return_value=deepcopy(envelope)),
            patch("ternela.observations.evaluate", wraps=observations.evaluate) as evaluate,
        ):
            result = client.refresh(watch["id"])
        self.assertEqual(evaluate.call_count, 1)
        self.assertEqual(result["receipt"]["result"]["status"], "ready")
        result = client.submit(watch["id"], "caller-assertion", 2, {"result": {"status": "ready"}})
        self.assertEqual(result["receipt"]["result"]["status"], "unknown")
        self.assertEqual(client.health()["formats"]["database"], 3)

    def test_sdk_session_does_not_load_ui_templates(self):
        with patch("workbench.server.demo.catalog", side_effect=AssertionError("UI-only work")):
            client = ObservationClient(self.base)
            self.assertEqual(client.token, self.server.service.token)
            session = client._request("/api/bootstrap?session=1")
            self.assertEqual(session, {"token": self.server.service.token})

    def test_mutation_requires_session_token(self):
        request = Request(
            self.base + "/api/watches",
            data=b'{"template_id":"n02"}',
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()

    def test_cross_origin_mutation_is_denied(self):
        request = Request(
            self.base + "/api/watches",
            data=b'{"template_id":"n02"}',
            headers={
                "Content-Type": "application/json",
                "X-Observation-Token": "test-session",
                "Origin": "https://untrusted.example",
            },
        )
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()

    def test_custom_published_port_preserves_origin_and_session_checks(self):
        self.server.public_port = 18888
        try:
            request = Request(
                self.base + "/api/watches",
                data=b'{"template_id":"n02"}',
                headers={
                    "Content-Type": "application/json",
                    "X-Observation-Token": "test-session",
                    "Host": "localhost:18888",
                    "Origin": "http://localhost:18888",
                },
            )
            with urlopen(request) as response:
                self.assertEqual(response.status, 201)
            request = Request(
                self.base + "/api/watches",
                data=b'{"template_id":"n02"}',
                headers={
                    "Content-Type": "application/json",
                    "X-Observation-Token": "test-session",
                    "Host": "localhost:18888",
                    "Origin": "https://untrusted.example",
                },
            )
            with self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
        finally:
            del self.server.public_port

    def test_health_exposes_version_and_refuses_foreign_host(self):
        from ternela import __version__

        self.assertEqual(ObservationClient(self.base).health()["version"], __version__)
        request = Request(self.base + "/api/health", headers={"Host": "untrusted.example:8765"})
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()

    def test_unknown_snapshot_and_bad_json_are_client_errors(self):
        client = ObservationClient(self.base)
        watch = client.watch("n02")
        with self.assertRaises(HTTPError) as error:
            client.submit(watch["id"], "event", 1, [])
        self.assertEqual(error.exception.code, 400)
        error.exception.close()

    def test_malformed_json_and_trailing_routes_are_rejected(self):
        for body in (b'{"template_id":"n02","template_id":"n03"}', b'{"ttl":NaN}'):
            request = Request(
                self.base + "/api/watches",
                data=body,
                headers={"Content-Type": "application/json", "X-Observation-Token": "test-session"},
            )
            with self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
        watch = ObservationClient(self.base).watch("n02")
        with self.assertRaises(HTTPError) as error:
            urlopen(self.base + "/api/watches/" + watch["id"] + "/outbox/extra")
        self.assertEqual(error.exception.code, 404)
        error.exception.close()

    def test_receipt_download_preserves_exact_input_integer(self):
        client = ObservationClient(self.base)
        watch = client.watch("n02")
        body = snapshots(watch["task"], watch["plan"], "success", "after")
        body["intent"]["body"]["extra_integer"] = 9007199254740993
        client.submit(watch["id"], "large-integer", 1, body)
        with urlopen(self.base + "/api/watches/" + watch["id"] + "/receipt") as response:
            raw = response.read()
        self.assertIn(b"9007199254740993", raw)
        with urlopen(self.base + "/api/watches/" + watch["id"] + "?receipt_text=1") as response:
            formatted = json.load(response)["receipt_text"]
        self.assertIn("9007199254740993", formatted)

    def test_static_files_cannot_expose_repository(self):
        for path in ["/data/sources/manifest.json", "/../README.md", "/workbench/server.py"]:
            with self.assertRaises(HTTPError) as error:
                urlopen(self.base + path)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()

    def test_monitor_lifecycle_and_health_through_sdk(self):
        client = ObservationClient(self.base)
        health = client.health()
        self.assertTrue(health["capabilities"]["public_github_monitoring"])
        self.assertFalse(health["capabilities"]["authenticated_connectors"])
        watch = client.watch("live-github-demo-1", ttl=900)
        monitor = client.monitor(watch["id"], interval=300)
        self.assertTrue(monitor["enabled"])
        self.assertTrue(client.get(watch["id"])["monitor"]["enabled"])
        self.assertFalse(client.pause(watch["id"])["enabled"])
        self.assertTrue(client.monitor(watch["id"])["enabled"])
        self.assertEqual(client.get(watch["id"])["explanation"]["reason"], "no_observation")

    def test_monitor_errors_have_actionable_messages(self):
        client = ObservationClient(self.base)
        watch = client.watch("n02")
        with self.assertRaisesRegex(HTTPError, "reviewed anonymous connector"):
            client.monitor(watch["id"])

    def test_sdk_recovers_local_session_after_service_restart(self):
        client = ObservationClient(self.base)
        watch = client.watch("live-github-demo-1", ttl=900)
        self.server.service.token = "replacement-session"
        try:
            self.assertTrue(client.monitor(watch["id"])["enabled"])
            self.assertEqual(client.token, "replacement-session")
        finally:
            self.server.service.token = "test-session"

    def test_registry_recipe_creation_refresh_and_schedule_use_provider(self):
        client = ObservationClient(self.base)
        for provider, package, version in (
            ("pypi", "httpx", "0.28.1"),
            ("npm", "@types/node", "22.10.0"),
        ):
            watch = client.release(provider, package, version)
            self.assertTrue(client.get(watch["id"])["live_available"])
            with patch(
                "workbench.server.collect", return_value=({}, self.engine.now())
            ) as collector:
                client.refresh(watch["id"])
            self.assertEqual(collector.call_args.kwargs["provider"], provider)
            self.assertEqual(client.status(watch["id"])["receipt"]["result"]["status"], "unknown")
            monitor = client.monitor(watch["id"])
            self.assertEqual(monitor["provider_key"], provider + "-public")
            client.pause(watch["id"])
        with self.assertRaises(HTTPError) as error:
            client.release("npm", "lodash", "latest")
        self.assertEqual(error.exception.code, 400)
        error.exception.close()

    def test_compact_status_and_gzip_preserve_exact_evidence(self):
        client = ObservationClient(self.base)
        watch = client.watch("n02")
        body = snapshots(watch["task"], watch["plan"], "success", "after")
        body["intent"]["body"]["large"] = 9007199254740993
        client.submit(watch["id"], "compressed", 1, body)
        full = client.get(watch["id"])
        self.assertEqual(full["receipt"]["input"]["intent"]["body"]["large"], 9007199254740993)
        small = client.status(watch["id"])
        self.assertEqual(small["receipt"]["result"], full["receipt"]["result"])
        self.assertNotIn("observations", small["receipt"])
        for accept, encoded in (
            ("gzip", True),
            ("gzip;q=0", False),
            ("*;q=1,gzip;q=0", False),
            ("gzip;q=0.5", True),
        ):
            request = Request(
                self.base + "/api/watches/" + watch["id"], headers={"Accept-Encoding": accept}
            )
            with urlopen(request) as response:
                raw = response.read()
                self.assertEqual(response.headers.get("Content-Encoding") == "gzip", encoded)
                self.assertEqual(int(response.headers["Content-Length"]), len(raw))
                self.assertEqual(response.headers["Vary"], "Accept-Encoding")
                self.assertIn(b"9007199254740993", gzip.decompress(raw) if encoded else raw)
        compact = client._request(f"/api/watches/{watch['id']}/outbox?compact=1")
        self.assertNotIn("input", compact[0]["receipt"])

    def test_generic_recipe_runs_status_readiness_through_the_existing_sdk_flow(self):
        from expansion_lab.storage import PROJECT

        client = ObservationClient(self.base)
        watch = client.recipe("github_status", {"component_id": "br0l2tvcx85d"})
        self.assertEqual(watch["phase"], "before")
        self.assertTrue(watch["live_available"])
        payload = json.loads(
            gzip.decompress((PROJECT / "evals/github_status_v1.json.gz").read_bytes())
        )["cases"][0]["envelope"]
        with patch("ternela.connectors.read_json", return_value=payload):
            client.refresh(watch["id"])
        self.assertEqual(
            client.wait(watch["id"], timeout=2)["receipt"]["result"]["status"], "ready"
        )
        self.assertTrue(client.monitor(watch["id"])["enabled"])
        client.pause(watch["id"])
        self.assertIn("github_status", client.health()["capabilities"]["public_recipes"])
        package = client.recipe("pypi", {"package": "httpx", "version": "0.28.1"})
        self.assertEqual(package["phase"], "after")

    def test_generic_recipe_rejects_unknown_provider_and_bad_arguments(self):
        client = ObservationClient(self.base)
        for provider, context in (
            ("github", {}),
            ("missing", {}),
            ("github_status", []),
            ("github_status", {}),
            ("github_status", {"component_id": "Actions"}),
            ("github_status", {"component_id": "br0l2tvcx85d", "host": "evil.example"}),
        ):
            with (
                self.subTest(provider=provider, context=context),
                self.assertRaises(HTTPError) as error,
            ):
                client.recipe(provider, context)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()


if __name__ == "__main__":
    unittest.main()
