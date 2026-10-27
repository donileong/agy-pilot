from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agy_pilot.config import Config
    from agy_pilot.controller import Antigravity2Controller
    from agy_pilot.storage import SessionStore


class BaseIMAdapter(ABC):
    """Abstract Base Class for Instant Messaging adapters/plugins (Telegram, Discord, Slack, etc.)."""

    def __init__(
        self,
        config: Config,
        controller: Antigravity2Controller,
        store: SessionStore,
    ) -> None:
        self.config = config
        self.controller = controller
        self.store = store

    @abstractmethod
    async def start(self) -> None:
        """Start listening for incoming messages or polling events."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down the adapter."""
        ...
