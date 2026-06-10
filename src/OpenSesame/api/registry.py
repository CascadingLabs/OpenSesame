"""Process-wide, ref-counted model registry.

Loading a transcription/vision pipeline costs hundreds of MB of VRAM and seconds
of disk I/O. Models must therefore be owned by a *process-wide* registry keyed by
``(kind, model_id, device)`` — never by a Solver instance or a per-call closure,
or 8 concurrent solves reload the model 8 times and OOM the GPU.

The heavy implementations (Whisper, ViT/CLIP, OCR) live in the solver modules and
register a *factory* here; the API package stays import-light and testable by
registering fake factories. ``acquire``/``release`` ref-count so ``Solver.engine()``
can deterministically free VRAM when the last holder exits.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

# A factory loads and returns an opaque provider object for a model key.
ProviderFactory = Callable[["ModelKey"], Any]


@dataclass(frozen=True)
class ModelKey:
    kind: str        # "whisper" | "vit" | "clip" | "ocr" | ...
    model_id: str
    device: str = "auto"


@dataclass
class _Entry:
    provider: Any
    refcount: int


class ModelRegistry:
    """Lazily loads providers via registered factories; caches + ref-counts them."""

    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}
        self._entries: dict[ModelKey, _Entry] = {}
        self._lock = threading.Lock()

    def register_factory(self, kind: str, factory: ProviderFactory) -> None:
        """Register how to load a provider of ``kind`` (e.g. 'whisper')."""

        with self._lock:
            self._factories[kind] = factory

    def has_factory(self, kind: str) -> bool:
        return kind in self._factories

    def acquire(self, key: ModelKey) -> Any:
        """Load (once) and ref-count a provider for ``key``. Returns the provider."""

        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                factory = self._factories.get(key.kind)
                if factory is None:
                    msg = (
                        f"no model factory registered for kind {key.kind!r}; "
                        "install the matching extra or register a provider."
                    )
                    raise LookupError(msg)
                entry = _Entry(provider=factory(key), refcount=0)
                self._entries[key] = entry
            entry.refcount += 1
            return entry.provider

    def release(self, key: ModelKey) -> None:
        """Drop a ref; unload the provider when the last holder releases."""

        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return
            entry.refcount -= 1
            if entry.refcount <= 0:
                self._entries.pop(key, None)
                _maybe_unload(entry.provider)

    def warmup(self, keys: list[ModelKey]) -> None:
        """Pre-load providers so a cold first call doesn't eat the solve timeout."""

        for key in keys:
            self.acquire(key)

    def loaded_keys(self) -> list[ModelKey]:
        with self._lock:
            return list(self._entries.keys())


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
