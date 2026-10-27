# Antigravity Pilot (agy-pilot) Glossary

A headless remote controller and bridge service allowing interaction and control of local Google Antigravity 2 AI sessions via Instant Messaging platforms.

## Domain Terms

**Antigravity Pilot (`agy-pilot`)**:
The headless background daemon and multi-platform bridge providing remote control of Google Antigravity 2.
_Avoid_: Bot, proxy

**Hub**:
The local background language server instance running as `subclient_type hub` that manages IDE workspaces and session state.
_Avoid_: Core, backend, server

**Host Bridge**:
The internal IPC URL and port pair exposed by the Hub, used to infer gRPC and agent API communication endpoints.
_Avoid_: Gateway, proxy

**AgentAPI**:
The command-line interface subcommand of `language_server` executed to query workspaces, conversations, and send prompt steps.
_Avoid_: API, CLI, RPC

**Transcript**:
The JSON-lines event log (`transcript.jsonl`) generated per conversation containing incremental execution steps, model thinking, and tool invocations.
_Avoid_: History, event log, audit log

**Step Tracker**:
The state machine responsible for tailing transcript deltas, tracking tool execution progress, and debouncing status messages.
_Avoid_: Event listener, stream handler

**IM Adapter**:
The pluggable interface and protocol adapter (e.g. Telegram, Discord, Slack) responsible for translating chat events and user commands to and from the remote controller.
_Avoid_: Bot, connector, frontend

**Session**:
The persistent mapping between an authorized user/chat and an active Antigravity project or conversation.
_Avoid_: Thread, connection
