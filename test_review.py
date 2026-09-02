"""Review panel: reading a call back off disk.

The HTTP layer is thin and exercised by hand; the logic worth pinning is how a
record is rebuilt from the append-only log, including a log that was cut off
mid-write.
"""

import json

import pytest

import review


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
