import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import jev_openrouter_run as j  # noqa: E402

BODY = {
    "state": {"page": {"url": "u", "title": "t", "text": "x"}, "elements": [], "recent_actions": []},
    "questions": {
        "operation": {"type": "choice", "criteria": {"CLICK": "c", "TYPE_TEXT": "t", "DONE": "d", "BLOCKED": "b"},
                      "instructions": {"goal": "g", "rules": "r"}},
        "click_target": {"type": "choice", "criteria": {"1": {"element": "[1] a"}, "2": {"element": "[2] b"}},
                         "instructions": {"goal": "g", "operation": "CLICK", "rules": ["r"]}},
        "type_text_target": {"type": "choice", "criteria": {"3": {"element": "[3] q"}},
                             "instructions": {"goal": "g", "operation": "TYPE_TEXT", "rules": ["r"]}},
    },
}


class TranslateTests(unittest.TestCase):
    def test_click_is_one_hot_and_jev_valid(self):
        r = j.translate(BODY, lambda m: 'Sure {"operation": "click", "target": "2"}')
        op, tgt = r["answers"]["operation"], r["answers"]["click_target"]
        self.assertEqual(op["choice"], "CLICK")
        self.assertEqual(set(op["probabilities"]), {"CLICK", "TYPE_TEXT", "DONE", "BLOCKED"})
        self.assertEqual(tgt["choice"], "2")
        self.assertEqual(sum(tgt["probabilities"].values()), 1.0)
        self.assertNotIn("type_text_target", r["answers"])

    def test_done_needs_no_target(self):
        r = j.translate(BODY, lambda m: '{"operation": "DONE", "target": null}')
        self.assertEqual(r["answers"]["operation"]["choice"], "DONE")

    def test_rejects_unoffered_choices_and_garbage(self):
        for reply in ('{"operation": "OPEN_URL", "target": null}', '{"operation": "CLICK", "target": "99"}', "no json", '{"x": 1}'):
            with self.assertRaises(ValueError, msg=reply):
                j.translate(BODY, lambda m, r=reply: r)

    def test_prompt_marks_page_untrusted_and_includes_targets(self):
        seen = {}
        j.translate(BODY, lambda m: seen.update(m=m) or '{"operation": "DONE", "target": null}')
        self.assertIn("untrusted", seen["m"][0]["content"])
        self.assertIn("CLICK", json.loads(seen["m"][1]["content"])["targets_by_operation"])


if __name__ == "__main__":
    unittest.main()
