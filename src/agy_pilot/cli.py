from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path

from .adapters import get_adapter
from .config import (
    DEFAULT_CONFIG_DIR,
    DEFAULT_CONFIG_FILE,
    DEFAULT_DATA_DIR,
    DEFAULT_LOG_DIR,
    Config,
)
from .controller import Antigravity2Controller
from .service import get_service_manager
from .storage import SessionStore


def init_configuration() -> None:
    """Interactive onboarding wizard to configure Bot Token, User IDs, and services."""
    print("=" * 60)
    print("🚀 Welcome to Antigravity Pilot (agy-pilot) Setup Wizard")
    print("=" * 60)
    print("\n[Step 1/3] Telegram Bot Token Setup:")
    print("  1. Open Telegram and search for @BotFather (https://t.me/BotFather).")
    print("  2. Send '/newbot' and follow instructions to name your bot.")
    print("  3. BotFather will provide an HTTP API token (e.g., 123456789:ABCdef...).")

    current_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    token_prompt = f"Enter your Bot Token [{current_token}]: " if current_token else "Enter your Bot Token: "

    while True:
        try:
            bot_token = input(token_prompt).strip()
        except (KeyboardInterrupt, EOFError):
            print("\nSetup cancelled.")
            return

        if not bot_token and current_token:
            bot_token = current_token
        if bot_token and ":" in bot_token:
            break
        print("❌ Invalid token format. It should look like '123456789:ABCdef-ghijklm...'")

    print("\n[Step 2/3] Allowed User ID(s) Setup (Security Whitelist):")
    print("  1. In Telegram, message @userinfobot (https://t.me/userinfobot).")
    print("  2. It will reply with your numeric User ID (e.g., 12345678).")
    print("  3. You can enter one ID, or multiple IDs separated by commas.")

    current_ids = os.getenv("TELEGRAM_ALLOWED_USER_IDS") or os.getenv("TELEGRAM_ALLOWED_USER_ID", "")
    id_prompt = f"Enter Telegram User ID(s) [{current_ids}]: " if current_ids else "Enter Telegram User ID(s): "

    while True:
        try:
            raw_ids = input(id_prompt).strip()
        except (KeyboardInterrupt, EOFError):
            print("\nSetup cancelled.")
            return

        if not raw_ids and current_ids:
            raw_ids = current_ids

        # Validate numbers
        parsed_ids = [s.strip() for s in raw_ids.replace(";", ",").split(",") if s.strip()]
        if parsed_ids and all(s.isdigit() for s in parsed_ids):
            user_ids_str = ",".join(parsed_ids)
            break
        print("❌ Invalid input. Please enter numeric user IDs separated by commas (e.g. 12345678, 87654321).")

    # Save to user config directory
    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_content = (
        "# Antigravity Pilot Configuration\n"
        "AGY_ADAPTER=telegram\n"
        f"TELEGRAM_BOT_TOKEN={bot_token}\n"
        f"TELEGRAM_ALLOWED_USER_IDS={user_ids_str}\n"
        "LOG_LEVEL=INFO\n"
    )
    DEFAULT_CONFIG_FILE.write_text(config_content, encoding="utf-8")
    try:
        DEFAULT_CONFIG_FILE.chmod(0o600)
    except Exception:
        pass

    print(f"\n✅ Configuration saved securely to: {DEFAULT_CONFIG_FILE}")

    # Step 3: Background service option
    print("\n[Step 3/3] Background Service Setup:")
    try:
        enable_service = input("Do you want to enable automatic background startup now? (Y/n): ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        enable_service = "n"

    if enable_service in ("", "y", "yes"):
        mgr = get_service_manager()
        mgr.install(auto_start=True)
        print("\n🎉 Setup complete! agy-pilot is now running in the background.")
        print("Send /start to your bot in Telegram to begin!")
        return

    print("\n🎉 Setup complete! You can start agy-pilot manually by running:")
    print("  agy-pilot")
    print("Or install the background service anytime with:")
    print("  agy-pilot service install")


def clean_resources(all_data: bool = False, force: bool = False) -> None:
    """Clean logs, background services, and optionally database/config files."""
    print("Cleaning Antigravity Pilot resources...")

    # 1. Uninstall background service
    mgr = get_service_manager()
    mgr.uninstall()

    # 2. Clean logs
    if DEFAULT_LOG_DIR.is_dir():
        for log_file in DEFAULT_LOG_DIR.glob("*.log"):
            try:
                log_file.unlink()
                print(f"  ✓ Deleted log: {log_file}")
            except Exception:
                pass

    # 3. If --all requested
    if all_data:
        if not force:
            try:
                confirm = input("⚠️  Delete database, saved sessions, downloads, and config? (y/N): ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                confirm = "n"
            if confirm not in ("y", "yes"):
                print("Skipped deleting data files.")
                return

        # Clean config
        if DEFAULT_CONFIG_FILE.is_file():
            DEFAULT_CONFIG_FILE.unlink()
            print(f"  ✓ Removed configuration file: {DEFAULT_CONFIG_FILE}")

        # Clean standard data directory
        if DEFAULT_DATA_DIR.is_dir():
            shutil.rmtree(DEFAULT_DATA_DIR, ignore_errors=True)
            print(f"  ✓ Removed data directory: {DEFAULT_DATA_DIR}")

    print("Cleanup complete.")


def update_application() -> None:
    """Self-update agy-pilot to latest version and hot-restart daemon."""
    print("Checking for agy-pilot updates...")
    cwd = Path.cwd()
    is_git_repo = (cwd / ".git").is_dir()

    if is_git_repo and shutil.which("git"):
        print("Detected local Git repository. Pulling latest commits...")
        res_pull = os.system("git pull")
        if res_pull != 0:
            print("Warning: git pull failed or encountered conflicts.")
            return

        uv_cmd = shutil.which("uv")
        if uv_cmd:
            print("Syncing dependencies with uv...")
            os.system(f"{uv_cmd} sync")
        else:
            print("Syncing dependencies with pip...")
            os.system("pip install -e .")
    else:
        if shutil.which("uv"):
            print("Upgrading via uv tool...")
            res_uv = os.system("uv tool upgrade agy-pilot")
            if res_uv != 0:
                os.system("uv tool install --reinstall git+https://github.com/donileong/agy-pilot.git")
        elif shutil.which("pipx"):
            print("Upgrading via pipx...")
            os.system("pipx upgrade agy-pilot")
        else:
            print("Upgrading via pip...")
            os.system("pip install --upgrade agy-pilot")

    print("Update complete.")
    mgr = get_service_manager()
    status = mgr.status()
    if status.installed:
        print("Restarting background daemon...")
        mgr.restart()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Antigravity Pilot: Headless Controller & IM Bridge for Google Antigravity 2"
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # Subcommand: service
    svc_parser = subparsers.add_parser("service", help="Manage background daemon service")
    svc_parser.add_argument(
        "action",
        choices=["install", "uninstall", "start", "stop", "restart", "status", "logs"],
        help="Action to perform on background service",
    )
    svc_parser.add_argument("-f", "--follow", action="store_true", help="Follow service logs")
    svc_parser.add_argument("-n", "--lines", type=int, default=30, help="Number of log lines to show")

    # Subcommand: init
    subparsers.add_parser("init", help="Run interactive onboarding wizard")

    # Subcommand: clean
    clean_parser = subparsers.add_parser("clean", help="Clean service, logs, and cache")
    clean_parser.add_argument("--all", action="store_true", help="Delete config and databases as well")
    clean_parser.add_argument("-f", "--force", action="store_true", help="Skip confirmation prompt")

    # Subcommand: update
    subparsers.add_parser("update", help="Update application to latest version")

    # Root flags
    parser.add_argument("--adapter", default=None, help="IM Adapter to activate")

    args = parser.parse_args()

    # Route: service subcommand
    if args.subcommand == "service":
        mgr = get_service_manager()
        action = args.action
        if action == "install":
            mgr.install(auto_start=True)
        elif action == "uninstall":
            mgr.uninstall()
        elif action == "start":
            mgr.start()
        elif action == "stop":
            mgr.stop()
        elif action == "restart":
            mgr.restart()
        elif action == "status":
            st = mgr.status()
            print(f"Service Type : {st.service_type}")
            print(f"Installed    : {'Yes' if st.installed else 'No'}")
            print(f"Status       : {'🟢 Running' if st.running else '⚪️ Stopped'}")
            if st.pid:
                print(f"PID          : {st.pid}")
            if st.exit_code is not None:
                print(f"Last Exit    : {st.exit_code}")
            if st.details:
                print(f"Details      : {st.details}")
        elif action == "logs":
            mgr.logs(follow=args.follow, lines=args.lines)
        return

    # Route: init
    if args.subcommand == "init":
        init_configuration()
        return

    # Route: clean
    if args.subcommand == "clean":
        clean_resources(all_data=args.all, force=args.force)
        return

    # Route: update
    if args.subcommand == "update":
        update_application()
        return

    config = Config.from_env()
    adapter_name = args.adapter or config.adapter or "telegram"

    if adapter_name == "telegram" and not config.bot_token:
        print("⚠️  No Telegram Bot Token configured.")
        if sys.stdin.isatty():
            try:
                run_wizard = input("Would you like to run the setup wizard now? (Y/n): ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                run_wizard = "n"
            if run_wizard in ("", "y", "yes"):
                init_configuration()
                config = Config.from_env()
                if not config.bot_token:
                    print("Setup did not complete. Exiting.")
                    return
            else:
                print("Run 'agy-pilot init' when you are ready to configure.")
                return
        else:
            print("Please set TELEGRAM_BOT_TOKEN or run 'agy-pilot init' to configure.")
            return

    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logging.info("Starting agy-pilot (adapter=%s)...", adapter_name)
    controller = Antigravity2Controller.discover()
    store = SessionStore(config.db_path)
    adapter = get_adapter(
        adapter_name,
        config=config,
        controller=controller,
        store=store,
    )

    try:
        asyncio.run(adapter.start())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Shutting down agy-pilot...")


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()

    logging.info("Starting agy-pilot (adapter=%s)...", adapter_name)
    controller = Antigravity2Controller.discover()
    store = SessionStore(config.db_path)
    adapter = get_adapter(
        adapter_name,
        config=config,
        controller=controller,
        store=store,
    )

    try:
        asyncio.run(adapter.start())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Shutting down agy-pilot...")


if __name__ == "__main__":
    main()
