from __future__ import annotations

import logging
import os
import re
import socket
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class Discovery:
    DEFAULT_EXECUTABLE_PATHS = [
        "/Applications/Antigravity 2.app/Contents/Resources/bin/language_server",
        "/Applications/Antigravity IDE.app/Contents/Resources/app/extensions/antigravity/bin/language_server_macos_arm",
    ]

    @classmethod
    def is_port_open(cls, host_port: str, timeout: float = 0.2) -> bool:
        try:
            host, port_str = host_port.split(":")
            with socket.create_connection((host, int(port_str)), timeout=timeout):
                return True
        except Exception:
            return False

    @classmethod
    def discover_executable(cls) -> Path | None:
        if env_path := os.getenv("ANTIGRAVITY_AGENTAPI_EXE"):
            p = Path(env_path)
            if p.is_file():
                return p
        for path_str in cls.DEFAULT_EXECUTABLE_PATHS:
            p = Path(path_str)
            if p.is_file():
                return p
        return None

    @classmethod
    def discover_db(cls) -> Path | None:
        home = Path.home()
        candidates = [
            home / ".gemini" / "antigravity" / "conversation_summaries.db",
            home / ".gemini" / "antigravity-ide" / "conversation_summaries.db",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    @classmethod
    def extract_hub_info(cls, ps_output: str) -> tuple[str | None, str | None]:
        csrf_re = re.compile(r"--csrf_token\s+([a-f0-9\-]+)")
        bridge_re = re.compile(r"--host_bridge_url=https?://[^:]+:(\d+)")
        port_re = re.compile(r"--port\s+(\d+)")

        csrf: str | None = None
        addr: str | None = None

        for line in ps_output.splitlines():
            if "language_server" in line and "subclient_type hub" in line:
                if m := csrf_re.search(line):
                    csrf = m.group(1)
                if m := bridge_re.search(line):
                    p = int(m.group(1))
                    addr = f"127.0.0.1:{p + 2}"
                elif m := port_re.search(line):
                    addr = f"127.0.0.1:{m.group(1)}"

                if csrf and addr:
                    return addr, csrf

        return addr, csrf

    @classmethod
    def extract_csrf_token(cls, ps_output: str) -> str | None:
        csrf_re = re.compile(r"--csrf_token\s+([a-f0-9\-]+)")
        for line in ps_output.splitlines():
            if "language_server" in line and (m := csrf_re.search(line)):
                return m.group(1)
        return None

    @classmethod
    def extract_hub_address(cls, ps_output: str) -> str | None:
        env_re = re.compile(r"ANTIGRAVITY_LS_ADDRESS=((?:127\.0\.0\.1|localhost):\d+)")
        for line in ps_output.splitlines():
            if m := env_re.search(line):
                return m.group(1)
        return None

    @classmethod
    def discover_hub(cls) -> tuple[str | None, str | None]:
        # 1. Prefer inspecting process environments (ps -wwE) to find live address and token
        try:
            res = subprocess.run(
                ["ps", "-wwE", "-eo", "command"],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                addr_re = re.compile(r"ANTIGRAVITY_LS_ADDRESS=((?:127\.0\.0\.1|localhost):\d+)")
                csrf_re = re.compile(r"ANTIGRAVITY_CSRF_TOKEN=([^\s]+)")
                for line in res.stdout.splitlines():
                    addr_m = addr_re.search(line)
                    csrf_m = csrf_re.search(line)
                    if addr_m:
                        addr = addr_m.group(1)
                        token = csrf_m.group(1) if csrf_m else None
                        if cls.is_port_open(addr):
                            return addr, token
        except Exception as e:
            logger.warning("Failed to inspect ps -wwE: %s", e)

        # 2. Fallback to CLI args in running language_server (ps -eo pid,command)
        hub_address: str | None = None
        csrf_token: str | None = None
        try:
            res = subprocess.run(
                ["ps", "-eo", "pid,command"],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                addr, token = cls.extract_hub_info(res.stdout)
                if addr and cls.is_port_open(addr):
                    hub_address = addr
                    csrf_token = token
                elif not csrf_token:
                    csrf_token = cls.extract_csrf_token(res.stdout)
        except Exception as e:
            logger.warning("Failed to run ps -eo pid,command: %s", e)

        # 3. Last resort fallback: accept unverified address if present
        if not hub_address:
            try:
                res = subprocess.run(
                    ["ps", "-wwE", "-eo", "command"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if res.returncode == 0:
                    hub_address = cls.extract_hub_address(res.stdout)
                    if not csrf_token:
                        csrf_token = cls.extract_csrf_token(res.stdout)
            except Exception as e:
                logger.warning("Failed fallback ps check: %s", e)

        return hub_address, csrf_token
