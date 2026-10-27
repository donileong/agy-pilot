from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .discovery import Discovery
from .transcript import (
    InteractionPrompt,
    LastTurnSummary,
    ProgressStepItem,
    StepTracker,
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

    @property
    def display_name(self) -> str:
        if self.workspace_uris:
            first = self.workspace_uris[0].removeprefix("file://")
            name = Path(first).name
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

    def list_projects(self) -> list[AntigravityProject]:
        if not self.db_path or not self.db_path.is_file():
            return []

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT project_id, workspace_uris, MAX(last_modified_time) as last_active, COUNT(*) as convo_count
                FROM conversation_summaries
                WHERE project_id != '' AND project_id != 'outside-of-project'
                GROUP BY project_id, workspace_uris
                ORDER BY last_active DESC;
                """
            )
            projects: list[AntigravityProject] = []
            for row in cursor.fetchall():
                pid, uris_raw, last_active, count = row
                try:
                    uris = json.loads(uris_raw)
                    if not isinstance(uris, list):
                        uris = [str(uris_raw)]
                except Exception:
                    uris = [str(uris_raw)]
                projects.append(
                    AntigravityProject(
                        project_id=pid,
                        workspace_uris=uris,
                        last_active=str(last_active),
                        conversation_count=int(count),
                    )
                )
            conn.close()
            return projects
        except Exception as e:
            logger.warning("Error querying projects from %s: %s", self.db_path, e)
            return []

    def get_project(self, project_id: str) -> AntigravityProject | None:
        for p in self.list_projects():
            if p.project_id == project_id or p.project_id.startswith(project_id):
                return p
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
        self, project_id: str, limit: int = 5, offset: int = 0
    ) -> tuple[list[ConversationItem], int]:
        if not self.db_path or not self.db_path.is_file():
            return [], 0

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("PRAGMA table_info(conversation_summaries);")
            columns = [col[1] for col in cursor.fetchall()]
            has_title = "title" in columns

            cursor.execute(
                "SELECT COUNT(*) FROM conversation_summaries WHERE project_id = ?;",
                (project_id,),
            )
            total_row = cursor.fetchone()
            total = int(total_row[0]) if total_row else 0

            if has_title:
                cursor.execute(
                    """
                    SELECT conversation_id, project_id, last_modified_time, title
                    FROM conversation_summaries
                    WHERE project_id = ?
                    ORDER BY last_modified_time DESC
                    LIMIT ? OFFSET ?;
                    """,
                    (project_id, limit, offset),
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
                    """
                    SELECT conversation_id, project_id, last_modified_time
                    FROM conversation_summaries
                    WHERE project_id = ?
                    ORDER BY last_modified_time DESC
                    LIMIT ? OFFSET ?;
                    """,
                    (project_id, limit, offset),
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
        if not self.hub_address:
            logger.error("Cannot send user interaction: hub_address is missing")
            return False

        url = f"http://{self.hub_address}/exa.language_server_pb.LanguageServerService/HandleCascadeUserInteraction"
        headers = {
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
            "x-codeium-csrf-token": self.hub_csrf_token or "",
        }

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

        import urllib.error
        import urllib.request

        for target_step in candidate_steps:
            payload: dict[str, Any] = {
                "cascadeId": conversation_id,
                "interaction": {
                    "stepIndex": target_step,
                    "interaction": {
                        "case": "askQuestion",
                        "value": {
                            "cancelled": False,
                            "responses": [response_dict],
                        },
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
                            "User interaction sent successfully: conv=%s, step=%d, opt=%d, trajectory=%s",
                            conversation_id,
                            target_step,
                            selected_option_index,
                            trajectory_id,
                        )
                        return True
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", "ignore")
                logger.warning(
                    "HandleCascadeUserInteraction failed for step %d (code %d): %s",
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
                logger.error("Failed to send user interaction to hub: %s", e)
                return False

        return False

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
        timeout_secs: float = 300.0,
        progress_callback: Callable[[str], None] | None = None,
        step_callback: Callable[[TranscriptStep], None] | None = None,
        interaction_callback: Callable[[InteractionPrompt], Any] | None = None,
    ) -> str:
        transcript_path = get_transcript_path(conversation_id)
        logger.info(
            "Waiting for model reply: conv=%s, after=%d, path=%s",
            conversation_id,
            after_step_index,
            transcript_path,
        )

        start_time = time.monotonic()
        tracker = StepTracker(initial_after_index=after_step_index)
        last_log_time = time.monotonic()
        notified_interaction_steps: set[int] = set()

        while (time.monotonic() - start_time) < timeout_secs:
            if transcript_path.is_file():
                try:
                    with transcript_path.open("r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            if step := TranscriptStep.parse_line(line):
                                if step.step_index <= after_step_index:
                                    continue

                                if prompt := step.get_ask_question_prompt():
                                    if step.step_index not in notified_interaction_steps:
                                        notified_interaction_steps.add(step.step_index)
                                        if interaction_callback:
                                            res = interaction_callback(prompt)
                                            if asyncio.iscoroutine(res):
                                                await res

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
                except Exception as e:
                    logger.warning("Error reading transcript during poll: %s", e)
            elif (time.monotonic() - last_log_time) >= 10.0:
                logger.warning("Waiting for transcript file to appear...")
                last_log_time = time.monotonic()

            await asyncio.sleep(0.3)

        raise TimeoutError(
            f"Timed out waiting for model reply in conversation {conversation_id} after {timeout_secs}s"
        )
