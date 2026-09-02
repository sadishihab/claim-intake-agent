# Submission — AssemblyAI Voice Agent Hackathon

## Title
The claim intake agent that refuses to guess

## Short description
A voice agent for insurance claim intake that cannot write a value into
the record unless a server-side validator approves it. When speech
recognition is wrong — and it is, reproducibly — the caller is asked,
not silently misfiled.

## Long description

### The problem

Speech recognition is good at sentences and much worse at the things a claim
form is made of. In the call logs in this repository, callers reading policy
numbers produced "My policy number is 3841188.",
"M-A-C-K-K-K-D-4-1-1-3-8.", "D411.", "KD41118." and "KD41282." — every one
rejected, none of them a policy on file. That is the visible half of the
problem, and it is the easy half: a value that is obviously wrong gets
rejected and the caller is asked again.

The dangerous half is a wrong value that looks right. The policy database here
holds BX7-4402, belonging to Marcus Halloway, and BX7-4420, belonging to Priya
Raghunathan — two real policies, two different people, one transposition
apart. It holds Denise Holloway, and "Dennis Holloway" is a mishearing rather
than a different claimant. A date arrives as "1st June" with no year, and
choosing a year silently decides whether the loss falls inside the policy
period. "Someone hit my car on purpose" is collision and vandalism at once,
and which one it is changes how the claim is handled.

A claim intake agent built the obvious way — transcribe, let the model extract
the fields, write them down — records every one of those without hesitating,
and the record it produces looks exactly like a correct one. There is no error
to surface later: the policy number matched, the name was close enough, the
date was in range. That is the failure this project is built around. Not that
the agent mishears, which it will, but that a mishearing can end up
indistinguishable from an answer.

### The approach

Nothing reaches the claim record unless a server-side validator approves it.
The agent's only way to write anything down is a `record_field` tool call,
handled in this process, and `validators.py` is one pure function per field
returning one of three verdicts. `accepted` means checked and unambiguous —
record it and move on. `rejected` means it cannot be right — explain and ask
again. `unconfirmed` means probably right and not certain enough to write, so
ask. The middle verdict is where the design actually lives: with only accept
and reject, every near miss forces a guess, and the near misses are exactly
where guessing costs the most.

What the agent says about a value is not left to the model either. Every
verdict carries a readback — the exact words to speak — and the system prompt
requires it verbatim. Policy numbers are read back in the NATO alphabet, which
is what makes BX7-4402 and BX7-4420 distinguishable out loud: "four four zero
two" against "four four two zero". The verdict's `reason` field is a
diagnostic and is never spoken aloud; "not close to 'Marcus Halloway' (ratio
0.60)" would disclose the policy holder's name to somebody who has just failed
to match it.

The rules that matter are enforced in code rather than asked for in the
prompt, because a model that had been told not to broke every one of them. An
accepted field cannot be overwritten by a later utterance — a deployed call
recorded a callback number three times for one answer, the last time when the
caller had only said "Right." — so a conflicting answer is held and the caller
has to agree to the change. Re-sending a value that already came back
unconfirmed is caught and turned back into the original question, because the
agent had been looping on a question the caller had never answered.
Confirming that a value is right and agreeing to replace a different one are
recorded as two separate consents, and neither substitutes for the other.
When the agent escalates to asking for the phonetic alphabet, the example
letters are derived at runtime from letters appearing in no policy on file —
currently Zulu and Quebec — because the first version offered "Bravo for B,
Kilo for K" to callers reading BX7- and KD4- numbers, and got those letters
back in the next transcript.

Everything else is deliberately small. `protocol.py` holds the Voice Agent
session and event loop and is shared by both clients; `agent.py` is a sound
card and Ctrl+C, `review.py` is a browser socket. Browser audio is proxied
through the server rather than minting a client token, so the API key never
reaches the page and validation cannot be bypassed from a client — there is a
test asserting that no credential appears in anything served. Every field
attempt is appended to `calls/<session_id>.jsonl` as it happens, so a reviewer
can see that a policy number was rejected twice before it stuck, and what the
caller actually said each time.

### What we measured

Three transcription levers were measured before any of them shipped —
`keyterms`, `transcription_prompt` and `max_accuracy` — each against a
baseline, over twelve clips: four policy numbers spoken naturally, spelled
out, and in NATO, rendered once through the agent's own text-to-speech so that
every condition heard identical audio. The baseline scored 12/12, which means
the test was at its ceiling and could not discriminate between anything.
Degrading the audio did not rescue it: additive noise at 15, 10 and 5 dB SNR
was still transcribed perfectly. `transcription_prompt` and `max_accuracy`
showed no effect anywhere and were not carried. That harness was a scratch
script and is not in the repository; what survives it is the commit message
and the decision it produced.

The fuzzy-match threshold for claimant names is 0.80, and that number was
measured rather than chosen. Mishearings of the holder names in
`policies.json` score between 0.86 and 0.97 — "Mark Halloway" against "Marcus
Halloway" is 0.86, "Dennis Holloway" against "Denise Holloway" is 0.93,
"Marcus Haloway" is 0.97 — while two genuinely different holders score 0.60
and below. 0.80 sits in an empty gap rather than on a slope. The seven-day
window for a date just outside the policy period is the opposite: a judgment,
not a measurement. A loss a few days outside cover is more often a caller
misremembering than an uncovered claim, but I have no data on where that stops
being true.

The browser client transcribed the agent's own voice as the caller on roughly
70% of turns, while the terminal client running the same prompt echoed zero
times across eleven sessions — which ruled out the microphone and the prompt
and pointed at the page. The page was forcing its AudioContexts to 24 kHz
while the device ran at 48 kHz; Firefox builds one media graph per sample rate
and feeds only the default graph's output to its echo canceller, so playback
was living somewhere the canceller could not hear it. Capture and playback now
share one context at the device's own rate, and resampling happens at the
edges. That was verified outside the browser: 48 kHz in yields exactly 24000
samples per second out, in 1200-sample chunks, with a 440 Hz tone still 440 Hz
afterwards.

Durability was tested by killing it. The agent was SIGKILLed mid-call to check
the append-only log: no summary was written, and the session header plus all
three attempts survived intact and readable, which is the whole reason the
trail is JSON Lines rather than one JSON object. The suite is 196 tests and
runs in under a second, and there is more test code than source code — 1,402
lines against 1,395. A fair share of those tests exist to pin a bug that a
live call found, which is why several are named after the thing that went
wrong rather than the thing that works.

### What went wrong, and what it taught us

A live call had just failed in a way that made the agent look useless. The
caller read their policy number over and over, and across two calls a few
minutes apart the transcripts came back "My policy number is 3841188.",
"M-A-C-K-K-K-D-4-1-1-3-8." and "D411." The validator was right to reject all
three, but the agent had nothing to offer except the format sentence again.
AssemblyAI's session config takes `input.keyterms`, a list of terms to bias
transcription toward, and the four policy numbers in `policies.json` were
sitting right there. I did not adopt it blind. I measured it against a
baseline and against the other two transcription levers, and I tested the risk
that seemed to matter: numbers deliberately not on file — BX7-4422, KD4-1187,
TJ2-9002 — all transcribed as themselves under keyterms, so it was not
dragging callers onto real policies. It also rescued a spelled TJ2-9002 that
the baseline rendered as "TJ too.", and TJ2-9002 is not on the keyterms list,
which I read as evidence that the bias was toward the shape of a policy number
rather than the specific strings. So keyterms shipped, derived from
`policies.json` so that it would track the database.

Three live sessions ran with keyterms enabled — the only three in the event
log. All three recorded KD4-1188, status accepted, reason "exact match on
file". Only one of them caught the recognizer in the act, and it is the
exhibit; the other two show a final value and nothing else, so they are not
evidence of the same thing. The partial transcripts for that one utterance,
in order, are "Yes.", "Yes, my policy number is", "Yes, my policy
number is C411.", "Yes, my policy number is KD4-1188." — and then the final,
"Yes, my policy number is KD4-1188." The recognizer's own first reading of the
number was C411. Its last was a policy on file, belonging to Denise Holloway.
Sixteen seconds into the call it was validated, accepted and written to the
claim, and the call ended on the next question. I had deliberately read a
number that is not on file, and said so at the time — but the audio is not
kept anywhere in this repository, so that part is my recollection rather than
something the log can prove. What the log does prove is the revision itself,
and that an hour and a half earlier the same speaker on the same microphone
had produced 3841188, MACKKDK41138 and D411, none of which is a policy number
at all.

What bothered me was not that a transcript was wrong. It was that nothing
downstream could possibly have caught it. Policy numbers are matched exactly
with no fuzzy fallback, precisely so that BX7-4402 can never resolve to
BX7-4420 — there is a test named for it. The readback spells numbers in NATO
so that the confusable pair separates out loud. Every attempt is logged with
the transcript it came from. All of that held, and all of it was useless,
because an exact match against `policies.json` was only ever evidence while
the recognizer knew nothing about `policies.json`. Feeding it the answer key
moved the match upstream of every guard I had written. A validator is handed a
value and a database and has no way to ask where the value came from, so a
wrong value that arrives already valid is invisible to it. Worse, the failure
suppressed the one thing that could still have caught it: accepted means the
agent moves on without reading the value back, so the more confident the wrong
answer looked, the less likely the caller was ever to hear it.

keyterms is gone. `protocol.py` carries a comment in the space where it used
to be, and two tests assert that it cannot come back — one checking the
session config, one checking that no transcription lever ships at all. The
rule went into `CLAUDE.md`: never bias the recognizer toward the answer key
when the recognizer's output is the evidence being validated. But removing
keyterms only removes the instance, so a policy number is no longer accepted
on a match alone either. It comes back unconfirmed carrying the NATO readback,
and only a `record_field` call with `confirmed: true` promotes it — enforced
in `ClaimRecord` rather than asked for in the prompt, and only against a value
the caller was actually read back, so a confirmed flag set on a first call
changes nothing. The part I keep coming back to is the test that cleared
keyterms in the first place. It used text-to-speech audio. Clean synthetic
speech gives the recognizer no reason to reach for a prior, so that test could
not have reproduced this failure under any conditions. I had already written
down, in the same commit, that TTS cannot produce human disfluency and that
its totals were therefore weak evidence — and then I let one of those totals
decide.

### What a reviewer sees

`/compare` is the three-minute version, and it is public on the deployed
instance. It renders one real call twice, side by side: what a system records
if it trusts the transcript, against what this one records, over six scenes —
the manufactured policy match, a spelled-out number, the twin of another
policy on file, a name one letter from the holder, a date with no year, and a
loss that is two categories at once. Every utterance on that page is verbatim
from `calls/` and `events.jsonl`, partials included. The naive column is the
value the model actually extracted; the validated column is computed at
request time by the real validators rather than written into the fixture, so
the page cannot drift from the code. Tests assert that no verdict is stored in
the fixture, and that rendering the page writes nothing to the call log.

The live panel at `/` is where a call is watched as it happens. It tails the
same `calls/*.jsonl` files the agent appends to rather than talking to the
agent, so it survives an agent restart and renders past calls through exactly
the same path as live ones. Every attempt appears in order with its status,
its reason, and the transcript it came from, and rejections and unconfirmed
attempts are never removed once a field is finally accepted — the interesting
part of a call is usually what happened before the value stuck.

The deployed instance is closed by default rather than by remembering. A
browser follows only the call it started, learning its session id from the
ready event and subscribing to that id alone. The global call listing, the
follow-newest stream and the interactive docs are local-only behind
`LOCAL_PANEL`, which is off unless it is set: forgetting the variable in
production hides things, it cannot expose them. Concurrent calls are capped
before any AssemblyAI session is opened, because every connection spends
money. `calls/` is gitignored and the deployed filesystem is ephemeral on
purpose, so nothing accumulates caller names, phone numbers and policy numbers
on a public host.

The repository itself is meant to be readable in one sitting: seven Python
files, about 1,400 lines, no framework beyond FastAPI, no build step, and a
raw WebSocket rather than the SDK. The commit history is the other artifact
worth opening — most commits are a bug found on a live call, what it actually
did, and why the fix lives where it does.

## Tags
AssemblyAI, Voice Agent API, Python, FastAPI, real-time, insurance,
speech-to-text

## Cover image
Screenshot of /compare — RECORDED / KD4-1188 beside UNCONFIRMED /
nothing recorded

## Links
- Repo: https://github.com/sadishihab/claim-intake-agent
- Live: https://web-production-74b77.up.railway.app
