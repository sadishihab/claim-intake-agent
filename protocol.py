"""The Voice Agent protocol, shared by every client.

Session configuration, event dispatch, tool calling and evidence logging live
here. Transport does not: audio in and out are callables the caller supplies, so
the terminal client can hand us a sound card and the web client a browser
socket. The API key is read here and never leaves the server process.
"""

import base64
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import websockets
from dotenv import load_dotenv

from validators import load_policies

load_dotenv()
API_KEY = os.environ.get("ASSEMBLYAI_API_KEY")
if not API_KEY:
    sys.exit("ASSEMBLYAI_API_KEY missing — put it in .env")

URL = "wss://agents.assemblyai.com/v1/ws"
EVENT_LOG = "events.jsonl"

FIELDS = ["policy_number", "claimant_name", "date_of_loss",
          "callback_phone", "loss_type", "description"]

load_dotenv()
API_KEY = os.environ.get("ASSEMBLYAI_API_KEY")
if not API_KEY:
    sys.exit("ASSEMBLYAI_API_KEY missing — put it in .env")

# Bias transcription toward the policy-number shape. Measured against baseline,
# transcription_prompt and max_accuracy: only this one moved anything. A spelled
# TJ2-9002 comes back as "TJ too." without it and "TJ2-9002." with it -- and that
# number is not on the list, so the bias is toward the pattern, not the strings.
# The documented ceiling is 100 terms.
KEYTERMS = [p["policy_number"] for p in load_policies()][:100]

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
        "- unconfirmed: the readback is a question the caller has to answer. Say "
        "it out loud, word for word, before you do anything else, then stop and "
        "wait. Do not skip it, do not shorten it, do not move on to the next "
        "field, and do not call record_field again until the caller has "
        "answered. The one-or-two-sentence limit never applies to a readback.\n"
        "- rejected: say the readback word for word. It already explains the "
        "problem and asks again. Then wait for a new answer.\n\n"
        "When a readback offers a choice, the caller's answer is the new value: "
        "if they pick vandalism, call record_field with \"vandalism\", not the "
        "words they used the first time. If the caller says something that does "
        "not answer the question, ask the question again — do not record what "
        "they just said and do not resend the old value. Never call record_field "
        "twice with the same value for the same field: a value that came back "
        "unconfirmed will never become accepted by sending it again.\n\n"
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
    "input": {"format": {"encoding": "audio/pcm"}, "keyterms": KEYTERMS},
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


@asynccontextmanager
async def connect():
    """Open the session and send the opening session.update."""
    headers = {"Authorization": f"Bearer {API_KEY}"}  # WS wants Bearer; REST does not
    async with websockets.connect(URL, additional_headers=headers) as ws:
        await ws.send(json.dumps({"type": "session.update", "session": SESSION}))
        yield ws


async def send_audio(ws, pcm):
    """Frame one chunk of PCM16 mono 24 kHz as an input.audio event."""
    await ws.send(json.dumps({"type": "input.audio",
                              "audio": base64.b64encode(pcm).decode()}))


async def end_session(ws):
    """session.end stops billing now; just closing leaves a paid 30s window."""
    await ws.send(json.dumps({"type": "session.end"}))


async def run_session(ws, claim, on_audio, on_event):
    """Dispatch every inbound event until the session ends.

    `on_audio(pcm_bytes)` receives decoded reply audio. `on_event(kind, data)`
    receives everything a client might display or act on: ready, user_partial,
    user_final, agent, tool, interrupted, error, ended. Both are awaited, so a
    client can write to a socket as easily as to a sound card.
    """
    tools = ToolQueue()
    async for raw in ws:
        event = json.loads(raw)
        log_event(event)
        etype = event.get("type")
        if etype == "session.ready":
            claim.start(event.get("session_id"))  # anchors every at_seconds
            await on_event("ready", {"session_id": event.get("session_id")})
        elif etype == "reply.audio":
            await on_audio(base64.b64decode(event["data"]))  # field is "data"
        elif etype == "transcript.user.delta":
            # Each delta is the full text so far — replace, don't append.
            await on_event("user_partial", {"text": event.get("text", "")})
        elif etype == "transcript.user":
            text = event.get("text", "")
            claim.note_user_transcript(event.get("item_id"), text)
            await on_event("user_final", {"text": text})
        elif etype == "transcript.agent":
            await on_event("agent", {"text": event.get("text", "")})
        elif etype == "tool.call":
            args = event.get("arguments") or {}  # already a dict, use as-is
            field, value = args.get("field"), args.get("value")
            verdict = claim.record(field, value)
            result = {"status": verdict.status, "reason": verdict.reason,
                      "readback": verdict.readback}
            await on_event("tool", {"field": field, "value": value, **result})
            tools.add(event["call_id"], result)
            await tools.flush(ws)
        elif etype in ("reply.started", "input.speech.started"):
            tools.note(etype)  # a turn is in flight, hold any results
        elif etype == "reply.done":
            tools.note(etype)
            if event.get("status") == "interrupted":
                tools.discard()  # the agent moved on; these results are stale
                await on_event("interrupted", {})
            else:
                await tools.flush(ws)
        elif etype == "session.error":
            await on_event("error", {"code": event.get("code"),
                                     "message": event.get("message")})
        elif etype == "session.ended":
            await on_event("ended", {})
            return
