import pytest

from agy_pilot.storage import SessionStore


@pytest.mark.asyncio
async def test_session_store_conversation_id(tmp_path):
    db_file = tmp_path / "test.db"
    store = SessionStore(db_file)
    await store.init()

    # Initial conversation id should be None
    assert await store.get_conversation_id() is None

    # Set conversation id
    await store.set_conversation_id("conv-abc-123")
    assert await store.get_conversation_id() == "conv-abc-123"

    # Reset conversation id
    await store.set_conversation_id(None)
    assert await store.get_conversation_id() is None

    # Test project_id
    assert await store.get_project_id() is None
    await store.set_project_id("proj-xyz-789")
    assert await store.get_project_id() == "proj-xyz-789"
    await store.set_project_id(None)
    assert await store.get_project_id() is None

    await store.close()


@pytest.mark.asyncio
async def test_session_store_message_logging(tmp_path):
    db_file = tmp_path / "test.db"
    store = SessionStore(db_file)
    await store.init()

    await store.log_message(12345678, "user", "Hello agent")
    await store.log_message(12345678, "assistant", "Hello human")

    logs = await store.get_recent_logs(limit=10)
    assert len(logs) == 2
    assert logs[0]["user_id"] == 12345678
    assert logs[0]["role"] == "user"
    assert logs[0]["content"] == "Hello agent"
    assert logs[1]["role"] == "assistant"
    await store.close()
