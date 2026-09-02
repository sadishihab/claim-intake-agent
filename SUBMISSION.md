# Submission: AssemblyAI Voice Agent Hackathon

## Title
The claim intake agent that refuses to guess

## Short description
A voice agent for insurance claim intake. It cannot write a value into the
record unless a server-side validator approves it. Speech recognition gets
these fields wrong reproducibly, and when it does the caller is asked about
it instead of the wrong value being filed.

## Long description

### The problem

Speech recognition handles sentences well. It does much worse on the things a
claim form is made of. In the call logs in this repository, callers reading
policy numbers produced "My policy number is 3841188.",
"M-A-C-K-K-K-D-4-1-1-3-8.", "D411.", "KD41118." and "KD41282." All of those
were rejected. None of them is a policy on file. That failure mode is the easy
one, because the value is obviously wrong and the agent just asks again.

The harder case is a wrong value that looks right. The policy database has
BX7-4402, belonging to Marcus Halloway, and BX7-4420, belonging to Priya
Raghunathan. Those two differ by one transposition and they are different
people. It also has Denise Holloway, and "Dennis Holloway" is a mishearing of
that name rather than a second claimant. A date can arrive as "1st June" with
no year. Filling a year in decides whether the loss falls inside the policy
period, and the caller never hears that decision get made. "Someone hit my car
on purpose" matches collision and vandalism at the same time, and which one it
is changes how the claim gets handled.

Build the agent the obvious way and every one of those gets written down.
Transcribe, let the model pull the fields out, save them. The record that comes
out looks like a correct record. Nothing surfaces later, because the policy
number matched, the name was close enough and the date was in range. The agent
will mishear things, and no amount of design stops that. This project is built
around what happens next, when a mishearing ends up indistinguishable from an
answer.

### The approach

Nothing reaches the claim record unless a server-side validator approves it.
The agent's only way to write anything down is a `record_field` tool call, and
that call is handled in this process. `validators.py` holds one pure function
per field, and each returns one of three verdicts. `accepted` means checked and
unambiguous, so record it and move on. `rejected` means it cannot be right, so
explain the problem and ask again. `unconfirmed` means probably right and not
certain enough to write down, so ask.

The unconfirmed verdict is where most of the design went. A system with only
accept and reject has to guess on every near miss, and the near misses are
where guessing costs the most.

The wording the agent uses is fixed in code too. Every verdict carries a
readback, which is the exact phrasing to speak, and the system prompt requires
it verbatim. Policy numbers are read back in the NATO alphabet, which is what
makes BX7-4402 and BX7-4420 tell apart out loud. "Four four zero two" against
"four four two zero". The verdict's `reason` field is a diagnostic and never
gets spoken. "Not close to 'Marcus Halloway' (ratio 0.60)" would hand the
policy holder's name to somebody who has just failed to match it.

The rules that matter are enforced in code rather than requested in the prompt.
Every one of them was broken by a model that had been told not to break it. An
accepted field cannot be overwritten by a later utterance, so a conflicting
answer gets held and the caller has to agree to the change. That rule came out
of a deployed call which recorded a callback number three times for one answer,
the last time when the caller had only said "Right." Re-sending a value that
already came back unconfirmed is caught and turned back into the original
question, because the agent had been looping on a question the caller never
answered. Confirming that a value is right and agreeing to replace a different
value are stored as two separate consents, and neither one substitutes for the
other. When the agent escalates to asking for the phonetic alphabet, the
example letters get derived at runtime from letters that appear in no policy on
file, currently Zulu and Quebec. The first version offered "Bravo for B, Kilo
for K" to callers reading BX7- and KD4- numbers, and got those letters back in
the next transcript.

The rest is small on purpose. `protocol.py` holds the Voice Agent session and
the event loop, and both clients share it. `agent.py` is a sound card and
Ctrl+C. `review.py` is a browser socket. Browser audio gets proxied through the
server instead of minting a client token, so the API key never reaches the page
and a client cannot bypass validation. A test asserts that no credential
appears in anything served. Every field attempt is appended to
`calls/<session_id>.jsonl` as it happens, so a reviewer can see that a policy
number was rejected twice before it stuck, and what the caller said each time.

### What we measured

Three transcription levers got measured before any of them shipped. `keyterms`,
`transcription_prompt` and `max_accuracy`, each against a baseline, over twelve
clips. The clips were four policy numbers spoken naturally, spelled out and in
NATO, rendered once through the agent's own text-to-speech so that every
condition heard identical audio. The baseline scored 12 out of 12, which put
the test at its ceiling where it could not discriminate between anything.
Adding noise did not rescue it. At 15, 10 and 5 dB SNR the audio was still
transcribed perfectly. `transcription_prompt` and `max_accuracy` showed no
effect anywhere and were not carried. That harness was a scratch script and is
not in the repository. What is left of it is the commit message and the
decision it produced.

The fuzzy-match threshold for claimant names is 0.80, and that number came out
of measurement. Mishearings of the holder names in `policies.json` score
between 0.86 and 0.97. "Mark Halloway" against "Marcus Halloway" is 0.86,
"Dennis Holloway" against "Denise Holloway" is 0.93, "Marcus Haloway" is 0.97.
Two genuinely different holders score 0.60 and below. So 0.80 sits in an empty
gap rather than on a slope.

The seven-day window for a date just outside the policy period is a judgment
call. A loss a few days outside cover is more often a caller misremembering
than an uncovered claim, and I have no data on where that stops being true.

The browser client transcribed the agent's own voice as the caller on roughly
70% of turns. The terminal client running the same prompt echoed zero times
across eleven sessions, which ruled out the microphone and the prompt and
pointed at the page. The page was forcing its AudioContexts to 24 kHz while the
device ran at 48 kHz. Firefox builds one media graph per sample rate and feeds
only the default graph's output to its echo canceller, so playback was living
somewhere the canceller could not hear it. Capture and playback now share one
context at the device's own rate, and the resampling happens at the edges. That
got verified outside the browser. 48 kHz in yields exactly 24000 samples per
second out, in 1200-sample chunks, and a 440 Hz tone is still 440 Hz
afterwards.

Durability got tested by killing the process. The agent was SIGKILLed mid-call
to check the append-only log. No summary was written, and the session header
plus all three attempts survived intact and readable. That is why the trail is
JSON Lines instead of one JSON object.

The suite is 196 tests and runs in under a second. There is slightly more test
code than source code, 1,402 lines against 1,395. A lot of those tests exist to
pin a bug that a live call found, which is why several are named after the
thing that went wrong.

### What went wrong, and what it taught us

A live call had just failed in a way that made the agent look useless. The
caller read their policy number over and over, and across two calls a few
minutes apart the transcripts came back "My policy number is 3841188.",
"M-A-C-K-K-K-D-4-1-1-3-8." and "D411." The validator was right to reject all
three. The agent had nothing to offer except the format sentence again.
AssemblyAI's session config takes `input.keyterms`, a list of terms to bias
transcription toward, and the four policy numbers in `policies.json` were
sitting right there. I did not adopt it blind. I measured it against a baseline
and against the other two transcription levers, and I tested the risk I thought
mattered. Numbers deliberately not on file, BX7-4422 and KD4-1187 and
TJ2-9002, all transcribed as themselves under keyterms, so it was not dragging
callers onto real policies. It also rescued a spelled TJ2-9002 that the
baseline rendered as "TJ too.", and TJ2-9002 is not on the keyterms list, which
I read as the bias being toward the shape of a policy number rather than the
specific strings. So it shipped, derived from `policies.json` so that it would
track the database.

Three live sessions ran with keyterms enabled, and those are the only three in
the event log. All three recorded KD4-1188 with status accepted and reason
"exact match on file". One of them caught the recognizer in the act, and that
one is the exhibit. The other two show a final value and nothing else, so they
do not demonstrate the same thing. The partials for that utterance, in order,
are "Yes.", "Yes, my policy number is", "Yes, my policy number is C411.", "Yes,
my policy number is KD4-1188." The final transcript reads "Yes, my policy
number is KD4-1188." So the recognizer's first reading of the number was C411
and its last was a policy on file, belonging to Denise Holloway. Sixteen
seconds into the call it was validated, accepted and written to the claim, and
the call ended on the next question. I had deliberately read a number that is
not on file and said so at the time. The audio is not kept anywhere in this
repository, so that part is my recollection and the log cannot prove it. What
the log does prove is the revision, and that an hour and a half earlier the
same speaker on the same microphone produced 3841188, MACKKDK41138 and D411,
none of which is a policy number at all.

A wrong transcript is ordinary. The part that bothered me is that nothing
downstream could have caught this one. Policy numbers are matched exactly with
no fuzzy fallback, so that BX7-4402 can never resolve to BX7-4420, and there is
a test named for it. The readback spells numbers in NATO so the confusable pair
separates out loud. Every attempt gets logged with the transcript it came from.
All of that held. All of it was useless. An exact match against
`policies.json` was only ever evidence while the recognizer knew nothing about
`policies.json`. Feeding it the answer key moved the match upstream of every
guard I had written. A validator gets handed a value and a database, and it has
no way to ask where the value came from, so a wrong value that arrives already
valid is invisible to it. The failure also suppressed the one thing that could
still have caught it. `accepted` means the agent moves on without reading the
value back, so the more confident the wrong answer looked, the less likely the
caller was to hear it.

keyterms is gone. `protocol.py` carries a comment in the space where it used to
be, and two tests assert that it cannot come back. One checks the session
config and one checks that no transcription lever ships at all. The rule went
into `CLAUDE.md`. Never bias the recognizer toward the answer key when the
recognizer's output is the evidence being validated. Removing keyterms only
removes the one instance, so a policy number is no longer accepted on a match
alone either. It comes back unconfirmed carrying the NATO readback, and only a
`record_field` call with `confirmed` set to true will promote it. `ClaimRecord`
enforces that rather than the prompt, and it only counts against a value the
caller was actually read back, so a confirmed flag set on a first call changes
nothing. The part I keep coming back to is the test that cleared keyterms in
the first place. It used text-to-speech audio. Clean synthetic speech gives the
recognizer no reason to reach for a prior, so that test could not have
reproduced this failure under any conditions. In the same commit I had written
down that TTS cannot produce human disfluency and that its totals were
therefore weak evidence. Then I let one of those totals decide.

### What a reviewer sees

`/compare` is the three-minute version, and it is public on the deployed
instance. It renders one real call twice, side by side. What a system records
if it trusts the transcript, against what this one records, over six scenes.
The scenes are the manufactured policy match, a spelled-out number, the twin of
another policy on file, a name one letter from the holder, a date with no year,
and a loss that falls into two categories at once. Every utterance on the page
is verbatim from `calls/` and `events.jsonl`, partials included. The naive
column is the value the model actually extracted. The validated column is
computed at request time by the real validators instead of written into the
fixture, so the page cannot drift away from the code. Tests assert that no
verdict is stored in the fixture, and that rendering the page writes nothing to
the call log.

The live panel at `/` is for watching a call as it happens. It tails the same
`calls/*.jsonl` files the agent appends to rather than talking to the agent, so
it survives an agent restart and renders old calls through the same path as
live ones. Every attempt shows up in order with its status, its reason and the
transcript it came from. Rejections and unconfirmed attempts stay on screen
after a field is finally accepted, because the interesting part of a call is
usually what happened before the value stuck.

The deployed instance is closed by default. A browser follows only the call it
started, learning its session id from the ready event and subscribing to that
id alone. The global call listing, the follow-newest stream and the interactive
docs sit behind `LOCAL_PANEL`, which is off unless it is set. Forgetting the
variable in production hides things and cannot expose them. Concurrent calls
are capped before any AssemblyAI session opens, because every connection spends
money. `calls/` is gitignored and the deployed filesystem is ephemeral on
purpose, so caller names and phone numbers and policy numbers do not accumulate
on a public host.

The repository should be readable in one sitting. Seven Python files, about
1,400 lines, no framework beyond FastAPI, no build step, and a raw WebSocket
instead of the SDK. The commit history is the other thing worth opening. Most
commits are a bug found on a live call, what it did, and why the fix lives
where it does.

## Tags
AssemblyAI, Voice Agent API, Python, FastAPI, real-time, insurance,
speech-to-text

## Cover image
Screenshot of /compare, showing RECORDED / KD4-1188 beside UNCONFIRMED /
nothing recorded

## Links
- Repo: https://github.com/sadishihab/claim-intake-agent
- Live: https://web-production-74b77.up.railway.app
