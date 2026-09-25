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
    "odoo-crm-semantics",
    "odoo-multiagent-orchestration",
}
FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
DOC_LINK = re.compile(r"\((\.\./\.\./\.\./docs/[^)]+)\)")
# Markdown 标题，用于校验链接里的锚点真的存在。
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.MULTILINE)

# A Skill that denies a capability the repository already ships sends the agent
# down an obsolete path. Each entry pairs a denial pattern with the files whose
# existence proves the denial false.
SHIPPED_CAPABILITY_CLAIMS = (
    (
        "odoo-rag-evaluation",
        re.compile(r"not yet production|no vector retrieval|RAGAS is not", re.IGNORECASE),
        (
            PROJECT_DIR / "backend" / "app" / "services" / "wiki_vector.py",
            PROJECT_DIR / "evals" / "run_wiki_rag_eval.py",
        ),
    ),
)


def count_tokens(text: str) -> int:
    """SKILL.md 的 token 数。

    优先用 `tiktoken`（venv 里已有，不新增依赖）。取不到编码时退回一个偏保守的
    估算：CJK 每字 1 token、其余每 4 字符 1 token——宁可把合规的判成超，
    也不要把超预算的放过去。
    """
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:
        cjk = len(re.findall(r"[　-鿿＀-￯]", text))
        return cjk + round((len(text) - cjk) / 4)


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

    def test_skill_section_references_still_exist(self) -> None:
        """Skill 正文里引用的 §N.M 必须在它链接的文档里真的存在。

        5 个 Skill × 20 多篇文档，章节号一定会漂。链接本身没坏、但指向的是错误段落，
        比链接 404 更难发现——开发者会照着错的段落做。
        """
        section_ref = re.compile(r"§\s*([0-9]+(?:\.[0-9]+)*)")
        heading_number = re.compile(r"^([0-9]+(?:\.[0-9]+)*)[.\s]")

        for name in EXPECTED_SKILLS:
            skill_path = SKILLS_DIR / name / "SKILL.md"
            _, body = load_skill(skill_path)
            referenced = set(section_ref.findall(body))
            if not referenced:
                continue

            available: set[str] = set()
            for link in DOC_LINK.findall(body):
                doc = (skill_path.parent / link.split("#", 1)[0]).resolve()
                if not doc.is_file():
                    continue
                for heading in HEADING.findall(doc.read_text(encoding="utf-8")):
                    match = heading_number.match(heading)
                    if match:
                        available.add(match.group(1).rstrip("."))

            for section in sorted(referenced):
                with self.subTest(skill=name, section=section):
                    self.assertIn(
                        section,
                        available,
                        f"{name}/SKILL.md 引用了 §{section}，但它链接的文档里没有这一节",
                    )

    def test_skill_bodies_stay_far_from_the_truncation_limit(self) -> None:
        """SKILL.md 不得越过会被截断的硬线。

        超长的 Skill 会被截断，而截断掉的通常正是末尾那些"不该用在哪"的边界说明——
        Skill 于是变得更容易误触发。

        **精确的 token 预算交给 Waza，这里只做一道粗的上界。** 理由是分工而不是偷懒：
        `waza check` 用的是它自己的分词器，而且会读 `.waza.yaml` 的 `warningThreshold`，
        它才是这个预算的权威。本地用 `tiktoken` 数出来的值**系统性偏高**——
        2026-09-17 实测，同样五份 Skill，本地比 Waza 多 19 到 266 个 token。
        拿偏高的数去卡 1200 提示线，会把 Waza 认为合规的 Skill 判超，
        然后逼着人删承重内容去凑一个虚假的差额。

        所以这里只卡 `limits.defaults` 的硬线：本地计数偏高，
        因此"本地计数没超硬线"是个保守结论，不会漏放真正超长的 Skill。
        1200 那条提示线由 `waza check` 负责，见 docs/17。
        """
        config = load_yaml(PROJECT_DIR / ".waza.yaml")
        hard_limit = config["tokens"]["limits"]["defaults"]["SKILL.md"]

        for name in EXPECTED_SKILLS:
            with self.subTest(skill=name):
                text = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
                tokens = count_tokens(text)
                self.assertLessEqual(
                    tokens,
                    hard_limit,
                    f"{name}/SKILL.md 本地计数 {tokens} token，越过 {hard_limit} 硬线会被截断。"
                    "把明细搬到 docs/ 并在 Skill 里留链接。",
                )

    def test_skill_eval_specs_resolve_skill_directories_against_the_spec_file(self) -> None:
        """`config.skill_directories` 必须写成相对 eval.yaml 的路径。

        这是 2026-09-17 第一次真正跑 `waza run` 时暴露的缺陷：五份评测里的
        `skill_directories` 全都写成了仓库根相对路径，于是 Waza 去找
        `evals/skills/<name>/.agents/skills/<name>`，报 `required skills not found`
        ——**这五份评测从写下来那天起就跑不了**。

        更容易踩的是：**同一个 spec 里两个字段的基准目录不一样**。
        `config.skill_directories` 相对 eval.yaml 所在目录，
        而 grader 的 `skill_path` 相对仓库根（进程工作目录）。
        把后者也改成 `../../../` 会让它解析到 `D:\\.agents\\...`。

        所以这条测试同时钉住两个方向，任何一边写错都会红。
        """
        for name in EXPECTED_SKILLS:
            eval_dir = EVALS_DIR / name
            spec_path = eval_dir / "eval.yaml"
            with self.subTest(skill=name):
                spec = load_yaml(spec_path)
                directories = spec["config"]["skill_directories"]
                self.assertTrue(directories, f"{name}: skill_directories 不能为空")
                for raw in directories:
                    resolved = (spec_path.parent / raw).resolve()
                    self.assertTrue(
                        resolved.is_dir(),
                        f"{name}: skill_directories 的 {raw!r} 相对 eval.yaml 解析不到目录"
                        f"（得到 {resolved}）。Waza 就是按这个基准找 Skill 的。",
                    )
                    self.assertEqual(
                        resolved,
                        (SKILLS_DIR / name).resolve(),
                        f"{name}: skill_directories 必须指向 .agents/skills/{name}",
                    )

                # grader 的 skill_path 反过来：相对仓库根。
                for task_path in sorted((eval_dir / "tasks").glob("*.yaml")):
                    grader_path = load_yaml(task_path)["graders"][0]["config"]["skill_path"]
                    self.assertTrue(
                        (PROJECT_DIR / grader_path).is_file(),
                        f"{name}/{task_path.name}: grader 的 skill_path {grader_path!r} "
                        "必须相对仓库根解析得到文件",
                    )

    def test_skill_bodies_do_not_deny_shipped_capabilities(self) -> None:
        for name, denial, shipped_paths in SHIPPED_CAPABILITY_CLAIMS:
            with self.subTest(skill=name):
                _, body = load_skill(SKILLS_DIR / name / "SKILL.md")
                shipped = [path.name for path in shipped_paths if path.is_file()]
                match = denial.search(body)
                if shipped and match:
                    self.fail(
                        f"{name}/SKILL.md still claims {match.group(0)!r} while "
                        f"{', '.join(shipped)} exist. Update the Skill body when a "
                        "capability ships."
                    )

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
