"""Serial transport for the MK2/MK3 interface."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import serialx

from .const import BAUD_RATE

type Opener = Callable[
    [str], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]
]

# Exceptions that indicate the connection is gone or cannot be opened.
CONNECTION_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    EOFError,
    serialx.SerialException,
)


async def open_serial(url: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open the interface at 2400 baud 8N1.

    ``url`` is a device path such as ``/dev/serial/by-id/...`` or any URL
    understood by serialx, e.g. ``socket://host:port`` for ser2net.
    """
    reader, writer = await serialx.open_serial_connection(
        url=url,
        baudrate=BAUD_RATE,
        parity=serialx.Parity.NONE,
        stopbits=serialx.StopBits.ONE,
        exclusive=True,
    )
    return reader, writer
