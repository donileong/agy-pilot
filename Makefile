.PHONY: help init run restart status logs test test-cov lint format sync update clean clean-all install-service uninstall-service

help:
	@echo "🛠️  agy-pilot (Antigravity Pilot) Management Commands:"
	@echo ""
	@echo "  make init              - 🧙 Interactive onboarding setup wizard"
	@echo "  make update            - 🔄 Self-update agy-pilot to latest version & restart"
	@echo "  make status            - 📌 Inspect daemon service status (PID, health)"
	@echo "  make restart           - 🚀 Restart background daemon service"
	@echo "  make logs              - 📜 Follow runtime logs"
	@echo "  make clean             - 🧹 Clean service and logs"
	@echo "  make clean-all         - 🗑️  Clean service, logs, database, and config"
	@echo "  make run               - 💻 Run directly in foreground (for debugging)"
	@echo "  make test              - 🧪 Run complete test suite"
	@echo "  make test-cov          - 📊 Run tests with code coverage report"
	@echo "  make lint              - 🔍 Check code quality and style with Ruff"
	@echo "  make format            - 🎨 Auto-format code with Ruff"
	@echo "  make sync              - 📦 Sync dependencies with uv"
	@echo "  make install-service   - ⚙️  Install and load background daemon"
	@echo "  make uninstall-service - 🗑️  Stop and remove background daemon"
	@echo ""

init:
	uv run agy-pilot init

clean:
	uv run agy-pilot clean

clean-all:
	uv run agy-pilot clean --all

run:
	uv run agy-pilot

restart:
	uv run agy-pilot service restart

update:
	uv run agy-pilot update

status:
	uv run agy-pilot service status

logs:
	uv run agy-pilot service logs -f

test:
	uv run pytest

test-cov:
	uv run pytest --cov=agy_pilot

lint:
	uv tool run ruff check .

format:
	uv tool run ruff format .

sync:
	uv sync

install-service:
	uv run agy-pilot service install

uninstall-service:
	uv run agy-pilot service uninstall
