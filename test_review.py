"""Review panel: reading a call back off disk.

The HTTP layer is thin and exercised by hand; the logic worth pinning is how a
record is rebuilt from the append-only log, including a log that was cut off
mid-write.
"""

import json

import pytest

import review
from validators import ACCEPTED, UNCONFIRMED


@pytest.fixture
def calls(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "CALLS", tmp_path)
    return tmp_path


def write_log(calls, session_id, attempts, started="2026-09-02T10:00:00+00:00"):
    lines = [{"type": "session.start", "session_id": session_id, "started_at": started}]
    lines += [{"type": "attempt", **a} for a in attempts]
    (calls / f"{session_id}.jsonl").write_text(
        "\n".join(json.dumps(l) for l in lines) + "\n")


def attempt(field, value, status, accepted_value=None, said="..."):
    return {"at_seconds": 1.0, "field": field, "value": value, "status": status,
            "reason": "r", "readback": "rb", "accepted_value": accepted_value,
            "heard": {"item_id": "i1", "text": said}}


def test_history_survives_a_field_being_accepted_later(calls):
    """Rejections stay in the record — that is the whole point of the panel."""
    write_log(calls, "s1", [
        attempt("policy_number", "BX7-440", "rejected"),
        attempt("policy_number", "ZZ9-0000", "rejected"),
        attempt("policy_number", "BX7-4402", "accepted", "BX7-4402"),
    ])
    r = review.build_record("s1")
    assert [a["status"] for a in r["attempts"]] == ["rejected", "rejected", "accepted"]
    assert r["accepted"] == {"policy_number": "BX7-4402"}


def test_accepted_uses_the_normalized_value(calls):
    """loss_type is spoken as a sentence and lands as an enum value."""
    write_log(calls, "s1", [
        attempt("loss_type", "someone hit me at the lights", "accepted", "collision")])
    assert review.build_record("s1")["accepted"] == {"loss_type": "collision"}


def test_a_torn_final_line_costs_one_attempt_not_the_call(calls):
    write_log(calls, "s1", [attempt("policy_number", "BX7-4402", "accepted", "BX7-4402"),
                            attempt("callback_phone", "5551234567", "accepted", "5551234567")])
    log = calls / "s1.jsonl"
    log.write_text(log.read_text()[:-14])       # chop mid-object
    r = review.build_record("s1")
    assert len(r["attempts"]) == 1 and r["session_id"] == "s1"


def test_summary_supplies_end_time_and_duration(calls):
    write_log(calls, "s1", [attempt("policy_number", "BX7-4402", "accepted", "BX7-4402")])
    assert review.build_record("s1")["ended_at"] is None
    (calls / "s1.json").write_text(json.dumps({
        "ended_at": "2026-09-02T10:02:00+00:00", "duration_seconds": 120.0,
        "accepted": {"policy_number": "BX7-4402"}}))
    r = review.build_record("s1")
    assert r["ended_at"] == "2026-09-02T10:02:00+00:00" and r["duration_seconds"] == 120.0


def test_a_half_written_summary_falls_back_to_the_log(calls):
    write_log(calls, "s1", [attempt("policy_number", "BX7-4402", "accepted", "BX7-4402")])
    (calls / "s1.json").write_text('{"ended_at": "2026-09')   # truncated
    r = review.build_record("s1")
    assert r["ended_at"] is None and r["accepted"] == {"policy_number": "BX7-4402"}


def test_history_is_chronological_even_if_the_log_is_not(calls):
    """Fields render in schema order; History renders in time order."""
    out_of_order = [
        {**attempt("loss_type", "fire", "accepted", "fire"), "at_seconds": 9.0},
        {**attempt("policy_number", "BX7-4402", "accepted", "BX7-4402"), "at_seconds": 1.0},
        {**attempt("claimant_name", "Marcus Halloway", "accepted"), "at_seconds": 4.0},
    ]
    write_log(calls, "s1", out_of_order)
    r = review.build_record("s1")
    assert [a["at_seconds"] for a in r["attempts"]] == [1.0, 4.0, 9.0]
    assert r["fields"][0] == "policy_number"   # schema order, served to the page


def test_equal_timestamps_keep_their_written_order(calls):
    write_log(calls, "s1", [
        {**attempt("policy_number", "first", "rejected"), "at_seconds": 2.0},
        {**attempt("policy_number", "second", "rejected"), "at_seconds": 2.0},
    ])
    assert [a["value"] for a in review.build_record("s1")["attempts"]] == ["first", "second"]


def test_newest_session_is_the_one_the_live_stream_follows(calls):
    import os, time
    write_log(calls, "older", [attempt("policy_number", "BX7-4402", "accepted")])
    write_log(calls, "newer", [attempt("policy_number", "BX7-4420", "accepted")])
    os.utime(calls / "older.jsonl", (time.time() - 60, time.time() - 60))
    assert review.newest_session() == "newer"


def test_no_calls_means_no_session(calls):
    assert review.newest_session() is None


# --- the browser must never see the key ----------------------------------

BROWSER_ASSETS = ["review.html", "pcm-processor.js"]


@pytest.mark.parametrize("name", BROWSER_ASSETS)
def test_browser_assets_carry_no_credentials(name):
    """Audio is proxied through this server precisely so the key stays here.
    Nothing served to the browser may carry it, an auth header, or a direct
    route to AssemblyAI that would need one."""
    import os, re
    from pathlib import Path

    text = (Path(review.__file__).parent / name).read_text()
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if key:
        assert key not in text
    assert "Bearer" not in text
    assert "Authorization" not in text
    assert "agents.assemblyai.com" not in text
    assert not re.search(r"[0-9a-f]{32,}", text)   # nothing key-shaped


def test_the_call_socket_is_registered():
    paths = [getattr(r, "path", "") for r in review.app.routes]
    assert "/ws/call" in paths and "/pcm-processor.js" in paths


def test_the_page_does_not_force_an_audiocontext_sample_rate():
    """Firefox builds one MediaTrackGraph per sample rate (bug 1387454) and
    feeds only the DEFAULT graph's output to the echo canceller (bug 1849108).
    Forcing 24 kHz put playback in its own graph, so the agent heard itself:
    70% of caller turns in a live call were verbatim copies of the agent's own
    speech. Capture resamples in the worklet instead, playback in createBuffer.
    """
    import re
    from pathlib import Path

    page = (Path(review.__file__).parent / "review.html").read_text()
    assert "new AudioContext()" in page, "context must use the device rate"
    assert not re.search(r"new AudioContext\s*\(\s*\{", page), \
        "a forced sampleRate splits the graph and breaks echo cancellation"
    assert page.count("new AudioContext") == 1, "one context, one graph"


def test_the_worklet_resamples_and_batches():
    """The worklet is the only thing converting to 24 kHz now, and it must
    batch: at 48 kHz a 128-frame process() call is 2.7 ms of audio."""
    from pathlib import Path

    js = (Path(review.__file__).parent / "pcm-processor.js").read_text()
    assert "inputSampleRate / targetSampleRate" in js
    assert "chunkSamples" in js and "this.filled" in js


# --- transcription config ------------------------------------------------

def test_keyterms_are_not_used():
    """Biasing the recognizer toward policies.json made it rewrite mis-heard
    input onto a real policy: "C411" came back "KD4-1188" and validated, three
    calls running. Exact match is only evidence while the recognizer knows
    nothing about the answer key."""
    import protocol

    assert "keyterms" not in protocol.SESSION["input"]
    assert not hasattr(protocol, "KEYTERMS")


def test_no_transcription_lever_is_carried():
    """transcription_prompt and max_accuracy measured at zero effect; keyterms
    measured actively harmful. None of them ship."""
    import protocol

    for lever in ("keyterms", "transcription_prompt", "transcription_mode"):
        assert lever not in protocol.SESSION["input"]


def test_record_field_exposes_the_confirmed_flag():
    import protocol

    props = protocol.TOOLS[0]["parameters"]["properties"]
    assert props["confirmed"]["type"] == "boolean"
    assert "confirmed" not in protocol.TOOLS[0]["parameters"]["required"]


# --- what a stranger can reach -------------------------------------------

def test_public_mode_is_the_default():
    """Locked down unless explicitly opened. Forgetting the variable in
    production would expose every caller's record; forgetting it locally only
    hides the picker."""
    import review

    assert review.LOCAL_PANEL is False, "LOCAL_PANEL must not default to on"


def test_interactive_docs_are_off_in_public_mode():
    import review

    assert review.app.docs_url is None
    assert review.app.redoc_url is None
    assert review.app.openapi_url is None


def test_the_global_listing_is_closed_in_public_mode():
    """Enumerating calls would let a stranger walk other people's claims."""
    import review
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        review.list_calls()
    assert exc.value.status_code == 404


def test_the_global_stream_is_closed_in_public_mode():
    """Following the newest call would show one viewer another caller's claim
    arriving live."""
    import asyncio

    import review
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        asyncio.run(review.stream())
    assert exc.value.status_code == 404


def test_a_call_can_still_be_read_by_its_own_id(calls):
    """Scoping, not blocking: the browser follows the call it started. Session
    ids are 32 random hex characters and are never listed."""
    write_log(calls, "s1", [attempt("policy_number", "BX7-4402", "accepted", "BX7-4402")])
    assert review.get_call("s1")["session_id"] == "s1"
    assert "/api/stream/{session_id}" in [getattr(r, "path", "") for r in review.app.routes]


def test_a_bad_session_id_is_rejected_by_the_scoped_stream():
    import asyncio

    import review
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        asyncio.run(review.stream_one("../../etc/passwd"))


def test_concurrent_calls_are_capped():
    """/ws/call spends money on every connection."""
    import review

    assert review.MAX_CALLS >= 1
    assert "_in_flight" in review.__dict__


# --- deployment ----------------------------------------------------------

def test_the_page_upgrades_the_socket_on_https():
    """A ws:// socket from an https:// page is blocked as mixed content, and
    silently: the call button would simply never connect."""
    from pathlib import Path

    page = (Path(review.__file__).parent / "review.html").read_text()
    assert 'location.protocol === "https:" ? "wss:" : "ws:"' in page
    assert "`ws://${location.host}" not in page


def test_procfile_binds_the_platform_port():
    from pathlib import Path

    proc = (Path(review.__file__).parent / "Procfile").read_text()
    assert "--host 0.0.0.0" in proc and "$PORT" in proc
    assert "--port 8000" not in proc


def test_the_key_is_read_once():
    """The block was duplicated when protocol.py was split out of agent.py."""
    from pathlib import Path

    src = (Path(review.__file__).parent / "protocol.py").read_text()
    assert src.count("load_dotenv()") == 1


# --- the comparison view --------------------------------------------------

def test_the_comparison_is_public_in_every_mode():
    """Judges see this on the deployed instance, where LOCAL_PANEL is off."""
    paths = [getattr(r, "path", "") for r in review.app.routes]
    assert "/compare" in paths and "/api/comparison" in paths


def test_the_validated_column_is_computed_not_written_down():
    """If it were written into the fixture it could drift from the code. Every
    verdict here comes from the real validators at request time."""
    import json
    from pathlib import Path

    spec = json.loads((Path(review.__file__).parent / "comparison.json").read_text())
    assert not any("validated" in scene for scene in spec["scenes"])
    for scene in review.comparison()["scenes"]:
        assert set(scene["validated"]) == {"status", "value", "readback", "reason"}


def test_the_headline_scene_is_the_manufactured_match():
    """A caller said C411, the recogniser returned a real policy, and exact
    match passed. Only the readback catches it."""
    headline = [s for s in review.comparison()["scenes"] if s.get("headline")]
    assert len(headline) == 1
    scene = headline[0]
    assert scene["naive"] == "KD4-1188"
    assert "C411" in " ".join(scene["partials"])
    assert scene["validated"]["status"] == UNCONFIRMED
    assert "Kilo Delta" in scene["validated"]["readback"]


def test_a_naive_reading_records_every_value_and_this_one_records_none():
    scenes = review.comparison()["scenes"]
    assert all(s["naive"] for s in scenes), "naive records something every time"
    assert not any(s["validated"]["status"] == ACCEPTED for s in scenes)


def test_rendering_the_comparison_writes_nothing(tmp_path, monkeypatch):
    """It builds ClaimRecords; none of them may touch the call log."""
    monkeypatch.setattr(review, "CALLS", tmp_path / "calls")
    review.comparison()
    assert not (tmp_path / "calls").exists()


# --- found by review ------------------------------------------------------

def test_a_failed_call_does_not_leak_a_concurrency_slot(monkeypatch):
    """The counter was incremented before the try that decrements it, and
    ClaimRecord loads policies.json and can raise. Two failures and /ws/call
    refuses every later connection until the server restarts."""
    import asyncio

    class Boom(Exception):
        pass

    def explode(**kw):
        raise Boom("policies.json unreadable")

    monkeypatch.setattr(review, "ClaimRecord", explode)

    class FakeBrowser:
        def __init__(self): self.sent = []
        async def accept(self): pass
        async def send_json(self, payload): self.sent.append(payload)
        async def close(self): pass
        async def receive(self): return {"type": "websocket.disconnect"}

    before = review._in_flight
    for _ in range(review.MAX_CALLS + 2):
        asyncio.run(review.call(FakeBrowser()))
        assert review._in_flight == before, "slot leaked"


def test_the_panel_takes_its_field_order_from_the_protocol():
    """Two copies of the schema order could drift; the tool enum and the Fields
    table would then disagree about what the six fields are."""
    import protocol

    assert review.FIELDS is protocol.FIELDS
