"""Field validators for voice claim intake.

One pure function per field. Each returns a Verdict; nothing here knows about
audio, WebSockets, or the agent loop. `readback` is the phrase the agent should
say aloud to confirm the value with the caller.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from enum import Enum
from pathlib import Path
from typing import Any

ACCEPTED, UNCONFIRMED, REJECTED = "accepted", "unconfirmed", "rejected"

# Measured, not guessed: mishearings of these holder names score 0.86-0.97,
# genuinely different holders score <=0.60. 0.80 sits in the empty gap.
NAME_FUZZY_MIN = 0.80

POLICIES_PATH = Path(__file__).with_name("policies.json")

NATO = {
    "A": "Alpha", "B": "Bravo", "C": "Charlie", "D": "Delta", "E": "Echo",
    "F": "Foxtrot", "G": "Golf", "H": "Hotel", "I": "India", "J": "Juliett",
    "K": "Kilo", "L": "Lima", "M": "Mike", "N": "November", "O": "Oscar",
    "P": "Papa", "Q": "Quebec", "R": "Romeo", "S": "Sierra", "T": "Tango",
    "U": "Uniform", "V": "Victor", "W": "Whiskey", "X": "X-ray", "Y": "Yankee",
    "Z": "Zulu",
}
DIGITS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
}


# The readback direction already owns these tables; invert them so a caller who
# answers in NATO is understood. "alfa"/"juliet" are common ASR spellings.
SPOKEN = {word.lower().replace("-", ""): letter for letter, word in NATO.items()}
SPOKEN |= {word: digit for digit, word in DIGITS.items()}
SPOKEN |= {"alfa": "A", "juliet": "J", "oh": "0"}


@dataclass(frozen=True)
class Verdict:
    """status is one of ACCEPTED / UNCONFIRMED / REJECTED."""

    status: str
    value: Any
    reason: str
    readback: str


class LossType(str, Enum):
    COLLISION = "collision"
    THEFT = "theft"
    FIRE = "fire"
    WATER = "water"
    VANDALISM = "vandalism"
    GLASS = "glass"


# Ordered so the readback lists candidates deterministically.
LOSS_KEYWORDS: dict[LossType, tuple[str, ...]] = {
    LossType.COLLISION: ("collision", "crash", "crashed", "rear-end", "rear ended",
                         "fender bender", "hit another", "hit me", "hit my car",
                         "someone hit", "ran into", "backed into", "sideswipe",
                         "side swipe"),
    LossType.THEFT: ("theft", "stolen", "stole", "robbed", "took my", "burglar"),
    LossType.FIRE: ("fire", "burned", "burnt", "flames", "smoke"),
    LossType.WATER: ("flood", "flooded", "water damage", "leak", "burst pipe"),
    LossType.VANDALISM: ("vandal", "vandalised", "vandalized", "keyed", "graffiti",
                         "smashed", "on purpose"),
    LossType.GLASS: ("windshield", "windscreen", "window", "glass"),
}


def load_policies(path: Path | None = None) -> list[dict]:
    with open(path or POLICIES_PATH) as f:
        return json.load(f)["policies"]


def _norm_name(text: str) -> str:
    """Lowercase, drop punctuation, collapse runs of whitespace to one space."""
    cleaned = re.sub(r"[^a-z ]", "", (text or "").lower())
    return re.sub(r" +", " ", cleaned).strip()


def _spell_digits(digits: str) -> str:
    return " ".join(DIGITS[d] for d in digits if d in DIGITS)


# --- policy number -------------------------------------------------------

POLICY_RE = re.compile(r"^[A-Z]{2}\d-\d{4}$")


def normalize_policy_number(spoken: str) -> str:
    """'bx7 4402', 'BX74402', or 'Bravo X-ray seven four four zero two'
    -> 'BX7-4402'. Shape is checked separately.

    Phonetic words are resolved here rather than left to the model: once the
    agent starts asking callers for the NATO alphabet, that is the form the
    answers arrive in.
    """
    text = (spoken or "").lower().replace("-", " ")
    text = text.replace("x ray", "xray")          # "X-ray" arrives as two tokens
    text = re.sub(r"[a-z]+", lambda m: SPOKEN.get(m.group(0), m.group(0)), text)
    raw = re.sub(r"[^A-Za-z0-9]", "", text).upper()
    return f"{raw[:3]}-{raw[3:]}" if len(raw) == 7 else raw


def spell_policy_number(number: str) -> str:
    """NATO for letters, single digits for numbers: 'Bravo X-ray seven, four four zero two'."""
    head, _, tail = number.partition("-")
    letters = " ".join(NATO.get(c, c) if c.isalpha() else DIGITS.get(c, c) for c in head)
    return f"{letters}, {_spell_digits(tail)}" if tail else letters


def validate_policy_number(spoken: str, policies: list[dict] | None = None) -> Verdict:
    """Exact match only. A near miss is REJECTED, never UNCONFIRMED -- fuzzy
    matching here is exactly how BX7-4402 becomes BX7-4420."""
    policies = load_policies() if policies is None else policies
    number = normalize_policy_number(spoken)
    if not POLICY_RE.match(number):
        return Verdict(REJECTED, None, f"{spoken!r} is not a policy number format",
                       "That doesn't sound like a policy number. It's two letters, "
                       "a digit, then four more digits.")
    match = next((p for p in policies if p["policy_number"] == number), None)
    if match is None:
        return Verdict(REJECTED, None, f"{number} is not on file",
                       f"I don't have a policy {spell_policy_number(number)}. "
                       "Could you read it to me again?")
    return Verdict(ACCEPTED, number, "exact match on file",
                   f"That's {spell_policy_number(number)}, correct?")


# --- claimant name -------------------------------------------------------

def validate_claimant_name(spoken: str, policy: dict) -> Verdict:
    holder = policy["holder_name"]
    said, expected = _norm_name(spoken), _norm_name(holder)
    if said and said == expected:
        return Verdict(ACCEPTED, holder, "exact match on policy holder",
                       f"Thanks, {holder}.")
    ratio = SequenceMatcher(None, said, expected).ratio()
    if ratio >= NAME_FUZZY_MIN:
        return Verdict(UNCONFIRMED, spoken, f"close to {holder!r} (ratio {ratio:.2f})",
                       f"I have {holder} on this policy. Could you spell your "
                       "surname for me?")
    return Verdict(REJECTED, None, f"not close to {holder!r} (ratio {ratio:.2f})",
                   "That name doesn't match the policy holder. Are you calling "
                   "about someone else's policy?")


# --- date of loss --------------------------------------------------------

# Strict YYYY-MM-DD. date.fromisoformat also accepts basic format (20260601)
# and week dates (2026-W22-1, which is 25 May) — neither is what the prompt
# asks the model for, and a week date would land on a day nobody said.
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_date_of_loss(spoken: str, policy: dict, today: date | None = None) -> Verdict:
    """`spoken` is ISO-8601 (YYYY-MM-DD); the model normalizes before calling."""
    today = today or date.today()
    text = (spoken or "").strip()
    if not ISO_DATE_RE.match(text):
        # No year means no date. Never fill one in — the caller has to say it.
        return Verdict(REJECTED, None, f"{spoken!r} is not an ISO-8601 date",
                       "Sorry, what date did this happen? I need the day, the "
                       "month and the year.")
    try:
        loss = date.fromisoformat(text)
    except ValueError:
        return Verdict(REJECTED, None, f"{spoken!r} is not a real date",
                       "That date doesn't look right. What date did this happen?")
    spoken_date = f"{loss:%A}, {loss.day} {loss:%B} {loss.year}"
    start = date.fromisoformat(policy["effective_date"])
    end = date.fromisoformat(policy["expiry_date"])
    if loss > today:
        return Verdict(REJECTED, None, f"{loss} is in the future",
                       f"{spoken_date} is in the future. When did it actually happen?")
    if loss < start or loss > end:
        return Verdict(REJECTED, None, f"{loss} outside policy period {start}..{end}",
                       f"{spoken_date} falls outside this policy's cover, which ran "
                       f"from {start:%d %B %Y} to {end:%d %B %Y}.")
    return Verdict(ACCEPTED, loss.isoformat(), "within policy period",
                   f"So that's {spoken_date}, correct?")


# --- callback phone ------------------------------------------------------

def validate_callback_phone(spoken: str) -> Verdict:
    digits = re.sub(r"\D", "", spoken or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        grouped = ", ".join(_spell_digits(g) for g in (digits[:3], digits[3:6], digits[6:]))
        return Verdict(ACCEPTED, digits, "10-digit number",
                       f"Let me read that back: {grouped}. Is that right?")
    if len(digits) == 7:
        return Verdict(UNCONFIRMED, digits, "7 digits, area code missing",
                       f"I got {_spell_digits(digits)}, but I need the area code too.")
    return Verdict(REJECTED, None, f"{len(digits)} digits is not a phone number",
                   "That's not a number I can call back on. What's the best number?")


# --- loss type -----------------------------------------------------------

def validate_loss_type(spoken: str) -> Verdict:
    text = (spoken or "").lower()
    hits = [lt for lt, words in LOSS_KEYWORDS.items() if any(w in text for w in words)]
    if len(hits) == 1:
        return Verdict(ACCEPTED, hits[0], f"matched {hits[0].value}",
                       f"Logging this as {hits[0].value}. Correct?")
    if len(hits) > 1:
        options = " or ".join(h.value for h in hits)
        return Verdict(UNCONFIRMED, hits, f"ambiguous between {options}",
                       f"Just so I file it right -- would you call that {options}?")
    return Verdict(REJECTED, None, "no loss type matched",
                   "I didn't catch what kind of damage this was. Can you describe it?")


# --- description ---------------------------------------------------------

def validate_description(spoken: str) -> Verdict:
    """Free text. Always accepted -- there is nothing here to get wrong."""
    text = (spoken or "").strip()
    return Verdict(ACCEPTED, text, "free text, no validation",
                   "Got it, I've noted that down.")
