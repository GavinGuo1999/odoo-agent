from __future__ import annotations

import asyncio


def selector_loop_factory(
    use_subprocess: bool = False,
) -> asyncio.AbstractEventLoop:
    """Psycopg async connections require SelectorEventLoop on Windows."""

    del use_subprocess
    return asyncio.SelectorEventLoop()
