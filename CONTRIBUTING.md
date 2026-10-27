# Contributing to Antigravity Pilot

Thank you for your interest in contributing to **Antigravity Pilot (`agy-pilot`)**! We welcome bug reports, feature requests, adapter extensions, and pull requests.

---

## Code of Conduct

By participating in this project, you agree to abide by the [Code of Conduct](CODE_OF_CONDUCT.md).

---

## Development Setup

We use [uv](https://github.com/astral-sh/uv) as the single, fast Python package and environment manager.

### Prerequisites
* Python 3.12+
* [uv](https://github.com/astral-sh/uv) (`curl -LsSf https://astral.sh/uv/install.sh | sh` or `brew install uv`)
* Google Antigravity 2 installed locally

### Setup Steps
```bash
# 1. Clone the repository
git clone https://github.com/donileong/agy-pilot.git
cd agy-pilot

# 2. Synchronize virtual environment and dependencies
uv sync

# 3. Copy configuration template
cp .env.example .env
# Edit .env with your Telegram Bot Token & User ID
```

---

## Testing & Quality Assurance

Before submitting any code changes, ensure all tests pass:

```bash
# Run test suite
make test

# Run tests with coverage report
make test-cov

# Run linting with Ruff
make lint
```

---

## Implementing a New IM Adapter

One of the best ways to contribute is by adding support for new Instant Messaging platforms (e.g. Discord, Slack, Feishu/Lark, DingTalk, Matrix):

1. **Subclass `BaseIMAdapter`**:
   Create a new directory in `src/agy_pilot/adapters/<platform>/` and implement `start()` and `stop()` methods:
   ```python
   from agy_pilot.adapters.base import BaseIMAdapter

   class DiscordAdapter(BaseIMAdapter):
       async def start(self) -> None:
           # Initialize client, register command handlers, and begin event loop
           ...

       async def stop(self) -> None:
           # Clean up connections
           ...
   ```
2. **Register the Adapter**:
   Add it to `src/agy_pilot/adapters/registry.py`:
   ```python
   from agy_pilot.adapters.registry import register_adapter
   register_adapter("discord", DiscordAdapter)
   ```
3. **Write Unit Tests**:
   Add test coverage in `tests/test_adapters.py`.

---

## Pull Request Guidelines

1. **Branch Naming**: Use descriptive branch names:
   - `feat/add-discord-adapter`
   - `fix/voice-download-timeout`
   - `docs/update-quickstart`
2. **Commit Messages**: Follow Conventional Commits format:
   - `feat: add Discord IM adapter support`
   - `fix: handle reconnection gracefully on network loss`
   - `docs: improve onboarding wizard instructions`
3. **Keep PRs Focused**: A single PR should address a single concern.
4. **Never Commit Secrets**: Do not commit tokens, keys, passwords, or personal absolute directory paths.
