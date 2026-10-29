from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)

SYSTEM_COMMANDS = {
    "start",
    "help",
    "new",
    "reset",
    "status",
    "projects",
    "skills",
    "cancel",
}


@dataclass
class SkillItem:
    name: str
    command_name: str
    description: str
    path: Path
    scope: str  # "workspace" | "global" | "builtin"


def normalize_command_name(name: str) -> str:
    """Normalize a skill name into a valid Telegram command name.

    Telegram command requirements: 1-32 characters, lowercase a-z, 0-9, and underscores.
    """
    clean = re.sub(r"[^a-zA-Z0-9_]", "_", name.lower())
    clean = re.sub(r"_+", "_", clean).strip("_")
    if not clean:
        clean = "skill"
    return clean[:32]


def sanitize_description_for_telegram(desc: str, fallback_name: str, max_len: int = 50) -> str:
    """Format description for Telegram BotCommand menu (short single line, max 50 chars)."""
    one_line = " ".join(desc.split()).strip()
    if not one_line:
        one_line = f"Execute {fallback_name} skill"
    if len(one_line) > max_len:
        one_line = one_line[: max_len - 3] + "..."
    return one_line


def parse_skill_frontmatter(path: Path) -> tuple[str, str]:
    """Parse YAML frontmatter from SKILL.md extracting name and description."""
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        logger.warning("Failed to read skill file %s: %s", path, e)
        return path.parent.name, ""

    if not content.startswith("---"):
        return path.parent.name, ""

    parts = content.split("---", 2)
    if len(parts) < 3:
        return path.parent.name, ""

    frontmatter = parts[1]
    name = path.parent.name
    desc_lines: list[str] = []
    in_desc = False

    for line in frontmatter.splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        if trimmed.startswith("name:"):
            in_desc = False
            raw_name = trimmed[len("name:") :].strip().strip("\"'")
            if raw_name:
                name = raw_name
        elif trimmed.startswith("description:"):
            in_desc = True
            raw_desc = trimmed[len("description:") :].strip().strip("\"'")
            if raw_desc and raw_desc not in (">-", ">", "|-", "|"):
                desc_lines.append(raw_desc)
        elif in_desc:
            if re.match(r"^[a-zA-Z0-9_\-]+:", trimmed):
                in_desc = False
            else:
                desc_lines.append(trimmed)

    description = " ".join(desc_lines).strip()
    return name, description


class SkillDiscovery:
    DEFAULT_GLOBAL_DIR = Path.home() / ".gemini" / "config" / "skills"
    DEFAULT_BUILTIN_DIR = Path.home() / ".gemini" / "antigravity" / "builtin" / "skills"

    @classmethod
    def discover_skills(
        cls,
        workspace_paths: Sequence[Path | str] | None = None,
        global_dir: Path | None = None,
        builtin_dir: Path | None = None,
    ) -> dict[str, SkillItem]:
        """Discover skills across workspace, global, and builtin directories.

        Precedence order (highest to lowest):
        1. Workspace (.agents/skills, .agent/skills)
        2. Global (~/.gemini/config/skills)
        3. Builtin (~/.gemini/antigravity/builtin/skills)
        """
        g_dir = global_dir or cls.DEFAULT_GLOBAL_DIR
        b_dir = builtin_dir or cls.DEFAULT_BUILTIN_DIR

        skills: dict[str, SkillItem] = {}

        # 1. Scan builtin skills (lowest precedence)
        cls._scan_directory(b_dir, "builtin", skills)

        # 2. Scan global skills (overrides builtin)
        cls._scan_directory(g_dir, "global", skills)

        # 3. Scan workspace skills (highest precedence)
        if workspace_paths:
            for ws in workspace_paths:
                ws_path = Path(ws).resolve()
                if not ws_path.is_dir():
                    continue
                candidates = [
                    ws_path / ".agents" / "skills",
                    ws_path / ".agent" / "skills",
                ]
                for cand in candidates:
                    cls._scan_directory(cand, "workspace", skills)

        return skills

    @classmethod
    def _scan_directory(cls, root: Path, scope: str, out_skills: dict[str, SkillItem]) -> None:
        if not root.is_dir():
            return

        for item in root.iterdir():
            if not item.is_dir():
                continue
            skill_md = item / "SKILL.md"
            if skill_md.is_file():
                name, desc = parse_skill_frontmatter(skill_md)
                cmd_name = normalize_command_name(name)
                out_skills[name] = SkillItem(
                    name=name,
                    command_name=cmd_name,
                    description=desc,
                    path=skill_md,
                    scope=scope,
                )


def wrap_skill_prompt(skill: SkillItem, user_query: str) -> str:
    """Format prompt with Antigravity official Desktop UI slash command metadata."""
    query = user_query.strip()
    return (
        f"<USER_REQUEST>\n"
        f"/{skill.name} {query}\n"
        f"</USER_REQUEST>\n"
        f"<ADDITIONAL_METADATA>\n"
        f"The user has mentioned some items in the form @[ITEM]. Here is extra information about the items that were mentioned by the user, in the order that they appear:\n\n"
        f"/{skill.name} is a [Slash Command]:\n"
        f'<SKILL>The user requested you read and use the "{skill.name}" skill. '
        f"The path to the skill file is:\n{skill.path.resolve()}</SKILL>\n"
        f"</ADDITIONAL_METADATA>"
    )


def match_skill_command(
    text: str, available_skills: dict[str, SkillItem]
) -> tuple[SkillItem, str] | None:
    """Check if message starts with /skill_name and match to an available skill."""
    if not text.startswith("/"):
        return None

    # Split command and query
    parts = text.split(maxsplit=1)
    raw_cmd = parts[0][1:]  # remove leading '/'
    # Strip potential @bot_username
    if "@" in raw_cmd:
        raw_cmd = raw_cmd.split("@", 1)[0]

    raw_cmd_lower = raw_cmd.lower()
    if raw_cmd_lower in SYSTEM_COMMANDS:
        return None

    query = parts[1] if len(parts) > 1 else ""

    # Check direct name match
    if raw_cmd_lower in available_skills:
        return available_skills[raw_cmd_lower], query

    # Check normalized command_name match (e.g. code_review -> code-review)
    for skill in available_skills.values():
        if skill.command_name == raw_cmd_lower:
            return skill, query

    # Check hyphenated variations
    alt_name = raw_cmd_lower.replace("_", "-")
    if alt_name in available_skills:
        return available_skills[alt_name], query

    return None
