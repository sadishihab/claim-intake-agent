"""End-to-end evidence capture: drive receive() with scripted events and read
back the call record a reviewer would open.

Same pattern as the socket harness, but against a fake WebSocket in-process, so
there is no port, no subprocess, and no audio device.
"""

import asyncio
import json
from datetime import date

import pytest

import agent
from agent import ToolQueue, receive
from intake import ClaimRecord
from validators import ACCEPTED, REJECTED, UNCONFIRMED, load_policies

POLICIES = load_policies()
TODAY = date(2026, 9, 2)


class FakeWS:
    """Async-iterates scripted server events; collects what the client sends."""

    def __init__(self, events):
        self.events = events
        self.sent = []

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def __aiter__(self):
        for e in self.events:
            yield json.dumps(e)
            await asyncio.sleep(0)  # let the client's sends interleave

    def tool_results(self):
        return [m for m in self.sent if m["type"] == "tool.result"]


class FakeSpeaker:
    def play(self, pcm): pass
    def flush(self): pass


def turn(call_id, field, value, said, item, status="completed"):
    """One caller utterance -> one record_field call -> end of reply."""
    return [
        {"type": "transcript.user", "item_id": item, "text": said},
        {"type": "reply.started", "reply_id": f"r_{call_id}"},
        {"type": "tool.call", "call_id": call_id, "name": "record_field",
         "arguments": {"field": field, "value": value}},
        {"type": "reply.done", "reply_id": f"r_{call_id}", "status": status},
    ]


# The call a reviewer needs to be able to reconstruct: the policy number was
# wrong twice before it stuck, and the surname needed spelling out.
SCRIPT = (
    [{"type": "session.ready", "session_id": "sess_test123"}]
    + turn("c1", "policy_number", "BX7-440", "my policy is B X 7 4 4 0", "item_1")
    + turn("c2", "policy_number", "ZZ9-0000", "sorry, zulu zulu nine zero zero zero zero", "item_2")
    + turn("c3", "policy_number", "BX7-4402", "it's bravo x-ray seven four four zero two", "item_3")
    + turn("c4", "claimant_name", "Marcus Holloway", "Marcus Holloway", "item_4")
    + turn("c5", "claimant_name", "Marcus Halloway", "H A L L O W A Y, Halloway", "item_5")
    + turn("c6", "loss_type", "someone hit me at the lights", "someone hit me at the lights", "item_6")
    + [{"type": "session.ended", "reason": "client"}]
)


@pytest.fixture
def call(tmp_path, monkeypatch):
    """Run the script through receive() and return (claim, ws, record dict)."""
    monkeypatch.setattr(agent, "EVENT_LOG", str(tmp_path / "events.jsonl"))
    claim = ClaimRecord(POLICIES, today=TODAY)
    ws = FakeWS(SCRIPT)
    asyncio.run(receive(ws, FakeSpeaker(), asyncio.Event(), ToolQueue(), claim))
    path = claim.write(tmp_path / "calls")
    return claim, ws, json.loads(path.read_text()), path


# --- the record on disk --------------------------------------------------

def test_record_is_written_under_the_session_id(call):
    _, _, record, path = call
    assert path.name == "sess_test123.json" and path.parent.name == "calls"
    assert record["session_id"] == "sess_test123"
    assert record["started_at"] and record["ended_at"]
    assert record["duration_seconds"] >= 0


def test_every_attempt_is_kept_not_just_accepted_ones(call):
    _, _, record, _ = call
    assert [a["field"] for a in record["attempts"]] == [
        "policy_number", "policy_number", "policy_number",
        "claimant_name", "claimant_name", "loss_type"]


def test_reviewer_can_see_policy_number_failed_twice_first(call):
    """The whole point: two rejections are visible, with what was said."""
    _, _, record, _ = call
    tries = [a for a in record["attempts"] if a["field"] == "policy_number"]
    assert [a["status"] for a in tries] == [REJECTED, REJECTED, ACCEPTED]
    assert [a["value"] for a in tries] == ["BX7-440", "ZZ9-0000", "BX7-4402"]
    assert "not a policy number format" in tries[0]["reason"]
    assert "not on file" in tries[1]["reason"]


def test_each_attempt_links_to_what_the_caller_said(call):
    _, _, record, _ = call
    heard = [(a["heard"]["item_id"], a["heard"]["text"]) for a in record["attempts"]]
    assert heard[0] == ("item_1", "my policy is B X 7 4 4 0")
    assert heard[2] == ("item_3", "it's bravo x-ray seven four four zero two")
    assert heard[4] == ("item_5", "H A L L O W A Y, Halloway")


def test_unconfirmed_is_recorded_but_not_accepted(call):
    _, _, record, _ = call
    names = [a for a in record["attempts"] if a["field"] == "claimant_name"]
    assert [a["status"] for a in names] == [UNCONFIRMED, ACCEPTED]
    assert "spell" in names[0]["readback"].lower()


def test_elapsed_seconds_never_go_backwards(call):
    _, _, record, _ = call
    times = [a["at_seconds"] for a in record["attempts"]]
    assert times == sorted(times) and times[0] >= 0


def test_final_accepted_values_only(call):
    _, _, record, _ = call
    assert record["accepted"] == {
        "policy_number": "BX7-4402",
        "claimant_name": "Marcus Halloway",
        "loss_type": "collision",   # the enum, serialized by value
    }


def test_readback_is_carried_verbatim_into_the_record(call):
    _, _, record, _ = call
    accepted_policy = [a for a in record["attempts"]
                       if a["field"] == "policy_number" and a["status"] == ACCEPTED][0]
    assert accepted_policy["readback"] == \
        "That's Bravo X-ray seven, four four zero two, correct?"


# --- the wire ------------------------------------------------------------

def test_a_tool_result_went_back_for_every_call(call):
    _, ws, record, _ = call
    results = ws.tool_results()
    assert [r["call_id"] for r in results] == ["c1", "c2", "c3", "c4", "c5", "c6"]
    assert [json.loads(r["result"])["status"] for r in results] == [
        REJECTED, REJECTED, ACCEPTED, UNCONFIRMED, ACCEPTED, ACCEPTED]


def test_nothing_is_written_when_the_session_never_started(tmp_path):
    """No session.ready means no session_id, so no file to name."""
    claim = ClaimRecord(POLICIES, today=TODAY)
    claim.record("policy_number", "BX7-4402")
    assert claim.write(tmp_path / "calls") is None
    assert not (tmp_path / "calls").exists()
