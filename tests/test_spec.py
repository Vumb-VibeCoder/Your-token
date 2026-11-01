"""Checks the skill against the Agent Skills spec (agentskills.io/specification) and the repo layout."""
import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "your-token"
ALLOWED = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


def frontmatter(path):
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "SKILL.md must start with YAML frontmatter"
    block = text[4:text.index("\n---\n", 4)]
    data, current = {}, None
    for line in block.splitlines():
        if not line.strip():
            continue
        if line.startswith((" ", "\t")):
            k, _, v = line.strip().partition(":")
            data[current][k.strip()] = v.strip().strip('"')
        else:
            k, _, v = line.partition(":")
            current = k.strip()
            data[current] = {} if not v.strip() else v.strip()
    return data


class SkillSpec(unittest.TestCase):
    def setUp(self):
        self.fm = frontmatter(SKILL / "SKILL.md")

    def test_only_known_fields(self):
        self.assertLessEqual(set(self.fm), ALLOWED)

    def test_name(self):
        name = self.fm["name"]
        self.assertTrue(1 <= len(name) <= 64)
        self.assertRegex(name, r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertEqual(name, SKILL.name, "name must match the parent directory")

    def test_description(self):
        d = self.fm["description"]
        self.assertTrue(1 <= len(d) <= 1024, len(d))
        self.assertIn("Use it whenever", d, "description should say when to use the skill")

    def test_compatibility_and_license(self):
        self.assertTrue(1 <= len(self.fm["compatibility"]) <= 500)
        self.assertEqual(self.fm["license"], "MIT")
        self.assertTrue((REPO / "LICENSE").read_text().startswith("MIT License"))

    def test_metadata_values_are_strings(self):
        for k, v in self.fm["metadata"].items():
            self.assertIsInstance(v, str, k)

    def test_body_is_short(self):
        self.assertLess((SKILL / "SKILL.md").read_text(encoding="utf-8").count("\n"), 500)

    def test_files_referenced_by_skill_exist(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        for rel in re.findall(r"`((?:scripts|assets)/[\w./-]+)`", text):
            self.assertTrue((SKILL / rel).exists(), rel)
        self.assertTrue((SKILL / "plans.json").exists())
        json.loads((SKILL / "plans.json").read_text(encoding="utf-8"))


class RepoLayout(unittest.TestCase):
    def test_marketplace_points_at_real_skill(self):
        m = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertTrue(m["name"] and m["owner"]["name"])
        for plugin in m["plugins"]:
            for rel in plugin["skills"]:
                self.assertTrue((REPO / rel / "SKILL.md").is_file(), rel)

    def test_no_em_dashes_in_prose(self):
        for p in [*REPO.glob("*.md"), *REPO.glob(".github/**/*.md"), SKILL / "SKILL.md",
                  SKILL / "assets" / "handoff_template.md"]:
            self.assertNotIn("\u2014", p.read_text(encoding="utf-8"), p.name)


if __name__ == "__main__":
    unittest.main()
