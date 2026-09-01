"""Minimal terminal voice agent on AssemblyAI's Voice Agent API — raw WebSocket,
no SDK. Every inbound event lands in events.jsonl. Ctrl+C hangs up cleanly.
Run with --list-devices to see audio devices and the .env lines that pin them.
"""

import asyncio, base64, json, os, queue, signal, sys, threading
from datetime import datetime, timezone

import numpy as np, sounddevice as sd, websockets
from dotenv import load_dotenv

URL = "wss://agents.assemblyai.com/v1/ws"
RATE = 24000  # PCM16 mono 24 kHz, both directions (the audio/pcm default)
BLOCK = 1200  # 50 ms of frames at 24 kHz — the chunk size the docs recommend
EVENT_LOG = "events.jsonl"

FIELDS = ["policy_number", "claimant_name", "date_of_loss",
          "callback_phone", "loss_type", "description"]

load_dotenv()
API_KEY = os.environ.get("ASSEMBLYAI_API_KEY")
if not API_KEY:
    sys.exit("ASSEMBLYAI_API_KEY missing — put it in .env")

TOOLS = [{
    "type": "function",
    "name": "record_field",
    "description": (
        "Record one piece of claim information the caller has given you. Call this "
        "every time the caller provides a value. Do not write anything down "
        "without calling this first."),
    "parameters": {
        "type": "object",
        "properties": {
            "field": {"type": "string", "enum": FIELDS,
                      "description": "Which field this value fills."},
            "value": {"type": "string",
                      "description": "The value exactly as you understood it, "
                                     "verbatim, with no cleanup or reformatting."},
        },
        "required": ["field", "value"],
    },
}]

# First message on the socket. voice/greeting/output.format are immutable
# once session.ready lands; system_prompt and tools are not.
SESSION = {
    "system_prompt": (
        "You are a claims intake assistant on a voice call. Collect these six "
        "fields, in this order: policy number, claimant name, date of loss, "
        "callback phone, loss type, and a description of what happened. Ask for "
        "one at a time and keep every reply to one or two short sentences. Call "
        "record_field the moment the caller gives you a value, before you "
        "acknowledge it or move on to the next field."),
    "greeting": "Hi, I can help you start a claim. Can I take your policy number?",
    "tools": TOOLS,
    "input": {"format": {"encoding": "audio/pcm"}},
    "output": {"voice": "anna", "format": {"encoding": "audio/pcm"}},
}


def log_event(event):
    """Append the full JSON of one inbound event, one object per line."""
    with open(EVENT_LOG, "a") as f:
        stamped = {"received_at": datetime.now(timezone.utc).isoformat(), **event}
        f.write(json.dumps(stamped) + "\n")


# --- audio devices -------------------------------------------------------

def suggested_pin(kind):
    """A stable named node, preferred over ALSA 'default' (a PipeWire alias)."""
    for d in sd.query_devices():
        if d[f"max_{kind}_channels"] > 0 and d["name"].startswith(f"alsa_{kind}."):
            return d["name"]
    return None


def list_devices():
    print(sd.query_devices())
    print("\nPin these in .env so PipeWire cannot re-select between runs:")
    for kind in ("input", "output"):
        if pin := suggested_pin(kind):
            print(f"  {kind.upper()}_DEVICE={pin}")


def resolve_device(spec, kind):
    """Resolve an index, a name substring, or the default — once, at startup.

    Monitor sources are never matched for input: they are a loopback of what is
    playing, so the agent would transcribe its own voice.
    """
    devices = sd.query_devices()
    if not spec:
        idx = sd.query_devices(kind=kind)["index"]
        print(f"  {kind:<6} [{idx}] {devices[idx]['name']}  <- unpinned, see --list-devices")
        return idx
    if spec.strip().isdigit():
        idx = int(spec)
    else:
        hits = [i for i, d in enumerate(devices)
                if spec.lower() in d["name"].lower()
                and d[f"max_{kind}_channels"] > 0
                and not (kind == "input" and d["name"].endswith(".monitor"))]
        if not hits:
            sys.exit(f"No {kind} device matches {spec!r}. Try --list-devices.")
        idx = hits[0]
    print(f"  {kind:<6} [{idx}] {devices[idx]['name']}")
    return idx


class Speaker:
    """Drains reply.audio chunks to the sound card on a dedicated thread.

    stream.write() blocks until the card has buffer room, so it stays off the
    event loop. Chunks go straight out; the hardware clock paces, never a sleep.
    """

    def __init__(self, device):
        self.q = queue.Queue()
        self.stream = sd.OutputStream(samplerate=RATE, channels=1, dtype="int16",
                                      device=device)
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


class ToolQueue:
    """Holds tool results until reply.done is the newest event received.

    Sending mid-turn cuts off the agent's transition phrase; sending after the
    next turn starts is too late. flush() is called from both the tool.call and
    reply.done handlers because a result can become ready on either side of it.
    """

    def __init__(self):
        self.last_event = None
        self.pending = []

    def note(self, etype):
        self.last_event = etype

    def add(self, call_id, result):
        self.pending.append((call_id, result))

    def discard(self):
        self.pending.clear()

    async def flush(self, ws):
        if self.last_event != "reply.done" or not self.pending:
            return
        for call_id, result in self.pending:
            await ws.send(json.dumps({"type": "tool.result", "call_id": call_id,
                                      "result": json.dumps(result)}))
        self.pending.clear()


async def send_mic(ws, ready, device):
    """Stream the mic as input.audio events, but only after session.ready."""
    await ready.wait()
    loop, chunks = asyncio.get_running_loop(), asyncio.Queue()

    def on_audio(indata, frames, time_info, status):
        loop.call_soon_threadsafe(chunks.put_nowait, bytes(indata))

    with sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16",
                           blocksize=BLOCK, device=device, callback=on_audio):
        print("mic live — speak, Ctrl+C to hang up\n")
        while True:
            payload = base64.b64encode(await chunks.get()).decode()
            await ws.send(json.dumps({"type": "input.audio", "audio": payload}))


async def receive(ws, speaker, ready, tools):
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
        elif etype == "tool.call":
            args = event.get("arguments") or {}  # already a dict, use as-is
            print(f'TOOL {args.get("field")} = "{args.get("value")}"')
            tools.add(event["call_id"], {"status": "accepted"})  # validators land here
            await tools.flush(ws)
        elif etype in ("reply.started", "input.speech.started"):
            tools.note(etype)  # a turn is in flight, hold any results
        elif etype == "reply.done":
            tools.note(etype)
            if event.get("status") == "interrupted":
                speaker.flush()
                tools.discard()  # the agent moved on; these results are stale
            else:
                await tools.flush(ws)
        elif etype == "session.error":
            print(f"[error] {event.get('code')}: {event.get('message')}")
        elif etype == "session.ended":
            return


async def main():
    print("audio devices:")
    in_dev = resolve_device(os.environ.get("INPUT_DEVICE"), "input")
    out_dev = resolve_device(os.environ.get("OUTPUT_DEVICE"), "output")
    speaker, tools = Speaker(out_dev), ToolQueue()
    ready, hangup = asyncio.Event(), asyncio.Event()
    # Ctrl+C sets an event; asyncio.run's own SIGINT cancels main, skipping cleanup.
    asyncio.get_running_loop().add_signal_handler(signal.SIGINT, hangup.set)
    headers = {"Authorization": f"Bearer {API_KEY}"}  # WS wants Bearer; REST does not

    async with websockets.connect(URL, additional_headers=headers) as ws:
        await ws.send(json.dumps({"type": "session.update", "session": SESSION}))
        mic = asyncio.create_task(send_mic(ws, ready, in_dev))
        rx = asyncio.create_task(receive(ws, speaker, ready, tools))
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
    if "--list-devices" in sys.argv:
        list_devices()
    else:
        asyncio.run(main())
