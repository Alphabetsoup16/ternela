# SPDX-License-Identifier: Apache-2.0
import unittest
from unittest.mock import Mock, patch

from workbench.client import ObservationClient


class ClientWaitTests(unittest.TestCase):
    def setUp(self):
        self.client = ObservationClient.__new__(ObservationClient)

    def test_wait_delivery_returns_historical_message_and_current_state_without_ack(self):
        message = {"id": 7, "receipt": {"result": {"status": "ready"}}}
        current = {"receipt": {"result": {"status": "unknown", "stale": True}}}
        self.client._request = Mock(side_effect=[[], [message], current])
        with patch("workbench.client.time.sleep"):
            value = self.client.wait_delivery("abc", "agent")
        self.assertEqual(value, {"message": message, "current": current})
        self.assertFalse(any("ack" in call.args[0] for call in self.client._request.call_args_list))

    def test_wait_delivery_deadline_does_not_ack_or_pause_monitor(self):
        self.client._request = Mock(return_value=[])
        with (
            patch("workbench.client.time.monotonic", side_effect=[0, 0, 2, 2]),
            patch("workbench.client.time.sleep"),
        ):
            with self.assertRaises(TimeoutError):
                self.client.wait_delivery("abc", "agent", timeout=1)
        self.assertEqual(self.client._request.call_count, 1)
        for timeout, interval in [(False, 1), (1, float("nan")), (float("inf"), 1)]:
            with self.assertRaises(ValueError):
                self.client.wait_delivery("abc", "agent", timeout, interval)

    def test_wait_returns_current_result_without_acknowledging_historical_events(self):
        current = {"receipt": {"result": {"status": "complete"}}}
        self.client._request = Mock(side_effect=[{"receipt": None}, current, current])
        with patch("workbench.client.time.sleep"):
            self.assertIs(self.client.wait("abc", poll_interval=0.01), current)
        self.assertEqual(
            [call.args[0] for call in self.client._request.call_args_list],
            ["/api/watches/abc/status", "/api/watches/abc/status", "/api/watches/abc"],
        )

    def test_wait_rechecks_terminal_decision_before_returning_full_watch(self):
        current = {"receipt": {"result": {"status": "complete"}}}
        self.client._request = Mock(
            side_effect=[
                current,
                {"receipt": {"result": {"status": "unknown", "stale": True}}},
                current,
                current,
            ]
        )
        with patch("workbench.client.time.sleep"):
            self.assertIs(self.client.wait("abc"), current)
        self.assertEqual(self.client._request.call_count, 4)

    def test_timeout_is_bounded_and_does_not_stop_monitor(self):
        self.client._request = Mock(return_value={"receipt": None})
        with (
            patch("workbench.client.time.monotonic", side_effect=[0, 0, 2, 2]),
            patch("workbench.client.time.sleep"),
        ):
            with self.assertRaisesRegex(TimeoutError, "monitor is unchanged"):
                self.client.wait("abc", timeout=1)
        self.assertEqual(self.client._request.call_count, 1)

    def test_unverifiable_task_does_not_wait_until_timeout(self):
        self.client._request = Mock(
            return_value={"receipt": {"result": {"status": "unverifiable"}}}
        )
        with self.assertRaisesRegex(ValueError, "unverifiable"):
            self.client.wait("abc")

    def test_wait_job_reports_interruption_and_preserves_job_id(self):
        self.client._request = Mock(
            return_value={"status": "interrupted", "error": "Service restarted"}
        )
        with self.assertRaisesRegex(ValueError, "job-123.*Service restarted"):
            self.client.wait_job("job-123")

    def test_invalid_wait_parameters_do_not_make_requests(self):
        self.client._request = Mock()
        for method in (self.client.wait, self.client.wait_job):
            for value in (True, 0, -1, 10**1000, float("nan"), float("inf"), "1"):
                with self.assertRaises(ValueError):
                    method("abc", timeout=value)
                with self.assertRaises(ValueError):
                    method("abc", poll_interval=value)
        self.client._request.assert_not_called()
