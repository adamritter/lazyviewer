"""Reusable key-combo registry primitives."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class KeyComboBinding:
    """Mapping from one or more key tokens to a single action callback."""

    combos: tuple[str, ...]
    handler: Callable[[], bool | None]


class KeyComboRegistry:
    """Small key-dispatch table with optional key normalization strategy."""

    def __init__(self, normalize: Callable[[str], str] | None = None) -> None:
        """Initialize empty registry with optional token normalizer."""
        self._normalize = normalize if normalize is not None else self._identity
        self._handlers: dict[str, Callable[[], bool | None]] = {}

    @staticmethod
    def _identity(key: str) -> str:
        """Return key unchanged for exact-match dispatch registries."""
        return key

    def register_binding(self, binding: KeyComboBinding) -> KeyComboRegistry:
        """Register one binding, overwriting existing handlers for same combos."""
        for combo in binding.combos:
            self._handlers[self._normalize(combo)] = binding.handler
        return self

    def register_bindings(self, *bindings: KeyComboBinding) -> KeyComboRegistry:
        """Register multiple bindings and return ``self`` for fluent usage."""
        for binding in bindings:
            self.register_binding(binding)
        return self

    def dispatch(self, key: str) -> bool | None:
        """Invoke bound handler for ``key`` and return its handled result."""
        handler = self._handlers.get(self._normalize(key))
        if handler is None:
            return None
        return handler()
