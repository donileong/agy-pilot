from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import CallbackQuery, Chat, File, Message, User, Voice

from agy_pilot.bot import AuthMiddleware, TelegramBridge
from agy_pilot.config import Config
from agy_pilot.controller import AntigravityProject, ConversationMetadata
from agy_pilot.transcript import LastTurnSummary


def create_mock_message(user_id=123, text=None, voice=None):
    msg = MagicMock(spec=Message)
    msg.from_user = MagicMock(spec=User)
    msg.from_user.id = user_id
    msg.chat = MagicMock(spec=Chat)
    msg.chat.id = 12345
    msg.message_id = 99
    msg.text = text
    msg.voice = voice
    msg.answer = AsyncMock()
    return msg


def get_handler(bridge: TelegramBridge, handler_name: str):
    handlers = [h for h in bridge.dp.message.handlers if hasattr(h, "callback")]
    return next(h.callback for h in handlers if handler_name in h.callback.__name__)


@pytest.fixture
def bridge_setup(tmp_path):
    config = Config(
        adapter="telegram",
        bot_token="123456789:AAGh_dummy_token_for_handlers",
        allowed_user_id=123,
        downloads_dir=tmp_path / "downloads",
        db_path=tmp_path / "bot.db",
    )
    mock_controller = MagicMock()
    mock_store = AsyncMock()
    bridge = TelegramBridge(config, mock_controller, mock_store)
    bridge.bot = AsyncMock()
    return bridge, mock_controller, mock_store


@pytest.mark.asyncio
async def test_auth_block(bridge_setup):
    bridge, _, _ = bridge_setup
    unauth_msg = create_mock_message(user_id=999, text="/start")
    start_handler = get_handler(bridge, "handle_start")

    await start_handler(unauth_msg)
    unauth_msg.answer.assert_called_with(
        "⛔️ Access Restricted: You are not authorized to use this bot."
    )


@pytest.mark.asyncio
async def test_auth_middleware():
    middleware = AuthMiddleware(allowed_user_id=123)
    dummy_handler = AsyncMock(return_value="ok")

    # 1. Allowed user
    msg_ok = create_mock_message(user_id=123)
    res_ok = await middleware(dummy_handler, msg_ok, {"event_from_user": msg_ok.from_user})
    assert res_ok == "ok"
    dummy_handler.assert_called_once()

    # 2. Blocked message from stranger
    dummy_handler.reset_mock()
    msg_blocked = create_mock_message(user_id=999)
    res_blocked = await middleware(
        dummy_handler, msg_blocked, {"event_from_user": msg_blocked.from_user}
    )
    assert res_blocked is None
    dummy_handler.assert_not_called()
    msg_blocked.answer.assert_called_with(
        "⛔️ Access Restricted: This bot is private and unauthorized."
    )

    # 3. Blocked callback query from stranger
    cb_blocked = create_mock_callback_query(user_id=999)
    res_cb = await middleware(dummy_handler, cb_blocked, {"event_from_user": cb_blocked.from_user})
    assert res_cb is None
    dummy_handler.assert_not_called()
    cb_blocked.answer.assert_called_with("⛔️ Access Restricted: Unauthorized", show_alert=True)


@pytest.mark.asyncio
async def test_start_and_help(bridge_setup):
    bridge, _, mock_store = bridge_setup
    mock_store.get_conversation_id.return_value = "conv-xyz"

    msg = create_mock_message(user_id=123, text="/start")
    start_handler = get_handler(bridge, "handle_start")

    await start_handler(msg)
    assert msg.answer.called
    args, _ = msg.answer.call_args
    assert "conv-xyz" in args[0]
    assert "Welcome to Antigravity Telegram Bridge" in args[0]


@pytest.mark.asyncio
async def test_new_and_reset(bridge_setup):
    bridge, _, mock_store = bridge_setup
    msg = create_mock_message(user_id=123, text="/new")
    new_handler = get_handler(bridge, "handle_reset")

    await new_handler(msg)
    mock_store.set_conversation_id.assert_called_with(None)
    assert msg.answer.called
    assert "Conversation reset" in msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_status_command(bridge_setup):
    bridge, mock_controller, mock_store = bridge_setup
    status_handler = get_handler(bridge, "handle_status")

    # 1. No active conversation
    mock_store.get_conversation_id.return_value = None
    msg1 = create_mock_message(user_id=123, text="/status")
    await status_handler(msg1)
    assert "No active conversation" in msg1.answer.call_args[0][0]

    # 2. Active conversation
    mock_store.get_conversation_id.return_value = "conv-active"
    mock_controller.get_conversation_metadata.return_value = ConversationMetadata(
        "conv-active", [], "2026-10-22"
    )
    msg2 = create_mock_message(user_id=123, text="/status")
    await status_handler(msg2)
    assert "conv-active" in msg2.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_projects_command(bridge_setup):
    bridge, mock_controller, _ = bridge_setup
    projects_handler = get_handler(bridge, "handle_projects")

    # Empty
    mock_controller.list_projects.return_value = []
    msg1 = create_mock_message(user_id=123, text="/projects")
    await projects_handler(msg1)
    assert "No local project history" in msg1.answer.call_args[0][0]

    # With projects
    mock_controller.list_projects.return_value = [
        AntigravityProject("p12345678", ["file:///my-proj"], "2026-10-22", 3)
    ]
    msg2 = create_mock_message(user_id=123, text="/projects")
    await projects_handler(msg2)
    assert "my-proj" in msg2.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_text_handler(bridge_setup):
    bridge, _, _ = bridge_setup
    text_handler = get_handler(bridge, "handle_text")

    msg = create_mock_message(user_id=123, text="Hello Antigravity")
    status_mock = MagicMock(message_id=77)
    msg.answer.return_value = status_mock

    await text_handler(msg)
    assert bridge.queue.qsize() == 1
    item = await bridge.queue.get()
    assert item.user_id == 123
    assert item.user_display_text == "Hello Antigravity"
    assert "Hello Antigravity" in item.prompt_for_agent
    assert item.status_message_id == 77


@pytest.mark.asyncio
async def test_voice_handler(bridge_setup, tmp_path):
    bridge, _, _ = bridge_setup
    voice_handler = get_handler(bridge, "handle_voice")

    voice_mock = MagicMock(spec=Voice)
    voice_mock.file_id = "voice-file-id"
    voice_mock.file_unique_id = "uniq123"

    msg = create_mock_message(user_id=123, voice=voice_mock)
    status_mock = MagicMock(message_id=88)
    msg.answer.return_value = status_mock

    mock_file = MagicMock(spec=File)
    mock_file.file_path = "voice/path.ogg"
    bridge.bot.get_file.return_value = mock_file
    bridge.bot.download_file = AsyncMock()

    await voice_handler(msg)
    assert bridge.queue.qsize() == 1
    item = await bridge.queue.get()
    assert "[Voice Message: " in item.user_display_text
    assert item.status_message_id == 88


def create_mock_callback_query(user_id=123, data=""):
    query = MagicMock(spec=CallbackQuery)
    query.from_user = MagicMock(spec=User)
    query.from_user.id = user_id
    query.data = data
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.edit_text = AsyncMock()
    query.message.delete = AsyncMock()
    return query


def get_callback_handler(bridge: TelegramBridge):
    handlers = [h for h in bridge.dp.callback_query.handlers if hasattr(h, "callback")]
    return handlers[0].callback if handlers else None


@pytest.mark.asyncio
async def test_callback_query_unauthorized(bridge_setup):
    bridge, _, _ = bridge_setup
    cb_handler = get_callback_handler(bridge)
    query = create_mock_callback_query(user_id=999, data="proj_page:0")

    await cb_handler(query)
    query.answer.assert_called_with("⛔️ Unauthorized operation", show_alert=True)


@pytest.mark.asyncio
async def test_callback_query_proj_flow(bridge_setup):
    bridge, mock_controller, mock_store = bridge_setup
    cb_handler = get_callback_handler(bridge)

    project = AntigravityProject("proj1", ["file:///repo1"], "2026-10-23", 2)
    mock_controller.list_projects.return_value = [project]
    mock_controller.get_project.return_value = project
    mock_controller.list_conversations.return_value = ([], 0)

    # 1. Page navigation
    q_page = create_mock_callback_query(user_id=123, data="proj_page:0")
    await cb_handler(q_page)
    assert q_page.message.edit_text.called

    # 2. Select project
    q_sel = create_mock_callback_query(user_id=123, data="proj_sel:proj1")
    await cb_handler(q_sel)
    assert q_sel.message.edit_text.called
    assert "repo1" in q_sel.message.edit_text.call_args[0][0]

    # 3. New conversation in project
    q_new = create_mock_callback_query(user_id=123, data="proj_new:proj1")
    await cb_handler(q_new)
    mock_store.set_project_id.assert_called_with("proj1")
    mock_store.set_conversation_id.assert_called_with(None)
    assert "Bound Project" in q_new.message.edit_text.call_args[0][0]

    # 4. Conversations page
    q_conv_page = create_mock_callback_query(user_id=123, data="conv_page:proj1:0")
    await cb_handler(q_conv_page)
    assert q_conv_page.message.edit_text.called

    # 5. Select conversation
    mock_controller.get_conversation_project_id.return_value = "proj1"
    mock_controller.get_last_turn_summary.return_value = LastTurnSummary(
        user_prompt="Explain quantum computing",
        assistant_reply="Quantum computing is a type of computation...",
        last_status="completed",
        is_truncated=True,
        full_reply="Quantum computing is a type of computation that harnesses the collective properties of quantum states.",
    )
    q_conv_sel = create_mock_callback_query(user_id=123, data="conv_sel:conv-target-999")
    await cb_handler(q_conv_sel)
    mock_store.set_conversation_id.assert_called_with("conv-target-999")
    mock_store.set_project_id.assert_called_with("proj1")
    assert "Switched to conversation" in q_conv_sel.message.edit_text.call_args[0][0]
    assert "Previous Context" in q_conv_sel.message.edit_text.call_args[0][0]
    assert "Explain quantum computing" in q_conv_sel.message.edit_text.call_args[0][0]
    assert q_conv_sel.message.edit_text.call_args[1].get("reply_markup") is not None

    # 6. View full last reply
    q_view = create_mock_callback_query(user_id=123, data="conv_view_last:conv-target-999")
    q_view.message.chat = MagicMock()
    q_view.message.chat.id = 12345
    await cb_handler(q_view)
    bridge.bot.send_message.assert_called()
    assert "Quantum computing is a type of computation" in bridge.bot.send_message.call_args[0][1]

    # 7. Close menu
    q_close = create_mock_callback_query(user_id=123, data="proj_close")
    await cb_handler(q_close)
    assert q_close.message.delete.called


@pytest.mark.asyncio
async def test_callback_query_view_and_close_steps(bridge_setup):
    from agy_pilot.transcript import ProgressStepItem

    bridge, mock_controller, _ = bridge_setup
    cb_handler = get_callback_handler(bridge)

    # 1. View steps from cache
    bridge._traces["conv-trace:0"] = (
        "conv-trace",
        0,
        [ProgressStepItem(step_index=1, description="💭 Planning")],
        2.5,
    )
    q_steps = create_mock_callback_query(user_id=123, data="steps:conv-trace:0")
    q_steps.message.reply = AsyncMock()

    await cb_handler(q_steps)
    q_steps.answer.assert_called()
    assert q_steps.message.reply.called
    reply_text, kwargs = q_steps.message.reply.call_args[0][0], q_steps.message.reply.call_args[1]
    assert "Full Execution Trace" in reply_text
    assert "2.5s" in reply_text
    assert "Planning" in reply_text
    assert kwargs.get("reply_markup") is not None

    # 2. View steps fallback to controller when not in cache
    mock_controller.get_steps_from_transcript.return_value = [
        ProgressStepItem(step_index=2, description="🛠️ Tool execution")
    ]
    q_steps_fallback = create_mock_callback_query(user_id=123, data="steps:conv-fallback:1")
    q_steps_fallback.message.reply = AsyncMock()

    await cb_handler(q_steps_fallback)
    mock_controller.get_steps_from_transcript.assert_called_with("conv-fallback", 1)
    assert q_steps_fallback.message.reply.called
    assert "Tool execution" in q_steps_fallback.message.reply.call_args[0][0]

    # 3. View steps via short trace id
    bridge._traces["short123"] = (
        "conv-short",
        0,
        [ProgressStepItem(step_index=3, description="🤖 Generating response")],
        1.0,
    )
    q_trace = create_mock_callback_query(user_id=123, data="trace:short123")
    q_trace.message.reply = AsyncMock()
    await cb_handler(q_trace)
    assert q_trace.message.reply.called
    assert "Generating response" in q_trace.message.reply.call_args[0][0]

    # 4. Close steps message
    q_close = create_mock_callback_query(user_id=123, data="close_steps")
    await cb_handler(q_close)
    q_close.answer.assert_called_with("Closed")
    assert q_close.message.delete.called


@pytest.mark.asyncio
async def test_skills_command_and_callbacks(bridge_setup, tmp_path):
    bridge, _, _ = bridge_setup
    mock_skill = MagicMock()
    mock_skill.name = "research"
    mock_skill.command_name = "research"
    mock_skill.description = "Primary research skill"
    mock_skill.path = tmp_path / "SKILL.md"
    mock_skill.scope = "global"

    bridge._get_active_skills = AsyncMock(return_value={"research": mock_skill})

    # 1. /skills command
    skills_handler = get_handler(bridge, "handle_skills")
    msg = create_mock_message(user_id=123, text="/skills")
    await skills_handler(msg)
    assert msg.answer.called
    text, kwargs = msg.answer.call_args[0][0], msg.answer.call_args[1]
    assert "Available Antigravity Skills" in text
    assert "/research" in text
    assert kwargs.get("reply_markup") is not None

    # 2. Callback query: skill_page
    cb_handler = bridge.dp.callback_query.handlers[0].callback
    q_page = create_mock_callback_query(user_id=123, data="skill_page:0")
    await cb_handler(q_page)
    assert q_page.message.edit_text.called
    assert "Available Antigravity Skills" in q_page.message.edit_text.call_args[0][0]

    # 3. Callback query: skill_sel
    q_sel = create_mock_callback_query(user_id=123, data="skill_sel:research:0")
    await cb_handler(q_sel)
    assert q_sel.message.edit_text.called
    detail_text = q_sel.message.edit_text.call_args[0][0]
    assert "Skill Details: research" in detail_text
    assert "Primary research skill" in detail_text

    # 4. Callback query: skill_close
    q_close = create_mock_callback_query(user_id=123, data="skill_close")
    await cb_handler(q_close)
    assert q_close.message.delete.called


@pytest.mark.asyncio
async def test_handle_text_skill_slash_command(bridge_setup, tmp_path):
    bridge, _, _ = bridge_setup
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("# doc", encoding="utf-8")

    from agy_pilot.skills import SkillItem

    mock_skill = SkillItem(
        name="research",
        command_name="research",
        description="Deep dive research",
        path=skill_file,
        scope="global",
    )
    bridge._get_active_skills = AsyncMock(return_value={"research": mock_skill})

    text_handler = get_handler(bridge, "handle_text")
    msg = create_mock_message(user_id=123, text="/research Playwright in Docker")

    await text_handler(msg)

    # 1. Answer status acknowledges skill activation
    assert msg.answer.called
    status_text = msg.answer.call_args[0][0]
    assert "Activating skill" in status_text
    assert "research" in status_text

    # 2. Queue item contains official XML payload
    assert not bridge.queue.empty()
    item = await bridge.queue.get()
    assert item.user_display_text == "💡 /research Playwright in Docker"
    assert "<USER_REQUEST>" in item.prompt_for_agent
    assert "/research Playwright in Docker" in item.prompt_for_agent
    assert "<ADDITIONAL_METADATA>" in item.prompt_for_agent
    assert (
        '<SKILL>The user requested you read and use the "research" skill.' in item.prompt_for_agent
    )


@pytest.mark.asyncio
async def test_sync_telegram_commands(bridge_setup, tmp_path):
    bridge, _, _ = bridge_setup
    from agy_pilot.skills import SkillItem

    skills = {
        "tdd": SkillItem("tdd", "tdd", "Test driven dev", tmp_path / "SKILL1.md", "global"),
        "code-review": SkillItem(
            "code-review", "code_review", "Code review", tmp_path / "SKILL2.md", "global"
        ),
    }
    bridge._get_active_skills = AsyncMock(return_value=skills)

    await bridge.sync_telegram_commands()

    bridge.bot.set_my_commands.assert_called_once()
    cmds = bridge.bot.set_my_commands.call_args[1]["commands"]
    cmd_names = [c.command for c in cmds]

    # System commands
    assert "skills" in cmd_names
    assert "projects" in cmd_names
    assert "new" in cmd_names

    # Skill commands
    assert "tdd" in cmd_names
    assert "code_review" in cmd_names
