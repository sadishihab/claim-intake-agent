"""Seed calls/ with demo data for the review panel.

Writes two calls: one finished, one left in progress so the panel shows a live
call. Both open with the arc the panel exists to demonstrate — a policy number
that fails on format, then fails as not on file, then finally sticks.

The two calls deliberately land on BX7-4402 and BX7-4420, the confusable pair,
so the readbacks differ audibly: "four four zero two" against "four four two zero".

    ./venv/bin/python seed_demo.py
    ./venv/bin/python seed_demo.py --drip 2   # pace the live call for a recording
"""

import argparse
import time
from datetime import date
from pathlib import Path

from intake import ClaimRecord
from validators import load_policies

CALLS = Path(__file__).parent / "calls"
TODAY = date(2026, 9, 2)

# (what the caller said, transcript item id, field, value the model extracted)
FAILED_POLICY_ARC = [
    ("hi, my policy is B X 7 4 4 0", "i1", "policy_number", "BX7-440"),
    ("sorry, let me start again, zulu zulu nine zero zero zero zero", "i2",
     "policy_number", "ZZ9-0000"),
]

DONE = FAILED_POLICY_ARC + [
    ("it's bravo x-ray seven, four four zero two", "i3", "policy_number", "BX7-4402"),
    ("Marcus Holloway", "i4", "claimant_name", "Marcus Holloway"),
    ("H A L L O W A Y, Halloway", "i5", "claimant_name", "Marcus Halloway"),
    ("it was the second of April", "i6", "date_of_loss", "2026-04-02"),
    ("no sorry, January the fifth", "i7", "date_of_loss", "2026-01-05"),
    ("five five five, one two three, four five six seven", "i8",
     "callback_phone", "555-123-4567"),
    ("someone hit me at the lights", "i9", "loss_type", "someone hit me at the lights"),
    ("the back bumper is crushed and the boot won't shut", "i10", "description",
     "back bumper crushed, boot will not shut"),
]

LIVE = FAILED_POLICY_ARC + [
    ("bravo x-ray seven, four four two zero", "i3", "policy_number", "BX7-4420"),
    ("Priya Raghunathon", "i4", "claimant_name", "Priya Raghunathon"),
    ("R A G H U N A T H A N", "i5", "claimant_name", "Priya Raghunathan"),
    ("someone smashed my window", "i6", "loss_type", "someone smashed my window"),
]


def play(session_id, turns, finish, gap):
    for suffix in (".jsonl", ".json"):        # only ever our own two sessions
        (CALLS / f"{session_id}{suffix}").unlink(missing_ok=True)
    claim = ClaimRecord(load_policies(), today=TODAY, directory=CALLS)
    claim.start(session_id)
    for said, item_id, field, value in turns:
        claim.note_user_transcript(item_id, said)
        verdict = claim.record(field, value)
        print(f"  {field:<15} {value[:28]:<30} {verdict.status}")
        time.sleep(gap)
    if finish:
        claim.write()
    return claim


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--drip", type=float, default=0.4, metavar="SECONDS",
                    help="pause between turns; raise it to watch the panel update live")
    args = ap.parse_args()

    print("finished call (sess_demo_done):")
    play("sess_demo_done", DONE, finish=True, gap=min(args.drip, 0.4))
    print("\nin-progress call (sess_demo_live), no summary written:")
    play("sess_demo_live", LIVE, finish=False, gap=args.drip)
    print(f"\nwrote {CALLS}/  —  ./venv/bin/uvicorn review:app --port 8000")


if __name__ == "__main__":
    main()
