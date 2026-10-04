import unittest

from benchmark import RUBRIC, digest, exact_restaurant, parse_json, sanitize_target, score, validate_prediction


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.target = {"restaurant": "Dave's Hot Chicken", "items": ["2 sliders with fries"]}

    def test_exact_restaurant_unicode_apostrophe(self):
        self.assertTrue(exact_restaurant({"restaurant": "Dave’s Hot Chicken"}, self.target))
        self.assertFalse(exact_restaurant({"restaurant": "Dave's Hot Chicken and Taco Bell"}, self.target))

    def test_item_points_only_at_target_restaurant(self):
        assessment = {"category": "hot_chicken", "item_match": "exact"}
        self.assertEqual(score({"restaurant": "Dave's Hot Chicken"}, self.target, assessment), 100)
        self.assertEqual(score({"restaurant": "Hattie B's"}, self.target, assessment), 45)
        assessment["item_match"] = "partial"
        self.assertEqual(score({"restaurant": "Dave's Hot Chicken"}, self.target, assessment), 85)

    def test_no_item_target_is_not_invented(self):
        self.target["items"] = []
        self.assertEqual(score({"restaurant": "Dave's Hot Chicken"}, self.target,
                               {"category": "hot_chicken", "item_match": "not_scored"}), 100)
        self.assertEqual(score({"restaurant": "Bowls of Rice"}, self.target,
                               {"category": "rice_bowl", "item_match": "not_scored"}), 20)

    def test_invalid_evaluator_category_is_rejected(self):
        with self.assertRaises(ValueError):
            score({"restaurant": "Dave's Hot Chicken"}, self.target,
                  {"category": "delicious", "item_match": "exact"})

    def test_json_and_prediction_validation(self):
        self.assertEqual(parse_json('```json\n{"a": 1}\n```'), {"a": 1})
        for text in ('Sure! {"a":1}', '[]'):
            with self.assertRaises(ValueError):
                parse_json(text)
        with self.assertRaises(ValueError):
            validate_prediction({"restaurant": "Dave's", "items": "slider", "reason": "yum"})

    def test_commitment_is_stable_and_changes_with_answer(self):
        self.assertEqual(digest(RUBRIC), digest(dict(reversed(list(RUBRIC.items())))))
        self.assertNotEqual(digest(self.target), digest({**self.target, "items": []}))

    def test_target_drops_personal_fields_and_rejects_invalid_names(self):
        target = {**self.target, "address": "private", "order_id": "private"}
        self.assertEqual(sanitize_target(target), self.target)
        with self.assertRaises(ValueError):
            sanitize_target({"restaurant": "Dave's", "items": [{"name": "slider"}]})


if __name__ == '__main__':
    unittest.main()
