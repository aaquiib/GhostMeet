"""
Throwaway WebSocket logging server for verifying the extension's audio
capture pipeline (Phase 1) without depending on the real backend
(Phase 2) existing yet. Not part of backend/ — nothing here is meant to
survive past this phase.

Accepts a connection, logs the byte length of every binary frame it
receives, and prints a running total every 10 frames.

Usage:
    python scripts/test_ws_echo.py [--host 0.0.0.0] [--port 8765]

Requires the `websockets` package (already in backend/requirements.txt,
so backend/.venv works fine — or `pip install websockets` standalone).

Point the extension's stored backend URL at this server while testing:
in the extension's side panel devtools console, run
    chrome.storage.local.set({ backendUrl: "ws://localhost:8765" })
then switch it back once Phase 2's real backend is up:
    chrome.storage.local.set({ backendUrl: "ws://localhost:8000/ws/transcribe" })
(or just chrome.storage.local.remove("backendUrl") to fall back to that
same default).
"""

import argparse
import asyncio

import websockets


async def handler(websocket):
    print(f"[connect] {websocket.remote_address}")
    frame_count = 0
    total_bytes = 0

    try:
        async for message in websocket:
            if isinstance(message, bytes):
                frame_count += 1
                total_bytes += len(message)
                print(f"[frame {frame_count}] {len(message)} bytes")

                if frame_count % 10 == 0:
                    print(f"[running total] {frame_count} frames, {total_bytes} bytes")
            else:
                print(f"[text frame ignored] {message!r}")
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        print(
            f"[disconnect] {websocket.remote_address} — "
            f"{frame_count} frames, {total_bytes} bytes total"
        )


async def main(host: str, port: int) -> None:
    async with websockets.serve(handler, host, port):
        print(f"Listening on ws://{host}:{port}")
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    asyncio.run(main(args.host, args.port))
