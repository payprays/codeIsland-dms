from __future__ import annotations

import asyncio
from typing import Any

from .protocol import from_json_line, to_json_line


MAX_RESPONSE_BYTES = 16 * 1024 * 1024


async def rpc_call(socket_path: str, method: str, params: dict[str, Any], request_id: str = "req-1") -> dict[str, Any]:
    reader, writer = await asyncio.open_unix_connection(socket_path, limit=MAX_RESPONSE_BYTES)
    try:
        writer.write(to_json_line({"id": request_id, "method": method, "params": params}))
        await writer.drain()
        line = await reader.readline()
        if not line:
            raise RuntimeError("no response from daemon")
        return from_json_line(line)
    finally:
        writer.close()
        await writer.wait_closed()
