from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from agy_pilot.controller import AntigravityProject, ConversationItem
from agy_pilot.skills import SkillItem
from agy_pilot.transcript import InteractionPrompt, LastTurnSummary


def build_projects_keyboard(
    projects: list[AntigravityProject], page: int = 0, page_size: int = 5
) -> tuple[str, InlineKeyboardMarkup]:
    total_projects = len(projects)
    total_pages = max(1, (total_projects + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start_idx = page * page_size
    current_slice = projects[start_idx : start_idx + page_size]

    text_lines = [
        f"📂 *Local Projects* (Page {page + 1}/{total_pages}, total {total_projects} projects):"
    ]
    for p in current_slice:
        text_lines.append(
            f"• *{p.display_name}* (`{p.project_id[:8]}`) - {p.conversation_count} conversations"
        )
    text_lines.append("\nPlease select a project below:")
    text = "\n".join(text_lines)

    buttons: list[list[InlineKeyboardButton]] = []
    for p in current_slice:
        label = f"📦 {p.display_name} ({p.conversation_count})"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"proj_sel:{p.project_id}")])

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"proj_page:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"proj_page:{page + 1}"))

    buttons.append(nav_row)
    buttons.append([InlineKeyboardButton(text="❌ Close", callback_data="proj_close")])

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def build_project_actions_keyboard(
    project: AntigravityProject,
) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"📂 *Project Details:*\n"
        f"• Name: *{project.display_name}*\n"
        f"• ID: `{project.project_id[:12]}`\n"
        f"• Conversations: {project.conversation_count}\n"
        f"• Last Active: {project.last_active}\n\n"
        f"Select an action:"
    )
    buttons = [
        [
            InlineKeyboardButton(
                text="➕ New Conversation in Project",
                callback_data=f"proj_new:{project.project_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"💬 Browse Conversations ({project.conversation_count})",
                callback_data=f"conv_page:{project.project_id}:0",
            )
        ],
        [InlineKeyboardButton(text="🔙 Back to Projects", callback_data="proj_page:0")],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def build_conversations_keyboard(
    project: AntigravityProject,
    items: list[ConversationItem],
    page: int,
    total_count: int,
    page_size: int = 5,
) -> tuple[str, InlineKeyboardMarkup]:
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    text = (
        f"💬 *Conversations: {project.display_name}*\n"
        f"(Page {page + 1}/{total_pages}, total {total_count} conversations)\n"
        f"Select a conversation to resume:"
    )

    buttons: list[list[InlineKeyboardButton]] = []
    for conv in items:
        label = f"💬 {conv.display_title[:28]}"
        buttons.append(
            [InlineKeyboardButton(text=label, callback_data=f"conv_sel:{conv.conversation_id}")]
        )

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️ Prev", callback_data=f"conv_page:{project.project_id}:{page - 1}"
            )
        )
    nav_row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton(
                text="Next ➡️", callback_data=f"conv_page:{project.project_id}:{page + 1}"
            )
        )

    buttons.append(nav_row)
    buttons.append(
        [
            InlineKeyboardButton(
                text="🔙 Back to Project Actions", callback_data=f"proj_sel:{project.project_id}"
            )
        ]
    )

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def build_skills_keyboard(
    skills: list[SkillItem], page: int = 0, page_size: int = 6
) -> tuple[str, InlineKeyboardMarkup]:
    total_skills = len(skills)
    total_pages = max(1, (total_skills + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start_idx = page * page_size
    current_slice = skills[start_idx : start_idx + page_size]

    text_lines = [
        f"💡 *Available Antigravity Skills* (Page {page + 1}/{total_pages}, total {total_skills} skills):",
        "Send `/<command> <arguments>` in chat to invoke, or click below for details:\n",
    ]
    for s in current_slice:
        desc_snip = (
            s.description[:35] + "..."
            if len(s.description) > 35
            else (s.description or "No description")
        )
        scope_badge = (
            "🏠 Workspace"
            if s.scope == "workspace"
            else ("🌐 Global" if s.scope == "global" else "📦 Builtin")
        )
        text_lines.append(f"• `/{s.command_name}` ({scope_badge}) - {desc_snip}")

    text = "\n".join(text_lines)

    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for s in current_slice:
        row.append(
            InlineKeyboardButton(
                text=f"💡 {s.command_name}",
                callback_data=f"skill_sel:{s.name}:{page}",
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"skill_page:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"skill_page:{page + 1}"))

    buttons.append(nav_row)
    buttons.append([InlineKeyboardButton(text="❌ Close", callback_data="skill_close")])

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def build_skill_detail_keyboard(
    skill: SkillItem, return_page: int = 0
) -> tuple[str, InlineKeyboardMarkup]:
    scope_badge = (
        "🏠 Workspace Skill"
        if skill.scope == "workspace"
        else ("🌐 Global Skill" if skill.scope == "global" else "📦 Builtin Skill")
    )
    text = (
        f"💡 *Skill Details: {skill.name}*\n"
        f"• Command: `/{skill.command_name}`\n"
        f"• Scope: {scope_badge}\n"
        f"• File: `{skill.path.name}`\n\n"
        f"*Description:*\n"
        f"{skill.description or 'No description provided.'}\n\n"
        f"👉 *Usage:*\n"
        f"Send directly in chat: `/{skill.command_name} <task instructions>`"
    )
    buttons = [
        [InlineKeyboardButton(text="🔙 Back to Skills", callback_data=f"skill_page:{return_page}")],
        [InlineKeyboardButton(text="❌ Close Details", callback_data="skill_close")],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def format_switch_message_and_keyboard(
    conv_id: str,
    proj_display: str,
    summary: LastTurnSummary,
) -> tuple[str, InlineKeyboardMarkup | None]:
    base_header = (
        f"✅ *Switched to conversation!*\n• Conversation ID: `{conv_id}`{proj_display}\n\n"
    )

    card_lines = []
    has_expand_button = False

    if summary.last_status == "completed":
        card_lines.append("━━━━━━━━━━━━━━━")
        card_lines.append("⏮️ *Previous Context*")
        if summary.user_prompt:
            first_line = (
                summary.user_prompt.splitlines()[0]
                if summary.user_prompt.splitlines()
                else summary.user_prompt
            )
            prompt_snip = f"{first_line[:60]}..." if len(first_line) > 60 else first_line
            card_lines.append(f"👤 *User:* {prompt_snip}")
        if summary.assistant_reply:
            card_lines.append(f"🤖 *Assistant:*\n{summary.assistant_reply}")
        card_lines.append("━━━━━━━━━━━━━━━\n")
        if summary.is_truncated:
            has_expand_button = True

    elif summary.last_status == "running_tool":
        card_lines.append("━━━━━━━━━━━━━━━")
        card_lines.append("⏮️ *Previous Context*")
        if summary.user_prompt:
            first_line = (
                summary.user_prompt.splitlines()[0]
                if summary.user_prompt.splitlines()
                else summary.user_prompt
            )
            prompt_snip = f"{first_line[:60]}..." if len(first_line) > 60 else first_line
            card_lines.append(f"👤 *User:* {prompt_snip}")
        tool_desc = summary.last_tool_name or "Unknown tool"
        card_lines.append(f"🛠️ *Executing Tool:* `{tool_desc}`")
        card_lines.append("━━━━━━━━━━━━━━━\n")

    elif summary.last_status == "pending_reply":
        card_lines.append("━━━━━━━━━━━━━━━")
        if summary.user_prompt:
            first_line = (
                summary.user_prompt.splitlines()[0]
                if summary.user_prompt.splitlines()
                else summary.user_prompt
            )
            prompt_snip = f"{first_line[:60]}..." if len(first_line) > 60 else first_line
            card_lines.append(f"👤 *Last Prompt:* {prompt_snip}")
        card_lines.append("💭 *Assistant reply pending*")
        card_lines.append("━━━━━━━━━━━━━━━\n")

    else:
        card_lines.append(
            "_(No previous messages in this conversation. Send a message to start.)_\n"
        )

    footer = "Subsequent text or voice messages will append to this conversation."
    card_body = "\n".join(card_lines)
    full_text = f"{base_header}{card_body}\n{footer}".strip()

    kb: InlineKeyboardMarkup | None = None
    if has_expand_button:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📜 View Full Reply",
                        callback_data=f"conv_view_last:{conv_id}",
                    )
                ]
            ]
        )

    return full_text, kb


def build_interaction_keyboard(
    prompt: InteractionPrompt, conversation_id: str
) -> tuple[str, InlineKeyboardMarkup]:
    text_lines = [
        "❓ *Antigravity Decision Required:*\n",
        f"*{prompt.question}*\n",
    ]
    if prompt.options:
        text_lines.append("Select an option below:")
        for opt in prompt.options:
            text_lines.append(f"`[{opt.index + 1}]` {opt.text}")
    else:
        text_lines.append("(No predefined options. Please answer directly or via desktop.)")

    text = "\n".join(text_lines)

    buttons: list[list[InlineKeyboardButton]] = []
    for opt in prompt.options:
        label = opt.text
        if len(label) > 36:
            label = f"{label[:33]}..."
        btn_text = f"{opt.index + 1}. {label}"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=btn_text,
                    callback_data=f"ia:{prompt.step_index}:{opt.index}",
                )
            ]
        )

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)
