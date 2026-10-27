# agy-pilot (Antigravity Pilot)

[![CI](https://github.com/donileong/agy-pilot/actions/workflows/ci.yml/badge.svg)](https://github.com/donileong/agy-pilot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Package Manager: uv](https://img.shields.io/badge/managed%20by-uv-orange.svg)](https://github.com/astral-sh/uv)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

**Antigravity Pilot (`agy-pilot`)** is an **unofficial**, self-hosted headless companion daemon and remote controller bridge for local instances of **Antigravity 2**. It enables developers to securely interact with their local coding assistant, monitor multi-step execution traces, and make interactive human-in-the-loop decisions via instant messaging platforms.

> [!IMPORTANT]
> **Legal Disclaimer & Fair Use Notice**
>
> 1. **Unofficial Project**: `agy-pilot` is an independent, community-driven open-source project. It is **not** an official product of, nor is it endorsed, affiliated, associated, sponsored, or supported by **Google LLC** or **Google DeepMind**.
> 2. **Trademarks**: "Google", "Antigravity", and associated marks, logos, and emblems are registered trademarks of Google LLC. Use of these names in this repository is strictly for descriptive, identification, and interoperability purposes under nominative fair use principles.
> 3. **Personal Self-Hosted Utility**: This software is designed solely as a personal productivity bridge connecting your own authorized chat client with your own locally installed, legally running Antigravity environment.
> 4. **Terms of Service Compliance**: Users are independently responsible for complying with the [Google Terms of Service](https://policies.google.com/terms) and [Google Generative AI Additional Terms of Service](https://policies.google.com/terms/generative-ai). This software must not be used for unauthorized commercial multi-tenant resale, high-frequency automated scraping, or bypass of authentication and access boundaries.
> 5. **Security & Execution Boundary**: By enabling instant messaging bridges, you allow authorized users to trigger local code execution and workspace edits on your host machine. Always enforce the `TELEGRAM_ALLOWED_USER_IDS` whitelist and never run with an open public whitelist.

```
+-------------------------------------------------------------------------+
|                              Chat Client                                |
|                   (Telegram, Discord, Slack, etc.)                      |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
|                             agy-pilot                                  |
|                                                                         |
|  +--------------------+   +-------------------+   +------------------+  |
|  | Pluggable Adapters |-->| Remote Controller |-->| Language Server  |  |
|  | (Telegram, etc.)   |   | & Session Store   |   | AgentAPI IPC     |  |
|  +--------------------+   +-------------------+   +------------------+  |
|                                                             |           |
|                                                             v           |
|                                                   +------------------+  |
|                                                   | Antigravity Hub  |  |
|                                                   | (Process Daemon) |  |
|                                                   +------------------+  |
+-------------------------------------------------------------------------+
```

---

## Key Features

* **Pluggable IM Adapter Architecture**:
  The core Antigravity remote engine is strictly separated from message delivery protocols via `BaseIMAdapter`. Telegram is provided out-of-the-box, with clean interfaces to plug in Discord, Slack, Feishu/Lark, or custom webhook bridges.
* **Live Progress Board & Trace Inspector**:
  Renders live progress updates directly in chat (thinking phases, MCP tool calls, file edits, bash commands) with debounced updates to prevent platform rate limits. Finished responses include an expandable trace button for complete auditability.
* **Interactive Human-in-the-Loop Support (`ask_question`)**:
  When Antigravity requests user feedback or decision branching via `ask_question`, the controller presents interactive inline buttons directly in the chat, allowing instantaneous resolution from mobile devices.
* **Dynamic Skill Discovery & Slash Commands**:
  Automatically discovers built-in, global (`~/.gemini/config/skills/`), and workspace-specific skills. Dynamically syncs available skills to the chat command menu for one-tap execution (e.g., `/research <prompt>`).
* **Native Voice Note Ingestion**:
  Accepts voice messages, streams audio to a local cache, and passes absolute audio paths into Antigravity for multimodal tools.
* **Background Daemonization via `launchd`**:
  Simple CLI flags to install, auto-load, monitor, or restart background daemons on macOS (`com.ii2d.agy-pilot`).

---

## Quickstart

### Prerequisites

* macOS (Darwin)
* [uv](https://github.com/astral-sh/uv) package manager
* Google Antigravity 2 installed and running

### 1. Installation

#### Option A: User Installation (Recommended)

Install `agy-pilot` as a standalone global tool using [uv](https://github.com/astral-sh/uv):

```bash
# Install directly from git repository
uv tool install git+https://github.com/donileong/agy-pilot.git

# Or install from PyPI (when published)
uv tool install agy-pilot
```

#### Option B: From Source (For Contributors)

Clone the repository and synchronize the environment:

```bash
git clone https://github.com/donileong/agy-pilot.git
cd agy-pilot
uv sync
```

### 2. Configuration & Telegram Setup

Before running `agy-pilot`, you need a Telegram Bot and your Telegram User ID(s) to whitelist authorized access.

#### Step A: Create a Telegram Bot
1. Open Telegram and search for **[@BotFather](https://t.me/BotFather)**.
2. Send `/newbot`.
3. Enter a display name for your bot (e.g. `My Antigravity Pilot`).
4. Enter a bot username ending with `bot` (e.g. `my_agy_pilot_bot`).
5. Copy the generated **HTTP API token** (e.g., `123456789:ABCdef-ghijklm...`).

#### Step B: Find Your Telegram User ID
For strict security, only user IDs explicitly in the whitelist can interact with your machine.
1. In Telegram, search for **[@userinfobot](https://t.me/userinfobot)** (or `@raw_data_bot`).
2. Press **Start** or send any text. It will immediately reply with your numeric `Id` (e.g. `123456789`).
3. *(Optional)* If you want to grant access to multiple colleagues or team members, have them find their numeric IDs as well.

#### Step C: Run the Interactive Setup Wizard

The easiest way to configure `agy-pilot` is to run the interactive setup wizard:

```bash
# Run the interactive configuration wizard
agy-pilot init

# Or if running from source:
make init
```

The wizard will:
- Guide you through entering the Bot Token.
- Prompt for Allowed User ID(s) (supports single ID or comma-separated list for multiple users, e.g., `12345678, 87654321`).
- Securely save settings to `~/.config/agy-pilot/config.env` with restricted file permissions (`0600`).
- Offer to immediately configure and start the background auto-start daemon (`launchd`).

*(Manual setup alternative: You can also place a `.env` file in your repository or export `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_USER_IDS`.)*

### 3. Background Daemon Management (`agy-pilot service`)

`agy-pilot` provides a structured, cross-platform service manager that natively targets macOS **`launchd`** (`com.ii2d.agy-pilot`) and Linux **`systemd --user`** (`agy-pilot.service`):

```bash
# Install and enable the background daemon (starts immediately and on boot)
agy-pilot service install

# Check service status (PID, health, and status)
agy-pilot service status

# Follow real-time daemon logs
agy-pilot service logs -f

# Restart the background service
agy-pilot service restart

# Stop or uninstall the service
agy-pilot service stop
agy-pilot service uninstall
```

*(If running from source, Makefile shortcuts are also available: `make install-service`, `make status`, `make logs`, `make restart`.)*

### 4. Updating

Keep `agy-pilot` up to date easily:

```bash
# Automatic self-update (handles uv tool or git repository) and hot-restarts daemon
agy-pilot update

# Or if working from source:
make update
```

### 5. Cleanup & Uninstallation

If you wish to remove runtime logs, background services, or wipe all cached sessions:

```bash
# Clean background service and log files:
agy-pilot clean

# Clean everything including database sessions, downloads, and config directory:
agy-pilot clean --all

# Or if working from source:
make clean       # removes service & logs
make clean-all   # removes service, logs, data directory, and config
```

### 6. Storage Locations

`agy-pilot` adheres strictly to standard platform directory specifications:
* **Configuration**: `~/.config/agy-pilot/config.env`
* **Data & SQLite Storage**:
  - macOS: `~/Library/Application Support/agy-pilot/agy_session.db`
  - Linux: `~/.local/share/agy-pilot/agy_session.db`
* **Logs**:
  - macOS: `~/Library/Logs/agy-pilot/`
  - Linux: systemd journal (`journalctl --user-unit agy-pilot.service`)

---

## Telegram Bot Commands

| Command | Description |
| :--- | :--- |
| `/skills` | Browse available Antigravity skills with interactive pagination |
| `/projects` | Interactively select workspace projects and browse/switch historical conversations |
| `/new` or `/reset` | Reset current session context and begin a fresh conversation |
| `/status` | View active project binding, conversation ID, and environment metadata |
| `/help` | Show command overview and usage instructions |
| `/<skill_name>` | Directly trigger a skill with arguments (e.g. `/research optimize database queries`) |

---

## Pluggable Adapter Development

To implement a new instant messaging adapter (e.g. Discord or Slack):

1. Inherit from `BaseIMAdapter` in `src/agy_pilot/adapters/base.py`:
   ```python
   from agy_pilot.adapters.base import BaseIMAdapter

   class DiscordAdapter(BaseIMAdapter):
       async def start(self) -> None:
           # Initialize client and start event loop
           ...

       async def stop(self) -> None:
           # Clean up sessions and shut down
           ...
   ```
2. Register the adapter in `src/agy_pilot/adapters/registry.py`:
   ```python
   from agy_pilot.adapters.registry import register_adapter
   register_adapter("discord", DiscordAdapter)
   ```
3. Run with `--adapter discord` or set `AGY_ADAPTER=discord`.

---

## Development & Testing

Run unit tests:

```bash
make test
```

Run test suite with code coverage:

```bash
make test-cov
```

Format and sync dependencies:

```bash
make sync
```

---

## Contributing & Community

Contributions are warmly welcomed! Please read our [Contributing Guide](CONTRIBUTING.md) to get started.

* [Contributing Guide](CONTRIBUTING.md)
* [Code of Conduct](CODE_OF_CONDUCT.md)
* [Security Policy](SECURITY.md)
* [Domain Glossary](GLOSSARY.md)

---

## License & Disclaimer

This project is licensed under the [MIT License](LICENSE).

This software is provided "as is", without warranty of any kind, express or implied, including but not limited to the warranties of merchantability, fitness for a particular purpose, and non-infringement. Authors and maintainers are not liable for any claims, damages, or unintended host actions resulting from the use of this software.
