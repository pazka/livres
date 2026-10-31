"""The source registry (sources/*.json) is the agents' main surface: these tests are its guard rails."""

import json
import unittest

from helpers import FIXTURES, SOURCES

from livres.domain import Mode
from livres.sources import KINDS, SpecError, load_specs, spec_from_dict, validate

# Hosts we refuse to ever list: shadow libraries / piracy (legal risk, malware risk). See docs/SOURCES.md.
FORBIDDEN = (
    "annas-archive", "z-lib", "zlibrary", "libgen", "library genesis", "welib", "booksee", "mobilism",
    "myanonamouse", "flibusta", "open-slum", "ebooks1001", "bdebooks", "ebookoz", "dpgroup", "mayberry",
)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.specs = load_specs(SOURCES)

    def test_every_spec_is_valid_and_named_after_its_id(self):
        self.assertGreaterEqual(len(self.specs), 3)
        for path in SOURCES.glob("*.json"):
            raw = json.loads(path.read_text())
            self.assertEqual(validate(raw), [], path.name)
            self.assertEqual(raw["id"], path.stem)

    def test_at_least_three_verified_sources_are_enabled(self):
        enabled = [s for s in self.specs if s.enabled and s.status == "verified"]
        self.assertGreaterEqual(len(enabled), 3, "Mom must never be left with fewer than three sources")

    def test_candidates_ship_disabled(self):
        for s in self.specs:
            if s.status == "candidate":
                self.assertFalse(s.enabled, f"{s.id} is a candidate: enable it only after a passing live check")

    def test_verified_sources_have_recorded_fixtures(self):
        for s in self.specs:
            if s.status == "verified":
                folder = FIXTURES / s.id
                self.assertTrue(folder.is_dir() and any(folder.iterdir()), f"tests/fixtures/{s.id}/ is required (AGENTS.md P1)")

    def test_no_shadow_library_ever(self):
        for path in SOURCES.glob("*.json"):
            text = path.read_text().lower()
            for bad in FORBIDDEN:
                self.assertNotIn(bad, text, f"{path.name} mentions a forbidden source: {bad}")

    def test_only_https(self):
        for s in self.specs:
            self.assertTrue(s.homepage.startswith("https://"), s.id)

    def test_known_kinds_and_modes(self):
        for s in self.specs:
            self.assertIn(s.kind, KINDS)
            self.assertTrue(set(s.modes) <= {Mode.SEARCH, Mode.HARVEST})

    def test_validation_catches_common_mistakes(self):
        good = json.loads((SOURCES / "ebooksgratuits.json").read_text())
        self.assertEqual(validate(good), [])
        cases = {
            "id": ("Bad Id", "lowercase"),
            "kind": ("scraper", "unknown kind"),
            "legal": ("ok", "legal"),
            "allowed_hosts": (["other.example"], "not in allowed_hosts"),
            "modes": (["crawl"], "modes"),
            "canary": ({"query": ""}, "canary"),
        }
        for key, (value, expected) in cases.items():
            raw = dict(good, **{key: value})
            problems = " ".join(validate(raw))
            self.assertIn(expected, problems, key)
        http_url = dict(good, config={"search_url": "http://www.ebooksgratuits.com/s?q={searchTerms}"})
        self.assertIn("https", " ".join(validate(http_url)))
        missing = {k: v for k, v in good.items() if k != "legal"}
        self.assertIn("missing 'legal'", validate(missing))
        with self.assertRaises(SpecError):
            spec_from_dict(dict(good, id="X"))


if __name__ == "__main__":
    unittest.main()
