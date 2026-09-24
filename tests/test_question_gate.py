"""Question/help-step contract on the deployed main runtime."""
import unittest

from tests._harness import ContractCase, runtime


class QuestionGateTests(ContractCase):
    def test_question_budget_ladder(self):
        """ask once, help once, then defer — unchanged contract."""
        core = runtime("gg_core")
        p = {"facts": {}, "questions": {}}
        for attempts, action in [
            (0, "ask"), (1, "help"), (2, "deferred"), (3, "deferred")
        ]:
            with self.subTest(attempts=attempts):
                p["questions"] = {"price": {"attempts": attempts}}
                self.assertEqual(core.question(p, "price"), action)

    def test_provided_answer_reuses(self):
        core = runtime("gg_core")
        p = {"facts": {"f": {"field_id": "price", "answer_state": "provided"}},
             "questions": {}}
        self.assertEqual(core.question(p, "price"), "reuse")

    def test_unknown_answer_reaches_help(self):
        """H8 fixed in P1: an 'unknown' answer consumes the ask-then-help
        ladder instead of short-circuiting to reuse.

        Baseline evidence (main 29abec0): question() returned 'reuse' for
        answer_state=unknown at attempts=1 — the help step was skipped.
        The other answer states keep their existing rules (reuse).
        """
        core = runtime("gg_core")
        p = {"facts": {"u": {"field_id": "area", "answer_state": "unknown"}},
             "questions": {"area": {"attempts": 1}}}
        self.assertEqual("help", core.question(p, "area"))
        p["questions"]["area"]["attempts"] = 0
        self.assertEqual("ask", core.question(p, "area"))
        p["questions"]["area"]["attempts"] = 2
        self.assertEqual("deferred", core.question(p, "area"))
        for state in ("provided", "explicit_none", "withheld",
                      "not_applicable"):
            with self.subTest(state=state):
                q = {"facts": {"u": {"field_id": "area",
                                     "answer_state": state}},
                     "questions": {}}
                self.assertEqual("reuse", core.question(q, "area"))


if __name__ == "__main__":
    unittest.main()
