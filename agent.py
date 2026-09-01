"""Minimal terminal voice agent on AssemblyAI's Voice Agent API — raw WebSocket,
no SDK. Every inbound event lands in events.jsonl. Ctrl+C hangs up cleanly.
"""

import asyncio, base64, json, os, queue, signal, sys, threading
from datetime import datetime, timezone

import numpy as np, sounddevice as sd, websockets
from dotenv import load_dotenv

URL = "wss://agents.assemblyai.com/v1/ws"
RATE = 24000  # PCM16 mono 24 kHz, both directions (the audio/pcm default)
BLOCK = 1200  # 50 ms of frames at 24 kHz — the chunk size the docs recommend
EVENT_LOG = "events.jsonl"

load_dotenv()
API_KEY = os.environ.get("ASSEMBLYAI_API_KEY")
if not API_KEY:
    sys.exit("ASSEMBLYAI_API_KEY missing — put it in .env")

# First message on the socket. voice/greeting/output.format are immutable
# once session.ready lands; system_prompt and turn_detection are not.
SESSION = {
    "system_prompt": (
        "You are a friendly claims intake assistant on a voice call. Keep every "
        "reply to one or two short sentences. Ask one question at a time, and read "
        "names, dates, and numbers back to confirm them."),
    "greeting": "Hi, I can help you start a claim. What happened?",
    "input": {"format": {"encoding": "audio/pcm"}},
    "output": {"voice": "anna", "format": {"encoding": "audio/pcm"}},
}


def log_event(event):
    """Append the full JSON of one inbound event, one object per line."""
    with open(EVENT_LOG, "a") as f:
        stamped = {"received_at": datetime.now(timezone.utc).isoformat(), **event}
        f.write(json.dumps(stamped) + "\n")


class Speaker:
    """Drains reply.audio chunks to the sound card on a dedicated thread.

    stream.write() blocks until the card has buffer room, so it stays off the
    event loop. Chunks go straight out; the hardware clock paces, never a sleep.
    """

    def __init__(self):
        self.q = queue.Queue()
        self.stream = sd.OutputStream(samplerate=RATE, channels=1, dtype="int16")
        self.stream.start()
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        while True:
            pcm = self.q.get()
            if pcm is None:
                return
            self.stream.write(pcm)

    def play(self, b64):
        self.q.put(np.frombuffer(base64.b64decode(b64), dtype=np.int16))

    def flush(self):
        """Drop queued speech after a barge-in so stale audio isn't heard."""
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break
        self.stream.abort()
        self.stream.start()

    def close(self):
        self.q.put(None)
        self.stream.close()


async def send_mic(ws, ready):
    """Stream the mic as input.audio events, but only after session.ready."""
    await ready.wait()
    loop, chunks = asyncio.get_running_loop(), asyncio.Queue()

    def on_audio(indata, frames, time_info, status):
        loop.call_soon_threadsafe(chunks.put_nowait, bytes(indata))

    with sd.RawInputStream(
        samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK, callback=on_audio
    ):
        print("mic live — speak, Ctrl+C to hang up\n")
        while True:
            payload = base64.b64encode(await chunks.get()).decode()
            await ws.send(json.dumps({"type": "input.audio", "audio": payload}))


async def receive(ws, speaker, ready):
    """Log every inbound event, then act on the ones we care about."""
    async for raw in ws:
        event = json.loads(raw)
        log_event(event)
        etype = event.get("type")
        if etype == "session.ready":
            print(f"session ready ({event.get('session_id')})")
            ready.set()
        elif etype == "reply.audio":
            speaker.play(event["data"])  # output field is "data", not "audio"
        elif etype == "transcript.user.delta":
            # Each delta is the full text so far — overwrite, don't append.
            print(f"\r  you: {event.get('text', '')}", end="", flush=True)
        elif etype == "transcript.user":
            print(f"\r  you: {event.get('text', '')}")
        elif etype == "transcript.agent":
            print(f"agent: {event.get('text', '')}")
        elif etype == "reply.done" and event.get("status") == "interrupted":
            speaker.flush()
        elif etype == "session.error":
            print(f"[error] {event.get('code')}: {event.get('message')}")
        elif etype == "session.ended":
            return


async def main():
    speaker = Speaker()
    ready, hangup = asyncio.Event(), asyncio.Event()
    # Ctrl+C sets an event; asyncio.run's own SIGINT cancels main, skipping cleanup.
    asyncio.get_running_loop().add_signal_handler(signal.SIGINT, hangup.set)
    headers = {"Authorization": f"Bearer {API_KEY}"}  # WS wants Bearer; REST does not

    async with websockets.connect(URL, additional_headers=headers) as ws:
        await ws.send(json.dumps({"type": "session.update", "session": SESSION}))
        mic = asyncio.create_task(send_mic(ws, ready))
        rx = asyncio.create_task(receive(ws, speaker, ready))
        await asyncio.wait([rx, asyncio.create_task(hangup.wait())],
                           return_when=asyncio.FIRST_COMPLETED)
        mic.cancel()
        print("\nhanging up…")
        # session.end stops billing now; just closing leaves a paid 30s window.
        try:
            await ws.send(json.dumps({"type": "session.end"}))
            await asyncio.wait_for(rx, timeout=5)
        except Exception:
            pass
        rx.cancel()
        speaker.close()


if __name__ == "__main__":
    asyncio.run(main())
