"""Dispatch layer: routing, and the state the validators depend on."""

from datetime import date

import pytest

from intake import ClaimRecord
from validators import ACCEPTED, REJECTED, UNCONFIRMED, LossType, load_policies

POLICIES = load_policies()
TODAY = date(2026, 9, 2)


def accept_policy(claim, value):
    """Two calls now: a policy number is held until the caller agrees to the
    readback, so every test that needs a resolved policy goes through here."""
    claim.record("policy_number", value)
    return claim.record("policy_number", value, confirmed=True)


@pytest.fixture
def claim(tmp_path):
    """tmp_path matters: any test that calls start() writes a log."""
    return ClaimRecord(POLICIES, today=TODAY, directory=tmp_path / "calls")


def test_accepted_policy_resolves_the_record_and_is_stored(claim):
    v = accept_policy(claim, "bx7 4402")
    assert v.status == ACCEPTED
    assert claim.fields["policy_number"] == "BX7-4402"
    assert claim.policy["holder_name"] == "Marcus Halloway"


def test_rejected_value_is_never_written(claim):
    assert claim.record("policy_number", "ZZ9-0000").status == REJECTED
    assert claim.fields == {} and claim.policy is None


def test_unconfirmed_value_is_never_written(claim):
    accept_policy(claim, "BX7-4402")
    v = claim.record("claimant_name", "Marcus Holloway")   # one letter off
    assert v.status == UNCONFIRMED
    assert "claimant_name" not in claim.fields


def test_name_before_policy_is_rejected_with_a_useful_readback(claim):
    """The validator needs a policy record; without one, say so and re-ask."""
    v = claim.record("claimant_name", "Marcus Halloway")
    assert v.status == REJECTED
    assert "policy number" in v.readback.lower()


def test_date_before_policy_is_rejected(claim):
    assert claim.record("date_of_loss", "2026-04-02").status == REJECTED


def test_date_is_checked_against_the_resolved_policy(claim):
    """2026-06-01 is inside BX7-4420's cover but outside BX7-4402's."""
    accept_policy(claim, "BX7-4420")
    assert claim.record("date_of_loss", "2026-06-01").status == ACCEPTED

    other = ClaimRecord(POLICIES, today=TODAY, directory=claim.directory)
    accept_policy(other, "BX7-4402")
    assert other.record("date_of_loss", "2026-06-01").status == REJECTED


def test_a_year_less_date_is_not_recorded(claim):
    """End of the chain: nothing lands in the record without a real year."""
    accept_policy(claim, "BX7-4420")
    assert claim.record("date_of_loss", "1st June").status == REJECTED
    assert "date_of_loss" not in claim.fields


def test_stateless_fields_need_no_policy(claim):
    assert claim.record("callback_phone", "(555) 123-4567").status == ACCEPTED
    assert claim.record("loss_type", "there was a fire").status == ACCEPTED
    assert claim.record("description", "anything at all").status == ACCEPTED
    assert claim.fields["callback_phone"] == "5551234567"
    assert claim.fields["loss_type"] is LossType.FIRE


# --- the retry loop from a live call -------------------------------------

AMBIGUOUS = "someone smashed my window"


def test_an_identical_resubmission_after_unconfirmed_is_caught(claim):
    """The live bug: the caller said something unrelated and the agent sent the
    same loss_type again, retrying forever on stale data."""
    first = claim.record("loss_type", AMBIGUOUS)
    assert first.status == UNCONFIRMED

    again = claim.record("loss_type", AMBIGUOUS)
    assert again.status == REJECTED
    assert "already sent" in again.reason and "not this value again" in again.reason
    assert "loss_type" not in claim.fields


def test_the_repeat_re_asks_the_question_the_caller_still_owes(claim):
    """The readback is reused, because the caller never answered it."""
    first = claim.record("loss_type", AMBIGUOUS)
    again = claim.record("loss_type", AMBIGUOUS)
    assert again.readback == first.readback
    assert "vandalism or glass" in again.readback


@pytest.mark.parametrize("repeat", [
    AMBIGUOUS, AMBIGUOUS.upper(), f"  {AMBIGUOUS}  ", AMBIGUOUS.capitalize()])
def test_the_guard_is_not_fooled_by_case_or_padding(claim, repeat):
    claim.record("loss_type", AMBIGUOUS)
    assert claim.record("loss_type", repeat).status == REJECTED


def test_the_guard_still_holds_on_the_third_and_fourth_try(claim):
    """The guard's own rejection must not mask the unconfirmed behind it."""
    claim.record("loss_type", AMBIGUOUS)
    assert [claim.record("loss_type", AMBIGUOUS).status for _ in range(3)] \
        == [REJECTED, REJECTED, REJECTED]


def test_picking_an_offered_option_is_recorded_as_that_option(claim):
    """What the agent should do instead: send the choice, not the old phrase."""
    claim.record("loss_type", AMBIGUOUS)
    chosen = claim.record("loss_type", "vandalism")
    assert chosen.status == ACCEPTED
    assert claim.fields["loss_type"] is LossType.VANDALISM


def test_a_genuinely_new_value_is_never_blocked(claim):
    """The guard must only catch identical repeats, not any retry."""
    claim.record("loss_type", AMBIGUOUS)
    assert claim.record("loss_type", "there was a fire").status == ACCEPTED


def test_repeating_an_accepted_value_is_not_treated_as_a_loop(claim):
    """Only unconfirmed values are blocked; nothing else changes behaviour."""
    claim.record("description", "the boot will not shut")
    assert claim.record("description", "the boot will not shut").status == ACCEPTED


def test_the_repeat_is_visible_in_the_call_record(claim):
    """A reviewer should see the loop being caught, not a silent no-op."""
    claim.start("sess_x")
    claim.record("loss_type", AMBIGUOUS)
    claim.record("loss_type", AMBIGUOUS)
    statuses = [a["status"] for a in claim.attempts if a["field"] == "loss_type"]
    assert statuses == [UNCONFIRMED, REJECTED]


# --- dates just outside cover --------------------------------------------

def test_a_date_a_few_days_outside_cover_is_asked_about(claim):
    """More often a caller misremembering than an uncovered claim, so ask."""
    accept_policy(claim, "BX7-4402")               # cover ended 2026-03-01
    v = claim.record("date_of_loss", "2026-03-04")
    assert v.status == UNCONFIRMED and v.confirmable
    assert "just outside" in v.readback
    assert "date_of_loss" not in claim.fields


def test_the_caller_can_confirm_a_near_miss_date(claim):
    accept_policy(claim, "BX7-4402")
    claim.record("date_of_loss", "2026-03-04")
    v = claim.record("date_of_loss", "2026-03-04", confirmed=True)
    assert v.status == ACCEPTED and claim.fields["date_of_loss"] == "2026-03-04"
    assert not v.readback.rstrip().endswith("?")


def test_a_date_far_outside_cover_cannot_be_confirmed_away(claim):
    accept_policy(claim, "BX7-4402")
    assert claim.record("date_of_loss", "2026-06-01").status == REJECTED
    assert claim.record("date_of_loss", "2026-06-01", confirmed=True).status == REJECTED
    assert "date_of_loss" not in claim.fields


def test_confirmed_cannot_skip_the_near_miss_question(claim):
    accept_policy(claim, "BX7-4402")
    v = claim.record("date_of_loss", "2026-03-04", confirmed=True)
    assert v.status == UNCONFIRMED and "date_of_loss" not in claim.fields


# --- an accepted field is not overwritten ---------------------------------

def test_repeating_the_same_answer_changes_nothing(claim):
    """A deployed call recorded callback_phone three times for one answer, the
    last when the caller only said "Right."."""
    assert claim.record("callback_phone", "555-123-4567").status == ACCEPTED
    assert claim.record("callback_phone", "555-123-4567").status == ACCEPTED
    assert claim.fields["callback_phone"] == "5551234567"


def test_a_different_answer_does_not_overwrite_silently(claim):
    """This is the one that matters: a stray utterance became a phone number
    because nothing stopped a later value replacing an earlier one."""
    claim.record("callback_phone", "555-123-4567")
    v = claim.record("callback_phone", "555-999-0000")
    assert v.status == UNCONFIRMED
    assert claim.fields["callback_phone"] == "5551234567"      # kept
    assert "already have" in v.readback


def test_a_deliberate_correction_still_lands(claim):
    """Immutable by accident, changeable on purpose."""
    claim.record("callback_phone", "555-123-4567")
    claim.record("callback_phone", "555-999-0000")
    v = claim.record("callback_phone", "555-999-0000", confirmed=True)
    assert v.status == ACCEPTED and claim.fields["callback_phone"] == "5559990000"


def test_every_field_is_protected_not_just_the_phone(claim):
    accept_policy(claim, "BX7-4420")
    claim.record("date_of_loss", "2026-06-01")
    assert claim.record("date_of_loss", "2026-01-05").status == UNCONFIRMED
    assert claim.fields["date_of_loss"] == "2026-06-01"

    # description takes any text, so two different answers are both valid and
    # the guard is the only thing standing between them.
    claim.record("description", "the back bumper is crushed")
    assert claim.record("description", "it happened at work").status == UNCONFIRMED
    assert claim.fields["description"] == "the back bumper is crushed"


def test_confirming_does_not_unblock_an_ambiguous_choice(claim):
    """confirmed answers "is this right?", not "which of these?". Resending an
    ambiguous loss_type stays a loop even with the flag set."""
    claim.record("loss_type", "someone smashed my window")
    assert claim.record("loss_type", "someone smashed my window",
                        confirmed=True).status == REJECTED
    assert "loss_type" not in claim.fields


def test_a_confirmed_policy_number_stops_asking(claim):
    """Its readback must not stay a question, or it gets read back again."""
    v = accept_policy(claim, "BX7-4420")
    assert v.status == ACCEPTED and not v.readback.rstrip().endswith("?")


# --- confirmation, because a match is not evidence -----------------------

def test_a_matching_policy_number_is_held_until_the_caller_agrees(claim):
    """The live failure: "C411" was transcribed as a real policy and accepted
    on the match alone. A match no longer promotes anything by itself."""
    v = claim.record("policy_number", "KD4-1188")
    assert v.status == UNCONFIRMED
    assert "Kilo Delta four" in v.readback and v.readback.endswith("correct?")
    assert "policy_number" not in claim.fields


def test_the_caller_agreeing_promotes_it(claim):
    claim.record("policy_number", "KD4-1188")
    v = claim.record("policy_number", "KD4-1188", confirmed=True)
    assert v.status == ACCEPTED
    assert claim.fields["policy_number"] == "KD4-1188"


def test_confirmed_is_ignored_when_nothing_was_read_back(claim):
    """Otherwise the model could set the flag on the first call and skip the
    readback entirely, which is the whole protection."""
    v = claim.record("policy_number", "KD4-1188", confirmed=True)
    assert v.status == UNCONFIRMED and claim.fields == {}


def test_confirmed_is_bound_to_the_value_that_was_read_back(claim):
    """Agreeing to one policy number must not confirm a different one."""
    claim.record("policy_number", "KD4-1188")            # read back
    v = claim.record("policy_number", "BX7-4402", confirmed=True)
    assert v.status == UNCONFIRMED and claim.fields == {}


def test_the_retry_guard_does_not_block_the_confirmation(claim):
    """Resending a value is normally a loop; this is the one case where it is
    exactly right."""
    claim.record("policy_number", "KD4-1188")
    v = claim.record("policy_number", "KD4-1188", confirmed=True)
    assert v.status == ACCEPTED
    assert "already sent" not in v.reason


def test_a_rejected_number_is_still_rejected_not_held(claim):
    assert claim.record("policy_number", "ZZ9-0000").status == REJECTED


def test_other_fields_are_unaffected_by_confirmation(claim):
    """Only policy_number carries this cost; the rest accept as before."""
    accept_policy(claim, "BX7-4420")
    assert claim.record("callback_phone", "555-123-4567").status == ACCEPTED
    assert claim.record("claimant_name", "Priya Raghunathan").status == ACCEPTED
    assert claim.record("description", "anything").status == ACCEPTED


# --- changing tack after a rejection -------------------------------------

def test_the_second_policy_rejection_asks_for_nato(claim):
    """The live call repeated the format three times while the caller read
    'M-A-C-K-K-K-D'. The second rejection should change the question."""
    first = claim.record("policy_number", "3841188")
    assert first.status == REJECTED
    assert "two letters" in first.readback          # unchanged on the first try

    second = claim.record("policy_number", "MACKKDK41138")
    assert second.status == REJECTED
    assert "Say each letter as a word" in second.readback
    assert "two letters, a digit" not in second.readback


def test_the_phonetic_examples_cannot_be_mistaken_for_an_answer(claim):
    """The first version offered "Bravo for B, Kilo for K" while callers were
    reading BX7- and KD4- numbers, and the letters came back in the transcript.
    Examples must come from letters no policy uses."""
    claim.record("policy_number", "junk")
    ask = claim.record("policy_number", "more junk").readback

    on_file = {c for p in POLICIES for c in p["policy_number"] if c.isalpha()}
    from validators import NATO
    offered = {c for c in NATO if f"for {c}" in ask}
    assert offered, "the ask should still give examples"
    assert not (offered & on_file), f"{offered & on_file} appear in a real policy"


def test_the_phonetic_ask_is_made_once(claim):
    """It was re-read on a live call after the number had already come
    through."""
    claim.record("policy_number", "junk")
    assert "Say each letter as a word" in claim.record("policy_number", "junk2").readback
    third = claim.record("policy_number", "junk3")
    assert "Say each letter as a word" not in third.readback


def test_no_phonetic_ask_once_a_value_has_come_through(claim):
    """79.2s on the live call: the escalation was re-read after the value
    landed."""
    claim.record("policy_number", "junk")
    claim.record("policy_number", "KD4-1188")          # held pending confirmation
    later = claim.record("policy_number", "junk again")
    assert "Say each letter as a word" not in later.readback


def test_the_nato_answer_lands_after_the_ask(claim):
    claim.record("policy_number", "3841188")
    assert "Say each letter as a word" in claim.record("policy_number", "D411").readback
    nato = "Bravo X-ray seven four four zero two"
    assert claim.record("policy_number", nato).status == UNCONFIRMED
    ok = claim.record("policy_number", nato, confirmed=True)
    assert ok.status == ACCEPTED and claim.fields["policy_number"] == "BX7-4402"


def test_escalation_is_scoped_to_policy_numbers(claim):
    """Other fields have their own recovery wording; NATO is for letters."""
    accept_policy(claim, "BX7-4420")
    claim.record("callback_phone", "nonsense")
    second = claim.record("callback_phone", "also nonsense")
    assert second.status == REJECTED
    assert "Say each letter as a word" not in second.readback


def test_unknown_field_is_rejected_not_crashed(claim):
    for bad in ("favourite_colour", None, ""):
        assert claim.record(bad, "x").status == REJECTED


def test_full_happy_path_collects_all_six(claim):
    assert accept_policy(claim, "BX7-4420").status == ACCEPTED
    for field, value in [
        ("claimant_name", "Priya Raghunathan"),
        ("date_of_loss", "2026-06-01"), ("callback_phone", "555-123-4567"),
        ("loss_type", "a pipe burst and flooded the kitchen"),
        ("description", "water everywhere"),
    ]:
        assert claim.record(field, value).status == ACCEPTED, field
    assert len(claim.fields) == 6
