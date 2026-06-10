"""Serialize VoidCrawl page access so concurrent helpers cannot poison the tab."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any


class SerializedPage:
    """Async proxy that routes every page call through one lock.

    VoidCrawl pages are single-flight: while one CDP call is in flight the
    underlying page slot is empty, so a concurrent call fails with
    "page is closed", and a cancelled in-flight call loses the page
    permanently. Sharing one page between tasks (for example a screenshot
    recorder next to the main actor flow) therefore requires mutual
    exclusion.
    """

    def __init__(self, page: Any) -> None:
        self._page = page
        self._lock = asyncio.Lock()

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._page, name)
        if not callable(attr):
            return attr

        async def call(*args: Any, **kwargs: Any) -> Any:
            async with self._lock:
                result = attr(*args, **kwargs)
                if inspect.isawaitable(result):
                    return await result
                return result

        return call


async def wait_page_op(awaitable: Any, *, timeout: float) -> Any:
    """Bound a page operation without cancelling it on timeout.

    ``asyncio.wait_for`` cancels the awaited operation when the timeout
    fires, and a cancelled in-flight VoidCrawl CDP call permanently loses
    the page. This helper lets the slow operation finish in the background
    and raises ``TimeoutError`` to the caller instead.
    """

    task = asyncio.ensure_future(awaitable)
    done, _pending = await asyncio.wait({task}, timeout=timeout)
    if not done:
        msg = f"page operation exceeded {timeout:.1f}s"
        raise TimeoutError(msg)
    return task.result()
