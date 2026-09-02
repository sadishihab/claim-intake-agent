"""Minimal terminal voice agent on AssemblyAI's Voice Agent API — raw WebSocket,
no SDK. Every inbound event lands in events.jsonl. Ctrl+C hangs up cleanly.
Run with --list-devices to see audio devices and the .env lines that pin them.

This file is the protocol: session config, event dispatch, tool calls.
Microphone, speaker, and device selection live in audio.py.
"""

import asyncio, base64, json, os, signal, sys
from contextlib import aclosing
from datetime import datetime, timezone

import websockets
from dotenv import load_dotenv

from audio import Speaker, list_devices, mic_chunks, resolve_device
from intake import ClaimRecord

URL = "wss://agents.assemblyai.com/v1/ws"
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
        "one at a time and keep every reply to one or two short sentences.\n\n"
        "Call record_field the moment the caller gives you a value, before you "
        "acknowledge it. Every call comes back with a status, a reason, and a "
        "readback. Act on the status:\n"
        "- accepted: go straight on to the next field. Do not read the value "
        "back and do not confirm it.\n"
        "- unconfirmed: say the readback word for word, then wait for the caller "
        "to confirm or correct it before moving on.\n"
        "- rejected: say the readback word for word. It already explains the "
        "problem and asks again. Then wait for a new answer.\n\n"
        "date_of_loss must be ISO-8601, YYYY-MM-DD, and you convert it before "
        "you call record_field: \"the first of June twenty twenty-five\" becomes "
        "\"2025-06-01\". If the caller gives a date with no year, ask them which "
        "year before you record anything. Never assume the current year, never "
        "carry a year over from another answer, and never infer one from the "
        "policy dates. A date without a year is not a date yet.\n\n"
        "The readback is authoritative and is the only thing you may say about a "
        "value. Speak it verbatim: never paraphrase it, never re-spell a value "
        "your own way, never invent a readback of your own, and never read a "
        "value back that was accepted. The reason field is for your understanding "
        "only — never say it aloud."),
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
    """Frame mic chunks as input.audio events, but only after session.ready."""
    await ready.wait()
    async with aclosing(mic_chunks(device)) as chunks:
        async for chunk in chunks:
            payload = base64.b64encode(chunk).decode()
            await ws.send(json.dumps({"type": "input.audio", "audio": payload}))


async def receive(ws, speaker, ready, tools, claim):
    """Log every inbound event, then act on the ones we care about."""
    async for raw in ws:
        event = json.loads(raw)
        log_event(event)
        etype = event.get("type")
        if etype == "session.ready":
            print(f"session ready ({event.get('session_id')})")
            claim.start(event.get("session_id"))  # anchors every at_seconds
            ready.set()
        elif etype == "reply.audio":
            speaker.play(base64.b64decode(event["data"]))  # field is "data", not "audio"
        elif etype == "transcript.user.delta":
            # Each delta is the full text so far — overwrite, don't append.
            print(f"\r  you: {event.get('text', '')}", end="", flush=True)
        elif etype == "transcript.user":
            print(f"\r  you: {event.get('text', '')}")
            # Attach this utterance to whatever field the agent records next.
            claim.note_user_transcript(event.get("item_id"), event.get("text", ""))
        elif etype == "transcript.agent":
            print(f"agent: {event.get('text', '')}")
        elif etype == "tool.call":
            args = event.get("arguments") or {}  # already a dict, use as-is
            field, value = args.get("field"), args.get("value")
            verdict = claim.record(field, value)
            print(f'TOOL {field} = "{value}" -> {verdict.status}')
            tools.add(event["call_id"], {"status": verdict.status,
                                         "reason": verdict.reason,
                                         "readback": verdict.readback})
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
    speaker, tools, claim = Speaker(out_dev), ToolQueue(), ClaimRecord()
    ready, hangup = asyncio.Event(), asyncio.Event()
    # Ctrl+C sets an event; asyncio.run's own SIGINT cancels main, skipping cleanup.
    asyncio.get_running_loop().add_signal_handler(signal.SIGINT, hangup.set)
    headers = {"Authorization": f"Bearer {API_KEY}"}  # WS wants Bearer; REST does not

    async with websockets.connect(URL, additional_headers=headers) as ws:
        await ws.send(json.dumps({"type": "session.update", "session": SESSION}))
        mic = asyncio.create_task(send_mic(ws, ready, in_dev))
        rx = asyncio.create_task(receive(ws, speaker, ready, tools, claim))
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
        if path := claim.write():
            print(f"call record: {path} ({len(claim.attempts)} attempts, "
                  f"{len(claim.fields)}/{len(FIELDS)} fields accepted)")


if __name__ == "__main__":
    if "--list-devices" in sys.argv:
        list_devices()
    else:
        asyncio.run(main())
