from __future__ import annotations

import asyncio

import pytest

from open_sesame.harness.page_sync import SerializedPage, wait_page_op


class SingleFlightPage:
    """Mimics VoidCrawl's take-work-replace page: concurrency is an error."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.max_in_flight = 0
        self.calls = 0
        self.url = "about:blank"

    async def eval_js(self, code: str) -> str:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        if self.in_flight > 1:
            self.in_flight -= 1
            msg = "page is closed"
            raise RuntimeError(msg)
        await asyncio.sleep(0.01)
        self.in_flight -= 1
        self.calls += 1
        return code


def test_serialized_page_serializes_concurrent_calls() -> None:
    async def scenario() -> SingleFlightPage:
        raw = SingleFlightPage()
        page = SerializedPage(raw)
        await asyncio.gather(*(page.eval_js(f"call-{index}") for index in range(8)))
        return raw

    raw = asyncio.run(scenario())
    assert raw.calls == 8
    assert raw.max_in_flight == 1


def test_serialized_page_passes_through_plain_attributes() -> None:
    page = SerializedPage(SingleFlightPage())
    assert page.url == "about:blank"


def test_wait_page_op_returns_result() -> None:
    async def scenario() -> str:
        async def op() -> str:
            return "done"

        return await wait_page_op(op(), timeout=1.0)

    assert asyncio.run(scenario()) == "done"


def test_wait_page_op_times_out_without_cancelling() -> None:
    async def scenario() -> tuple[bool, bool]:
        finished = asyncio.Event()

        async def slow_op() -> None:
            await asyncio.sleep(0.05)
            finished.set()

        timed_out = False
        try:
            await wait_page_op(slow_op(), timeout=0.01)
        except TimeoutError:
            timed_out = True
        await asyncio.sleep(0.1)
        return timed_out, finished.is_set()

    timed_out, op_finished = asyncio.run(scenario())
    assert timed_out
    assert op_finished


def test_wait_page_op_propagates_operation_error() -> None:
    async def scenario() -> None:
        async def failing_op() -> None:
            msg = "boom"
            raise ValueError(msg)

        await wait_page_op(failing_op(), timeout=1.0)

    with pytest.raises(ValueError, match="boom"):
        asyncio.run(scenario())
