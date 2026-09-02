"""Local review panel: watch a call as it happens, or read a past one.

Tails the same calls/*.jsonl the agent appends to, so it needs no connection to
the running agent, survives an agent restart, and renders past calls through
exactly the same path as live ones.

    ./venv/bin/uvicorn review:app --reload --port 8000
"""

import asyncio
import contextlib
import json
import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse

import protocol
from intake import ClaimRecord

HERE = Path(__file__).parent
CALLS = HERE / "calls"
POLL_SECONDS = 0.3
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")

FIELDS = ["policy_number", "claimant_name", "date_of_loss",
          "callback_phone", "loss_type", "description"]

# Locked down by default. Forgetting this variable in production would expose
# every caller's record; forgetting it locally only hides the picker.
LOCAL_PANEL = os.environ.get("LOCAL_PANEL") == "1"

# A public /ws/call is a tap on a billed API. Reject past the cap rather than
# queue, so a browser is told no instead of hanging.
MAX_CALLS = int(os.environ.get("MAX_CALLS", "2"))
_in_flight = 0

app = FastAPI(title="claim review panel",
              docs_url="/docs" if LOCAL_PANEL else None,
              redoc_url="/redoc" if LOCAL_PANEL else None,
              openapi_url="/openapi.json" if LOCAL_PANEL else None)


def read_lines(path):
    """Parse a JSONL call log, skipping a torn final line from a crash."""
    if not path.exists():
        return []
    entries = []
    for line in path.read_text().splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return entries


def build_record(session_id):
    """One call, from the append-only log, enriched by the summary if it exists."""
    entries = read_lines(CALLS / f"{session_id}.jsonl")
    head = next((e for e in entries if e.get("type") == "session.start"), {})
    attempts = [e for e in entries if e.get("type") == "attempt"]
    # The log is append-ordered already; sorting is a stable no-op that makes
    # "History is chronological" a property of the data, not of how it was written.
    attempts.sort(key=lambda a: a.get("at_seconds", 0))
    accepted = {a["field"]: a.get("accepted_value", a["value"])
                for a in attempts if a["status"] == "accepted"}
    record = {"session_id": session_id, "started_at": head.get("started_at"),
              "ended_at": None, "duration_seconds": None,
              "attempts": attempts, "accepted": accepted, "fields": FIELDS}
    summary = CALLS / f"{session_id}.json"
    if summary.exists():
        try:
            done = json.loads(summary.read_text())
            record["ended_at"] = done.get("ended_at")
            record["duration_seconds"] = done.get("duration_seconds")
            record["accepted"] = done.get("accepted", accepted)
        except json.JSONDecodeError:
            pass  # summary half-written; the log is still authoritative
    return record


def newest_session():
    logs = sorted(CALLS.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    return logs[-1].stem if logs else None


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/")
def index():
    return FileResponse(HERE / "review.html")


@app.get("/pcm-processor.js")
def worklet():
    """AudioWorklet modules load from a URL, so the processor is its own file."""
    return FileResponse(HERE / "pcm-processor.js", media_type="text/javascript")


@app.get("/api/config")
def config():
    """The page asks what it is allowed to show."""
    return {"local_panel": LOCAL_PANEL}


@app.get("/api/calls")
def list_calls():
    """Every call on disk, newest first. Local only: on a public host this
    would let a stranger enumerate other people's claims."""
    if not LOCAL_PANEL:
        raise HTTPException(404, "not found")
    logs = sorted(CALLS.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for log in logs:
        r = build_record(log.stem)
        out.append({"session_id": r["session_id"], "started_at": r["started_at"],
                    "attempts": len(r["attempts"]), "accepted": len(r["accepted"]),
                    "ended": r["ended_at"] is not None})
    return out


@app.get("/api/calls/{session_id}")
def get_call(session_id: str):
    if not SAFE_ID.match(session_id):
        raise HTTPException(400, "bad session id")
    if not (CALLS / f"{session_id}.jsonl").exists():
        raise HTTPException(404, "no such call")
    return build_record(session_id)


@app.get("/api/stream/{session_id}")
async def stream_one(session_id: str):
    """Follow one call. The browser only learns its own session id, and the id
    is 32 random hex characters, so this is an unlisted capability rather than
    something a stranger can walk."""
    if not SAFE_ID.match(session_id):
        raise HTTPException(400, "bad session id")
    return StreamingResponse(one_session(session_id), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


async def one_session(session_id):
    """Snapshot, then each attempt as it is appended, then ended."""
    snapshot = build_record(session_id)
    sent = len(snapshot["attempts"])
    ended = False
    yield sse("call", snapshot)
    while True:
        attempts = [e for e in read_lines(CALLS / f"{session_id}.jsonl")
                    if e.get("type") == "attempt"]
        for attempt in attempts[sent:]:
            yield sse("attempt", attempt)
        sent = len(attempts)
        if not ended and (CALLS / f"{session_id}.json").exists():
            ended = True
            yield sse("ended", build_record(session_id))
        await asyncio.sleep(POLL_SECONDS)


@app.get("/api/stream")
async def stream():
    """Follow the newest call, whichever it is. Local only: on a public host
    one viewer would watch another caller's claim arrive live. Emits `call` on (re)start, `attempt` per append,
    `ended` when the summary lands, `idle` when there is nothing to show."""
    if not LOCAL_PANEL:
        raise HTTPException(404, "not found")

    async def events():
        current, sent, ended, idle = None, 0, False, False
        while True:
            newest = newest_session()
            if newest != current:
                current, sent, ended, idle = newest, 0, False, False
                if current:
                    snapshot = build_record(current)
                    # The snapshot already carries these; count them as sent so
                    # the delta loop below does not replay them as duplicates.
                    sent = len(snapshot["attempts"])
                    yield sse("call", snapshot)
            if current is None:
                if not idle:
                    idle = True
                    yield sse("idle", {})
            else:
                attempts = [e for e in read_lines(CALLS / f"{current}.jsonl")
                            if e.get("type") == "attempt"]
                for attempt in attempts[sent:]:
                    yield sse("attempt", attempt)
                sent = len(attempts)
                if not ended and (CALLS / f"{current}.json").exists():
                    ended = True
                    yield sse("ended", build_record(current))
            await asyncio.sleep(POLL_SECONDS)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.websocket("/ws/call")
async def call(browser: WebSocket):
    """Browser <-> here <-> AssemblyAI.

    Only the audio transport changes: validation, tool handling and evidence
    logging all still run in this process, and the API key never goes near the
    browser. Mic audio arrives as binary frames and reply audio goes back the
    same way; everything else is JSON.
    """
    global _in_flight
    await browser.accept()
    if _in_flight >= MAX_CALLS:
        await browser.send_json({"kind": "error", "code": "busy",
                                 "message": "Too many calls in progress. Try again shortly."})
        return await browser.close()
    _in_flight += 1
    claim = ClaimRecord(directory=CALLS)
    ready, stop = asyncio.Event(), asyncio.Event()

    async def on_audio(pcm):
        await browser.send_bytes(pcm)

    async def on_event(kind, data):
        if kind == "ready":
            ready.set()
        await browser.send_json({"kind": kind, **data})

    async def pump_browser(ws):
        """Mic frames up. Held until session.ready, per the events reference."""
        while True:
            message = await browser.receive()
            if message["type"] == "websocket.disconnect":
                return stop.set()
            if (chunk := message.get("bytes")) is not None:
                if ready.is_set():
                    await protocol.send_audio(ws, chunk)
            elif (text := message.get("text")) is not None:
                if json.loads(text).get("action") == "hangup":
                    return stop.set()

    try:
        async with protocol.connect() as ws:
            up = asyncio.create_task(pump_browser(ws))
            down = asyncio.create_task(
                protocol.run_session(ws, claim, on_audio, on_event))
            await asyncio.wait([down, asyncio.create_task(stop.wait())],
                               return_when=asyncio.FIRST_COMPLETED)
            up.cancel()
            try:
                await protocol.end_session(ws)
                await asyncio.wait_for(down, timeout=5)
            except Exception:
                pass
            down.cancel()
    except Exception as exc:                      # bad key, network, anything
        with contextlib.suppress(Exception):
            await browser.send_json({"kind": "error", "code": "server",
                                     "message": str(exc)})
    finally:
        _in_flight -= 1
        if path := claim.write():
            print(f"call record: {path} ({len(claim.attempts)} attempts)")
        with contextlib.suppress(Exception):
            await browser.close()
