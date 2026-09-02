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
