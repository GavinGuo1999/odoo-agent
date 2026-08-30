from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml


PROJECT_DIR = Path(__file__).resolve().parents[2]
SKILLS_DIR = PROJECT_DIR / ".agents" / "skills"
EVALS_DIR = PROJECT_DIR / "evals" / "skills"
EXPECTED_SKILLS = {
    "odoo-agent-development",
    "odoo-readonly-testing",
    "odoo-rag-evaluation",
}
FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
DOC_LINK = re.compile(r"\((\.\./\.\./\.\./docs/[^)]+)\)")


def load_yaml(path: Path) -> dict:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"Expected a YAML mapping: {path}")
    return payload


def load_skill(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER.match(text)
    if match is None:
        raise AssertionError(f"Missing SKILL.md frontmatter: {path}")
    metadata = yaml.safe_load(match.group(1))
    if not isinstance(metadata, dict):
        raise AssertionError(f"Invalid SKILL.md frontmatter: {path}")
    return metadata, text[match.end() :]


class ProjectSkillTests(unittest.TestCase):
    def test_project_skills_have_discovery_and_ui_metadata(self) -> None:
        for name in EXPECTED_SKILLS:
            with self.subTest(skill=name):
                skill_dir = SKILLS_DIR / name
                metadata, body = load_skill(skill_dir / "SKILL.md")
                interface = load_yaml(skill_dir / "agents" / "openai.yaml")["interface"]

                self.assertEqual(metadata["name"], name)
                self.assertGreaterEqual(len(metadata["description"]), 40)
                self.assertNotIn("TODO", body)
                self.assertIn(f"${name}", interface["default_prompt"])
                self.assertGreaterEqual(len(interface["short_description"]), 25)
                self.assertLessEqual(len(interface["short_description"]), 64)

    def test_skill_document_links_resolve(self) -> None:
        for name in EXPECTED_SKILLS:
            skill_path = SKILLS_DIR / name / "SKILL.md"
            _, body = load_skill(skill_path)
            links = DOC_LINK.findall(body)
            self.assertTrue(links, f"{name} must link its maintained project contract")
            for link in links:
                with self.subTest(skill=name, link=link):
                    self.assertTrue((skill_path.parent / link).resolve().is_file())

    def test_waza_defaults_are_offline_and_repo_scoped(self) -> None:
        config = load_yaml(PROJECT_DIR / ".waza.yaml")

        self.assertEqual(config["paths"]["skills"], ".agents/skills/")
        self.assertEqual(config["paths"]["evals"], "evals/skills/")
        self.assertEqual(config["defaults"]["engine"], "mock")
        self.assertEqual(config["defaults"]["model"], "mock")

    def test_waza_eval_samples_cover_positive_and_negative_triggers(self) -> None:
        task_ids: set[str] = set()
        for name in EXPECTED_SKILLS:
            with self.subTest(skill=name):
                eval_dir = EVALS_DIR / name
                spec = load_yaml(eval_dir / "eval.yaml")
                tasks = [load_yaml(path) for path in sorted((eval_dir / "tasks").glob("*.yaml"))]

                self.assertEqual(spec["skill"], name)
                self.assertEqual(spec["config"]["executor"], "mock")
                self.assertEqual(spec["config"]["required_skills"], [name])
                self.assertEqual(len(tasks), 2)
                self.assertEqual({task["expected"]["should_trigger"] for task in tasks}, {True, False})

                for task in tasks:
                    self.assertNotIn(task["id"], task_ids)
                    task_ids.add(task["id"])
                    grader = task["graders"][0]
                    expected_mode = "positive" if task["expected"]["should_trigger"] else "negative"
                    self.assertEqual(grader["type"], "trigger")
                    self.assertEqual(grader["config"]["mode"], expected_mode)
                    self.assertTrue((PROJECT_DIR / grader["config"]["skill_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
