from pathlib import Path
import pytest
from agy_pilot.skills import (
    SkillDiscovery,
    SkillItem,
    match_skill_command,
    normalize_command_name,
    parse_skill_frontmatter,
    sanitize_description_for_telegram,
    wrap_skill_prompt,
)


def test_normalize_command_name():
    assert normalize_command_name("research") == "research"
    assert normalize_command_name("code-review") == "code_review"
    assert normalize_command_name("Setup-TS-Deep-Modules") == "setup_ts_deep_modules"
    assert normalize_command_name("123-abc_test") == "123_abc_test"
    assert normalize_command_name("!@#$%^&*") == "skill"


def test_sanitize_description_for_telegram():
    short = "Brief description."
    assert sanitize_description_for_telegram(short, "foo") == "Brief description."

    multiline = "Line 1.\n  Line 2.   \n Line 3."
    assert sanitize_description_for_telegram(multiline, "foo") == "Line 1. Line 2. Line 3."

    empty = "   "
    assert sanitize_description_for_telegram(empty, "my-skill") == "Execute my-skill skill"

    long_text = "a" * 300
    res = sanitize_description_for_telegram(long_text, "foo")
    assert len(res) <= 250
    assert res.endswith("...")


def test_parse_skill_frontmatter(tmp_path: Path):
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"

    # 1. Multi-line description with YAML indicators
    skill_file.write_text(
        "---\n"
        "name: custom-skill\n"
        "description: >-\n"
        "  First line of description.\n"
        "  Second line of description.\n"
        "---\n"
        "\n# Content",
        encoding="utf-8",
    )
    name, desc = parse_skill_frontmatter(skill_file)
    assert name == "custom-skill"
    assert "First line of description." in desc
    assert "Second line of description." in desc

    # 2. No frontmatter fallback
    skill_file.write_text("# Just Markdown", encoding="utf-8")
    name2, desc2 = parse_skill_frontmatter(skill_file)
    assert name2 == "my-skill"
    assert desc2 == ""


def test_discover_skills_precedence(tmp_path: Path):
    builtin_dir = tmp_path / "builtin"
    global_dir = tmp_path / "global"
    ws_dir = tmp_path / "workspace"

    # Setup directories
    for d in [builtin_dir / "alpha", global_dir / "alpha", global_dir / "beta", ws_dir / ".agents" / "skills" / "beta"]:
        d.mkdir(parents=True)

    (builtin_dir / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Builtin alpha\n---\n", encoding="utf-8"
    )
    (global_dir / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Global alpha\n---\n", encoding="utf-8"
    )
    (global_dir / "beta" / "SKILL.md").write_text(
        "---\nname: beta\ndescription: Global beta\n---\n", encoding="utf-8"
    )
    (ws_dir / ".agents" / "skills" / "beta" / "SKILL.md").write_text(
        "---\nname: beta\ndescription: Workspace beta\n---\n", encoding="utf-8"
    )

    skills = SkillDiscovery.discover_skills(
        workspace_paths=[ws_dir],
        global_dir=global_dir,
        builtin_dir=builtin_dir,
    )

    assert "alpha" in skills
    assert skills["alpha"].description == "Global alpha"
    assert skills["alpha"].scope == "global"

    assert "beta" in skills
    assert skills["beta"].description == "Workspace beta"
    assert skills["beta"].scope == "workspace"


def test_wrap_skill_prompt(tmp_path: Path):
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("# doc", encoding="utf-8")
    skill = SkillItem(
        name="research",
        command_name="research",
        description="Investigate questions",
        path=skill_path,
        scope="global",
    )

    wrapped = wrap_skill_prompt(skill, "test inquiry")
    assert "<USER_REQUEST>" in wrapped
    assert "/research test inquiry" in wrapped
    assert "<ADDITIONAL_METADATA>" in wrapped
    assert '<SKILL>The user requested you read and use the "research" skill.' in wrapped
    assert str(skill_path.resolve()) in wrapped


def test_match_skill_command(tmp_path: Path):
    p1 = tmp_path / "SKILL1.md"
    p2 = tmp_path / "SKILL2.md"
    p1.touch()
    p2.touch()

    skills = {
        "research": SkillItem("research", "research", "Desc 1", p1, "global"),
        "code-review": SkillItem("code-review", "code_review", "Desc 2", p2, "global"),
    }

    # Matches direct name
    m1 = match_skill_command("/research hello world", skills)
    assert m1 is not None
    assert m1[0].name == "research"
    assert m1[1] == "hello world"

    # Matches normalized command_name (underscore)
    m2 = match_skill_command("/code_review check diff", skills)
    assert m2 is not None
    assert m2[0].name == "code-review"
    assert m2[1] == "check diff"

    # Matches hyphenated
    m3 = match_skill_command("/code-review check diff", skills)
    assert m3 is not None
    assert m3[0].name == "code-review"

    # Matches with @bot_username
    m4 = match_skill_command("/research@my_bot hello", skills)
    assert m4 is not None
    assert m4[0].name == "research"
    assert m4[1] == "hello"

    # Ignores system commands
    assert match_skill_command("/start", skills) is None
    assert match_skill_command("/help", skills) is None
    assert match_skill_command("/new", skills) is None
    assert match_skill_command("/projects", skills) is None
    assert match_skill_command("/skills", skills) is None

    # Unknown command
    assert match_skill_command("/unknown_command", skills) is None

    # Plain text
    assert match_skill_command("not a command", skills) is None
