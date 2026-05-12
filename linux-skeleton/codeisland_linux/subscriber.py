from __future__ import annotations

import argparse
import asyncio
import json

from .client import MAX_RESPONSE_BYTES
from .protocol import default_socket_path, from_json_line, to_json_line


async def subscribe(socket_path: str, pretty: bool) -> None:
    reader, writer = await asyncio.open_unix_connection(socket_path, limit=MAX_RESPONSE_BYTES)
    try:
        writer.write(to_json_line({"id": "req-subscribe", "method": "subscribe", "params": {"topics": ["sessions", "interactions"]}}))
        await writer.drain()
        while True:
            line = await reader.readline()
            if not line:
                break
            payload = from_json_line(line)
            if pretty:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(json.dumps(payload, sort_keys=True))
    finally:
        writer.close()
        await writer.wait_closed()


def main() -> None:
    parser = argparse.ArgumentParser(description="Minimal Phase 0 subscriber")
    parser.add_argument("--socket-path", default=default_socket_path())
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    asyncio.run(subscribe(args.socket_path, args.pretty))


if __name__ == "__main__":
    main()
