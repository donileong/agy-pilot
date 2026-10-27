import json
from pathlib import Path

from agy_pilot.transcript import (
    StepTracker,
    ToolCall,
    TranscriptStep,
    format_tool_call,
    get_transcript_path,
)


def test_parse_step_with_string_thinking():
    raw = {
        "step_index": 142,
        "source": "MODEL",
        "type": "PLANNER_RESPONSE",
        "status": "DONE",
        "content": "Finished reading the file.",
        "thinking": "Let's review the contents carefully.",
        "tool_calls": [],
    }
    step = TranscriptStep.from_dict(raw)
    assert step.step_index == 142
    assert step.source == "MODEL"
    assert step.has_thinking() is True
    assert step.get_thinking_text() == "Let's review the contents carefully."
    assert step.content == "Finished reading the file."
    assert step.is_final_reply() == "Finished reading the file."


def test_parse_step_with_dict_thinking():
    raw = {
        "step_index": 143,
        "source": "MODEL",
        "type": "PLANNER_RESPONSE",
        "status": "DONE",
        "content": "Done",
        "thinking": {"text": "Thinking in structured format."},
        "tool_calls": [{"name": "read_file"}],
    }
    step = TranscriptStep.from_dict(raw)
    assert step.step_index == 143
    assert step.has_thinking() is True
    assert step.get_thinking_text() == "Thinking in structured format."
    assert len(step.tool_calls) == 1
    # Has tools, so is_final_reply should be None
    assert step.is_final_reply() is None


def test_thinking_edge_cases():
    step_none = TranscriptStep(thinking=None)
    assert step_none.has_thinking() is False
    assert step_none.get_thinking_text() is None

    step_bool_f = TranscriptStep(thinking=False)
    assert step_bool_f.has_thinking() is False

    step_bool_t = TranscriptStep(thinking=True)
    assert step_bool_t.has_thinking() is True

    step_dict_notext = TranscriptStep(thinking={"other": 123})
    assert step_dict_notext.has_thinking() is True
    assert "123" in step_dict_notext.get_thinking_text()


def test_format_tool_calls():
    # Explicit toolSummary
    tc1 = ToolCall(name="some_tool", args={"toolSummary": "Building container"})
    assert format_tool_call(tc1) == "Building container"

    # MCP tool
    tc2 = ToolCall(name="call_mcp_tool", args={"ServerName": "o2", "ToolName": "SearchSQL"})
    assert format_tool_call(tc2) == "o2/SearchSQL"

    tc2_single = ToolCall(name="call_mcp_tool", args={"ToolName": "SearchSQL"})
    assert format_tool_call(tc2_single) == "SearchSQL"

    # run_command short and long
    tc3_short = ToolCall(name="run_command", args={"CommandLine": "echo hello"})
    assert format_tool_call(tc3_short) == "Execute command: echo hello"

    tc3_long = ToolCall(name="run_command", args={"CommandLine": "a" * 50})
    assert format_tool_call(tc3_long).startswith("Execute command: ")
    assert format_tool_call(tc3_long).endswith("...")

    # view_file / read_file
    tc4 = ToolCall(name="view_file", args={"AbsolutePath": "/path/to/main.py"})
    assert format_tool_call(tc4) == "Read file main.py"

    tc4_read = ToolCall(name="read_file", args={"Path": "/path/to/data.json"})
    assert format_tool_call(tc4_read) == "Read file data.json"

    # write_to_file / write_file
    tc5 = ToolCall(name="write_to_file", args={"TargetFile": "/path/to/output.txt"})
    assert format_tool_call(tc5) == "Write file output.txt"

    # grep_search
    tc6 = ToolCall(name="grep_search", args={"Query": "TODO"})
    assert format_tool_call(tc6) == "Search code: TODO"

    # Fallback
    tc7 = ToolCall(name="custom_action", args={})
    assert format_tool_call(tc7) == "custom_action"


def test_step_tracker_and_live_markdown(tmp_path):
    log_file = tmp_path / "transcript.jsonl"
    tracker = StepTracker(log_file, initial_after_index=0)

    # Empty format
    assert "Initializing reasoning & planning" in tracker.format_live_markdown(elapsed_secs=2)

    # Add 5 steps to test folding of > 4 steps
    steps = [
        TranscriptStep(
            step_index=1, source="MODEL", step_type="PLANNER_RESPONSE", thinking="Thinking 1"
        ),
        TranscriptStep(
            step_index=2,
            source="MODEL",
            step_type="PLANNER_RESPONSE",
            tool_calls=[ToolCall(name="read_file", args={"Path": "a.txt"})],
        ),
        TranscriptStep(step_index=3, source="USER", step_type="GENERIC"),
        TranscriptStep(
            step_index=4, source="MODEL", step_type="PLANNER_RESPONSE", thinking="Thinking 4"
        ),
        TranscriptStep(
            step_index=5, source="MODEL", step_type="PLANNER_RESPONSE", content="Answering"
        ),
    ]

    for s in steps:
        tracker.process_step(s)

    md = tracker.format_live_markdown(elapsed_secs=10)
    assert "completed previous 1 steps" in md
    assert "▶ 🤖 Generating response `[Step 5]`" in md


def test_get_transcript_path(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cid = "test-conv-id"
    p = get_transcript_path(cid)
    assert cid in str(p)
    assert p.name == "transcript.jsonl"


def test_extract_user_prompt():
    from agy_pilot.transcript import extract_user_prompt

    # None or empty
    assert extract_user_prompt(None) is None
    assert extract_user_prompt("") is None

    # Normal text
    assert extract_user_prompt("Hello Antigravity") == "Hello Antigravity"

    # Enclosed in USER_REQUEST
    raw = "<USER_REQUEST>\nFix the bug in controller\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\ntime: 123\n</ADDITIONAL_METADATA>"
    assert extract_user_prompt(raw) == "Fix the bug in controller"

    # Stripping tags without USER_REQUEST
    raw2 = "Some user prompt\n<ADDITIONAL_METADATA>\ninfo\n</ADDITIONAL_METADATA>"
    assert extract_user_prompt(raw2) == "Some user prompt"


def test_get_last_turn_summary(tmp_path):
    from agy_pilot.transcript import get_last_turn_summary

    # 1. Non-existent file
    summary_empty = get_last_turn_summary(
        "non_existent_conv", path_override=tmp_path / "none.jsonl"
    )
    assert summary_empty.last_status == "empty"

    # 2. Complete turn with long reply
    t_file = tmp_path / "transcript.jsonl"
    long_reply = "A" * 500
    lines = [
        json.dumps(
            {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "content": "<USER_REQUEST>What is Python?</USER_REQUEST>",
            }
        ),
        json.dumps(
            {
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "content": long_reply,
                "tool_calls": [],
            }
        ),
    ]
    t_file.write_text("\n".join(lines), encoding="utf-8")

    summary_completed = get_last_turn_summary("conv1", path_override=t_file)
    assert summary_completed.last_status == "completed"
    assert summary_completed.user_prompt == "What is Python?"
    assert summary_completed.is_truncated is True
    assert len(summary_completed.assistant_reply) == 303  # 300 chars + "..."
    assert summary_completed.full_reply == long_reply

    # 3. Running tool turn
    lines_tool = [
        json.dumps(
            {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "content": "<USER_REQUEST>Search for bugs</USER_REQUEST>",
            }
        ),
        json.dumps(
            {
                "step_index": 1,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "tool_calls": [{"name": "run_command", "args": {"CommandLine": "pytest"}}],
            }
        ),
    ]
    t_file.write_text("\n".join(lines_tool), encoding="utf-8")
    summary_tool = get_last_turn_summary("conv2", path_override=t_file)
    assert summary_tool.last_status == "running_tool"
    assert summary_tool.last_tool_name == "Execute command: pytest"
    assert summary_tool.user_prompt == "Search for bugs"

    # 4. Pending reply turn
    lines_pending = [
        json.dumps(
            {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "content": "Just asked",
            }
        ),
    ]
    t_file.write_text("\n".join(lines_pending), encoding="utf-8")
    summary_pending = get_last_turn_summary("conv3", path_override=t_file)
    assert summary_pending.last_status == "pending_reply"
    assert summary_pending.user_prompt == "Just asked"


def test_format_steps_summary():
    from agy_pilot.transcript import ProgressStepItem, format_steps_summary

    # Empty
    assert "No detailed steps recorded" in format_steps_summary([])

    # Few items
    items = [
        ProgressStepItem(step_index=1, description="💭 Deep reasoning"),
        ProgressStepItem(step_index=2, description="🛠️ Tool execution: Read file main.py"),
    ]
    res = format_steps_summary(items, elapsed_secs=3.5)
    assert "2 steps" in res
    assert "3.5s" in res
    assert "1. 💭 Deep reasoning `[Step 1]`" in res
    assert "2. 🛠️ Tool execution: Read file main.py `[Step 2]`" in res

    # Many items (>40)
    many_items = [
        ProgressStepItem(step_index=i, description=f"Step description {i}") for i in range(1, 55)
    ]
    res_many = format_steps_summary(many_items)
    assert "54 steps" in res_many
    assert "omitted" in res_many
    assert "1. Step description 1 `[Step 1]`" in res_many
    assert "54. Step description 54 `[Step 54]`" in res_many


def test_get_steps_from_transcript(tmp_path):
    from agy_pilot.transcript import get_steps_from_transcript

    t_file = tmp_path / "transcript.jsonl"
    lines = [
        json.dumps(
            {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "hi"}
        ),
        json.dumps(
            {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "thinking": "plan"}
        ),
        json.dumps(
            {
                "step_index": 2,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "tool_calls": [{"name": "read_file", "args": {"Path": "a.txt"}}],
            }
        ),
        json.dumps(
            {
                "step_index": 3,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "content": "Done",
            }
        ),
    ]
    t_file.write_text("\n".join(lines), encoding="utf-8")

    # All steps after index 0
    steps = get_steps_from_transcript("c1", after_step_index=0, path_override=t_file)
    assert len(steps) == 3
    assert steps[0].step_index == 1
    assert steps[1].step_index == 2
    assert steps[2].step_index == 3

    # Filter with after_step_index = 2
    steps_filtered = get_steps_from_transcript("c1", after_step_index=2, path_override=t_file)
    assert len(steps_filtered) == 1
    assert steps_filtered[0].step_index == 3

    # Non-existent file
    assert get_steps_from_transcript("c2", path_override=tmp_path / "nonexistent.jsonl") == []
