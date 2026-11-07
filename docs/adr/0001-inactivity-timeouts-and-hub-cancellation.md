# Two-Tier Inactivity and Ceiling Timeouts with Active Hub Cancellation

We replace the hard 300s execution timeout with two-tier timeout management (inactivity stall detection and global ceiling) and implement active cascade cancellation via Hub RPC.

## Context

Previously, `agy-pilot` enforced a static 300.0s total wall-clock timeout on `wait_for_model_reply`. For long-running agent tasks, complex builds, and subagents, 300 seconds is insufficient. Furthermore, if a turn timed out or the user wanted to abort, `agy-pilot` could only stop its local listener while the background agent in Antigravity continued executing uncontrollably.

## Decision

1. **Two-Tier Timeout Strategy**:
   - `inactivity_timeout_secs` (default 600s / 10m): resets each time a new transcript step, progress update, or user interaction event is observed.
   - `max_timeout_secs` (default 3600s / 1h): upper safety bound preventing runaway execution.
   - Preserves `interaction_timeout_secs` (default 1800s / 30m) when awaiting human inputs (`ask_question` / `tool_approval`).

2. **Active Cancellation via ConnectRPC**:
   - Add `cancel_execution(conversation_id)` to `Controller` which sends a `CancelCascadeInvocation` RPC to the Hub with `killBackgroundTasks: true`, falling back to `ForceStopCascadeTree`.
   - Provide explicit cancellation controls to the user: an inline `⏹ Cancel` button on Telegram progress messages, alongside extending `/cancel` to abort active runs.
