"""Every status for every field, plus the two confusion cases that matter most:
BX7-4402 vs BX7-4420, and Halloway vs Holloway.
"""

from datetime import date

import pytest

from validators import (
    ACCEPTED, REJECTED, UNCONFIRMED, LossType, load_policies,
    normalize_policy_number, spell_policy_number, validate_callback_phone,
    validate_claimant_name, validate_date_of_loss, validate_description,
    validate_loss_type, validate_policy_number,
)

POLICIES = load_policies()
HALLOWAY = next(p for p in POLICIES if p["policy_number"] == "BX7-4402")   # Marcus Halloway
RAGHUNATHAN = next(p for p in POLICIES if p["policy_number"] == "BX7-4420")  # Priya
TODAY = date(2026, 9, 2)  # pinned so the suite doesn't rot


# --- policy number -------------------------------------------------------

def test_policy_number_accepted_exact():
    v = validate_policy_number("BX7-4402", POLICIES)
    assert v.status == ACCEPTED and v.value == "BX7-4402"


@pytest.mark.parametrize("spoken", ["bx7 4402", "BX74402", " bx7-4402 ", "B X 7 4 4 0 2"])
def test_policy_number_accepts_spoken_spacing(spoken):
    """Transcripts arrive with arbitrary spacing; the identity still has to land."""
    assert validate_policy_number(spoken, POLICIES).value == "BX7-4402"


@pytest.mark.parametrize("spoken", ["hello", "BX7-440", "BX7-44021", "7BX-4402", ""])
def test_policy_number_rejected_bad_format(spoken):
    assert validate_policy_number(spoken, POLICIES).status == REJECTED


def test_policy_number_rejected_well_formed_but_not_on_file():
    v = validate_policy_number("ZZ9-0000", POLICIES)
    assert v.status == REJECTED and "not on file" in v.reason


# --- the confusable pair: this is the whole point ------------------------

def test_confusable_policy_numbers_resolve_to_different_holders():
    a = validate_policy_number("BX7-4402", POLICIES)
    b = validate_policy_number("BX7-4420", POLICIES)
    assert a.status == b.status == ACCEPTED
    assert a.value != b.value
    assert HALLOWAY["holder_name"] == "Marcus Halloway"
    assert RAGHUNATHAN["holder_name"] == "Priya Raghunathan"


def test_transposed_digits_never_fuzzy_match_onto_a_real_policy():
    """BX7-4402 and BX7-4420 differ by a transposition. A validator that fuzzy
    matched would hand the caller the wrong person's policy."""
    for typo, real in [("BX7-4024", "BX7-4402"), ("BX7-4422", "BX7-4420")]:
        v = validate_policy_number(typo, POLICIES)
        assert v.status == REJECTED, f"{typo} must not resolve to {real}"
        assert v.value is None


def test_policy_number_never_returns_unconfirmed():
    """By design there is no 'did you mean?' path on policy numbers."""
    probes = ["BX7-4402", "BX7-4420", "BX7-4403", "ZZ9-0000", "nonsense", ""]
    assert all(validate_policy_number(p, POLICIES).status != UNCONFIRMED for p in probes)


def test_readback_uses_nato_and_single_digits():
    assert spell_policy_number("BX7-4402") == "Bravo X-ray seven, four four zero two"
    assert "four four two zero" in spell_policy_number("BX7-4420")
    assert "Bravo X-ray seven" in validate_policy_number("BX7-4402", POLICIES).readback


@pytest.mark.parametrize("spoken,expected", [
    ("Bravo X-ray seven, four four zero two", "BX7-4402"),
    ("bravo x-ray seven four four two zero", "BX7-4420"),
    ("Kilo Delta four one one eight eight", "KD4-1188"),
    ("Tango Juliet two nine zero zero one", "TJ2-9001"),   # ASR drops a 't'
    ("Tango Juliett two, nine zero zero one", "TJ2-9001"),
])
def test_a_caller_answering_in_nato_is_understood(spoken, expected):
    """Once the agent starts asking for the phonetic alphabet, that is the form
    answers arrive in. The confusable pair must still separate: 'four four zero
    two' and 'four four two zero' are different policies."""
    v = validate_policy_number(spoken, POLICIES)
    assert v.status == ACCEPTED and v.value == expected


def test_nato_decoding_does_not_swallow_ordinary_words():
    for junk in ("hello", "my policy is somewhere", "BX7-440", ""):
        assert validate_policy_number(junk, POLICIES).status == REJECTED


def test_normalize_is_idempotent():
    assert normalize_policy_number(normalize_policy_number("bx7 4402")) == "BX7-4402"


# --- claimant name -------------------------------------------------------

@pytest.mark.parametrize("spoken", ["Marcus Halloway", "marcus halloway", "  Marcus   Halloway  "])
def test_name_accepted_exact(spoken):
    assert validate_claimant_name(spoken, HALLOWAY).status == ACCEPTED


@pytest.mark.parametrize("spoken", ["Marcus Holloway", "Marcus Haloway", "Mark Halloway"])
def test_name_fuzzy_is_unconfirmed_not_accepted(spoken):
    """A mishearing must ask for a spelling, never silently pass."""
    v = validate_claimant_name(spoken, HALLOWAY)
    assert v.status == UNCONFIRMED
    assert "spell" in v.readback.lower()


def test_similar_surname_of_a_different_real_holder_is_rejected():
    """Denise Holloway is a real holder on KD4-1188. Against Marcus Halloway's
    policy she must be rejected, not waved through as a near match."""
    assert validate_claimant_name("Denise Holloway", HALLOWAY).status == REJECTED


@pytest.mark.parametrize("spoken", ["Priya Raghunathan", "Samuel Okonkwo", "", "asdf"])
def test_name_rejected_when_nowhere_near(spoken):
    assert validate_claimant_name(spoken, HALLOWAY).status == REJECTED


def test_fuzzy_and_rejected_bands_do_not_overlap():
    near = "Priya Raghunathon"   # one letter off
    far = "Marcus Halloway"      # a different holder entirely
    assert validate_claimant_name(near, RAGHUNATHAN).status == UNCONFIRMED
    assert validate_claimant_name(far, RAGHUNATHAN).status == REJECTED


# --- date of loss --------------------------------------------------------

def test_date_accepted_within_policy_period():
    v = validate_date_of_loss("2026-04-02", RAGHUNATHAN, today=TODAY)
    assert v.status == ACCEPTED and v.value == "2026-04-02"


def test_date_readback_is_unambiguous():
    """No 03/04 ambiguity: weekday, day, month name, year."""
    v = validate_date_of_loss("2026-04-02", RAGHUNATHAN, today=TODAY)
    assert "Thursday, 2 April 2026" in v.readback


def test_date_rejected_in_future():
    v = validate_date_of_loss("2026-12-25", RAGHUNATHAN, today=TODAY)
    assert v.status == REJECTED and "future" in v.reason


def test_date_rejected_before_effective():
    v = validate_date_of_loss("2025-01-01", RAGHUNATHAN, today=TODAY)
    assert v.status == REJECTED and "outside policy period" in v.reason


def test_date_rejected_after_expiry():
    """Halloway's cover ended 2026-03-01; a loss after that is not covered
    even though it is safely in the past."""
    v = validate_date_of_loss("2026-06-01", HALLOWAY, today=TODAY)
    assert v.status == REJECTED and "outside policy period" in v.reason


def test_date_boundaries_are_inclusive():
    for d in (HALLOWAY["effective_date"], HALLOWAY["expiry_date"]):
        assert validate_date_of_loss(d, HALLOWAY, today=TODAY).status == ACCEPTED


@pytest.mark.parametrize("spoken", ["last Tuesday", "03/04/2025", "", "2026-13-01"])
def test_date_rejected_when_unparseable(spoken):
    assert validate_date_of_loss(spoken, RAGHUNATHAN, today=TODAY).status == REJECTED


@pytest.mark.parametrize("spoken", [
    "1st June", "June 1", "the first of June", "06-01", "0601", "06/01",
    "--06-01", "2026-06", "1 June", "June", "next Tuesday",
])
def test_a_date_without_a_year_never_gets_one_invented(spoken):
    """A year-less date is not a date. The agent has to go back and ask, so the
    validator must reject rather than fill in this year, the policy's year, or
    anything else."""
    v = validate_date_of_loss(spoken, RAGHUNATHAN, today=TODAY)
    assert v.status == REJECTED
    assert v.value is None

    # Nothing anywhere in the verdict may carry a guessed year.
    plausible = {str(TODAY.year), str(TODAY.year - 1),
                 RAGHUNATHAN["effective_date"][:4], RAGHUNATHAN["expiry_date"][:4]}
    assert not any(year in (v.value or "") for year in plausible)
    assert not any(year in v.readback for year in plausible)


def test_the_year_less_readback_asks_for_the_year(spoken="1st June"):
    """The readback is what the agent says, so it has to name what is missing."""
    v = validate_date_of_loss(spoken, RAGHUNATHAN, today=TODAY)
    assert "year" in v.readback.lower()


@pytest.mark.parametrize("spoken", ["20260601", "2026-W22-1", "2026-152"])
def test_only_yyyy_mm_dd_is_accepted(spoken):
    """date.fromisoformat also takes basic and week formats. 2026-W22-1 is 25
    May — a date nobody said out loud — so the prompt's format is enforced."""
    assert validate_date_of_loss(spoken, RAGHUNATHAN, today=TODAY).status == REJECTED


# --- callback phone ------------------------------------------------------

@pytest.mark.parametrize("spoken", ["5551234567", "(555) 123-4567", "1-555-123-4567"])
def test_phone_accepted_and_normalized(spoken):
    v = validate_callback_phone(spoken)
    assert v.status == ACCEPTED and v.value == "5551234567"


def test_phone_readback_is_digit_by_digit():
    v = validate_callback_phone("555-123-4567")
    assert "five five five, one two three, four five six seven" in v.readback


def test_phone_unconfirmed_without_area_code():
    v = validate_callback_phone("123-4567")
    assert v.status == UNCONFIRMED and "area code" in v.readback


@pytest.mark.parametrize("spoken", ["12345", "", "not a phone", "5551234567890"])
def test_phone_rejected(spoken):
    assert validate_callback_phone(spoken).status == REJECTED


# --- loss type -----------------------------------------------------------

@pytest.mark.parametrize("spoken,expected", [
    ("someone rear ended me at the lights", LossType.COLLISION),
    ("my car was stolen from the driveway", LossType.THEFT),
    ("there was a fire in the garage", LossType.FIRE),
    ("a pipe burst and flooded the kitchen", LossType.WATER),
    ("they keyed the whole side of it", LossType.VANDALISM),
    ("a rock cracked my windshield", LossType.GLASS),
])
def test_loss_type_accepted(spoken, expected):
    v = validate_loss_type(spoken)
    assert v.status == ACCEPTED and v.value is expected


def test_loss_type_unconfirmed_when_ambiguous():
    """'smashed my window' is vandalism or glass; the agent must ask."""
    v = validate_loss_type("someone smashed my window")
    assert v.status == UNCONFIRMED
    assert set(v.value) == {LossType.VANDALISM, LossType.GLASS}
    assert "?" in v.readback


@pytest.mark.parametrize("spoken", [
    "someone hit me", "someone hit my car", "a car hit me at the junction",
    "they rear ended me", "someone backed into me in the car park",
    "I got sideswiped on the motorway", "someone hit my vehicle",
])
def test_collision_covers_how_people_actually_say_it(spoken):
    """Plain phrasings a caller uses; none of these matched before."""
    v = validate_loss_type(spoken)
    assert v.status == ACCEPTED and v.value is LossType.COLLISION


@pytest.mark.parametrize("spoken,expected", [
    ("someone hit my window with a rock", {LossType.COLLISION, LossType.GLASS}),
    ("someone hit my car on purpose", {LossType.COLLISION, LossType.VANDALISM}),
    ("a car crashed into my house and it caught fire", {LossType.COLLISION, LossType.FIRE}),
])
def test_widened_collision_asks_instead_of_guessing(spoken, expected):
    """Broader collision terms overlap other categories. Overlap must ask:
    'hit my car on purpose' is a deliberate act, not a road accident."""
    v = validate_loss_type(spoken)
    assert v.status == UNCONFIRMED and set(v.value) == expected


@pytest.mark.parametrize("spoken", ["I want to change my address", "", "hello there"])
def test_loss_type_rejected_when_nothing_matches(spoken):
    assert validate_loss_type(spoken).status == REJECTED


# --- description ---------------------------------------------------------

@pytest.mark.parametrize("spoken", ["It was raining and I slid", "", "   ", "!!!"])
def test_description_always_accepted(spoken):
    assert validate_description(spoken).status == ACCEPTED


# --- shared contract -----------------------------------------------------

def test_every_verdict_has_a_readback_and_rejected_carries_no_value():
    verdicts = [
        validate_policy_number("BX7-4402", POLICIES), validate_policy_number("nope", POLICIES),
        validate_claimant_name("Marcus Halloway", HALLOWAY),
        validate_claimant_name("Marcus Holloway", HALLOWAY),
        validate_claimant_name("nobody", HALLOWAY),
        validate_date_of_loss("2026-04-02", RAGHUNATHAN, today=TODAY),
        validate_date_of_loss("2099-01-01", RAGHUNATHAN, today=TODAY),
        validate_callback_phone("5551234567"), validate_callback_phone("123-4567"),
        validate_callback_phone("x"), validate_loss_type("fire"),
        validate_loss_type("smashed my window"), validate_loss_type("hello"),
        validate_description("anything"),
    ]
    for v in verdicts:
        assert v.status in (ACCEPTED, UNCONFIRMED, REJECTED)
        assert v.readback.strip() and v.reason.strip()
        if v.status == REJECTED:
            assert v.value is None


def test_accepted_readbacks_are_never_questions():
    """An accepted readback is not spoken, so it must not be shaped like a
    question. Three of them were, and the agent duly asked them: it read an
    accepted phone number back and treated the caller's "Right." as a new
    answer."""
    from datetime import date as _date

    accepted = [
        validate_claimant_name("Priya Raghunathan", RAGHUNATHAN),
        validate_date_of_loss("2026-06-01", RAGHUNATHAN, today=_date(2026, 9, 2)),
        validate_callback_phone("555-123-4567"),
        validate_loss_type("there was a fire"),
        validate_description("anything"),
    ]
    for v in accepted:
        assert v.status == ACCEPTED
        assert not v.readback.rstrip().endswith("?"), v.readback


@pytest.mark.parametrize("spoken,gap", [("2026-03-04", 3), ("2026-03-08", 7),
                                        ("2025-02-25", 4)])
def test_a_near_miss_date_is_asked_about_not_refused(spoken, gap):
    """HALLOWAY's cover ran 2025-03-01 to 2026-03-01. A few days either side is
    more often a caller misremembering than an uncovered claim."""
    v = validate_date_of_loss(spoken, HALLOWAY, today=TODAY)
    assert v.status == UNCONFIRMED and v.confirmable
    assert f"{gap} day" in v.reason


@pytest.mark.parametrize("spoken", ["2026-03-09", "2026-06-01", "2025-02-21"])
def test_a_date_well_outside_cover_is_still_refused(spoken):
    v = validate_date_of_loss(spoken, HALLOWAY, today=TODAY)
    assert v.status == REJECTED and not v.confirmable


def test_a_future_date_is_never_confirmable():
    """A loss cannot have happened yet, so there is nothing to confirm."""
    v = validate_date_of_loss("2027-01-01", RAGHUNATHAN, today=TODAY)
    assert v.status == REJECTED and not v.confirmable
