"""Terminal voice agent on AssemblyAI's Voice Agent API.

The protocol lives in protocol.py and is shared with the web client; this file
is the sound card, the device pinning and the Ctrl+C handling. Every inbound
event lands in events.jsonl, and the call record in calls/<session_id>.json.

Run with --list-devices to see audio devices and the .env lines that pin them.
"""

import asyncio
import os
import signal
import sys
from contextlib import aclosing

from audio import Speaker, list_devices, mic_chunks, resolve_device
from intake import ClaimRecord
from protocol import FIELDS, connect, end_session, run_session, send_audio


async def pump_mic(ws, ready, device):
    """Stream the mic upstream, but only after session.ready."""
    await ready.wait()
    async with aclosing(mic_chunks(device)) as chunks:
        async for chunk in chunks:
            await send_audio(ws, chunk)


def terminal_sink(speaker, ready):
    """Turn protocol events into terminal output and speaker control."""
    async def on_audio(pcm):
        speaker.play(pcm)

    async def on_event(kind, data):
        if kind == "ready":
            print(f"session ready ({data['session_id']})")
            ready.set()
        elif kind == "user_partial":
            print(f"\r  you: {data['text']}", end="", flush=True)
        elif kind == "user_final":
            print(f"\r  you: {data['text']}")
        elif kind == "agent":
            print(f"agent: {data['text']}")
        elif kind == "tool":
            print(f'TOOL {data["field"]} = "{data["value"]}" -> {data["status"]}')
        elif kind == "interrupted":
            speaker.flush()
        elif kind == "error":
            print(f"[error] {data['code']}: {data['message']}")

    return on_audio, on_event


async def main():
    print("audio devices:")
    in_dev = resolve_device(os.environ.get("INPUT_DEVICE"), "input")
    out_dev = resolve_device(os.environ.get("OUTPUT_DEVICE"), "output")
    speaker, claim = Speaker(out_dev), ClaimRecord()
    ready, hangup = asyncio.Event(), asyncio.Event()
    # Ctrl+C sets an event; asyncio.run's own SIGINT cancels main, skipping cleanup.
    asyncio.get_running_loop().add_signal_handler(signal.SIGINT, hangup.set)
    on_audio, on_event = terminal_sink(speaker, ready)

    async with connect() as ws:
        mic = asyncio.create_task(pump_mic(ws, ready, in_dev))
        rx = asyncio.create_task(run_session(ws, claim, on_audio, on_event))
        await asyncio.wait([rx, asyncio.create_task(hangup.wait())],
                           return_when=asyncio.FIRST_COMPLETED)
        mic.cancel()
        print("\nhanging up…")
        try:
            await end_session(ws)
            await asyncio.wait_for(rx, timeout=5)
        except Exception:
            pass
        rx.cancel()
        speaker.close()
        if path := claim.write():
            print(f"call record: {path} ({len(claim.attempts)} attempts, "
                  f"{len(claim.fields)}/{len(FIELDS)} fields accepted)")


if __name__ == "__main__":
    if "--list-devices" in sys.argv:
        list_devices()
    else:
        asyncio.run(main())
