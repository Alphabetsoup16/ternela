# SPDX-License-Identifier: Apache-2.0
"""Every release connector runs the same contract and negative-evidence oracles."""

import json
import re
import tempfile
import time
import unittest
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from expansion_lab.storage import PROJECT, Store
from ternela.connectors import (
    CONNECTORS,
    ConnectorRegistry,
    release_providers,
    request,
    source_index,
)
from ternela.providers.base import PublicConnector
from ternela.providers.pypi import PyPIConnector
from ternela.providers.release import EvidenceField, ReleaseConnector
from ternela.validation import admit
from workbench.engine import Watches, observe
from workbench.live import collect
from workbench.monitor import Monitors

FIXTURES = json.loads((PROJECT / "tests/fixtures/connector_contracts.json").read_text())[
    "connectors"
]


def run_case(connector, fixture, payload, status=200):
    task, plan = connector.recipe(**fixture["context"])
    envelope = {"http_status": status, "body": payload, "headers": {}, "headers_complete": True}
    with (
        patch("ternela.connectors.CONNECTORS", ConnectorRegistry((connector,))),
        patch("ternela.connectors.read_json", return_value=envelope),
    ):
        captured, _ = collect(plan, task["context"], "after", connector.provider)
    result, observed = observe(plan, task["context"], captured, "after")
    return result, captured, observed


def contract_mismatches(connector, fixture):
    failures = []
    for mutation in fixture["mutations"]:
        payload = deepcopy(fixture["payload"])
        parent = payload
        for key in mutation["path"][:-1]:
            parent = parent[key]
        key = mutation["path"][-1]
        if mutation.get("delete"):
            del parent[key]
        else:
            parent[key] = mutation["value"]
        actual = run_case(connector, fixture, payload)[0]["status"]
        if actual != mutation["expected"]:
            failures.append(
                {"id": mutation["id"], "expected": mutation["expected"], "actual": actual}
            )
    return failures


class ConnectorContractTests(unittest.TestCase):
    def test_fixture_coverage_matches_registered_release_connectors(self):
        from ternela.providers.release import ReleaseConnector

        expected = {
            key
            for key, connector in CONNECTORS.connectors.items()
            if isinstance(connector, ReleaseConnector)
        }
        self.assertEqual({item["provider"] for item in FIXTURES}, expected)
        self.assertEqual(len(FIXTURES), len(expected))
        self.assertEqual(set(release_providers()), expected)

    def test_shared_contract_accepts_valid_metadata_without_mutating_inputs(self):
        for fixture in FIXTURES:
            with self.subTest(provider=fixture["provider"]):
                connector = CONNECTORS.get(fixture["provider"])
                original = deepcopy(fixture)
                task, plan = connector.recipe(**fixture["context"])
                admit(plan, task)
                result, captured, observed = run_case(connector, fixture, fixture["payload"])
                self.assertEqual(result["status"], "complete")
                self.assertEqual(captured["release"][0]["source_body"], fixture["payload"])
                self.assertTrue(observed["release"]["collection_complete"])
                self.assertEqual(fixture, original)

    def test_all_declared_negative_metadata_oracles(self):
        for fixture in FIXTURES:
            connector = CONNECTORS.get(fixture["provider"])
            with self.subTest(provider=fixture["provider"]):
                self.assertEqual(contract_mismatches(connector, fixture), [])

    def test_unknown_on_errors_and_malformed_roots_for_every_release_connector(self):
        for fixture in FIXTURES:
            connector = CONNECTORS.get(fixture["provider"])
            for status in (0, 301, 401, 403, 404, 429, 500, 503):
                with self.subTest(provider=fixture["provider"], http=status):
                    self.assertEqual(
                        run_case(connector, fixture, fixture["payload"], status)[0]["status"],
                        "unknown",
                    )
            for payload in (None, [], True, "unexpected", 42, {}):
                with self.subTest(provider=fixture["provider"], root=payload):
                    result, captured, _ = run_case(connector, fixture, payload)
                    self.assertEqual(result["status"], "unknown")
                    self.assertEqual(captured["release"][0]["source_body"], payload)
                    self.assertEqual(captured["release"][0]["source_http_status"], 200)

    def test_many_wrong_resources_and_versions_cannot_complete(self):
        for fixture in FIXTURES:
            connector = CONNECTORS.get(fixture["provider"])
            for number in range(32):
                for key, value in (("name", f"other-{number}"), ("version", f"99.0.{number}")):
                    payload = deepcopy(fixture["payload"])
                    target = payload["info"] if "info" in payload else payload
                    target[key] = value
                    with self.subTest(provider=fixture["provider"], field=key, number=number):
                        self.assertEqual(
                            run_case(connector, fixture, payload)[0]["status"], "pending"
                        )

    def test_foreign_origin_resource_and_unsupported_operations_fail_before_transport(self):
        for fixture in FIXTURES:
            connector = CONNECTORS.get(fixture["provider"])
            task, plan = connector.recipe(**fixture["context"])
            call = dict(plan["requests"][0], bindings={"path": fixture["context"]})
            # Empty locations are equivalent to absence in already-admitted plans.
            with patch("ternela.connectors.read_json", return_value={"http_status": 404}) as reader:
                request(
                    connector.provider,
                    dict(call, bindings={"path": fixture["context"], "body": {}, "header": {}}),
                )
                reader.assert_called_once()
            for bad in (
                dict(call, operation="POST /anything"),
                dict(call, next_url="https://evil.example/a"),
                dict(call, bindings={"path": fixture["context"], "query": {"redirect": "x"}}),
                dict(
                    call,
                    bindings={"path": fixture["context"], "header": {"Authorization": "secret"}},
                ),
            ):
                with (
                    self.subTest(provider=fixture["provider"], bad=bad),
                    patch("ternela.connectors.read_json") as reader,
                ):
                    with self.assertRaises(ValueError):
                        request(connector.provider, bad)
                    reader.assert_not_called()
        connector = CONNECTORS.get("github")
        for target in (
            "https://evil.example/repos/a/b",
            "https://api.github.com/repos/other/repo",
            "http://api.github.com/repos/a/b",
        ):
            with patch("ternela.connectors.read_json") as reader:
                with self.assertRaises(ValueError):
                    request(
                        "github",
                        {
                            "operation": "GET /repos/{owner}/{repo}",
                            "bindings": {"path": {"owner": "a", "repo": "b"}},
                            "next_url": target,
                        },
                    )
                reader.assert_not_called()

    def test_registry_and_provider_descriptors_are_immutable_and_duplicates_rejected(self):
        with self.assertRaises(ValueError):
            ConnectorRegistry((CONNECTORS.get("pypi"), CONNECTORS.get("pypi")))
        with self.assertRaises(ValueError):
            ConnectorRegistry((object(),))
        with self.assertRaises(TypeError):
            CONNECTORS.connectors["new"] = CONNECTORS.get("pypi")
        with self.assertRaises(FrozenInstanceError):
            CONNECTORS.connectors = {}
        for connector in CONNECTORS.connectors.values():
            with self.assertRaises(FrozenInstanceError):
                connector.host = "other.example"
        for host in ("http://example.com", "user@example.com", "example.com:443", "example.com/a"):
            with self.assertRaises(ValueError):
                PublicConnector("test", host, "GET /test", "https://example.com/docs")

    def test_contract_oracle_detects_a_broken_normalizer(self):
        class Broken(PyPIConnector):
            def normalize(self, body):
                result = super().normalize(body)
                result["usable_files"] = 1
                return result

        fixture = FIXTURES[0]
        original = CONNECTORS.get("pypi")
        broken = Broken(
            original.provider, original.host, original.operation, original.documentation
        )
        payload = dict(fixture["payload"], urls=[])
        self.assertEqual(run_case(original, fixture, payload)[0]["status"], "pending")
        self.assertEqual(run_case(broken, fixture, payload)[0]["status"], "complete")
        failures = contract_mismatches(broken, fixture)
        self.assertIn("empty-files", {item["id"] for item in failures})

    def test_shared_schema_validation_rejects_normalizer_type_drift(self):
        class WrongType(PyPIConnector):
            def normalize(self, body):
                result = super().normalize(body)
                result["usable_files"] = "1"
                return result

        original = CONNECTORS.get("pypi")
        connector = WrongType(
            original.provider, original.host, original.operation, original.documentation
        )
        result, captured, _ = run_case(connector, FIXTURES[0], FIXTURES[0]["payload"])
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(captured["release"][0]["http_status"], 0)
        self.assertEqual(captured["release"][0]["source_http_status"], 200)
        self.assertEqual(captured["release"][0]["source_body"], FIXTURES[0]["payload"])

    def test_new_connector_runs_through_unmodified_watch_monitor_and_restart(self):
        class TestRelease(ReleaseConnector):
            purpose = "an observed published flag"
            conditions = (
                EvidenceField("published", "boolean", "eq", True, "Release must be published."),
            )

            def package_name(self, value):
                if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9-]+", value):
                    raise ValueError("Invalid test project")
                return value

            def normalize(self, body):
                release = body["release"]
                if type(release["published"]) is not bool:
                    raise ValueError("Missing published flag")
                return {
                    "name": self.package_name(release["project"]),
                    "version": self.version_name(release["number"]),
                    "published": release["published"],
                }

        connector = TestRelease(
            "contract-test",
            "releases.example.test",
            "GET /projects/{package}/versions/{version}",
            "https://example.test/docs",
        )
        fixture = {
            "context": {"package": "contract-demo", "version": "1.2.3"},
            "payload": {
                "release": {"project": "contract-demo", "number": "1.2.3", "published": False}
            },
        }
        now = time.time()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("ternela.connectors.CONNECTORS", ConnectorRegistry((connector,))),
        ):
            self.assertEqual(release_providers(), (connector.provider,))
            root = Path(directory)
            engine = Watches(root / "watch.sqlite3", Store(root / "data"), lambda: now)
            task, plan = connector.recipe(**fixture["context"])
            self.assertEqual(
                source_index(connector.provider).provenance["provider"], connector.provider
            )
            watch = engine.create(task, plan, ttl=120)
            monitors = Monitors(engine)
            monitors.start(watch["id"], interval=60)
            envelope = {
                "http_status": 200,
                "body": fixture["payload"],
                "headers": {},
                "headers_complete": True,
            }
            with (
                patch("ternela.connectors.read_json", return_value=envelope) as reader,
                patch("workbench.live.time.time", side_effect=lambda: now),
            ):
                self.assertTrue(monitors.run_once())
                self.assertEqual(reader.call_args.args[1], connector.host)
                self.assertEqual(engine.get(watch["id"])["receipt"]["result"]["status"], "pending")
                now += 60
                envelope["body"]["release"]["published"] = True
                monitors.run_once()
                self.assertEqual(engine.get(watch["id"])["receipt"]["result"]["status"], "complete")
                now += 60
                envelope["http_status"] = 503
                monitors.run_once()
                self.assertEqual(engine.get(watch["id"])["receipt"]["result"]["status"], "unknown")
                now += 120
                restarted = Watches(engine.path, engine.store, lambda: now)
                envelope["http_status"] = 200
                Monitors(restarted).run_once()
                self.assertEqual(
                    restarted.get(watch["id"])["receipt"]["result"]["status"], "complete"
                )
            now += 121
            self.assertTrue(restarted.get(watch["id"])["receipt"]["result"]["stale"])
            messages = restarted.outbox(watch["id"])
            self.assertEqual(
                [m["receipt"]["result"]["status"] for m in messages],
                ["pending", "complete", "unknown", "unknown", "complete", "unknown"],
            )
            self.assertTrue(messages[3]["receipt"]["result"]["stale"])
            restarted.ack(watch["id"], messages[-1]["id"])
            self.assertIsNotNone(restarted.outbox(watch["id"])[-1]["acked"])
