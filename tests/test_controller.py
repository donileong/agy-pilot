import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agy_pilot.controller import (
    Antigravity2Controller,
    AntigravityProject,
)


def test_project_display_name():
    p1 = AntigravityProject("proj-12345678", ["file:///path/to/my-repo"], "2026-10-22", 5)
    assert p1.display_name == "my-repo"

    p2 = AntigravityProject("proj1234extra", [], "2026-10-22", 1)
    assert p2.display_name == "proj1234"

    p3 = AntigravityProject("abc", [], "2026-10-22", 1)
    assert p3.display_name == "abc"


def test_execute_agentapi_once(tmp_path):
    controller = Antigravity2Controller(
        executable_path=tmp_path / "ls",
        db_path=None,
        hub_address="127.0.0.1:5000",
        hub_csrf_token="token",
    )

    # Success JSON output
    mock_ok = MagicMock(returncode=0, stdout='{"response": {"ok": true}}', stderr="")
    with patch("subprocess.run", return_value=mock_ok):
        res = controller._execute_agentapi_once(["test"])
        assert res == {"response": {"ok": True}}

    # Empty stdout
    mock_empty = MagicMock(returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=mock_empty):
        assert controller._execute_agentapi_once(["test"]) == {}

    # Error status code
    mock_err = MagicMock(returncode=1, stdout="", stderr="Something went wrong")
    with patch("subprocess.run", return_value=mock_err):
        with pytest.raises(RuntimeError) as exc_info:
            controller._execute_agentapi_once(["test"])
        assert "Something went wrong" in str(exc_info.value)


def test_run_agentapi_connection_retry(tmp_path):
    controller = Antigravity2Controller(
        executable_path=tmp_path / "ls",
        db_path=None,
        hub_address="127.0.0.1:5000",
        hub_csrf_token="token",
    )

    with (
        patch.object(controller, "refresh_hub") as mock_refresh,
        patch.object(controller, "_execute_agentapi_once") as mock_exec,
    ):
        # First call fails with Unavailable, second succeeds
        mock_exec.side_effect = [
            RuntimeError("Unavailable: connection refused"),
            {"response": "success"},
        ]
        res = controller.run_agentapi(["test"])
        assert res == {"response": "success"}
        mock_refresh.assert_called_once()


def test_list_projects(tmp_path):
    repo_dir = tmp_path / "repo1"
    repo_dir.mkdir(parents=True)
    uri = repo_dir.as_uri()

    db_file = tmp_path / "test_summaries.db"
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE conversation_summaries (
            project_id TEXT,
            workspace_uris TEXT,
            last_modified_time TEXT
        );
    """)
    conn.execute(
        f"INSERT INTO conversation_summaries VALUES ('pid1', '[\"{uri}\"]', '2026-10-22 10:00:00');"
    )
    conn.execute(
        f"INSERT INTO conversation_summaries VALUES ('pid1', '[\"{uri}\"]', '2026-10-22 11:00:00');"
    )
    conn.execute(
        "INSERT INTO conversation_summaries VALUES ('outside-of-project', '[]', '2026-10-22 12:00:00');"
    )
    conn.commit()
    conn.close()

    controller = Antigravity2Controller(
        executable_path=tmp_path / "ls",
        db_path=db_file,
        hub_address=None,
        hub_csrf_token=None,
    )
    projects = controller.list_projects()
    assert len(projects) == 1
    assert projects[0].project_id == "pid1"
    assert projects[0].conversation_count == 2
    assert projects[0].display_name == "repo1"

    # Test get_project
    assert controller.get_project("pid1") is not None
    assert controller.get_project("pid1").display_name == "repo1"
    assert controller.get_project("nonexistent") is None


def test_list_projects_filters_deleted_paths(tmp_path):
    valid_dir = tmp_path / "active-repo"
    valid_dir.mkdir(parents=True)
    deleted_dir = tmp_path / "deleted-repo"  # Not created on disk

    db_file = tmp_path / "test_filter.db"
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE conversation_summaries (
            project_id TEXT,
            workspace_uris TEXT,
            last_modified_time TEXT
        );
    """)
    conn.execute(
        f"INSERT INTO conversation_summaries VALUES ('p_active', '[\"{valid_dir.as_uri()}\"]', '2026-10-22 12:00:00');"
    )
    conn.execute(
        f"INSERT INTO conversation_summaries VALUES ('p_deleted', '[\"{deleted_dir.as_uri()}\"]', '2026-10-22 10:00:00');"
    )
    conn.commit()
    conn.close()

    controller = Antigravity2Controller(
        executable_path=tmp_path / "ls",
        db_path=db_file,
        hub_address=None,
        hub_csrf_token=None,
    )

    # With validation (default)
    active_projects = controller.list_projects(validate_paths=True)
    assert len(active_projects) == 1
    assert active_projects[0].project_id == "p_active"

    # Without validation
    all_projects = controller.list_projects(validate_paths=False)
    assert len(all_projects) == 2


def test_list_projects_filters_default_cli_projects(tmp_path):
    repo_gui = tmp_path / "gui-repo"
    repo_gui.mkdir(parents=True)
    repo_cli = tmp_path / "cli-repo"
    repo_cli.mkdir(parents=True)

    db_file = tmp_path / "test_cli_filter.db"
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE conversation_summaries (
            project_id TEXT,
            workspace_uris TEXT,
            last_modified_time TEXT
        );
    """)
    conn.execute(
        f"INSERT INTO conversation_summaries VALUES ('p_gui', '[\"{repo_gui.as_uri()}\"]', '2026-10-22 12:00:00');"
    )
    conn.execute(
        f"INSERT INTO conversation_summaries VALUES ('default-cli-project', '[\"{repo_cli.as_uri()}\"]', '2026-10-22 11:00:00');"
    )
    conn.commit()
    conn.close()

    controller = Antigravity2Controller(
        executable_path=tmp_path / "ls",
        db_path=db_file,
        hub_address=None,
        hub_csrf_token=None,
    )

    # Default: filter out CLI projects
    projects_default = controller.list_projects()
    assert len(projects_default) == 1
    assert projects_default[0].project_id == "p_gui"

    # Explicit: include CLI projects
    projects_all = controller.list_projects(include_cli_projects=True)
    assert len(projects_all) == 2
    pids = {p.project_id for p in projects_all}
    assert "p_gui" in pids
    assert "default-cli-project" in pids


def test_list_projects_merges_canonical_paths_and_pids(tmp_path):
    repo_dir = tmp_path / "shared-repo"
    repo_dir.mkdir(parents=True)
    uri1 = repo_dir.as_uri()
    uri2 = repo_dir.as_uri() + "/"  # Trailing slash variation

    db_file = tmp_path / "test_merge.db"
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE conversation_summaries (
            project_id TEXT,
            workspace_uris TEXT,
            last_modified_time TEXT
        );
    """)
    # Session 1: pid_old with 3 convs
    conn.execute(
        f"INSERT INTO conversation_summaries VALUES ('pid_old', '[\"{uri1}\"]', '2026-10-20 10:00:00');"
    )
    conn.execute(
        f"INSERT INTO conversation_summaries VALUES ('pid_old', '[\"{uri1}\"]', '2026-10-20 11:00:00');"
    )
    conn.execute(
        f"INSERT INTO conversation_summaries VALUES ('pid_old', '[\"{uri2}\"]', '2026-10-20 12:00:00');"
    )
    # Session 2: pid_new with 2 convs
    conn.execute(
        f"INSERT INTO conversation_summaries VALUES ('pid_new', '[\"{uri1}\"]', '2026-10-25 10:00:00');"
    )
    conn.execute(
        f"INSERT INTO conversation_summaries VALUES ('pid_new', '[\"{uri2}\"]', '2026-10-25 15:00:00');"
    )
    conn.commit()
    conn.close()

    controller = Antigravity2Controller(
        executable_path=tmp_path / "ls",
        db_path=db_file,
        hub_address=None,
        hub_csrf_token=None,
    )

    projects = controller.list_projects()
    assert len(projects) == 1
    p = projects[0]
    # Merged under newest active project id
    assert p.project_id == "pid_new"
    assert p.conversation_count == 5
    assert "pid_new" in p.related_project_ids
    assert "pid_old" in p.related_project_ids

    # Both project IDs can be retrieved via get_project
    assert controller.get_project("pid_new") is not None
    assert controller.get_project("pid_old") is not None


def test_list_projects_disambiguates_duplicate_folder_names(tmp_path):
    dir_work = tmp_path / "work" / "client"
    dir_work.mkdir(parents=True)
    dir_pers = tmp_path / "personal" / "client"
    dir_pers.mkdir(parents=True)

    db_file = tmp_path / "test_disambiguate.db"
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE conversation_summaries (
            project_id TEXT,
            workspace_uris TEXT,
            last_modified_time TEXT
        );
    """)
    conn.execute(
        f"INSERT INTO conversation_summaries VALUES ('p_work', '[\"{dir_work.as_uri()}\"]', '2026-10-25 12:00:00');"
    )
    conn.execute(
        f"INSERT INTO conversation_summaries VALUES ('p_pers', '[\"{dir_pers.as_uri()}\"]', '2026-10-25 10:00:00');"
    )
    conn.commit()
    conn.close()

    controller = Antigravity2Controller(
        executable_path=tmp_path / "ls",
        db_path=db_file,
        hub_address=None,
        hub_csrf_token=None,
    )

    projects = controller.list_projects()
    assert len(projects) == 2
    names = {p.display_name for p in projects}
    assert "client (work)" in names
    assert "client (personal)" in names


def test_list_conversations_and_project_lookup(tmp_path):
    db_file = tmp_path / "test_conversations.db"
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE conversation_summaries (
            conversation_id TEXT,
            project_id TEXT,
            last_modified_time TEXT,
            title TEXT
        );
    """)
    conn.execute(
        "INSERT INTO conversation_summaries VALUES ('c1', 'projA', '2026-10-23 12:00:00', 'Task 1');"
    )
    conn.execute(
        "INSERT INTO conversation_summaries VALUES ('c2', 'projA', '2026-10-23 11:00:00', 'Task 2');"
    )
    conn.execute(
        "INSERT INTO conversation_summaries VALUES ('c3', 'projB', '2026-10-23 10:00:00', '');"
    )
    conn.commit()
    conn.close()

    controller = Antigravity2Controller(
        executable_path=tmp_path / "ls",
        db_path=db_file,
        hub_address=None,
        hub_csrf_token=None,
    )

    items, total = controller.list_conversations("projA", limit=5, offset=0)
    assert total == 2
    assert len(items) == 2
    assert items[0].conversation_id == "c1"
    assert items[0].display_title == "Task 1"

    # Empty title fallback
    items_b, total_b = controller.list_conversations("projB", limit=5, offset=0)
    assert total_b == 1
    assert "2026-10-23" in items_b[0].display_title

    # Multi-project IDs lookup
    items_multi, total_multi = controller.list_conversations(["projA", "projB"], limit=5, offset=0)
    assert total_multi == 3
    assert len(items_multi) == 3

    # Test get_conversation_project_id
    assert controller.get_conversation_project_id("c1") == "projA"
    assert controller.get_conversation_project_id("c3") == "projB"
    assert controller.get_conversation_project_id("nonexistent") is None


def test_conversation_helpers(tmp_path):
    controller = Antigravity2Controller(
        executable_path=tmp_path / "ls",
        db_path=None,
        hub_address=None,
        hub_csrf_token=None,
    )

    # get_conversation_metadata
    mock_meta = {
        "response": {
            "conversationMetadata": {
                "metadata": {
                    "createdAt": "2026-10-22T10:00:00Z",
                    "workspaceUris": ["file:///path/repo"],
                }
            }
        }
    }
    with patch.object(controller, "run_agentapi", return_value=mock_meta):
        meta = controller.get_conversation_metadata("conv-1")
        assert meta.conversation_id == "conv-1"
        assert meta.created_at == "2026-10-22T10:00:00Z"
        assert meta.workspace_uris == ["file:///path/repo"]

    # new_conversation
    mock_new = {"response": {"newConversation": {"conversationId": "new-conv-999"}}}
    with patch.object(controller, "run_agentapi", return_value=mock_new):
        cid = controller.new_conversation(prompt="Hi", title="Test Title")
        assert cid == "new-conv-999"

    # send_message
    with patch.object(controller, "run_agentapi") as mock_api:
        controller.send_message("conv-1", "msg")
        mock_api.assert_called_with(["send-message", "conv-1", "msg"], None)


def test_get_last_step_index(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cid = "test-conv-last-step"
    log_file = (
        tmp_path
        / ".gemini"
        / "antigravity"
        / "brain"
        / cid
        / ".system_generated"
        / "logs"
        / "transcript.jsonl"
    )
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text(
        json.dumps({"step_index": 5, "source": "MODEL", "type": "PLANNER_RESPONSE"})
        + "\n"
        + json.dumps({"step_index": 12, "source": "MODEL", "type": "PLANNER_RESPONSE"})
        + "\n"
    )

    controller = Antigravity2Controller(
        executable_path=tmp_path / "ls",
        db_path=None,
        hub_address=None,
        hub_csrf_token=None,
    )
    assert controller.get_last_step_index(cid) == 12
    assert controller.get_last_step_index("nonexistent-conv") == -1


@pytest.mark.asyncio
async def test_wait_for_model_reply_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cid = "test-conv-reply"
    log_file = (
        tmp_path
        / ".gemini"
        / "antigravity"
        / "brain"
        / cid
        / ".system_generated"
        / "logs"
        / "transcript.jsonl"
    )
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Write steps: running step with thinking, followed by final done step
    log_file.write_text(
        json.dumps(
            {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "thinking": "Thinking"}
        )
        + "\n"
        + json.dumps(
            {
                "step_index": 2,
                "source": "MODEL",
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "content": "Final reply here",
            }
        )
        + "\n"
    )

    controller = Antigravity2Controller(
        executable_path=tmp_path / "ls",
        db_path=None,
        hub_address=None,
        hub_csrf_token=None,
    )

    progress_calls = []
    step_calls = []

    reply = await controller.wait_for_model_reply(
        conversation_id=cid,
        after_step_index=0,
        timeout_secs=2.0,
        progress_callback=lambda board: progress_calls.append(board),
        step_callback=lambda s: step_calls.append(s),
    )

    assert reply == "Final reply here"
    assert len(step_calls) >= 1
