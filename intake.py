"""Per-call claim state: turn one record_field tool call into a Verdict, and
keep the evidence trail for the whole call.

Attempts are appended to calls/<session_id>.jsonl as they happen, so a crash or
a dropped socket keeps everything up to that point. The complete record is
written to calls/<session_id>.json on clean shutdown.

validators.py is stateless — one pure function per field. Two of those functions
need the caller's policy record, which only exists once the policy number has
been accepted. This module holds that state, the accepted values, and every
attempt in order so a reviewer can see what was said before a field stuck.
"""

import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from validators import (ACCEPTED, REJECTED, UNCONFIRMED, Verdict, load_policies,
                        validate_callback_phone, validate_claimant_name,
                        validate_date_of_loss, validate_description,
                        validate_loss_type, validate_policy_number)

NEEDS_POLICY = ("claimant_name", "date_of_loss")

# Fields where an exact match is not on its own evidence of correctness, so the
# caller has to hear the value back and agree before it is written. See the
# recognizer/answer-key note in CLAUDE.md.
CONFIRM_FIELDS = ("policy_number",)

# Said on the second rejection of a policy number. Repeating the format a third
# time is what the agent did on a live call while the caller read "M-A-C-K-K-K-D";
# the phonetic alphabet is the direction the readback already speaks fluently.
NATO_RETRY = ("Let's try that a different way. Give me the two letters as words "
              "-- Bravo for B, Kilo for K -- then the digits one at a time.")


def _plain(value):
    """LossType is a str Enum; store its value so the JSON reads 'collision'."""
    return value.value if isinstance(value, Enum) else value


class ClaimRecord:
    """Accepted field values for one call, the policy they belong to, and the
    full attempt history including rejections."""

    def __init__(self, policies=None, today=None, directory="calls"):
        self.policies = load_policies() if policies is None else policies
        self.directory = Path(directory)
        self.today = today       # pinned in tests; None means date.today()
        self.policy = None       # resolved when policy_number is accepted
        self.fields = {}         # accepted values only — never a rejected one
        self.attempts = []       # every record_field call, in order
        self.heard = None        # most recent transcript.user
        # (field, value) pairs we asked the caller to confirm. Resending one of
        # these is the answer to a question, not a retry loop.
        self._confirm_holds = set()
        self.session_id = None
        self.started_at = None   # wall clock, for the reader
        self._t0 = None          # monotonic, for durations

    # --- fed by the protocol layer ---------------------------------------

    def start(self, session_id):
        self.session_id = session_id
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._t0 = time.monotonic()
        self._append({"type": "session.start", "session_id": session_id,
                      "started_at": self.started_at})

    def note_user_transcript(self, item_id, text):
        """Latest final caller utterance, attached to whatever they say next."""
        self.heard = {"item_id": item_id, "text": text}

    def elapsed(self):
        """Seconds since session.ready. Monotonic, so a clock step can't make
        this go backwards mid-call."""
        return 0.0 if self._t0 is None else round(time.monotonic() - self._t0, 3)

    # --- validation ------------------------------------------------------

    def record(self, field, value, confirmed=False):
        # A confirming resubmission is the one case where repeating a value is
        # the right thing to do, so it skips the retry guard. Only a value we
        # actually asked about counts: an unconfirmed that meant "choose one of
        # these" is still a loop when the same words come back.
        verdict = None
        if not (confirmed and self._hold_key(field, value) in self._confirm_holds):
            verdict = self._resent_unconfirmed(field, value)
        verdict = verdict or self._validate(field, value)
        verdict = self._already_recorded(field, value, verdict, confirmed)
        verdict = self._needs_confirmation(field, value, verdict, confirmed)
        verdict = self._escalate(field, verdict)
        self.attempts.append({
            "at_seconds": self.elapsed(),
            "field": field,
            "value": value,
            "status": verdict.status,
            "reason": verdict.reason,
            "readback": verdict.readback,
            # The normalized value, so the log alone is enough to render a call:
            # loss_type arrives as "someone hit me" and lands as "collision".
            "accepted_value": _plain(verdict.value) if verdict.status == ACCEPTED else None,
            "heard": self.heard,
        })
        self._append({"type": "attempt", **self.attempts[-1]})
        if verdict.status == ACCEPTED:
            self.fields[field] = verdict.value
            if field == "policy_number":
                self.policy = next(p for p in self.policies
                                   if p["policy_number"] == verdict.value)
        return verdict

    def _resent_unconfirmed(self, field, value):
        """Catch the agent re-sending a value that already came back unconfirmed.

        Unconfirmed means the caller was asked a question and has not answered
        it. Sending the same words again cannot change the answer, so the agent
        sits in a retry loop on stale data. Returns the original readback — the
        question the caller still owes an answer to — with a status that stops
        the retry. Returns None when this is not a repeat.
        """
        said = (value or "").strip().casefold()
        # Scan every prior attempt, not just the latest: once this exact value
        # has come back unconfirmed it can never become accepted by resending,
        # and stopping at the most recent match would let the guard's own
        # rejection mask the unconfirmed behind it on the third try.
        asked = next((a for a in self.attempts
                      if a["field"] == field
                      and (a["value"] or "").strip().casefold() == said
                      and a["status"] == UNCONFIRMED), None)
        if asked is None:
            return None
        return Verdict(
            REJECTED, None,
            f"{field} was already sent as {value!r} and came back unconfirmed; "
            "ask the caller to choose and send their answer, not this value again",
            asked["readback"])

    @staticmethod
    def _hold_key(field, value):
        return field, (value or "").strip().casefold()

    def _already_recorded(self, field, value, verdict, confirmed):
        """An accepted field is not overwritten by a later utterance.

        A deployed call recorded callback_phone three times for one answer, the
        last when the caller only said "Right." Nothing stopped a later value
        replacing an earlier one, on any field, which is how a stray utterance
        becomes a claimant's phone number.

        The same answer again is idempotent. A different answer is held: two
        conflicting values means guessing, so the caller is asked and the
        correction only lands with confirmed=True.
        """
        if verdict.status != ACCEPTED or field not in self.fields:
            return verdict
        if _plain(verdict.value) == _plain(self.fields[field]):
            return verdict                      # same answer, nothing changes
        if confirmed:
            return verdict                      # the caller corrected it on purpose
        self._confirm_holds.add(self._hold_key(field, value))
        return replace(verdict, status=UNCONFIRMED,
                       reason=f"{field} is already recorded as "
                              f"{_plain(self.fields[field])!r}; this is a different answer",
                       readback="I already have an answer for that one. "
                                "Did you want to change it?")

    def _needs_confirmation(self, field, value, verdict, confirmed):
        """Hold an otherwise-accepted value until the caller agrees to it.

        An exact match against policies.json used to be strong evidence, because
        the recognizer knew nothing about the policy list. That stopped being
        true the moment transcription was biased toward the list, and a caller
        saying "C411" was handed a real policy. The readback is independent
        evidence in a way the match itself no longer is.

        `confirmed` only counts against a value we actually read back, otherwise
        the model could set the flag on the first call and skip the whole thing.
        """
        if field not in CONFIRM_FIELDS or verdict.status != ACCEPTED:
            return verdict
        said = (value or "").strip().casefold()
        read_back = any(a["field"] == field and a["status"] == UNCONFIRMED
                        and (a["value"] or "").strip().casefold() == said
                        for a in self.attempts)
        if confirmed and read_back:
            # Accepted readbacks are never spoken, so this must not stay a
            # question the model is tempted to ask again.
            return replace(verdict, readback="Thanks, I have that.")
        self._confirm_holds.add(self._hold_key(field, value))
        return replace(verdict, status=UNCONFIRMED,
                       reason=verdict.reason + "; unconfirmed until the caller "
                              "agrees to the readback")

    def _escalate(self, field, verdict):
        """Change tack on a second rejection instead of repeating a sentence the
        caller has already failed to act on.

        Only the readback changes: it is the one thing the agent speaks verbatim,
        so putting the new question there is more reliable than asking the prompt
        to remember to switch.
        """
        if field != "policy_number" or verdict.status != REJECTED:
            return verdict
        already = sum(1 for a in self.attempts
                      if a["field"] == field and a["status"] == REJECTED)
        return verdict if already < 1 else replace(verdict, readback=NATO_RETRY)

    def _validate(self, field, value):
        if field == "policy_number":
            return validate_policy_number(value, self.policies)
        if field == "callback_phone":
            return validate_callback_phone(value)
        if field == "loss_type":
            return validate_loss_type(value)
        if field == "description":
            return validate_description(value)
        if field in NEEDS_POLICY:
            if self.policy is None:
                # Name what failed and what to ask for next: the agent reads
                # this back rather than guessing its way around the gap.
                return Verdict(REJECTED, None,
                               f"cannot check {field} before a policy number is on file",
                               "Before I take that, could I have your policy number?")
            if field == "claimant_name":
                return validate_claimant_name(value, self.policy)
            return validate_date_of_loss(value, self.policy, today=self.today)
        return Verdict(REJECTED, None, f"unknown field {field!r}",
                       "Sorry, I didn't catch which detail that was.")

    # --- the call record -------------------------------------------------

    def as_dict(self):
        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": self.elapsed(),
            "attempts": self.attempts,
            "accepted": {k: _plain(v) for k, v in self.fields.items()},
        }

    def log_path(self):
        """Append-only trail, written as the call happens."""
        return self.directory / f"{self.session_id}.jsonl"

    def record_path(self):
        """Complete record, written once on clean shutdown."""
        return self.directory / f"{self.session_id}.json"

    def _append(self, obj):
        """Append one line to the call log.

        JSON Lines, not JSON: a crash mid-call leaves every completed line
        readable, where a half-written JSON object would parse as nothing. Never
        raises — failing to log must not end a live call.
        """
        if not self.session_id:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with open(self.log_path(), "a") as f:
                f.write(json.dumps(obj) + "\n")
        except OSError as exc:
            print(f"[warn] could not append to call log: {exc}")

    def write(self):
        """Write the complete record. Returns the path, or None if the session
        never reached session.ready and so has no id to name the file by."""
        if not self.session_id:
            return None
        path = self.record_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.as_dict(), indent=2))
        return path
