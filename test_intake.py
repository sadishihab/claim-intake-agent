"""Dispatch layer: routing, and the state the validators depend on."""

from datetime import date

import pytest

from intake import ClaimRecord
from validators import ACCEPTED, REJECTED, UNCONFIRMED, LossType, load_policies

POLICIES = load_policies()
TODAY = date(2026, 9, 2)


@pytest.fixture
def claim():
    return ClaimRecord(POLICIES, today=TODAY)


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

    other = ClaimRecord(POLICIES, today=TODAY)
    other.record("policy_number", "BX7-4402")
    assert other.record("date_of_loss", "2026-06-01").status == REJECTED


def test_stateless_fields_need_no_policy(claim):
    assert claim.record("callback_phone", "(555) 123-4567").status == ACCEPTED
    assert claim.record("loss_type", "there was a fire").status == ACCEPTED
    assert claim.record("description", "anything at all").status == ACCEPTED
    assert claim.fields["callback_phone"] == "5551234567"
    assert claim.fields["loss_type"] is LossType.FIRE


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
