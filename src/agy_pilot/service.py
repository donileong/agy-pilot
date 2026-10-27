from __future__ import annotations

import os
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import DEFAULT_CONFIG_DIR, DEFAULT_LOG_DIR

SERVICE_LABEL = "com.ii2d.agy-pilot"
SYSTEMD_SERVICE_NAME = "agy-pilot.service"


def get_executable_command() -> list[str]:
    """Resolve the standalone agy-pilot command or fallback to python module."""
    exe = shutil.which("agy-pilot")
    if exe:
        return [exe]
    return [sys.executable, "-m", "agy_pilot.cli"]


@dataclass
class ServiceStatus:
    installed: bool
    running: bool
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    service_type: str = "unknown"
    details: str = ""


class BaseServiceManager(ABC):
    @abstractmethod
    def install(self, auto_start: bool = True) -> bool:
        pass

    @abstractmethod
    def uninstall(self) -> bool:
        pass

    @abstractmethod
    def start(self) -> bool:
        pass

    @abstractmethod
    def stop(self) -> bool:
        pass

    @abstractmethod
    def restart(self) -> bool:
        pass

    @abstractmethod
    def status(self) -> ServiceStatus:
        pass

    @abstractmethod
    def logs(self, follow: bool = False, lines: int = 30) -> None:
        pass


class LaunchdManager(BaseServiceManager):
    """macOS launchd user agent service manager."""

    def __init__(self) -> None:
        self.home = Path.home()
        self.launch_agents = self.home / "Library" / "LaunchAgents"
        self.plist_path = self.launch_agents / f"{SERVICE_LABEL}.plist"
        self.log_dir = DEFAULT_LOG_DIR
        self.stdout_log = self.log_dir / "agy-pilot.stdout.log"
        self.stderr_log = self.log_dir / "agy-pilot.stderr.log"

    def _render_plist(self) -> str:
        exec_args = get_executable_command()
        exec_xml = "\n".join(f"        <string>{arg}</string>" for arg in exec_args)

        raw_path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin")
        path_env = f"{self.home}/.local/bin:/opt/homebrew/bin:{raw_path}"

        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{SERVICE_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
{exec_xml}
    </array>
    <key>WorkingDirectory</key>
    <string>{DEFAULT_CONFIG_DIR}</string>
    <key>StandardOutPath</key>
    <string>{self.stdout_log}</string>
    <key>StandardErrorPath</key>
    <string>{self.stderr_log}</string>
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{path_env}</string>
        <key>HOME</key>
        <string>{self.home}</string>
    </dict>
</dict>
</plist>
"""

    def install(self, auto_start: bool = True) -> bool:
        self.launch_agents.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        content = self._render_plist()
        self.plist_path.write_text(content, encoding="utf-8")
        print(f"Created launchd service configuration: {self.plist_path}")

        if auto_start:
            return self.start()
        return True

    def start(self) -> bool:
        if not self.plist_path.is_file():
            print(f"Service definition not found: {self.plist_path}")
            return False
        # Unload any stale instance before loading
        os.system(f"launchctl unload -w '{self.plist_path}' 2>/dev/null")
        res = os.system(f"launchctl load -w '{self.plist_path}'")
        if res == 0:
            print(f"Service loaded and started: {SERVICE_LABEL}")
            return True
        print(f"Failed to load service (status code {res})")
        return False

    def stop(self) -> bool:
        if not self.plist_path.is_file():
            print("Service is not installed.")
            return False
        res = os.system(f"launchctl unload -w '{self.plist_path}' 2>/dev/null")
        if res == 0:
            print(f"Service stopped: {SERVICE_LABEL}")
            return True
        print("Service was not running or failed to stop.")
        return False

    def restart(self) -> bool:
        uid = os.getuid()
        service_target = f"gui/{uid}/{SERVICE_LABEL}"
        res = os.system(f"launchctl kickstart -k {service_target} 2>/dev/null")
        if res == 0:
            print(f"Successfully restarted service: {service_target}")
            return True
        # Fallback to unload / load
        return self.start()

    def uninstall(self) -> bool:
        if self.plist_path.is_file():
            self.stop()
            self.plist_path.unlink(missing_ok=True)
            print(f"Uninstalled service: {self.plist_path}")
            return True
        print("Service plist not found.")
        return False

    def status(self) -> ServiceStatus:
        if not self.plist_path.is_file():
            return ServiceStatus(installed=False, running=False, service_type="launchd", details="Not installed")

        try:
            out = subprocess.check_output(["launchctl", "list"], text=True, stderr=subprocess.DEVNULL)
            for line in out.splitlines():
                parts = line.strip().split()
                if len(parts) >= 3 and parts[2] == SERVICE_LABEL:
                    pid_str, exit_str = parts[0], parts[1]
                    pid = int(pid_str) if pid_str.isdigit() else None
                    exit_code = int(exit_str) if exit_str.lstrip("-").isdigit() else None
                    running = pid is not None and pid > 0
                    return ServiceStatus(
                        installed=True,
                        running=running,
                        pid=pid,
                        exit_code=exit_code,
                        service_type="launchd",
                        details=f"PID: {pid or 'N/A'}, Last Exit Code: {exit_code}",
                    )
        except Exception as e:
            return ServiceStatus(
                installed=True,
                running=False,
                service_type="launchd",
                details=f"Error querying launchctl: {e}",
            )

        return ServiceStatus(
            installed=True,
            running=False,
            service_type="launchd",
            details="Installed but inactive",
        )

    def logs(self, follow: bool = False, lines: int = 30) -> None:
        target_log = self.stderr_log if self.stderr_log.is_file() else self.stdout_log
        if not target_log.is_file():
            print(f"No logs found yet in {self.log_dir}")
            return
        cmd = ["tail", f"-n{lines}"]
        if follow:
            cmd.append("-f")
        cmd.append(str(target_log))
        try:
            subprocess.run(cmd)
        except KeyboardInterrupt:
            pass


class SystemdManager(BaseServiceManager):
    """Linux systemd user service manager."""

    def __init__(self) -> None:
        self.home = Path.home()
        self.unit_dir = self.home / ".config" / "systemd" / "user"
        self.unit_path = self.unit_dir / SYSTEMD_SERVICE_NAME

    def _render_unit(self) -> str:
        exec_args = get_executable_command()
        exec_str = " ".join(exec_args)
        return f"""[Unit]
Description=Antigravity Pilot (agy-pilot) Daemon
After=network.target

[Service]
Type=simple
ExecStart={exec_str}
WorkingDirectory={DEFAULT_CONFIG_DIR}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""

    def install(self, auto_start: bool = True) -> bool:
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.unit_path.write_text(self._render_unit(), encoding="utf-8")
        print(f"Created systemd unit configuration: {self.unit_path}")
        os.system("systemctl --user daemon-reload")
        if auto_start:
            return self.start()
        return True

    def start(self) -> bool:
        os.system("systemctl --user daemon-reload")
        res = os.system(f"systemctl --user enable --now {SYSTEMD_SERVICE_NAME}")
        return res == 0

    def stop(self) -> bool:
        res = os.system(f"systemctl --user stop {SYSTEMD_SERVICE_NAME}")
        return res == 0

    def restart(self) -> bool:
        res = os.system(f"systemctl --user restart {SYSTEMD_SERVICE_NAME}")
        return res == 0

    def uninstall(self) -> bool:
        if self.unit_path.is_file():
            self.stop()
            os.system(f"systemctl --user disable {SYSTEMD_SERVICE_NAME} 2>/dev/null")
            self.unit_path.unlink(missing_ok=True)
            os.system("systemctl --user daemon-reload")
            print(f"Uninstalled service: {self.unit_path}")
            return True
        print("Systemd service not found.")
        return False

    def status(self) -> ServiceStatus:
        if not self.unit_path.is_file():
            return ServiceStatus(installed=False, running=False, service_type="systemd", details="Not installed")
        res = os.system(f"systemctl --user is-active --quiet {SYSTEMD_SERVICE_NAME}")
        running = (res == 0)
        return ServiceStatus(
            installed=True,
            running=running,
            service_type="systemd",
            details="Active (running)" if running else "Inactive",
        )

    def logs(self, follow: bool = False, lines: int = 30) -> None:
        cmd = ["journalctl", "--user-unit", SYSTEMD_SERVICE_NAME, f"-n{lines}"]
        if follow:
            cmd.append("-f")
        try:
            subprocess.run(cmd)
        except KeyboardInterrupt:
            pass


def get_service_manager() -> BaseServiceManager:
    """Return platform-appropriate service manager."""
    if sys.platform == "darwin":
        return LaunchdManager()
    return SystemdManager()
