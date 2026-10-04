import json
from pathlib import Path
import unittest

from cli_runners import DEFAULT_MODELS
from suite import aggregate, baselines, brier_score, make_prompt, make_tasks, top_choice, validate_probabilities


class SuiteTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.history = json.loads((root / "examples/history.json").read_text())
        self.target = json.loads((root / "examples/target.json").read_text())
        self.orders, self.menu, self.tasks, _ = make_tasks(self.history, self.target)

    def test_historical_tasks_see_only_older_orders(self):
        self.assertEqual(len(self.tasks), 4)
        for task, index in zip(self.tasks[:3], (2, 1, 0)):
            visible = task["history"]
            self.assertEqual([o["restaurant"] for o in visible],
                             [o["restaurant"] for o in self.orders[index+1:]])
            self.assertEqual([o["sequence"] for o in visible], list(range(1, len(visible)+1)))
        self.assertEqual(self.tasks[-1]["history"], self.orders)

    def test_answers_never_sent_in_prompt_and_no_combined_future_context(self):
        task = self.tasks[0]
        prompt = make_prompt(task, self.menu, "neutral guide")
        payload = json.loads(prompt.split("\n")[-1])
        self.assertEqual(set(payload), {"history_newest_first", "meal_choices"})
        self.assertEqual(payload["history_newest_first"], task["history"])
        self.assertNotIn('"answer"', prompt)
        self.assertNotIn('"task_id"', prompt)
        self.assertEqual(make_prompt(task, self.menu, "neutral guide"), prompt)

    def test_brier_extremes_and_chance(self):
        self.assertEqual(brier_score({"a": 1, "b": 0}, "a"), 100)
        self.assertEqual(brier_score({"a": 0, "b": 1}, "a"), 0)
        self.assertEqual(brier_score({"a": 0.5, "b": 0.5}, "a"), 75)
        self.assertAlmostEqual(brier_score({c["id"]: 1/6 for c in self.menu}, "M01"), 58.3333333333)

    def test_same_top_guess_can_have_different_probability_scores(self):
        uncertain = {"a": 0.51, "b": 0.49}
        certain = {"a": 0.99, "b": 0.01}
        self.assertEqual(top_choice(uncertain), top_choice(certain))
        self.assertGreater(brier_score(uncertain, "b"), brier_score(certain, "b"))

    def test_probabilities_are_validated_and_only_rounding_is_normalized(self):
        menu = [{"id": "a"}, {"id": "b"}]
        self.assertEqual(validate_probabilities({"probabilities": {"a": 0.5, "b": 0.5}}, menu), {"a": 0.5, "b": 0.5})
        rounded = validate_probabilities({"probabilities": {"a": 0.499, "b": 0.499}}, menu)
        self.assertAlmostEqual(sum(rounded.values()), 1)
        for probs in ({"a": 1}, {"a": True, "b": 0}, {"a": -0.1, "b": 1.1},
                      {"a": 0.4, "b": 0.4}, {"a": float('nan'), "b": 0.5}):
            with self.assertRaises(ValueError):
                validate_probabilities({"probabilities": probs}, menu)

    def test_target_cannot_create_a_new_menu_option(self):
        with self.assertRaises(ValueError):
            make_tasks(self.history, {"restaurant": "Made-up secret craving", "items": ["answer"]})

    def test_meal_ids_do_not_encode_history_order(self):
        reordered = {"orders": list(reversed(self.history["orders"]))}
        _, candidates, _, _ = make_tasks(reordered, self.target)
        self.assertEqual(candidates, self.menu)

    def test_baselines_use_visible_history_and_fixed_smoothing(self):
        rows = baselines(self.tasks, self.menu)
        self.assertEqual([r["label"] for r in rows], ["Uniform chance", "Order frequency", "Repeat latest"])
        self.assertAlmostEqual(rows[0]["score"], 58.3333333333)
        for row in rows:
            self.assertEqual(len(row["tasks"]), 4)
            for task in row["tasks"]:
                self.assertAlmostEqual(sum(task["probabilities"].values()), 1)

    def test_failed_tasks_cannot_improve_average_by_being_dropped(self):
        row = {"status": "ok", "score": 100, "correct": True, "tokens_total": 20,
               "cost_usd": None, "latency_seconds": 1, "task_id": "H1"}
        result = aggregate(DEFAULT_MODELS[0], [row], 4)
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["score"])
        self.assertIsNone(result["cost_per_task_usd"])
        self.assertIsNone(result["tokens_per_task"])


if __name__ == "__main__":
    unittest.main()
