"""Issue planning of the daily canary: the agents' work queue must open, update and close correctly."""

import unittest

import helpers  # noqa: F401

from livres.canary import plan


def r(id, ok, status="verified", enabled=True, detail="x"):
    return {"id": id, "name": id.title(), "ok": ok, "status": status, "enabled": enabled, "detail": detail}


class CanaryPlanTests(unittest.TestCase):
    def test_opens_one_issue_per_broken_source_with_protocol_and_mention(self):
        actions = plan([r("a", True), r("b", False, detail="HTTP 503")], {}, [], mention="@claude", run_url="RUN")
        self.assertEqual(len(actions), 1)
        a = actions[0]
        self.assertEqual((a["action"], a["title"], a["labels"]), ("open", "source-down: b", ["source-down"]))
        self.assertIn("HTTP 503", a["body"])
        self.assertIn("P2", a["body"])
        self.assertIn("@claude", a["body"])

    def test_comments_instead_of_duplicating(self):
        issues = [{"number": 7, "title": "source-down: b", "labels": ["source-down"]}]
        actions = plan([r("b", False)], {}, issues)
        self.assertEqual(actions, [{"action": "comment", "number": 7, "body": actions[0]["body"]}])

    def test_closes_recovered_sources_only_if_checked(self):
        issues = [
            {"number": 7, "title": "source-down: b", "labels": ["source-down"]},
            {"number": 8, "title": "source-down: gone", "labels": ["source-down"]},
        ]
        actions = plan([r("b", True)], {}, issues)
        self.assertEqual([(a["action"], a["number"]) for a in actions], [("close", 7)])

    def test_production_view_counts_too(self):
        prod = {"sources": [{"id": "a", "state": "down", "enabled": True, "detail": "timeout"}]}
        actions = plan([r("a", True)], prod, [])
        self.assertEqual(actions[0]["title"], "source-down: a")
        self.assertIn("timeout", actions[0]["body"])

    def test_disabled_in_production_is_not_an_alert(self):
        prod = {"sources": [{"id": "a", "state": "down", "enabled": False}]}
        self.assertEqual(plan([r("a", True)], prod, []), [])

    def test_passing_candidate_gets_one_issue(self):
        cand = r("gallica", True, status="candidate", enabled=False)
        actions = plan([cand], {}, [])
        self.assertEqual(actions[0]["title"], "source-candidate-ok: gallica")
        self.assertEqual(plan([cand], {}, [{"number": 3, "title": "source-candidate-ok: gallica", "labels": []}]), [])
        self.assertEqual(plan([r("gallica", False, status="candidate", enabled=False)], {}, []), [])


if __name__ == "__main__":
    unittest.main()
