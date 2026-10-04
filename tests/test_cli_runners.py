import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import cli_runners as runners


class ResponseTests(unittest.TestCase):
    def test_codex_uses_final_message_and_reported_usage(self):
        events = [
            {"type": "item.completed", "item": {"type": "reasoning", "text": "private"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": '{"pick":"daves"}'}},
            {"type": "turn.completed", "usage": {"input_tokens": 51, "cached_input_tokens": 10, "output_tokens": 8}},
        ]
        result = runners._parse_response("codex", "\n".join(map(json.dumps, events)))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["usage"]["input_tokens"], 51)
        self.assertEqual(result["tokens_total"], 59)
        self.assertIsNone(result["cost_usd"])
        self.assertEqual(result["cost_source"], "unreported")
        self.assertNotIn("private", result["text"])
        self.assertIsNone(result["reported_model"])

    def test_open_code_counts_multiple_steps_and_nested_cache(self):
        events = [
            {"type": "text", "part": {"type": "text", "text": '{"pick":"daves"}'}},
            {"type": "step_finish", "part": {"reason": "stop", "tokens": {"input": 12, "output": 4, "reasoning": 2, "cache": {"read": 3, "write": 0}}}},
        ]
        result = runners._parse_response("opencode", "\n".join(map(json.dumps, events)))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["usage"]["cache"]["read"], 3)
        self.assertEqual(result["tokens_total"], 21)

    def test_claude_reports_model_and_usage_without_invented_counts(self):
        event = {"type": "result", "subtype": "success", "result": '{"pick":"daves"}',
                 "usage": {"input_tokens": 100, "output_tokens": 9},
                 "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 100}}}
        result = runners._parse_response("claude", json.dumps(event))
        self.assertEqual(result["reported_model"], "claude-sonnet-5-5")
        self.assertEqual(result["usage"], {"input_tokens": 100, "output_tokens": 9})
        self.assertEqual(result["tokens_total"], 109)

    def test_claude_cost_and_cache_creation_detail_are_not_double_counted(self):
        event = {"type": "result", "subtype": "success", "result": "answer",
                 "total_cost_usd": 0.023,
                 "usage": {"input_tokens": 100, "output_tokens": 9,
                           "cache_creation_input_tokens": 50, "cache_read_input_tokens": 200,
                           "cache_creation": {"ephemeral_5m_input_tokens": 30,
                                              "ephemeral_1h_input_tokens": 20}},
                 "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 100, "costUSD": 0.023,
                                                       "private": "do not retain"}}}
        result = runners._parse_response("claude", json.dumps(event))
        self.assertEqual(result["tokens_total"], 359)
        self.assertEqual(result["cost_usd"], 0.023)
        self.assertEqual(result["cost_source"], "claude_cli_estimate")
        self.assertEqual(result["model_usage"], {"claude-sonnet-5-5": {"inputTokens": 100, "costUSD": 0.023}})

    def test_open_code_sums_costs_and_honors_reasoning_in_total(self):
        events = [
            {"type": "text", "part": {"text": "answer"}},
            {"type": "step_finish", "part": {"cost": 0.01, "reason": "continue",
                "tokens": {"input": 12, "output": 4, "reasoning": 2,
                           "cache": {"read": 3, "write": 0}, "total": 21}}},
            {"type": "step_finish", "part": {"cost": 0.02, "reason": "stop",
                "tokens": {"input": 9, "output": 5, "reasoning": 7,
                           "cache": {"read": 1, "write": 0}, "total": 22}}},
        ]
        result = runners._parse_response("opencode", "\n".join(map(json.dumps, events)))
        self.assertEqual(result["tokens_total"], 43)
        self.assertAlmostEqual(result["cost_usd"], 0.03)
        self.assertEqual(result["cost_source"], "opencode_cli_estimate")

    def test_missing_step_metadata_does_not_make_partial_sum_look_complete(self):
        events = [{"type": "text", "part": {"text": "answer"}},
                  {"type": "step_finish", "part": {"cost": 0.02, "tokens": {"total": 30}}},
                  {"type": "step_finish", "part": {"reason": "stop"}}]
        result = runners._parse_response("opencode", "\n".join(map(json.dumps, events)))
        self.assertIsNone(result["tokens_total"])
        self.assertIsNone(result["cost_usd"])
        self.assertEqual(result["cost_source"], "unreported")

    def test_explicit_zero_cost_is_reported_but_missing_cost_is_unknown(self):
        for value, expected in [(0, 0), (None, None), (-1, None), (True, None), (float("nan"), None)]:
            with self.subTest(cost=value):
                event = {"type": "result", "subtype": "success", "result": "answer",
                         "total_cost_usd": value}
                result = runners._parse_response("claude", json.dumps(event))
                self.assertEqual(result["cost_usd"], expected)
                self.assertEqual(result["cost_source"], "claude_cli_estimate" if expected is not None else "unreported")

    def test_codex_accepts_only_explicit_dollar_cost_without_repricing_tokens(self):
        for field, expected in [("cost_usd", 0.04), ("total_cost_usd", 0.04), ("cost", None)]:
            with self.subTest(field=field):
                events = [{"type": "item.completed", "item": {"type": "agent_message", "text": "answer"}},
                          {"type": "turn.completed", "usage": {"input_tokens": 50,
                           "cached_input_tokens": 40, "output_tokens": 10,
                           "output_tokens_details": {"reasoning_tokens": 8}, field: 0.04}}]
                result = runners._parse_response("codex", "\n".join(map(json.dumps, events)))
                self.assertEqual(result["tokens_total"], 60)
                self.assertEqual(result["cost_usd"], expected)
                self.assertEqual(result["cost_source"], "codex_cli_reported" if expected else "unreported")

    def test_partial_token_usage_remains_unknown(self):
        event = {"type": "result", "subtype": "success", "result": "answer",
                 "usage": {"input_tokens": 100}}
        result = runners._parse_response("claude", json.dumps(event))
        self.assertIsNone(result["tokens_total"])

    def test_tools_invalidate_otherwise_successful_prediction(self):
        events = [{"type": "item.completed", "item": {"type": "command_execution", "command": "cat secret"}},
                  {"type": "item.completed", "item": {"type": "agent_message", "text": "answer"}},
                  {"type": "turn.completed", "usage": {"input_tokens": 1}}]
        result = runners._parse_response("codex", "\n".join(map(json.dumps, events)))
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["text"], "")

    def test_no_completed_turn_is_an_error_not_a_prediction(self):
        result = runners._parse_response("codex", '{"type":"thread.started"}')
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["usage"])
        self.assertIsNone(result["cost_usd"])
        self.assertIsNone(result["tokens_total"])

    def test_error_does_not_publish_secret_stderr(self):
        summary = runners._error_summary("authentication failed token=sk-SECRET user@example.com /private/file", 1)
        self.assertNotIn("SECRET", summary)
        self.assertNotIn("example", summary)
        self.assertIn("authentication", summary)


class ProcessTests(unittest.TestCase):
    @patch.object(runners, "find_cli", return_value=None)
    def test_missing_cli_is_explicitly_unavailable(self, _):
        result = runners.run_model(runners.DEFAULT_MODELS[0], "prompt")
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["usage"])

    @patch.object(runners, "_execute", return_value=(0, '{"type":"result","subtype":"success","result":"answer","total_cost_usd":0.03,"usage":{"input_tokens":8,"output_tokens":2}}', ""))
    @patch.object(runners, "find_cli", return_value="/bin/claude")
    def test_run_passes_prompt_stdin_and_isolates_working_directory(self, _, execute):
        result = runners.run_model(runners.DEFAULT_MODELS[1], "only these clues", timeout=5)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["cost_usd"], 0.03)
        self.assertEqual(result["tokens_total"], 10)
        argv, prompt, cwd, env, timeout = execute.call_args.args
        self.assertEqual(prompt, "only these clues")
        self.assertNotIn(prompt, argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertIn("--safe-mode", argv)
        self.assertIn("doordash-benchmark-", str(cwd))
        self.assertFalse(cwd.exists())
        self.assertNotIn("CLAUDECODE", env)
        self.assertLessEqual(timeout, 5)

    @patch.object(runners, "_execute", side_effect=subprocess.TimeoutExpired("codex", 1))
    @patch.object(runners, "find_cli", return_value="/bin/codex")
    def test_timeout_is_not_zero_score_or_missing_model(self, *_):
        result = runners.run_model(runners.DEFAULT_MODELS[3], "prompt", timeout=1)
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["text"], "")

    @patch.object(runners, "_stop_process_tree")
    @patch.object(runners.subprocess, "Popen")
    def test_timeout_kills_process_group_and_reaps_process(self, popen, kill):
        process = Mock()
        process.communicate.side_effect = [subprocess.TimeoutExpired("binary", 1), ("", "")]
        popen.return_value = process
        with self.assertRaises(subprocess.TimeoutExpired):
            runners._execute(["binary"], "prompt", Path("/tmp"), {}, 1)
        kill.assert_called_once_with(process)
        self.assertEqual(process.communicate.call_count, 2)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    @patch.object(runners, "_restore_provider_options")
    @patch.object(runners, "_execute", return_value=(0, json.dumps({"provider": {"meta": {"name": "Meta"}}, "instructions": ["private clues"], "agent": {"default": {"prompt": "secret"}}}), ""))
    def test_open_code_copies_only_provider_config(self, *_):
        env = runners._opencode_environment("opencode", Path("/tmp/isolated"), {"HOME": "/original", "OPENCODE_CONFIG": "/private/config"})
        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(config["provider"], {"meta": {"name": "Meta"}})
        self.assertEqual(config["instructions"], [])
        self.assertEqual(config["permission"], "deny")
        self.assertNotIn("private clues", env["OPENCODE_CONFIG_CONTENT"])
        self.assertNotIn("OPENCODE_CONFIG", env)
        self.assertEqual(env["HOME"], "/original")

    def test_jsonc_parser_preserves_urls_and_commas_inside_strings(self):
        result = runners._parse_jsonc('{/* comment */ "url":"https://example.test/a,}", //comment\n "list":[1,2,],}')
        self.assertEqual(result, {"url": "https://example.test/a,}", "list": [1, 2]})

    def test_redacted_api_key_restored_as_reference_without_reading_key_file(self):
        with tempfile.TemporaryDirectory() as folder:
            config_home = Path(folder)
            config_dir = config_home / "opencode"
            config_dir.mkdir()
            # Missing credential file is deliberate: restoring config must not
            # read it. The installed CLI alone resolves authentication.
            (config_dir / "opencode.json").write_text(json.dumps({"provider": {"meta": {
                "options": {"apiKey": "{file:missing-credential}"}}}}))
            providers = {"meta": {"options": {"apiKey": "***", "baseURL": "https://example.test"}}}
            runners._restore_provider_options(providers, {"XDG_CONFIG_HOME": str(config_home), "HOME": folder})
            key_reference = providers["meta"]["options"]["apiKey"]
            self.assertTrue(key_reference.startswith("{file:"))
            self.assertTrue(str(config_dir / "missing-credential") in key_reference)
            self.assertFalse((config_dir / "missing-credential").exists())


if __name__ == "__main__":
    unittest.main()
