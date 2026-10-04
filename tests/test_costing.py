import unittest

from costing import estimate_cost


class CostingTests(unittest.TestCase):
    def setUp(self):
        self.prices = {"models": {"test": {"input_per_million": 2, "cached_input_per_million": 0.1,
                                            "cache_write_per_million": 2.5, "output_per_million": 10}}}

    def test_reference_cost_does_not_count_cached_input_twice(self):
        row = {"cli": "codex", "model": "test", "cost_usd": None,
               "usage": {"input_tokens": 1000, "cached_input_tokens": 600,
                         "cache_write_input_tokens": 100, "output_tokens": 50}}
        value, source = estimate_cost(row, self.prices)
        self.assertAlmostEqual(value, (300*2 + 600*0.1 + 100*2.5 + 50*10)/1e6)
        self.assertEqual(source, "reference_price_estimate")

    def test_cli_reported_zero_is_kept(self):
        self.assertEqual(estimate_cost({"cost_usd": 0, "cost_source": "cli"}, self.prices), (0, "cli"))

    def test_unknown_rates_or_incomplete_usage_are_not_free(self):
        for row in ({"cli": "codex", "model": "missing", "usage": {}},
                    {"cli": "codex", "model": "test", "usage": {"input_tokens": 10, "output_tokens": 10}},
                    {"cli": "opencode", "cost_usd": None}):
            self.assertEqual(estimate_cost(row, self.prices), (None, "unreported"))


if __name__ == "__main__":
    unittest.main()
