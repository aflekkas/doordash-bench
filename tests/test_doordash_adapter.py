import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import doordash_adapter as adapter


class DoorDashAdapterTests(unittest.TestCase):
    def setUp(self):
        self.raw = {
            "isError": False,
            "structuredContent": {"success": True, "orders": [{
                "store_name": "Example Chicken", "order_uuid": "private-uuid",
                "store_id": "private-store", "order_date": "private-date",
                "address": "private-address", "payment": {"last4": "private-card"},
                "items": [{"name": "Two sliders", "item_id": "private-item", "quantity": 2}],
            }]},
        }

    def test_export_removes_sensitive_fields(self):
        clean = adapter.sanitize_history(self.raw)
        self.assertEqual(clean["orders"], [{"sequence": 1, "restaurant": "Example Chicken", "items": ["Two sliders"]}])
        self.assertNotIn("private-", json.dumps(clean))

    def test_read_only_command_and_bounded_history(self):
        with patch.object(adapter, "find_cli", return_value="/verified/dd-cli"), patch.object(
            adapter.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(self.raw), "")
        ) as run:
            adapter.fetch_history(max_orders=7, days=30)
            argv = run.call_args.args[0]
            self.assertEqual(argv[:4], ["/verified/dd-cli", "--json-output", "order", "history"])
            self.assertEqual(argv[4:8], ["--max", "7", "--days", "30"])
            self.assertNotIn("shell", run.call_args.kwargs)

    def test_failure_does_not_leak_subprocess_output(self):
        with patch.object(adapter, "find_cli", return_value="dd-cli"), patch.object(
            adapter.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "private-token", "private-address")
        ):
            with self.assertRaises(adapter.DoorDashError) as context:
                adapter.fetch_history()
            self.assertNotIn("private-", str(context.exception))

    def test_text_envelope_and_control_characters(self):
        raw = {"orders": [{"store_name": "Chicken\nShop", "items": [{"name": "  Fries\t "}]}]}
        result = adapter.sanitize_history({"content": [{"type": "text", "text": json.dumps(raw)}]})
        self.assertEqual(result["orders"][0]["restaurant"], "Chicken Shop")
        self.assertEqual(result["orders"][0]["items"], ["Fries"])

    def test_error_and_malformed_history_fail_closed(self):
        for raw in ({"isError": True}, {"orders": "not a list"}, {"orders": [], "success": False}):
            with self.subTest(raw=raw), self.assertRaises(adapter.DoorDashError):
                adapter.sanitize_history(raw)

    def test_invalid_bounds_do_not_invoke_cli(self):
        with patch.object(adapter.subprocess, "run") as run:
            for kwargs in ({"max_orders": 0}, {"max_orders": 101}, {"days": 0}, {"days": 366}):
                with self.subTest(kwargs=kwargs), self.assertRaises(adapter.DoorDashError):
                    adapter.fetch_history(**kwargs)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
