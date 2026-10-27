from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Voice,
)

from agy_pilot.adapters.base import BaseIMAdapter
from agy_pilot.config import Config
from agy_pilot.controller import (
    Antigravity2Controller,
    inject_headless_constraints,
)
from agy_pilot.skills import (
    SkillDiscovery,
    SkillItem,
    match_skill_command,
    sanitize_description_for_telegram,
    wrap_skill_prompt,
)
from agy_pilot.storage import SessionStore
from agy_pilot.transcript import (
    InteractionPrompt,
    ProgressStepItem,
    StepTracker,
    TranscriptStep,
    format_steps_summary,
)

from .heartbeat import StatusHeartbeat
from .keyboards import (
    build_conversations_keyboard,
    build_interaction_keyboard,
    build_project_actions_keyboard,
    build_projects_keyboard,
    build_skill_detail_keyboard,
    build_skills_keyboard,
    format_switch_message_and_keyboard,
)
from .middleware import AuthMiddleware

logger = logging.getLogger(__name__)


def split_message_chunks(text: str, max_chunk_size: int = 4000) -> list[str]:
    """Split long response text safely for Telegram character limits."""
    if not text:
        return []
    if len(text) <= max_chunk_size:
        return [text]

    chunks: list[str] = []
    current_lines: list[str] = []
    current_len = 0

    for line in text.splitlines(keepends=True):
        line_len = len(line)
        if current_len + line_len > max_chunk_size:
            if current_lines:
                chunks.append("".join(current_lines).rstrip("\n"))
                current_lines = []
                current_len = 0

            # If a single line exceeds max_chunk_size, break by chunk slice
            if line_len > max_chunk_size:
                for i in range(0, line_len, max_chunk_size):
                    chunk_part = line[i : i + max_chunk_size]
                    if i + max_chunk_size >= line_len:
                        current_lines.append(chunk_part)
                        current_len += len(chunk_part)
                    else:
                        chunks.append(chunk_part)
                continue

        current_lines.append(line)
        current_len += line_len

    if current_lines:
        chunks.append("".join(current_lines).rstrip("\n"))

    return [c for c in chunks if c.strip()]


@dataclass
class QueueItem:
    chat_id: int
    user_id: int
    user_display_text: str
    prompt_for_agent: str
    status_message_id: int | None = None


class TelegramAdapter(BaseIMAdapter):
    """Telegram Instant Messaging adapter for headless Antigravity control."""

    def __init__(
        self,
        config: Config,
        controller: Antigravity2Controller,
        store: SessionStore,
    ):
        super().__init__(
            config=config,
            controller=controller,
            store=store,
        )
        if not config.bot_token:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN is not configured. Please set TELEGRAM_BOT_TOKEN environment variable."
            )

        self.bot = Bot(token=config.bot_token)
        self.dp = Dispatcher()
        self.dp.message.outer_middleware(AuthMiddleware(self.config.allowed_user_ids))
        self.dp.callback_query.outer_middleware(AuthMiddleware(self.config.allowed_user_ids))
        self.queue: asyncio.Queue[QueueItem] = asyncio.Queue(maxsize=50)
        self._worker_task: asyncio.Task | None = None
        self._pending_interactions: dict[str, InteractionPrompt] = {}
        self._traces: dict[str, tuple[str, int, list[ProgressStepItem], float | None]] = {}

        self._register_handlers()

    def _check_auth(self, message: Message) -> bool:
        user = message.from_user
        if user and user.id in self.config.allowed_user_ids:
            return True
        return False

    async def _download_voice_file(self, voice: Voice, message_id: int) -> Path:
        self.config.downloads_dir.mkdir(parents=True, exist_ok=True)
        filename = f"voice_{message_id}_{voice.file_unique_id}.ogg"
        local_path = self.config.downloads_dir / filename
        file_info = await self.bot.get_file(voice.file_id)
        if not file_info.file_path:
            raise RuntimeError("File path not provided by Telegram")
        await self.bot.download_file(file_info.file_path, destination=local_path)
        return local_path.resolve()

    async def _dispatch_reply_chunks(
        self,
        chat_id: int,
        status_message_id: int | None,
        reply: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        chunks = split_message_chunks(reply, max_chunk_size=4000) or [
            "(Task completed with no output text)"
        ]
        first_chunk = chunks[0]
        is_single = len(chunks) == 1
        markup_for_first = reply_markup if is_single else None

        edit_first_kwargs: dict[str, Any] = {}
        send_first_kwargs: dict[str, Any] = {}
        if markup_for_first is not None:
            edit_first_kwargs["reply_markup"] = markup_for_first
            send_first_kwargs["reply_markup"] = markup_for_first

        if status_message_id:
            try:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_message_id,
                    text=first_chunk,
                    parse_mode=ParseMode.MARKDOWN,
                    **edit_first_kwargs,
                )
            except TelegramBadRequest:
                try:
                    await self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=status_message_id,
                        text=first_chunk,
                        **edit_first_kwargs,
                    )
                except Exception as e2:
                    logger.warning("Plain text edit failed: %s, sending new message", e2)
                    await self.bot.send_message(chat_id, first_chunk, **send_first_kwargs)
            except Exception as e:
                logger.warning("Edit message failed: %s, sending new message", e)
                await self.bot.send_message(chat_id, first_chunk, **send_first_kwargs)
        else:
            await self.bot.send_message(chat_id, first_chunk, **send_first_kwargs)

        for i, overflow in enumerate(chunks[1:], start=1):
            is_last = i == len(chunks) - 1
            markup_for_overflow = reply_markup if is_last else None
            overflow_kwargs: dict[str, Any] = {}
            if markup_for_overflow is not None:
                overflow_kwargs["reply_markup"] = markup_for_overflow
            try:
                await self.bot.send_message(
                    chat_id,
                    overflow,
                    parse_mode=ParseMode.MARKDOWN,
                    **overflow_kwargs,
                )
            except TelegramBadRequest:
                await self.bot.send_message(chat_id, overflow, **overflow_kwargs)

    async def _get_active_skills(self) -> dict[str, SkillItem]:
        current_proj = await self.store.get_project_id()
        workspace_paths: list[Path] = []
        if current_proj and isinstance(current_proj, str):
            if p := self.controller.get_project(current_proj):
                for u in p.workspace_uris:
                    clean = u.removeprefix("file://")
                    workspace_paths.append(Path(clean))
        return SkillDiscovery.discover_skills(workspace_paths=workspace_paths)

    async def sync_telegram_commands(self) -> None:
        try:
            active_skills = await self._get_active_skills()
            commands: list[BotCommand] = [
                BotCommand(command="skills", description="Browse available Antigravity skills"),
                BotCommand(command="projects", description="Browse local projects & conversations"),
                BotCommand(command="new", description="Reset and start a new conversation"),
                BotCommand(
                    command="status", description="Show active project and conversation status"
                ),
                BotCommand(command="help", description="Show help instructions"),
            ]

            registered_cmd_names = {c.command for c in commands}
            sorted_skills = sorted(active_skills.values(), key=lambda s: s.name)
            total_chars = sum(len(c.command) + len(c.description) for c in commands)

            for s in sorted_skills:
                if len(commands) >= 100:
                    break
                if s.command_name in registered_cmd_names:
                    continue
                desc = sanitize_description_for_telegram(s.description, s.name, max_len=45)
                if total_chars + len(s.command_name) + len(desc) > 4000:
                    break
                total_chars += len(s.command_name) + len(desc)
                registered_cmd_names.add(s.command_name)
                commands.append(BotCommand(command=s.command_name, description=desc))

            for uid in self.config.allowed_user_ids:
                if uid > 0:
                    await self.bot.set_my_commands(
                        commands=commands,
                        scope=BotCommandScopeChat(chat_id=uid),
                    )
            logger.info(
                "Successfully synchronized %d Telegram commands (skills: %d) for %d user(s)",
                len(commands),
                len(registered_cmd_names) - 5,
                len(self.config.allowed_user_ids),
            )
        except Exception as e:
            logger.warning("Failed to synchronize Telegram bot commands: %s", e)

    def _register_handlers(self) -> None:
        @self.dp.message(Command("start", "help"))
        async def handle_start(message: Message):
            if not self._check_auth(message):
                await message.answer(
                    "⛔️ Access Restricted: You are not authorized to use this bot."
                )
                return

            current_id = await self.store.get_conversation_id()
            if not isinstance(current_id, str):
                current_id = None
            current_proj = await self.store.get_project_id()
            if not isinstance(current_proj, str):
                current_proj = None
            conv_display = (
                current_id
                if current_id
                else "None (new conversation will be created on next message)"
            )
            proj_display = "outside-of-project"
            if current_proj:
                if p := self.controller.get_project(current_proj):
                    proj_display = p.display_name
                else:
                    proj_display = current_proj[:8]

            reply = (
                "🤖 *Welcome to Antigravity Telegram Bridge*\n\n"
                "Send me text or voice messages to interact with your local Antigravity instance.\n\n"
                f"📌 Active Project: `{proj_display}`\n"
                f"📌 Active Conversation: `{conv_display}`\n\n"
                "*Available Commands:*\n"
                "• `/skills` - Browse available Antigravity skills (supports details and shortcuts)\n"
                "• `/projects` - Interactively browse projects and switch conversations\n"
                "• `/new` or `/reset` - Reset current conversation\n"
                "• `/status` - Show environment and session status\n"
                "• `/help` - Show help instructions\n"
                "• `/<skill_name>` - Invoke a specific skill (e.g. `/research explore topic`)"
            )
            await message.answer(reply, parse_mode=ParseMode.MARKDOWN)

        @self.dp.message(Command("new", "reset"))
        async def handle_reset(message: Message):
            if not self._check_auth(message):
                return
            await self.store.set_conversation_id(None)
            current_proj = await self.store.get_project_id()
            if not isinstance(current_proj, str):
                current_proj = None
            proj_name = "outside-of-project mode"
            if current_proj:
                if p := self.controller.get_project(current_proj):
                    proj_name = f"Project `{p.display_name}`"
                else:
                    proj_name = f"Project `{current_proj[:8]}`"

            logger.info("User requested new conversation reset")
            await message.answer(
                f"✅ *Conversation reset!*\nNext message will create a new conversation in Antigravity ({proj_name}).\nUse /projects to switch projects.",
                parse_mode=ParseMode.MARKDOWN,
            )

        @self.dp.message(Command("status"))
        async def handle_status(message: Message):
            if not self._check_auth(message):
                return
            current_id = await self.store.get_conversation_id()
            if not isinstance(current_id, str):
                current_id = None
            current_proj = await self.store.get_project_id()
            if not isinstance(current_proj, str):
                current_proj = None

            proj_display = "outside-of-project"
            if current_proj:
                if p := self.controller.get_project(current_proj):
                    proj_display = p.display_name
                else:
                    proj_display = current_proj[:8]

            if not current_id:
                text = (
                    f"📌 *Current Status:*\n"
                    f"• Bound Project: `{proj_display}`\n"
                    f"• Bound Conversation: No active conversation (new conversation will be created on next message)."
                )
                await message.answer(text, parse_mode=ParseMode.MARKDOWN)
                return

            meta_info = "Unable to fetch metadata"
            try:
                meta = self.controller.get_conversation_metadata(current_id)
                meta_info = f"Created at: {meta.created_at}"
            except Exception as e:
                logger.warning("Failed to get meta: %s", e)

            text = (
                f"📌 *Current Status:*\n"
                f"• Bound Project: `{proj_display}`\n"
                f"• Bound Conversation: `{current_id}`\n"
                f"• Metadata: {meta_info}"
            )
            await message.answer(text, parse_mode=ParseMode.MARKDOWN)

        @self.dp.message(Command("projects"))
        async def handle_projects(message: Message):
            if not self._check_auth(message):
                return
            projects = self.controller.list_projects()
            if not projects:
                await message.answer("No local project history detected.")
                return

            text, kb = build_projects_keyboard(projects, page=0)
            await message.answer(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

        @self.dp.message(Command("skills"))
        async def handle_skills(message: Message):
            if not self._check_auth(message):
                return
            skills_dict = await self._get_active_skills()
            if not skills_dict:
                await message.answer("No available Antigravity skills detected.")
                return
            sorted_skills = sorted(skills_dict.values(), key=lambda s: s.name)
            text, kb = build_skills_keyboard(sorted_skills, page=0)
            await message.answer(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

        @self.dp.callback_query()
        async def handle_callback_query(query: CallbackQuery):
            user = query.from_user
            if not user or user.id not in self.config.allowed_user_ids:
                await query.answer("⛔️ Unauthorized operation", show_alert=True)
                return

            data = query.data or ""
            if data == "noop":
                await query.answer()
                return

            if data == "proj_close":
                await query.answer()
                if query.message:
                    try:
                        await query.message.delete()
                    except Exception:
                        await query.message.edit_text("❌ Menu closed.")
                return

            if data == "skill_close":
                await query.answer()
                if query.message:
                    try:
                        await query.message.delete()
                    except Exception:
                        await query.message.edit_text("❌ Skill menu closed.")
                return

            if data.startswith("skill_page:"):
                page = int(data.split(":")[1])
                skills_dict = await self._get_active_skills()
                sorted_skills = sorted(skills_dict.values(), key=lambda s: s.name)
                text, kb = build_skills_keyboard(sorted_skills, page=page)
                await query.answer()
                if query.message:
                    await query.message.edit_text(
                        text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN
                    )
                return

            if data.startswith("skill_sel:"):
                parts = data.split(":")
                skill_name = parts[1]
                return_page = int(parts[2]) if len(parts) > 2 else 0
                skills_dict = await self._get_active_skills()
                skill = skills_dict.get(skill_name)
                if not skill:
                    for s in skills_dict.values():
                        if s.command_name == skill_name:
                            skill = s
                            break
                if not skill:
                    await query.answer("Skill not found", show_alert=True)
                    return
                text, kb = build_skill_detail_keyboard(skill, return_page=return_page)
                await query.answer()
                if query.message:
                    await query.message.edit_text(
                        text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN
                    )
                return

            if data.startswith("proj_page:"):
                page = int(data.split(":")[1])
                projects = self.controller.list_projects()
                if not projects:
                    await query.answer("No project data", show_alert=True)
                    return
                text, kb = build_projects_keyboard(projects, page=page)
                await query.answer()
                if query.message:
                    await query.message.edit_text(
                        text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN
                    )
                return

            if data.startswith("proj_sel:"):
                proj_id = data.split(":")[1]
                project = self.controller.get_project(proj_id)
                if not project:
                    await query.answer("Project not found", show_alert=True)
                    return
                text, kb = build_project_actions_keyboard(project)
                await query.answer()
                if query.message:
                    await query.message.edit_text(
                        text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN
                    )
                return

            if data.startswith("proj_new:"):
                proj_id = data.split(":")[1]
                project = self.controller.get_project(proj_id)
                proj_name = project.display_name if project else proj_id[:8]
                await self.store.set_project_id(proj_id)
                await self.store.set_conversation_id(None)
                asyncio.create_task(self.sync_telegram_commands())
                await query.answer(f"Selected project: {proj_name}")
                if query.message:
                    msg = (
                        f"✅ *Bound Project:* `{proj_name}`\n\n"
                        f"Next text or voice message will start a new conversation under this project."
                    )
                    await query.message.edit_text(msg, parse_mode=ParseMode.MARKDOWN)
                return

            if data.startswith("conv_page:"):
                parts = data.split(":")
                proj_id = parts[1]
                page = int(parts[2])
                project = self.controller.get_project(proj_id)
                if not project:
                    await query.answer("Project not found", show_alert=True)
                    return
                items, total = self.controller.list_conversations(proj_id, limit=5, offset=page * 5)
                text, kb = build_conversations_keyboard(
                    project, items, page=page, total_count=total
                )
                await query.answer()
                if query.message:
                    await query.message.edit_text(
                        text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN
                    )
                return

            if data.startswith("conv_sel:"):
                conv_id = data.split(":")[1]
                proj_id = self.controller.get_conversation_project_id(conv_id)
                await self.store.set_conversation_id(conv_id)
                if proj_id:
                    await self.store.set_project_id(proj_id)

                proj_display = ""
                if proj_id:
                    if p := self.controller.get_project(proj_id):
                        proj_display = f"\n• Project: `{p.display_name}`"

                summary = self.controller.get_last_turn_summary(conv_id)
                msg, kb = format_switch_message_and_keyboard(conv_id, proj_display, summary)

                asyncio.create_task(self.sync_telegram_commands())
                await query.answer("Conversation switched!")
                if query.message:
                    try:
                        await query.message.edit_text(
                            msg, reply_markup=kb, parse_mode=ParseMode.MARKDOWN
                        )
                    except TelegramBadRequest:
                        await query.message.edit_text(msg, reply_markup=kb)
                return

            if data.startswith("conv_view_last:"):
                conv_id = data.split(":")[1]
                summary = self.controller.get_last_turn_summary(conv_id)
                if not summary.full_reply:
                    await query.answer("No previous reply content found", show_alert=True)
                    return
                await query.answer()
                chat_id = query.message.chat.id if query.message else query.from_user.id
                header = f"📜 *Conversation `{conv_id[:8]}` Latest Reply:*\n\n"
                await self._dispatch_reply_chunks(
                    chat_id=chat_id,
                    status_message_id=None,
                    reply=f"{header}{summary.full_reply}",
                )
                return

            if data.startswith("ia:"):
                parts = data.split(":")
                step_idx = int(parts[1])
                opt_idx = int(parts[2])
                active_conv = await self.store.get_conversation_id()
                if not active_conv:
                    await query.answer("No active conversation found", show_alert=True)
                    return

                cached_prompt = self._pending_interactions.get(f"{active_conv}:{step_idx}")
                q_text = cached_prompt.question if cached_prompt else ""
                q_opts = [o.text for o in cached_prompt.options] if cached_prompt else None

                success = self.controller.send_user_interaction(
                    conversation_id=active_conv,
                    step_index=step_idx,
                    selected_option_index=opt_idx,
                    question_text=q_text,
                    options=q_opts,
                )
                if success:
                    self._pending_interactions.pop(f"{active_conv}:{step_idx}", None)
                    await query.answer("Selection submitted")
                    if query.message:
                        btn_text = ""
                        if query.message.reply_markup:
                            for row in query.message.reply_markup.inline_keyboard:
                                for btn in row:
                                    if btn.callback_data == data:
                                        btn_text = btn.text
                                        break
                        confirmed_label = btn_text or f"Option {opt_idx + 1}"
                        orig_text = query.message.text or ""
                        updated_text = (
                            f"{orig_text}\n\n"
                            f"✅ *Selected:* `{confirmed_label}`\n"
                            f"⚡️ Antigravity is continuing execution..."
                        )
                        try:
                            await query.message.edit_text(
                                updated_text,
                                parse_mode=ParseMode.MARKDOWN,
                            )
                        except Exception:
                            pass
                else:
                    await query.answer(
                        "Submission failed, please retry or confirm on desktop", show_alert=True
                    )
                return

            if data.startswith("steps:") or data.startswith("trace:"):
                cached_steps: list[ProgressStepItem] = []
                elapsed_val: float | None = None

                if data.startswith("steps:"):
                    parts = data.split(":", 2)
                    if len(parts) >= 3:
                        target_conv_id = parts[1]
                        try:
                            after_idx = int(parts[2])
                        except ValueError:
                            after_idx = -1

                        trace_key = f"{target_conv_id}:{after_idx}"
                        if trace_key in self._traces:
                            _, _, cached_steps, elapsed_val = self._traces[trace_key]
                        else:
                            cached_steps = self.controller.get_steps_from_transcript(
                                target_conv_id, after_idx
                            )
                elif data.startswith("trace:"):
                    trace_id = data.split(":", 1)[1]
                    if trace_id in self._traces:
                        _, _, cached_steps, elapsed_val = self._traces[trace_id]

                summary_text = format_steps_summary(cached_steps, elapsed_secs=elapsed_val)
                close_kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="❌ Close Details", callback_data="close_steps")]
                    ]
                )
                await query.answer()

                if query.message:
                    try:
                        await query.message.reply(
                            summary_text,
                            reply_markup=close_kb,
                            parse_mode=ParseMode.MARKDOWN,
                        )
                    except TelegramBadRequest:
                        await query.message.reply(
                            summary_text,
                            reply_markup=close_kb,
                        )
                    except Exception as e:
                        logger.warning("Failed to reply with steps summary: %s", e)
                        try:
                            await query.message.answer(
                                summary_text,
                                reply_markup=close_kb,
                            )
                        except Exception:
                            pass
                return

            if data == "close_steps":
                await query.answer("Closed")
                if query.message:
                    try:
                        await query.message.delete()
                    except Exception as e:
                        logger.debug("Failed to delete steps message: %s", e)
                return

            await query.answer()

        @self.dp.message(F.voice)
        async def handle_voice(message: Message):
            if not self._check_auth(message):
                return

            voice = message.voice
            if not voice:
                return

            user_id = message.from_user.id if message.from_user else 0
            status_msg = await message.answer("📥 Downloading voice message...")

            try:
                abs_path = await self._download_voice_file(voice, message.message_id)
                prompt = (
                    f"The user sent a voice message. The audio file has been saved locally at: {abs_path}.\n"
                    "Please inspect or process this audio file with your tools, and respond to the user."
                )
                prompt_with_constraints = inject_headless_constraints(prompt)

                await self.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id,
                    text="⏳ Voice saved, queued for execution...",
                )

                item = QueueItem(
                    chat_id=message.chat.id,
                    user_id=user_id,
                    user_display_text=f"[Voice Message: {abs_path.name}]",
                    prompt_for_agent=prompt_with_constraints,
                    status_message_id=status_msg.message_id,
                )
                await self.queue.put(item)
            except Exception as e:
                logger.error("Voice handling failed: %s", e)
                await self.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id,
                    text=f"❌ Voice download failed: {e}",
                )

        @self.dp.message(F.text)
        async def handle_text(message: Message):
            if not self._check_auth(message):
                return

            text = message.text.strip() if message.text else ""
            if not text:
                return

            user_id = message.from_user.id if message.from_user else 0

            # Check if this message invokes an Antigravity skill slash command
            active_skills = await self._get_active_skills()
            if match := match_skill_command(text, active_skills):
                skill, query = match
                prompt_text = wrap_skill_prompt(skill, query)
                prompt = inject_headless_constraints(prompt_text)
                user_display = f"💡 /{skill.name} {query}".strip()
                status_header = f"💡 Activating skill *{skill.name}*..."
            else:
                prompt = inject_headless_constraints(text)
                user_display = text
                status_header = "⏳ Queued for execution..."

            status_msg = await message.answer(status_header, parse_mode=ParseMode.MARKDOWN)
            item = QueueItem(
                chat_id=message.chat.id,
                user_id=user_id,
                user_display_text=user_display,
                prompt_for_agent=prompt,
                status_message_id=status_msg.message_id,
            )
            await self.queue.put(item)

    async def _worker_loop(self) -> None:
        logger.info("Sequential worker queue loop started")
        while True:
            item = await self.queue.get()
            try:
                await self._process_item(item)
            except Exception as e:
                logger.exception("Unexpected error in worker loop: %s", e)
            finally:
                self.queue.task_done()

    async def _process_item(self, item: QueueItem) -> None:
        logger.info(
            "Processing queued item for user %d: %s", item.user_id, item.user_display_text[:50]
        )
        await self.store.log_message(item.user_id, "user", item.user_display_text)
        start_time = time.monotonic()
        step_count = 0

        heartbeat = StatusHeartbeat(
            bot=self.bot,
            chat_id=item.chat_id,
            message_id=item.status_message_id,
            initial_status="⚡️ *Starting Antigravity...*\n💭 Reasoning & planning...",
        ).start()

        current_conv_id = await self.store.get_conversation_id()
        if not isinstance(current_conv_id, str) or not current_conv_id.strip():
            current_conv_id = None
        else:
            current_conv_id = current_conv_id.strip()

        current_project_id = await self.store.get_project_id()
        if isinstance(current_project_id, str) and current_project_id.strip():
            target_project_id = current_project_id.strip()
        else:
            target_project_id = "outside-of-project"
        conv_id: str
        after_step: int

        try:
            if current_conv_id:
                after_step = self.controller.get_last_step_index(current_conv_id)
                heartbeat.update_status(
                    "⚡️ *Appending to conversation...*\n💭 Reasoning & analyzing..."
                )
                self.controller.send_message(
                    current_conv_id, item.prompt_for_agent, target_project_id
                )
                conv_id = current_conv_id
            else:
                proj_name = ""
                if current_project_id:
                    if p := self.controller.get_project(current_project_id):
                        proj_name = f" ({p.display_name})"
                heartbeat.update_status(
                    f"🆕 *Creating new conversation{proj_name}...*\n💭 Planning task..."
                )
                conv_id = self.controller.new_conversation(
                    prompt=item.prompt_for_agent,
                    project_id=target_project_id,
                    model="flash",
                    title="Telegram Interaction",
                )
                await self.store.set_conversation_id(conv_id)
                logger.info(
                    "Created new Antigravity conversation: %s in project: %s",
                    conv_id,
                    target_project_id,
                )
                after_step = 0
        except Exception as e:
            elapsed = time.monotonic() - start_time
            logger.error("Failed to send/create conversation: %s", e)
            await heartbeat.stop()
            err_msg = f"❌ Task launch failed: {e}\nUse /new to reset conversation and retry."
            if item.status_message_id:
                try:
                    await self.bot.edit_message_text(
                        chat_id=item.chat_id,
                        message_id=item.status_message_id,
                        text=err_msg,
                    )
                except Exception:
                    pass
            return

        tracker = StepTracker(initial_after_index=after_step)
        recorded_steps: list[ProgressStepItem] = []

        def on_step(step: TranscriptStep) -> None:
            nonlocal step_count
            step_count += 1
            if item_step := tracker.process_step(step):
                recorded_steps.append(item_step)

        async def on_interaction(prompt: InteractionPrompt) -> None:
            self._pending_interactions[f"{conv_id}:{prompt.step_index}"] = prompt
            text, kb = build_interaction_keyboard(prompt, conv_id)
            try:
                await self.bot.send_message(
                    chat_id=item.chat_id,
                    text=text,
                    reply_markup=kb,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception as e:
                logger.error("Failed to send interaction message to Telegram: %s", e)

        try:
            reply = await self.controller.wait_for_model_reply(
                conversation_id=conv_id,
                after_step_index=after_step,
                timeout_secs=300.0,
                progress_callback=lambda board: heartbeat.update_status(board),
                step_callback=on_step,
                interaction_callback=on_interaction,
            )
        except Exception as e:
            elapsed = time.monotonic() - start_time
            logger.error("Error waiting for model reply: %s", e)
            await heartbeat.stop()
            err_msg = f"❌ Execution failed: {e}\nUse /new to reset conversation and retry."
            if item.status_message_id:
                try:
                    await self.bot.edit_message_text(
                        chat_id=item.chat_id,
                        message_id=item.status_message_id,
                        text=err_msg,
                    )
                except Exception:
                    pass
            return

        elapsed = time.monotonic() - start_time

        await heartbeat.stop()
        await self.store.log_message(item.user_id, "assistant", reply)

        reply_markup = None
        total_steps = len(recorded_steps) if recorded_steps else step_count
        if total_steps > 0:
            trace_key = f"{conv_id}:{after_step}"
            if len(f"steps:{trace_key}".encode("utf-8")) <= 64:
                cb_data = f"steps:{trace_key}"
            else:
                trace_id = uuid.uuid4().hex[:12]
                cb_data = f"trace:{trace_id}"
                self._traces[trace_id] = (conv_id, after_step, list(recorded_steps), elapsed)

            self._traces[trace_key] = (conv_id, after_step, list(recorded_steps), elapsed)
            if len(self._traces) > 200:
                oldest_keys = list(self._traces.keys())[:50]
                for k in oldest_keys:
                    self._traces.pop(k, None)

            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"📜 View Execution Steps ({total_steps} steps)",
                            callback_data=cb_data,
                        )
                    ]
                ]
            )

        await self._dispatch_reply_chunks(
            item.chat_id, item.status_message_id, reply, reply_markup=reply_markup
        )

    async def start(self) -> None:
        self.config.downloads_dir.mkdir(parents=True, exist_ok=True)
        await self.store.init()
        self._worker_task = asyncio.create_task(self._worker_loop())

        try:
            # Delete global commands so unauthenticated users cannot see command menu
            await self.bot.delete_my_commands()
            # Scope commands strictly to the allowed user's chat ID including available skills
            await self.sync_telegram_commands()
        except Exception as e:
            logger.warning("Failed to register bot commands: %s", e)

        logger.info(
            "Starting Telegram adapter polling (allowed_user_ids=%s)...",
            self.config.allowed_user_ids,
        )
        try:
            await self.dp.start_polling(self.bot)
        finally:
            await self.stop()

    async def stop(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        await self.store.close()
        await self.bot.session.close()


TelegramBridge = TelegramAdapter
