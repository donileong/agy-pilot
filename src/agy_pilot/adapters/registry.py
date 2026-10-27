from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Type

from .base import BaseIMAdapter

if TYPE_CHECKING:
    from agy_pilot.config import Config
    from agy_pilot.controller import Antigravity2Controller
    from agy_pilot.storage import SessionStore

AdapterFactory = Callable[
    ["Config", "Antigravity2Controller", "SessionStore"],
    BaseIMAdapter,
]

_ADAPTER_REGISTRY: dict[str, AdapterFactory] = {}


def register_adapter(name: str, factory_or_cls: Type[BaseIMAdapter] | AdapterFactory) -> None:
    """Register an IM adapter factory or class by name."""
    _ADAPTER_REGISTRY[name.lower()] = factory_or_cls


def get_adapter(
    name: str,
    config: Config,
    controller: Antigravity2Controller,
    store: SessionStore,
) -> BaseIMAdapter:
    """Retrieve and instantiate an IM adapter by name."""
    normalized_name = name.lower()

    # Lazy-register default built-in adapters if not already present
    if normalized_name == "telegram" and "telegram" not in _ADAPTER_REGISTRY:
        from .telegram import TelegramAdapter
        register_adapter("telegram", TelegramAdapter)

    factory = _ADAPTER_REGISTRY.get(normalized_name)
    if not factory:
        available = ", ".join(_ADAPTER_REGISTRY.keys()) or "telegram"
        raise ValueError(
            f"Unsupported IM adapter '{name}'. Available adapters: {available}"
        )
    return factory(config, controller, store)
