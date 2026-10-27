from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from agy_pilot.bot import TelegramBridge, build_interaction_keyboard
from agy_pilot.config import Config
from agy_pilot.controller import Antigravity2Controller
from agy_pilot.transcript import (
    InteractionPrompt,
    QuestionOption,
    StepTracker,
    ToolCall,
    TranscriptStep,
)


def test_get_ask_question_prompt_list_and_str():
    # 1. Questions as list
    step1 = TranscriptStep(
        step_index=10,
        source="MODEL",
        step_type="PLANNER_RESPONSE",
        tool_calls=[
            ToolCall(
                name="ask_question",
                args={
                    "questions": [
                        {
                            "question": "Which database should we use?",
                            "options": ["PostgreSQL", "SQLite", "MySQL"],
                            "is_multi_select": False,
                        }
                    ]
                },
            )
        ],
    )
    prompt1 = step1.get_ask_question_prompt()
    assert prompt1 is not None
    assert prompt1.step_index == 10
    assert prompt1.question == "Which database should we use?"
    assert len(prompt1.options) == 3
    assert prompt1.options[0].text == "PostgreSQL"
    assert prompt1.options[1].text == "SQLite"
    assert not prompt1.is_multi_select

    # 2. Questions as JSON string
    questions_json = json.dumps(
        [
            {
                "question": "Select target deployment:",
                "options": ["Production", "Staging"],
                "is_multi_select": True,
            }
        ]
    )
    step2 = TranscriptStep(
        step_index=20,
        source="MODEL",
        step_type="PLANNER_RESPONSE",
        tool_calls=[ToolCall(name="ask_question", args={"questions": questions_json})],
    )
    prompt2 = step2.get_ask_question_prompt()
    assert prompt2 is not None
    assert prompt2.step_index == 20
    assert prompt2.question == "Select target deployment:"
    assert len(prompt2.options) == 2
    assert prompt2.is_multi_select is True

    # 3. No ask_question
    step3 = TranscriptStep(
        step_index=30,
        source="MODEL",
        step_type="PLANNER_RESPONSE",
        tool_calls=[ToolCall(name="run_command", args={"CommandLine": "ls"})],
    )
    assert step3.get_ask_question_prompt() is None


def test_step_tracker_with_ask_question():
    tracker = StepTracker()
    step = TranscriptStep(
        step_index=5,
        source="MODEL",
        step_type="PLANNER_RESPONSE",
        tool_calls=[
            ToolCall(
                name="ask_question",
                args={
                    "questions": [
                        {
                            "question": "Confirm delete directory /tmp/build?",
                            "options": ["Yes", "No"],
                        }
                    ]
                },
            )
        ],
    )
    item = tracker.process_step(step)
    assert item is not None
    assert "Awaiting user decision: Confirm delete directory" in item.description


def test_build_interaction_keyboard():
    prompt = InteractionPrompt(
        step_index=42,
        question="Select an environment",
        options=[
            QuestionOption(index=0, text="Dev"),
            QuestionOption(index=1, text="Very long environment name that needs truncation for button display"),
        ],
    )
    text, kb = build_interaction_keyboard(prompt, "conv-abc")
    assert "Select an environment" in text
    assert "`[1]` Dev" in text
    assert len(kb.inline_keyboard) == 2
    btn1 = kb.inline_keyboard[0][0]
    btn2 = kb.inline_keyboard[1][0]
    assert btn1.text == "1. Dev"
    assert btn1.callback_data == "ia:42:0"
    assert btn2.callback_data == "ia:42:1"
    assert "..." in btn2.text


def test_send_user_interaction_success(tmp_path):
    controller = Antigravity2Controller(
        executable_path=tmp_path / "exe",
        db_path=None,
        hub_address="127.0.0.1:54321",
        hub_csrf_token="test-token",
    )

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_res = MagicMock()
        mock_res.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_res

        success = controller.send_user_interaction("conv-123", step_index=15, selected_option_index=1)
        assert success is True

        args, kwargs = mock_urlopen.call_args
        req = args[0]
        assert req.get_header("X-codeium-csrf-token") == "test-token"
        assert req.get_header("Connect-protocol-version") == "1"
        payload = json.loads(req.data.decode())
        assert payload["cascadeId"] == "conv-123"
        assert payload["interaction"]["stepIndex"] == 16
        assert payload["interaction"]["interaction"]["case"] == "askQuestion"
        assert payload["interaction"]["interaction"]["value"]["responses"][0]["selectedOptionIds"] == ["1"]


def test_send_user_interaction_missing_hub(tmp_path):
    controller = Antigravity2Controller(
        executable_path=tmp_path / "exe",
        db_path=None,
        hub_address=None,
        hub_csrf_token=None,
    )
    assert controller.send_user_interaction("conv-123", 1, 0) is False
    assert controller.get_trajectory_id("conv-123") is None


def test_send_user_interaction_network_error(tmp_path):
    controller = Antigravity2Controller(
        executable_path=tmp_path / "exe",
        db_path=None,
        hub_address="127.0.0.1:54321",
        hub_csrf_token="tok",
    )
    with patch("urllib.request.urlopen", side_effect=RuntimeError("connection refused")):
        assert controller.send_user_interaction("conv-123", 1, 0) is False


@pytest.mark.asyncio
async def test_wait_for_model_reply_triggers_interaction_callback(tmp_path):
    controller = Antigravity2Controller(
        executable_path=tmp_path / "exe",
        db_path=None,
        hub_address=None,
        hub_csrf_token=None,
    )

    log_dir = tmp_path / ".gemini" / "antigravity" / "brain" / "conv-test" / ".system_generated" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    t_file = log_dir / "transcript.jsonl"

    step_question = {
        "step_index": 1,
        "source": "MODEL",
        "type": "PLANNER_RESPONSE",
        "status": "DONE",
        "tool_calls": [
            {
                "name": "ask_question",
                "args": {
                    "questions": [
                        {
                            "question": "Proceed with deployment?",
                            "options": ["Yes", "Cancel"],
                        }
                    ]
                },
            }
        ],
    }
    step_final = {
        "step_index": 2,
        "source": "MODEL",
        "type": "PLANNER_RESPONSE",
        "status": "DONE",
        "content": "Deployment completed successfully.",
    }

    t_file.write_text(json.dumps(step_question) + "\n" + json.dumps(step_final) + "\n")

    interaction_called = []

    def on_interaction(prompt: InteractionPrompt):
        interaction_called.append(prompt)

    with patch("agy_pilot.controller.get_transcript_path", return_value=t_file):
        reply = await controller.wait_for_model_reply(
            conversation_id="conv-test",
            after_step_index=0,
            timeout_secs=5.0,
            interaction_callback=on_interaction,
        )

    assert reply == "Deployment completed successfully."
    assert len(interaction_called) == 1
    assert interaction_called[0].question == "Proceed with deployment?"


@pytest.mark.asyncio
async def test_callback_query_interaction_response(tmp_path):
    config = Config(
        adapter="telegram",
        bot_token="123456789:AAGh_dummy_token_for_unit_tests",
        allowed_user_id=123,
        downloads_dir=tmp_path / "downloads",
        db_path=tmp_path / "test.db",
    )
    mock_controller = MagicMock()
    mock_controller.send_user_interaction.return_value = True

    mock_store = AsyncMock()
    mock_store.get_conversation_id.return_value = "conv-active"

    bridge = TelegramBridge(config, mock_controller, mock_store)

    # Simulate callback query for ia:10:0
    query = MagicMock(spec=CallbackQuery)
    query.from_user = User(id=123, is_bot=False, first_name="Test")
    query.data = "ia:10:0"
    query.message = MagicMock(spec=Message)
    query.message.text = "Question: Select plan"
    query.message.reply_markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="1. Fast Plan", callback_data="ia:10:0")]]
    )
    query.answer = AsyncMock()
    query.message.edit_text = AsyncMock()

    # Get the registered callback handler
    handlers = bridge.dp.callback_query.handlers
    handler_obj = handlers[-1]  # or the handle_callback_query
    await handler_obj.callback(query)

    mock_controller.send_user_interaction.assert_called_with(
        conversation_id="conv-active",
        step_index=10,
        selected_option_index=0,
        question_text="",
        options=None,
    )
    query.answer.assert_called_with("Selection submitted")
    query.message.edit_text.assert_called()
    assert "Fast Plan" in query.message.edit_text.call_args[0][0]
