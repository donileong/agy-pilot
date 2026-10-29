from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    name: str = ""
    args: dict[str, Any] | None = None


@dataclass
class QuestionOption:
    index: int
    text: str


@dataclass
class InteractionPrompt:
    step_index: int
    question: str
    options: list[QuestionOption]
    is_multi_select: bool = False
    raw_args: dict[str, Any] = field(default_factory=dict)


@dataclass
class TranscriptStep:
    step_index: int = 0
    source: str = ""
    step_type: str = ""
    status: str | None = None
    content: str | None = None
    thinking: Any = None
    created_at: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TranscriptStep:
        raw_tools = data.get("tool_calls") or []
        tools: list[ToolCall] = []
        for t in raw_tools:
            if isinstance(t, dict):
                tools.append(ToolCall(name=t.get("name", ""), args=t.get("args")))

        return cls(
            step_index=data.get("step_index", 0),
            source=data.get("source", ""),
            step_type=data.get("type", ""),
            status=data.get("status"),
            content=data.get("content"),
            thinking=data.get("thinking"),
            created_at=data.get("created_at"),
            tool_calls=tools,
        )

    @classmethod
    def parse_line(cls, line: str) -> TranscriptStep | None:
        trimmed = line.strip()
        if not trimmed:
            return None
        try:
            data = json.loads(trimmed)
            if isinstance(data, dict):
                return cls.from_dict(data)
        except Exception as e:
            logger.warning("Failed to parse transcript line: %s, line: %s", e, trimmed[:100])
        return None

    def has_thinking(self) -> bool:
        if self.thinking is None:
            return False
        if isinstance(self.thinking, bool):
            return self.thinking
        if isinstance(self.thinking, str):
            return bool(self.thinking.strip())
        if isinstance(self.thinking, dict):
            return bool(self.thinking)
        return True

    def get_thinking_text(self) -> str | None:
        if isinstance(self.thinking, str):
            return self.thinking
        if isinstance(self.thinking, dict):
            if "text" in self.thinking and isinstance(self.thinking["text"], str):
                return self.thinking["text"]
            return json.dumps(self.thinking, ensure_ascii=False)
        return None

    def is_final_reply(self) -> str | None:
        if (
            self.source == "MODEL"
            and self.step_type == "PLANNER_RESPONSE"
            and self.status == "DONE"
        ):
            if not self.tool_calls:
                if self.content:
                    cleaned = self.content.strip()
                    if cleaned:
                        return cleaned
        return None

    def get_ask_question_prompt(self) -> InteractionPrompt | None:
        for tc in self.tool_calls:
            if tc.name == "ask_question" and tc.args:
                questions_val = tc.args.get("questions")
                if isinstance(questions_val, str):
                    try:
                        questions_val = json.loads(questions_val)
                    except Exception:
                        questions_val = []
                if isinstance(questions_val, list) and questions_val:
                    first_q = questions_val[0]
                    if isinstance(first_q, dict):
                        q_text = str(first_q.get("question", "")).strip()
                        raw_opts = first_q.get("options", [])
                        opts = [
                            QuestionOption(index=i, text=str(opt).strip())
                            for i, opt in enumerate(raw_opts)
                        ]
                        is_multi = bool(first_q.get("is_multi_select", False))
                        return InteractionPrompt(
                            step_index=self.step_index,
                            question=q_text,
                            options=opts,
                            is_multi_select=is_multi,
                            raw_args=tc.args,
                        )
        return None


def _format_mcp(args: dict[str, Any]) -> str | None:
    server = args.get("ServerName", "")
    tool = args.get("ToolName", "")
    if server and tool:
        return f"{server}/{tool}"
    return str(tool) if tool else None


def _format_cmd(args: dict[str, Any]) -> str | None:
    if cmd := args.get("CommandLine"):
        first_line = cmd.splitlines()[0] if str(cmd).splitlines() else str(cmd)
        snippet = f"{first_line[:30]}..." if len(first_line) > 30 else first_line
        return f"Execute command: {snippet}"
    return None


def _format_read(args: dict[str, Any]) -> str | None:
    if path := (args.get("AbsolutePath") or args.get("Path")):
        return f"Read file {Path(path).name}"
    return None


def _format_write(args: dict[str, Any]) -> str | None:
    if path := (args.get("TargetFile") or args.get("Path")):
        return f"Write file {Path(path).name}"
    return None


def _format_grep(args: dict[str, Any]) -> str | None:
    if q := args.get("Query"):
        return f"Search code: {q}"
    return None


TOOL_FORMATTERS: dict[str, Any] = {
    "call_mcp_tool": _format_mcp,
    "run_command": _format_cmd,
    "view_file": _format_read,
    "read_file": _format_read,
    "write_to_file": _format_write,
    "write_file": _format_write,
    "grep_search": _format_grep,
}


def format_tool_call(tc: ToolCall) -> str:
    if tc.args and isinstance(tc.args, dict):
        args = tc.args
        # Explicit human summary first
        if summary := (args.get("toolSummary") or args.get("toolAction")):
            if isinstance(summary, str) and summary.strip():
                return summary.strip()

        if formatter := TOOL_FORMATTERS.get(tc.name):
            if formatted := formatter(args):
                return formatted

    return tc.name or "tool"


@dataclass
class ProgressStepItem:
    step_index: int
    description: str


class StepTracker:
    def __init__(self, file_path: Path | str | None = None, initial_after_index: int = -1):
        self.file_path = Path(file_path) if file_path else None
        self.last_seen_index = initial_after_index
        self.steps: list[ProgressStepItem] = []
        self._file_offset: int = 0

    def process_step(self, step: TranscriptStep) -> ProgressStepItem | None:
        if step.step_index <= self.last_seen_index:
            return None
        self.last_seen_index = step.step_index

        if prompt := step.get_ask_question_prompt():
            snippet = prompt.question.splitlines()[0] if prompt.question else ""
            if len(snippet) > 30:
                snippet = f"{snippet[:30]}..."
            desc = f"❓ Awaiting user decision: {snippet}"
        elif step.tool_calls:
            tools_str = ", ".join(format_tool_call(tc) for tc in step.tool_calls)
            desc = f"🛠️ Tool execution: {tools_str}"
        elif step.step_type == "GENERIC":
            desc = "📊 Tool completed, analyzing data"
        elif step.has_thinking():
            desc = "💭 Deep reasoning & task planning"
        elif step.content and step.source == "MODEL":
            desc = "🤖 Generating response"
        else:
            return None

        item = ProgressStepItem(step_index=step.step_index, description=desc)
        self.steps.append(item)
        return item

    def get_new_steps(self) -> list[TranscriptStep]:
        if not self.file_path or not self.file_path.exists():
            return []

        new_steps: list[TranscriptStep] = []
        try:
            with self.file_path.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(self._file_offset)
                for line in f:
                    if step := TranscriptStep.parse_line(line):
                        if step.step_index > self.last_seen_index:
                            new_steps.append(step)
                            self.last_seen_index = max(self.last_seen_index, step.step_index)
                self._file_offset = f.tell()
        except Exception as e:
            logger.warning("Error reading transcript file %s: %s", self.file_path, e)

        return new_steps

    def format_live_markdown(self, elapsed_secs: int) -> str:
        out = f"⚡️ *Antigravity Execution in Progress* ({elapsed_secs}s)\n\n"
        if not self.steps:
            out += "💭 Initializing reasoning & planning...\n"
            return out

        out += "📋 *Current Execution Steps:*\n"
        total = len(self.steps)
        max_visible = 4
        start_idx = 0
        if total > max_visible:
            omitted = total - max_visible
            out += f"  _...completed previous {omitted} steps_\n"
            start_idx = omitted

        for i, item in enumerate(self.steps[start_idx:], start=start_idx):
            is_last = i + 1 == total
            prefix = "▶" if is_last else "✓"
            out += f"  {prefix} {item.description} `[Step {item.step_index}]`\n"

        return out


def format_steps_summary(steps: list[ProgressStepItem], elapsed_secs: float | None = None) -> str:
    if not steps:
        return "📋 *No detailed steps recorded for this execution*"

    total = len(steps)
    header = f"📋 *Full Execution Trace* ({total} steps"
    if elapsed_secs is not None:
        header += f", elapsed {elapsed_secs:.1f}s"
    header += "):\n\n"

    lines: list[str] = []
    max_items = 40
    if total > max_items:
        head_count = 25
        tail_count = 10
        omitted = total - head_count - tail_count
        for i, item in enumerate(steps[:head_count], 1):
            lines.append(f"{i}. {item.description} `[Step {item.step_index}]`")
        lines.append(f"\n  _...omitted {omitted} intermediate steps...\n")
        for i, item in enumerate(steps[total - tail_count :], total - tail_count + 1):
            lines.append(f"{i}. {item.description} `[Step {item.step_index}]`")
    else:
        for i, item in enumerate(steps, 1):
            lines.append(f"{i}. {item.description} `[Step {item.step_index}]`")

    return header + "\n".join(lines)


def get_steps_from_transcript(
    conversation_id: str,
    after_step_index: int = -1,
    path_override: Path | None = None,
) -> list[ProgressStepItem]:
    path = path_override or get_transcript_path(conversation_id)
    if not path.is_file():
        return []
    tracker = StepTracker(initial_after_index=after_step_index)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if step := TranscriptStep.parse_line(line):
                    if step.step_index <= after_step_index:
                        continue
                    tracker.process_step(step)
    except Exception as e:
        logger.warning("Failed to read transcript for steps (%s): %s", conversation_id, e)
    return tracker.steps


def get_transcript_path(conversation_id: str) -> Path:
    home = Path.home()
    candidates = [
        home
        / ".gemini"
        / "antigravity"
        / "brain"
        / conversation_id
        / ".system_generated"
        / "logs"
        / "transcript.jsonl",
        home
        / ".gemini"
        / "antigravity-ide"
        / "brain"
        / conversation_id
        / ".system_generated"
        / "logs"
        / "transcript.jsonl",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


@dataclass
class LastTurnSummary:
    user_prompt: str | None = None
    assistant_reply: str | None = None
    last_status: str = "empty"  # "completed", "running_tool", "pending_reply", "empty"
    last_tool_name: str | None = None
    is_truncated: bool = False
    full_reply: str | None = None


def extract_user_prompt(raw: str | None) -> str | None:
    if not raw:
        return None
    match = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", raw, re.DOTALL)
    if match:
        cleaned = match.group(1).strip()
    else:
        cleaned = re.sub(
            r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", "", raw, flags=re.DOTALL
        )
        cleaned = re.sub(
            r"<USER_SETTINGS_CHANGE>.*?</USER_SETTINGS_CHANGE>", "", cleaned, flags=re.DOTALL
        )
        cleaned = cleaned.strip()
    return cleaned if cleaned else None


def get_last_turn_summary(
    conversation_id: str,
    path_override: Path | None = None,
) -> LastTurnSummary:
    path = path_override or get_transcript_path(conversation_id)
    if not path.is_file():
        return LastTurnSummary(last_status="empty")

    # Prefer transcript_full.jsonl if present to avoid truncated fields
    full_path = path.with_name("transcript_full.jsonl")
    target_path = full_path if full_path.is_file() else path

    last_user_prompt: str | None = None
    last_assistant_reply: str | None = None
    last_step: TranscriptStep | None = None
    last_tool_step: TranscriptStep | None = None

    try:
        with target_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                step = TranscriptStep.parse_line(line)
                if not step:
                    continue
                last_step = step

                if step.step_type == "USER_INPUT" or step.source == "USER_EXPLICIT":
                    if prompt := extract_user_prompt(step.content):
                        last_user_prompt = prompt

                if step.source == "MODEL" and step.step_type == "PLANNER_RESPONSE":
                    if step.tool_calls:
                        last_tool_step = step
                    elif step.content and step.content.strip():
                        last_assistant_reply = step.content.strip()
                        last_tool_step = None
    except Exception as e:
        logger.warning(
            "Failed to parse transcript for last turn summary (%s): %s", conversation_id, e
        )
        return LastTurnSummary(last_status="empty")

    if not last_step:
        return LastTurnSummary(last_status="empty")

    if last_assistant_reply:
        full_reply = last_assistant_reply
        max_preview_len = 300
        is_trunc = len(full_reply) > max_preview_len
        preview_text = f"{full_reply[:max_preview_len]}..." if is_trunc else full_reply
        return LastTurnSummary(
            user_prompt=last_user_prompt,
            assistant_reply=preview_text,
            last_status="completed",
            is_truncated=is_trunc,
            full_reply=full_reply,
        )

    if last_tool_step and last_tool_step.tool_calls:
        tool_name = format_tool_call(last_tool_step.tool_calls[0])
        return LastTurnSummary(
            user_prompt=last_user_prompt,
            last_status="running_tool",
            last_tool_name=tool_name,
        )

    if last_user_prompt:
        return LastTurnSummary(
            user_prompt=last_user_prompt,
            last_status="pending_reply",
        )

    return LastTurnSummary(last_status="empty")


def format_duration(seconds: float) -> str:
    secs = max(0, int(round(seconds)))
    if secs < 60:
        return f"{secs}s"
    minutes = secs // 60
    rem_secs = secs % 60
    if minutes < 60:
        return f"{minutes}m {rem_secs}s"
    hours = minutes // 60
    rem_mins = minutes % 60
    return f"{hours}h {rem_mins}m"


@dataclass
class FileChange:
    path: str
    action: str = "modified"  # "created", "modified", "deleted"


@dataclass
class RunRecap:
    step_count: int = 0
    duration_seconds: float = 0.0
    touched_files: list[FileChange] = field(default_factory=list)
    git_diff_stat: str | None = None

    def format_markdown(self) -> str:
        lines: list[str] = []
        dur_str = format_duration(self.duration_seconds) if self.duration_seconds > 0 else ""
        dur_part = f" · {dur_str}" if dur_str else ""
        step_word = "step" if self.step_count == 1 else "steps"

        if not self.touched_files:
            lines.append(
                f"⚡️ *Run Recap* · {self.step_count} {step_word}{dur_part} · _read-only (no files modified)_"
            )
            return "\n".join(lines)

        file_count = len(self.touched_files)
        file_word = "file" if file_count == 1 else "files"
        lines.append(
            f"⚡️ *Run Recap* · {file_count} {file_word} touched · {self.step_count} {step_word}{dur_part}\n"
        )

        lines.append(f"📝 *Touched Files ({file_count})*")
        max_files = 10
        if file_count > max_files:
            for fc in self.touched_files[:8]:
                lines.append(f"• `{fc.path}` _({fc.action})_")
            lines.append(f"• _...and {file_count - 8} more files_")
        else:
            for fc in self.touched_files:
                lines.append(f"• `{fc.path}` _({fc.action})_")

        if self.git_diff_stat:
            lines.append(f"\n📊 *Git Diff Stats*\n```text\n{self.git_diff_stat}\n```")

        return "\n".join(lines)


def _extract_file_path(args: dict[str, Any]) -> str | None:
    for key in ("Path", "path", "TargetFile", "file_path", "AbsolutePath", "FilePath"):
        if val := args.get(key):
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def _normalize_path(file_path: str, workspace_dir: Path | str | None = None) -> str:
    p = Path(file_path)
    if workspace_dir:
        try:
            ws_path = Path(workspace_dir).resolve()
            if p.is_absolute():
                p_resolved = p.resolve()
                return p_resolved.relative_to(ws_path).as_posix()
        except Exception:
            pass
    return p.as_posix()


FILE_TOOL_ACTIONS: dict[str, str] = {
    "delete_file": "deleted",
    "edit_file": "modified",
    "create_file": "created",
    "write_file": "created",
    "write_to_file": "created",
    "create_or_update_file": "modified",
}


def get_git_diff_stat(repo_dir: Path | str | None, max_lines: int = 10) -> str | None:
    if not repo_dir:
        return None
    repo_path = Path(repo_dir)
    if not repo_path.is_dir():
        return None
    try:
        import subprocess

        res = subprocess.run(
            ["git", "diff", "HEAD", "--stat"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
        out = ""
        if res.returncode == 0 and res.stdout.strip():
            out = res.stdout.strip()
        else:
            res2 = subprocess.run(
                ["git", "diff", "--stat"],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                timeout=3.0,
                check=False,
            )
            if res2.returncode == 0 and res2.stdout.strip():
                out = res2.stdout.strip()

        if not out:
            return None

        lines = out.splitlines()
        if len(lines) > max_lines:
            head_lines = lines[: max_lines - 2]
            tail_line = lines[-1]
            return "\n".join(
                head_lines + [f"... ({len(lines) - max_lines + 1} more files)", tail_line]
            )
        return out
    except Exception as e:
        logger.debug("Failed to get git diff stat: %s", e)
        return None


def extract_run_recap(
    steps: list[TranscriptStep] | list[dict[str, Any]],
    duration_seconds: float = 0.0,
    workspace_dir: Path | str | None = None,
    include_git_diff: bool = False,
    step_count: int | None = None,
) -> RunRecap:
    parsed_steps: list[TranscriptStep] = []
    for s in steps:
        if isinstance(s, TranscriptStep):
            parsed_steps.append(s)
        elif isinstance(s, dict):
            if parsed := TranscriptStep.from_dict(s):
                parsed_steps.append(parsed)

    actual_step_count = step_count if step_count is not None else len(parsed_steps)
    touched: dict[str, FileChange] = {}

    for step in parsed_steps:
        for tc in step.tool_calls:
            t_name = tc.name
            t_args = tc.args or {}

            # Handle MCP wrapper calls (e.g., github/create_or_update_file)
            if t_name == "call_mcp_tool":
                if sub_tool := t_args.get("ToolName"):
                    t_name = str(sub_tool)
                if isinstance(t_args.get("Arguments"), dict):
                    t_args = t_args["Arguments"]

            if t_name in FILE_TOOL_ACTIONS:
                raw_path = _extract_file_path(t_args)
                if not raw_path:
                    continue
                norm_path = _normalize_path(raw_path, workspace_dir)

                action = FILE_TOOL_ACTIONS[t_name]
                if t_name in ("write_file", "write_to_file"):
                    if t_args.get("Overwrite") is True or t_args.get("Append") is True:
                        action = "modified"

                if norm_path not in touched:
                    touched[norm_path] = FileChange(path=norm_path, action=action)
                else:
                    if action == "deleted":
                        touched[norm_path].action = "deleted"
                    elif touched[norm_path].action != "created":
                        touched[norm_path].action = action

    git_diff: str | None = None
    if include_git_diff and workspace_dir:
        git_diff = get_git_diff_stat(workspace_dir)

    return RunRecap(
        step_count=actual_step_count,
        duration_seconds=duration_seconds,
        touched_files=list(touched.values()),
        git_diff_stat=git_diff,
    )
