from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import subprocess
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from .discovery import Discovery
from .transcript import (
    InteractionPrompt,
    LastTurnSummary,
    ProgressStepItem,
    StepTracker,
    ToolApprovalPrompt,
    TranscriptStep,
    get_last_turn_summary,
    get_steps_from_transcript,
    get_transcript_path,
)

logger = logging.getLogger(__name__)

HEADLESS_REMOTE_EXECUTION_CONSTRAINTS = (
    "(System Notice:\n"
    "1. The user is operating remotely via an instant messaging bridge. Interactive question modals (ask_question) are fully supported via interactive buttons.\n"
    "2. If you need user decision or confirmation, invoke ask_question tool with 2-4 concise choices.\n"
    "3. Prioritize autonomous task execution. Only ask questions when encountering critical branching decisions.)\n\n"
    "[HEADLESS REMOTE EXECUTION CONSTRAINTS]:\n"
    "Remote IM bridge is active. Interactive question modals (ask_question) are fully supported."
)


def inject_headless_constraints(prompt: str) -> str:
    return f"{prompt}\n\n{HEADLESS_REMOTE_EXECUTION_CONSTRAINTS}"


@dataclass
class AntigravityProject:
    project_id: str
    workspace_uris: list[str]
    last_active: str
    conversation_count: int
    related_project_ids: list[str] = field(default_factory=list)
    custom_display_name: str | None = None

    @property
    def display_name(self) -> str:
        if self.custom_display_name:
            return self.custom_display_name
        if self.workspace_uris:
            first = self.workspace_uris[0]
            if first.startswith("file://"):
                parsed = urlparse(first)
                path_str = unquote(parsed.path)
            else:
                path_str = first
            name = Path(path_str).name
            if name:
                return name
        return self.project_id[:8] if len(self.project_id) >= 8 else self.project_id


@dataclass
class ConversationMetadata:
    conversation_id: str
    workspace_uris: list[str]
    created_at: str


@dataclass
class ConversationItem:
    conversation_id: str
    project_id: str
    last_modified: str
    title: str = ""

    @property
    def display_title(self) -> str:
        if self.title and self.title.strip():
            return self.title.strip()
        date_part = self.last_modified.split(" ")[0] if self.last_modified else ""
        if date_part:
            return f"Session {date_part} ({self.conversation_id[:6]})"
        return f"Session {self.conversation_id[:8]}"


class Antigravity2Controller:
    def __init__(
        self,
        executable_path: Path,
        db_path: Path | None,
        hub_address: str | None,
        hub_csrf_token: str | None,
    ):
        self.executable_path = executable_path
        self.db_path = db_path
        self.hub_address = hub_address
        self.hub_csrf_token = hub_csrf_token

    @classmethod
    def discover(cls) -> Antigravity2Controller:
        exe = Discovery.discover_executable()
        if not exe:
            raise FileNotFoundError(
                f"Antigravity language_server binary not found. Checked: {Discovery.DEFAULT_EXECUTABLE_PATHS}"
            )
        db_path = Discovery.discover_db()
        hub_addr, hub_csrf = Discovery.discover_hub()

        logger.info(
            "Initialized Antigravity2Controller: exe=%s, hub=%s, csrf=%s",
            exe,
            hub_addr,
            hub_csrf[:8] if hub_csrf else None,
        )
        return cls(
            executable_path=exe,
            db_path=db_path,
            hub_address=hub_addr,
            hub_csrf_token=hub_csrf,
        )

    def refresh_hub(self) -> tuple[str | None, str | None]:
        new_hub, new_csrf = Discovery.discover_hub()
        logger.info("Dynamically refreshed Antigravity Hub: %s", new_hub)
        self.hub_address = new_hub
        self.hub_csrf_token = new_csrf
        return new_hub, new_csrf

    def run_agentapi(self, args: list[str], project_id: str | None = None) -> dict[str, Any]:
        try:
            return self._execute_agentapi_once(args, project_id)
        except Exception as e:
            err_str = str(e)
            is_conn_err = any(
                term in err_str
                for term in [
                    "Unavailable",
                    "connection error",
                    "connection refused",
                    "Error while dialing",
                    "connection reset",
                    "broken pipe",
                ]
            )
            if is_conn_err:
                logger.warning(
                    "Detected connection error to language_server (%s), refreshing hub and retrying...",
                    err_str,
                )
                self.refresh_hub()
                return self._execute_agentapi_once(args, project_id)
            raise

    def _execute_agentapi_once(
        self, args: list[str], project_id: str | None = None
    ) -> dict[str, Any]:
        cmd = [str(self.executable_path), "agentapi", *args]
        env = {k: v for k, v in os.environ.items() if not k.startswith("ANTIGRAVITY_")}

        if self.hub_address:
            env["ANTIGRAVITY_LS_ADDRESS"] = self.hub_address
        if self.hub_csrf_token:
            env["ANTIGRAVITY_CSRF_TOKEN"] = self.hub_csrf_token
        if project_id:
            env["ANTIGRAVITY_PROJECT_ID"] = project_id

        res = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
        stdout = res.stdout.strip()
        stderr = res.stderr.strip()

        if res.returncode != 0:
            err_msg = stderr if stderr else stdout
            logger.error("agentapi failed code %d: %s", res.returncode, err_msg)
            raise RuntimeError(f"agentapi failed with code {res.returncode}: {err_msg}")

        if not stdout:
            return {}

        try:
            return json.loads(stdout)
        except Exception as e:
            raise ValueError(f"Failed to parse agentapi JSON response: {stdout}") from e

    @staticmethod
    def _parse_workspace_uris(uris_raw: Any) -> list[str]:
        if not uris_raw:
            return []
        try:
            uris = json.loads(uris_raw)
            if isinstance(uris, list):
                return [str(u) for u in uris if u]
            if isinstance(uris, str) and uris:
                return [uris]
        except Exception:
            pass
        return [str(uris_raw)] if str(uris_raw).strip() else []

    @staticmethod
    def _extract_workspace_path(uris: list[str]) -> Path | None:
        if not uris:
            return None
        first = uris[0]
        if not first:
            return None
        try:
            if first.startswith("file://"):
                parsed = urlparse(first)
                path_str = unquote(parsed.path)
            else:
                path_str = first
            if path_str:
                return Path(path_str)
        except Exception:
            pass
        return None

    def list_projects(
        self,
        validate_paths: bool = True,
        include_cli_projects: bool = False,
    ) -> list[AntigravityProject]:
        if not self.db_path or not self.db_path.is_file():
            return []

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cli_filter = "" if include_cli_projects else "AND project_id != 'default-cli-project'"
            cursor.execute(
                f"""
                SELECT project_id, workspace_uris, last_modified_time
                FROM conversation_summaries
                WHERE project_id != '' AND project_id != 'outside-of-project' {cli_filter}
                ORDER BY last_modified_time DESC;
                """
            )
            rows = cursor.fetchall()
            conn.close()

            projects_map: dict[str, AntigravityProject] = {}
            for pid, uris_raw, last_active in rows:
                uris = self._parse_workspace_uris(uris_raw)
                path = self._extract_workspace_path(uris)

                if validate_paths:
                    if not path or not path.exists():
                        continue
                    group_key = str(path.resolve())
                else:
                    group_key = str(path.resolve()) if path else pid

                if group_key not in projects_map:
                    canonical_uris = [path.resolve().as_uri()] if path else uris
                    projects_map[group_key] = AntigravityProject(
                        project_id=pid,
                        workspace_uris=canonical_uris,
                        last_active=str(last_active),
                        conversation_count=1,
                        related_project_ids=[pid],
                    )
                else:
                    entry = projects_map[group_key]
                    entry.conversation_count += 1
                    if pid not in entry.related_project_ids:
                        entry.related_project_ids.append(pid)

            projects = list(projects_map.values())

            # Disambiguate projects that share the same directory leaf name
            name_counts = Counter(p.display_name for p in projects)
            for p in projects:
                if name_counts[p.display_name] > 1 and p.workspace_uris:
                    p_path = self._extract_workspace_path(p.workspace_uris)
                    if p_path and p_path.parent.name:
                        p.custom_display_name = f"{p.display_name} ({p_path.parent.name})"

            return projects
        except Exception as e:
            logger.warning("Error querying projects from %s: %s", self.db_path, e)
            return []

    def get_project(
        self,
        project_id: str,
        validate_paths: bool = True,
        include_cli_projects: bool = False,
    ) -> AntigravityProject | None:
        for p in self.list_projects(
            validate_paths=validate_paths, include_cli_projects=include_cli_projects
        ):
            if p.project_id == project_id or p.project_id.startswith(project_id):
                return p
            if any(
                pid == project_id or pid.startswith(project_id) for pid in p.related_project_ids
            ):
                return p

        # Fallback without path validation in case the project folder was moved/deleted
        if validate_paths:
            return self.get_project(project_id, validate_paths=False, include_cli_projects=True)
        return None

    def get_conversation_project_id(self, conversation_id: str) -> str | None:
        if not self.db_path or not self.db_path.is_file():
            return None
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT project_id FROM conversation_summaries WHERE conversation_id = ? LIMIT 1;",
                (conversation_id,),
            )
            row = cursor.fetchone()
            conn.close()
            if row and row[0]:
                return str(row[0])
            return None
        except Exception as e:
            logger.warning("Error getting project_id for conversation %s: %s", conversation_id, e)
            return None

    def list_conversations(
        self, project_id: str | list[str], limit: int = 5, offset: int = 0
    ) -> tuple[list[ConversationItem], int]:
        if not self.db_path or not self.db_path.is_file():
            return [], 0

        pids = [project_id] if isinstance(project_id, str) else list(project_id)
        if not pids:
            return [], 0

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("PRAGMA table_info(conversation_summaries);")
            columns = [col[1] for col in cursor.fetchall()]
            has_title = "title" in columns

            placeholders = ",".join("?" * len(pids))

            cursor.execute(
                f"SELECT COUNT(*) FROM conversation_summaries WHERE project_id IN ({placeholders});",
                pids,
            )
            total_row = cursor.fetchone()
            total = int(total_row[0]) if total_row else 0

            if has_title:
                cursor.execute(
                    f"""
                    SELECT conversation_id, project_id, last_modified_time, title
                    FROM conversation_summaries
                    WHERE project_id IN ({placeholders})
                    ORDER BY last_modified_time DESC
                    LIMIT ? OFFSET ?;
                    """,
                    (*pids, limit, offset),
                )
                items = [
                    ConversationItem(
                        conversation_id=str(row[0]),
                        project_id=str(row[1]),
                        last_modified=str(row[2]),
                        title=str(row[3] or ""),
                    )
                    for row in cursor.fetchall()
                ]
            else:
                cursor.execute(
                    f"""
                    SELECT conversation_id, project_id, last_modified_time
                    FROM conversation_summaries
                    WHERE project_id IN ({placeholders})
                    ORDER BY last_modified_time DESC
                    LIMIT ? OFFSET ?;
                    """,
                    (*pids, limit, offset),
                )
                items = [
                    ConversationItem(
                        conversation_id=str(row[0]),
                        project_id=str(row[1]),
                        last_modified=str(row[2]),
                        title="",
                    )
                    for row in cursor.fetchall()
                ]
            conn.close()
            return items, total
        except Exception as e:
            logger.warning("Error querying conversations for project %s: %s", project_id, e)
            return [], 0
            return [], 0

    def get_conversation_metadata(self, conversation_id: str) -> ConversationMetadata:
        val = self.run_agentapi(["get-conversation-metadata", conversation_id])
        meta_node = val.get("response", {}).get("conversationMetadata", {}).get("metadata", {})
        created_at = meta_node.get("createdAt", "")
        workspace_uris = meta_node.get("workspaceUris", [])
        return ConversationMetadata(
            conversation_id=conversation_id,
            workspace_uris=workspace_uris,
            created_at=created_at,
        )

    def new_conversation(
        self,
        prompt: str,
        project_id: str | None = None,
        model: str = "flash",
        title: str | None = None,
    ) -> str:
        args = ["new-conversation", f"--model={model}"]
        if title:
            args.append(f"--title={title}")
        args.append(prompt)

        res = self.run_agentapi(args, project_id)
        conv_id = res.get("response", {}).get("newConversation", {}).get("conversationId")
        if not conv_id:
            raise RuntimeError(f"Failed to obtain conversationId from response: {res}")
        return str(conv_id)

    def send_message(self, recipient_id: str, content: str, project_id: str | None = None) -> None:
        self.run_agentapi(["send-message", recipient_id, content], project_id)

    def get_trajectory_id(self, conversation_id: str) -> str | None:
        if not self.db_path or not self.db_path.is_file():
            return None

        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute(
                "SELECT raw_summary FROM conversation_summaries WHERE conversation_id = ? LIMIT 1;",
                (conversation_id,),
            )
            row = c.fetchone()
            conn.close()
            if not row or not row[0]:
                return None

            raw = row[0]
            pos = 0
            while pos < len(raw):
                val, shift = 0, 0
                while True:
                    b = raw[pos]
                    pos += 1
                    val |= (b & 0x7F) << shift
                    shift += 7
                    if not (b & 0x80):
                        break
                tag, wire = val >> 3, val & 7
                if wire == 0:
                    while True:
                        b = raw[pos]
                        pos += 1
                        if not (b & 0x80):
                            break
                elif wire == 2:
                    v, s = 0, 0
                    while True:
                        b = raw[pos]
                        pos += 1
                        v |= (b & 0x7F) << s
                        s += 7
                        if not (b & 0x80):
                            break
                    chunk = raw[pos : pos + v]
                    pos += v
                    if tag == 4:
                        return chunk.decode("utf-8", "ignore")
                elif wire == 1:
                    pos += 8
                elif wire == 5:
                    pos += 4
                else:
                    break
        except Exception as e:
            logger.warning(
                "Error getting trajectory_id for conversation %s: %s", conversation_id, e
            )
        return None

    def send_user_interaction(
        self,
        conversation_id: str,
        step_index: int,
        selected_option_index: int,
        custom_write_in: str = "",
        question_text: str = "",
        options: list[str] | None = None,
    ) -> bool:
        trajectory_id = self.get_trajectory_id(conversation_id)

        response_dict: dict[str, Any] = {
            "selectedOptionIds": [str(selected_option_index)] if selected_option_index >= 0 else [],
            "writeInResponse": custom_write_in,
            "skipped": False,
        }
        if question_text:
            response_dict["question"] = question_text
        if options:
            response_dict["options"] = options

        candidate_steps = [step_index + 1, step_index]
        value = {
            "cancelled": False,
            "responses": [response_dict],
        }

        return self._dispatch_cascade_interaction(
            conversation_id=conversation_id,
            case_name="askQuestion",
            value_dict=value,
            candidate_steps=candidate_steps,
            trajectory_id=trajectory_id,
        )

    def send_tool_approval(
        self,
        conversation_id: str,
        step_index: int,
        allow: bool,
        trajectory_id: str | None = None,
    ) -> bool:
        traj_id = trajectory_id or self.get_trajectory_id(conversation_id)
        candidate_steps = [step_index, step_index + 1, step_index - 1]
        value = {"confirm": allow}
        return self._dispatch_cascade_interaction(
            conversation_id=conversation_id,
            case_name="approvalInteraction",
            value_dict=value,
            candidate_steps=candidate_steps,
            trajectory_id=traj_id,
        )

    def _dispatch_cascade_interaction(
        self,
        conversation_id: str,
        case_name: str,
        value_dict: dict[str, Any],
        candidate_steps: list[int],
        trajectory_id: str | None = None,
    ) -> bool:
        if not self.hub_address:
            logger.error("Cannot dispatch cascade interaction: hub_address is missing")
            return False

        url = f"http://{self.hub_address}/exa.language_server_pb.LanguageServerService/HandleCascadeUserInteraction"
        headers = {
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
            "x-codeium-csrf-token": self.hub_csrf_token or "",
        }

        import urllib.error
        import urllib.request

        for target_step in candidate_steps:
            if target_step < 0:
                continue

            payload: dict[str, Any] = {
                "cascadeId": conversation_id,
                "interaction": {
                    "stepIndex": target_step,
                    "interaction": {
                        "case": case_name,
                        "value": value_dict,
                    },
                },
            }
            if trajectory_id:
                payload["interaction"]["trajectoryId"] = trajectory_id

            try:
                data_bytes = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=5) as res:
                    if res.status in (200, 204):
                        logger.info(
                            "Cascade interaction (%s) sent successfully: conv=%s, step=%d, trajectory=%s",
                            case_name,
                            conversation_id,
                            target_step,
                            trajectory_id,
                        )
                        return True
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", "ignore")
                logger.debug(
                    "HandleCascadeUserInteraction (%s) failed for step %d (code %d): %s",
                    case_name,
                    target_step,
                    e.code,
                    err_body,
                )
                if (
                    "input not registered for step" in err_body
                    and target_step != candidate_steps[-1]
                ):
                    continue
                return False
            except Exception as e:
                logger.error("Failed to send cascade interaction (%s) to hub: %s", case_name, e)
                return False

        return False

    def _post_connectrpc(
        self, endpoint: str, payload: dict[str, Any], timeout: float = 5.0
    ) -> tuple[bool, int | None, str]:
        """Dispatches a JSON ConnectRPC request to the Hub and returns (ok, status_code, response_text)."""
        if not self.hub_address:
            return False, None, "hub_address is missing"

        url = f"http://{self.hub_address}/exa.language_server_pb.LanguageServerService/{endpoint}"
        headers = {
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
            "x-codeium-csrf-token": self.hub_csrf_token or "",
        }

        import urllib.error
        import urllib.request

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as res:
                body = res.read().decode("utf-8", "ignore") if res.readable() else ""
                return res.status in (200, 204), res.status, body
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", "ignore")
            return False, e.code, err_body
        except Exception as e:
            return False, None, str(e)

    def cancel_execution(self, conversation_id: str) -> bool:
        """Actively aborts the current execution in the Hub via ConnectRPC."""
        if not self.hub_address:
            logger.error("Cannot cancel execution: hub_address is missing")
            return False

        # 1. Attempt CancelCascadeInvocation
        payload_invoc = {
            "cascadeId": conversation_id,
            "killBackgroundTasks": True,
        }
        ok, code, body = self._post_connectrpc("CancelCascadeInvocation", payload_invoc)
        if ok:
            logger.info("CancelCascadeInvocation succeeded for conversation %s", conversation_id)
            return True
        logger.debug(
            "CancelCascadeInvocation returned code %s for %s: %s",
            code,
            conversation_id,
            body,
        )

        # 2. Fallback to ForceStopCascadeTree
        payload_force = {
            "conversationId": conversation_id,
        }
        ok_force, code_force, body_force = self._post_connectrpc(
            "ForceStopCascadeTree", payload_force
        )
        if ok_force:
            logger.info("ForceStopCascadeTree succeeded for conversation %s", conversation_id)
            return True
        logger.warning(
            "ForceStopCascadeTree returned code %s for %s: %s",
            code_force,
            conversation_id,
            body_force,
        )
        return False

    def query_waiting_approval(self, conversation_id: str) -> ToolApprovalPrompt | None:
        if not self.hub_address:
            return None

        url = f"http://{self.hub_address}/exa.language_server_pb.LanguageServerService/GetCascadeTrajectory"
        headers = {
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
            "x-codeium-csrf-token": self.hub_csrf_token or "",
        }

        import urllib.error
        import urllib.request

        payload = {"cascadeId": conversation_id}
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=3) as res:
                if res.status == 200:
                    data = json.loads(res.read().decode("utf-8"))
                    traj = data.get("trajectory") or {}
                    traj_id = traj.get("trajectoryId", "")
                    steps = traj.get("steps") or []
                    if steps:
                        last_step = steps[-1]
                        return ToolApprovalPrompt.from_step_dict(
                            step_data=last_step,
                            default_step_index=len(steps) - 1,
                            trajectory_id=traj_id,
                        )
        except Exception as e:
            logger.debug("Failed to query waiting approval for conv %s: %s", conversation_id, e)
        return None

    def get_last_step_index(self, conversation_id: str) -> int:
        transcript_path = get_transcript_path(conversation_id)
        if not transcript_path.is_file():
            return -1

        max_idx = -1
        try:
            with transcript_path.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if step := TranscriptStep.parse_line(line):
                        if step.step_index > max_idx:
                            max_idx = step.step_index
        except Exception as e:
            logger.warning("Failed to read transcript for last step: %s", e)
        return max_idx

    def get_last_turn_summary(self, conversation_id: str) -> LastTurnSummary:
        return get_last_turn_summary(conversation_id)

    def get_steps_from_transcript(
        self, conversation_id: str, after_step_index: int = -1
    ) -> list[ProgressStepItem]:
        return get_steps_from_transcript(conversation_id, after_step_index)

    async def wait_for_model_reply(
        self,
        conversation_id: str,
        after_step_index: int,
        timeout_secs: float | None = None,
        inactivity_timeout_secs: float = 600.0,
        max_timeout_secs: float = 3600.0,
        interaction_timeout_secs: float = 1800.0,
        progress_callback: Callable[[str], None] | None = None,
        step_callback: Callable[[TranscriptStep], None] | None = None,
        interaction_callback: Callable[[InteractionPrompt], Any] | None = None,
        approval_callback: Callable[[ToolApprovalPrompt], Any] | None = None,
        dismiss_interaction_callback: Callable[[int], Any] | None = None,
    ) -> str:
        if timeout_secs is not None:
            inactivity_timeout_secs = timeout_secs
            max_timeout_secs = timeout_secs
        transcript_path = get_transcript_path(conversation_id)
        logger.info(
            "Waiting for model reply: conv=%s, after=%d, path=%s",
            conversation_id,
            after_step_index,
            transcript_path,
        )

        start_time = time.monotonic()
        last_activity_time = time.monotonic()
        interaction_start_time: float | None = None
        tracker = StepTracker(initial_after_index=after_step_index)
        last_log_time = time.monotonic()
        last_approval_check_time = 0.0
        notified_interaction_steps: set[int] = set()
        notified_approval_steps: set[int] = set()
        has_pending_interaction = False
        last_file_offset: int = 0

        async def _dismiss_steps_up_to(current_step_idx: int) -> None:
            if not dismiss_interaction_callback:
                return
            all_notified = list(notified_interaction_steps | notified_approval_steps)
            for s_idx in all_notified:
                if s_idx < current_step_idx:
                    res = dismiss_interaction_callback(s_idx)
                    if asyncio.iscoroutine(res):
                        await res

        while True:
            now = time.monotonic()

            # Check Hub for WAITING tool approvals every 1s if approval_callback is configured
            if approval_callback and (now - last_approval_check_time) >= 1.0:
                last_approval_check_time = now
                if approval := self.query_waiting_approval(conversation_id):
                    if not has_pending_interaction:
                        has_pending_interaction = True
                        interaction_start_time = now
                    if approval.step_index not in notified_approval_steps:
                        notified_approval_steps.add(approval.step_index)
                        last_activity_time = now
                        res = approval_callback(approval)
                        if asyncio.iscoroutine(res):
                            await res

            # Check interaction timeout ceiling
            if has_pending_interaction:
                if (
                    interaction_start_time is not None
                    and (now - interaction_start_time) >= interaction_timeout_secs
                ):
                    raise TimeoutError(
                        f"Timed out waiting for user interaction/approval in conversation {conversation_id} after {interaction_timeout_secs}s"
                    )
            elif (now - last_activity_time) >= inactivity_timeout_secs:
                raise TimeoutError(
                    f"Timed out due to inactivity in conversation {conversation_id} after {inactivity_timeout_secs}s without progress"
                )
            elif (now - start_time) >= max_timeout_secs:
                raise TimeoutError(
                    f"Timed out reached maximum execution duration in conversation {conversation_id} after {max_timeout_secs}s"
                )

            if transcript_path.is_file():
                try:
                    file_size = transcript_path.stat().st_size
                    if file_size < last_file_offset:
                        # File was truncated/recreated, reset offset
                        last_file_offset = 0

                    if file_size > last_file_offset:
                        with transcript_path.open("r", encoding="utf-8", errors="replace") as f:
                            f.seek(last_file_offset)
                            for line in f:
                                if step := TranscriptStep.parse_line(line):
                                    if step.step_index <= after_step_index:
                                        continue

                                    last_activity_time = time.monotonic()

                                    if prompt := step.get_ask_question_prompt():
                                        if not has_pending_interaction:
                                            has_pending_interaction = True
                                            interaction_start_time = time.monotonic()
                                        if step.step_index not in notified_interaction_steps:
                                            notified_interaction_steps.add(step.step_index)
                                            if interaction_callback:
                                                res = interaction_callback(prompt)
                                                if asyncio.iscoroutine(res):
                                                    await res

                                    # If subsequent steps arrived, notify dismissal of prior interactions
                                    if notified_interaction_steps and step.step_index > max(
                                        notified_interaction_steps
                                    ):
                                        has_pending_interaction = False
                                        interaction_start_time = None
                                        await _dismiss_steps_up_to(step.step_index)

                                    if notified_approval_steps and step.step_index > max(
                                        notified_approval_steps
                                    ):
                                        has_pending_interaction = False
                                        interaction_start_time = None
                                        await _dismiss_steps_up_to(step.step_index)

                                    if tracker.process_step(step):
                                        elapsed = int(time.monotonic() - start_time)
                                        if progress_callback:
                                            live_board = tracker.format_live_markdown(elapsed)
                                            progress_callback(live_board)
                                        if step_callback:
                                            step_callback(step)

                                    if final_text := step.is_final_reply():
                                        logger.info(
                                            "Model produced final reply: step=%d, len=%d",
                                            step.step_index,
                                            len(final_text),
                                        )
                                        return final_text
                            last_file_offset = f.tell()
                except Exception as e:
                    logger.warning("Error reading transcript during poll: %s", e)
            elif (time.monotonic() - last_log_time) >= 10.0:
                logger.warning("Waiting for transcript file to appear...")
                last_log_time = time.monotonic()

            await asyncio.sleep(0.3)
