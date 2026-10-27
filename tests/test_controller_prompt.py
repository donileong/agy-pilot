from agy_pilot.controller import inject_headless_constraints


def test_inject_headless_constraints():
    user_prompt = "Refactor the database queries."
    injected = inject_headless_constraints(user_prompt)

    assert user_prompt in injected
    assert "HEADLESS REMOTE EXECUTION CONSTRAINTS" in injected
    assert "ask_question" in injected
