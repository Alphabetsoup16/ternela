# SPDX-License-Identifier: Apache-2.0
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from expansion_lab.storage import Store
from workbench.client import ObservationClient
from workbench.demo import snapshots
from workbench.engine import Watches
from workbench.server import Handler


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        root = Path(cls.temporary.name)
        cls.engine = Watches(root / "watches.sqlite3", Store(root / "data"))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # Source/compiler records are read-only; watch receipts remain in the isolated test store.
        cls.server.service = SimpleNamespace(
            engine=cls.engine, token="test-session", store=Store(), jobs={}
        )
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

    def test_mutation_requires_session_token(self):
        request = Request(
            self.base + "/api/watches",
            data=b'{"template_id":"n02"}',
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 403)

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

    def test_unknown_snapshot_and_bad_json_are_client_errors(self):
        client = ObservationClient(self.base)
        watch = client.watch("n02")
        with self.assertRaises(HTTPError) as error:
            client.submit(watch["id"], "event", 1, [])
        self.assertEqual(error.exception.code, 400)

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
        watch = ObservationClient(self.base).watch("n02")
        with self.assertRaises(HTTPError) as error:
            urlopen(self.base + "/api/watches/" + watch["id"] + "/outbox/extra")
        self.assertEqual(error.exception.code, 404)

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


if __name__ == "__main__":
    unittest.main()
