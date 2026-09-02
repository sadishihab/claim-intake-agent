"""Dispatch layer: routing, and the state the validators depend on."""

from datetime import date

import pytest

from intake import ClaimRecord
from validators import ACCEPTED, REJECTED, UNCONFIRMED, LossType, load_policies

POLICIES = load_policies()
TODAY = date(2026, 9, 2)


@pytest.fixture
def claim(tmp_path):
    """tmp_path matters: any test that calls start() writes a log."""
    return ClaimRecord(POLICIES, today=TODAY, directory=tmp_path / "calls")


def test_accepted_policy_resolves_the_record_and_is_stored(claim):
    v = claim.record("policy_number", "bx7 4402")
    assert v.status == ACCEPTED
    assert claim.fields["policy_number"] == "BX7-4402"
    assert claim.policy["holder_name"] == "Marcus Halloway"


def test_rejected_value_is_never_written(claim):
    assert claim.record("policy_number", "ZZ9-0000").status == REJECTED
    assert claim.fields == {} and claim.policy is None


def test_unconfirmed_value_is_never_written(claim):
    claim.record("policy_number", "BX7-4402")
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
    claim.record("policy_number", "BX7-4420")
    assert claim.record("date_of_loss", "2026-06-01").status == ACCEPTED

    other = ClaimRecord(POLICIES, today=TODAY, directory=claim.directory)
    other.record("policy_number", "BX7-4402")
    assert other.record("date_of_loss", "2026-06-01").status == REJECTED


def test_a_year_less_date_is_not_recorded(claim):
    """End of the chain: nothing lands in the record without a real year."""
    claim.record("policy_number", "BX7-4420")
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


# --- changing tack after a rejection -------------------------------------

def test_the_second_policy_rejection_asks_for_nato(claim):
    """The live call repeated the format three times while the caller read
    'M-A-C-K-K-K-D'. The second rejection should change the question."""
    first = claim.record("policy_number", "3841188")
    assert first.status == REJECTED
    assert "two letters" in first.readback          # unchanged on the first try

    second = claim.record("policy_number", "MACKKDK41138")
    assert second.status == REJECTED
    assert "Bravo for B" in second.readback
    assert "two letters, a digit" not in second.readback


def test_the_escalation_persists_and_then_the_nato_answer_lands(claim):
    claim.record("policy_number", "3841188")
    claim.record("policy_number", "D411")
    assert "Bravo for B" in claim.record("policy_number", "D42").readback
    ok = claim.record("policy_number", "Bravo X-ray seven four four zero two")
    assert ok.status == ACCEPTED and claim.fields["policy_number"] == "BX7-4402"


def test_escalation_is_scoped_to_policy_numbers(claim):
    """Other fields have their own recovery wording; NATO is for letters."""
    claim.record("policy_number", "BX7-4420")
    claim.record("callback_phone", "nonsense")
    second = claim.record("callback_phone", "also nonsense")
    assert second.status == REJECTED and "Bravo for B" not in second.readback


def test_unknown_field_is_rejected_not_crashed(claim):
    for bad in ("favourite_colour", None, ""):
        assert claim.record(bad, "x").status == REJECTED


def test_full_happy_path_collects_all_six(claim):
    for field, value in [
        ("policy_number", "BX7-4420"), ("claimant_name", "Priya Raghunathan"),
        ("date_of_loss", "2026-06-01"), ("callback_phone", "555-123-4567"),
        ("loss_type", "a pipe burst and flooded the kitchen"),
        ("description", "water everywhere"),
    ]:
        assert claim.record(field, value).status == ACCEPTED, field
    assert len(claim.fields) == 6
