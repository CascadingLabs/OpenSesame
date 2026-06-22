"""Process-wide model cache.

Loading a transcription/vision pipeline costs hundreds of MB of VRAM and seconds
of disk I/O, so models are owned by a *process-wide* registry keyed by
``(kind, model_id, device)``; never by a Solver instance or a per-call closure,
or 8 concurrent solves reload the model 8 times and OOM the GPU.

``get`` loads once and caches for the process lifetime, so a plain
``await solver.solve(...)`` warms on the first call and stays warm; no caller
ceremony. ``Solver.engine()`` is optional sugar: it pre-warms the policy's models
and unloads them on exit for deterministic VRAM cleanup. The heavy implementations
(Whisper, ViT/CLIP, OCR) live in the solver modules and register a *factory* here,
so the API package stays import-light and is tested with fakes.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

# A factory loads and returns an opaque provider object for a model key.
ProviderFactory = Callable[["ModelKey"], Any]


@dataclass(frozen=True)
class ModelKey:
    kind: str        # "whisper" | "tiles" | "ocr" | ...
    model_id: str
    device: str = "auto"


class ModelRegistry:
    """Lazily loads providers via registered factories; caches them per process."""

    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}
        self._cache: dict[ModelKey, Any] = {}
        self._lock = threading.Lock()

    def register_factory(self, kind: str, factory: ProviderFactory) -> None:
        """Register how to load a provider of ``kind`` (e.g. 'whisper')."""

        with self._lock:
            self._factories[kind] = factory

    def has_factory(self, kind: str) -> bool:
        return kind in self._factories

    def get(self, key: ModelKey) -> Any:
        """Return the provider for ``key``, loading + caching it on first use."""

        with self._lock:
            provider = self._cache.get(key)
            if provider is None:
                factory = self._factories.get(key.kind)
                if factory is None:
                    msg = (
                        f"no model factory registered for kind {key.kind!r}; "
                        "install the matching extra or register a provider."
                    )
                    raise LookupError(msg)
                provider = factory(key)
                self._cache[key] = provider
            return provider

    def warmup(self, keys: list[ModelKey]) -> list[ModelKey]:
        """Pre-load providers (skipping kinds with no factory). Returns those loaded."""

        loaded: list[ModelKey] = []
        for key in keys:
            if self.has_factory(key.kind):
                self.get(key)
                loaded.append(key)
        return loaded

    def unload(self, key: ModelKey) -> None:
        """Free + drop one cached provider (e.g. to reclaim VRAM)."""

        with self._lock:
            provider = self._cache.pop(key, None)
        if provider is not None:
            _maybe_unload(provider)

    def unload_all(self) -> None:
        with self._lock:
            items = list(self._cache.values())
            self._cache.clear()
        for provider in items:
            _maybe_unload(provider)

    def loaded_keys(self) -> list[ModelKey]:
        with self._lock:
            return list(self._cache.keys())


def _maybe_unload(provider: Any) -> None:
    # Providers may expose an explicit unload hook (e.g. to free VRAM).
    unload = getattr(provider, "unload", None)
    if callable(unload):
        try:
            unload()
        except Exception:
            pass


# Default shared registry. Solvers reuse this unless given their own.
_DEFAULT = ModelRegistry()


def default_registry() -> ModelRegistry:
    return _DEFAULT
