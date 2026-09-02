# Voice claim intake agent

A voice agent that takes an insurance claim over the phone or the browser, and
**refuses to write down anything it is not sure it heard correctly.**

Speech recognition is wrong often enough that a claims system built on raw
transcripts records wrong policy numbers, wrong names and wrong dates without
ever noticing. Every value here is checked against a policy database before it
is recorded, and the agent asks when it cannot be sure.

See it in one screen: **`/compare`** renders one real call twice, side by side —
what a system records if it trusts the transcript, against what this one
records. Every utterance on that page is verbatim from the call logs.

## Three verdicts

Each field is validated by a pure function that returns one of three verdicts,
and the verdict decides what the agent says next.

| verdict | means | the agent |
| --- | --- | --- |
| `accepted` | checked and unambiguous | records it, says nothing, moves on |
| `unconfirmed` | probably right, cannot be sure | reads the value back and waits |
| `rejected` | cannot be right | explains the problem and asks again |

The middle one is the point. A system with only accept and reject has to guess
on every near miss: a name one letter from the policy holder, a loss that is
both collision and vandalism, a date three days outside cover. Each of those is
more often a mishearing than a real answer, so the agent asks.

What the agent says is not left to the model. Every verdict carries a
**readback** — the exact words to speak — and the system prompt requires it
verbatim. A policy number is read back in the NATO alphabet, which is what
makes `BX7-4402` and `BX7-4420`, two real policies belonging to different
people, distinguishable out loud: *"four four zero two"* against *"four four
two zero"*.

Every attempt is appended to `calls/<session_id>.jsonl` as it happens, so a
reviewer can see that a policy number was rejected twice before it stuck, and
what the caller actually said each time.

## The finding: a validator can be made structurally powerless

The most interesting thing in this repository is a mistake.

AssemblyAI supports `input.keyterms`, a list of words to bias transcription
toward. Feeding it the real policy numbers is the obvious move, and on clean
speech it measurably helped. It shipped.

On a live call I deliberately read a policy number that is **not** on file.
That the number was wrong is my recollection — the audio is not kept, so the
log cannot prove what went into the microphone. What the log does show is the
recogniser revising its own answer onto a keyterm:

```
partial  'Yes, my policy number is C411.'
partial  'Yes, my policy number is KD4-1188.'   <- revised onto a keyterm
FINAL    'Yes, my policy number is KD4-1188.'
-> recorded 'KD4-1188'  (a real policy, a different person)
```

Three sessions ran with keyterms — the only three in the event log — and all
three recorded `KD4-1188`. This is the one whose partials caught the revision
happening; the other two show only the final value, so they are not evidence
of the same thing. Before keyterms, the same speaker on the same microphone
produced `3841188`, `MACKKDK41138` and `D411` — all rejected, all safe.

**Every downstream guard still passed.** Exact match only, no fuzzy matching,
the confusable-pair separation, the full evidence trail: all intact, all
useless. Exact match against the policy database was only ever evidence
*because the recogniser knew nothing about the policy database*. keyterms wired
the answer key into the recogniser, and the match stopped meaning anything. A
wrong value that arrives already valid is invisible to a validator.

The rule this produced, now in `CLAUDE.md`:

> Never bias the recogniser toward the answer key when the recogniser's output
> is the evidence being validated.

The fix was two parts. keyterms is gone. And a policy number is never accepted
on a match alone — it is held until the caller hears it read back and agrees,
enforced in `ClaimRecord` rather than asked for in the prompt, so a model that
sets the confirmation flag without asking changes nothing.

A synthetic test had cleared keyterms before it shipped: clean text-to-speech
audio gives the recogniser no reason to reach for a prior, so it could not
reproduce the failure. The lesson underneath the lesson is that a passing test
on unrepresentative input is not evidence.

## Running it

```bash
python -m venv venv && ./venv/bin/pip install -r requirements.txt
echo "ASSEMBLYAI_API_KEY=..." > .env
```

**Browser** — the agent, the live review panel, and the comparison view:

```bash
LOCAL_PANEL=1 ./venv/bin/uvicorn review:app --port 8000
```

Open `http://127.0.0.1:8000` and press Start call. `getUserMedia` needs a secure
origin, so use `localhost` rather than a LAN address. `/compare` is the
side-by-side view.

**Terminal** — same agent, same protocol, a sound card instead of a browser:

```bash
sudo apt install libportaudio2          # PortAudio, for the terminal client only
./venv/bin/python agent.py --list-devices
./venv/bin/python agent.py
```

Use headphones: native audio APIs have no echo cancellation, so on speakers the
agent hears itself and interrupts every reply. Browsers handle this for you.

```bash
./venv/bin/python -m pytest -q          # the test suite
./venv/bin/python seed_demo.py          # demo calls for the panel
```

`LOCAL_PANEL=1` opens the full call list, the follow-newest stream and the API
docs. It is **off by default**: the deployed instance scopes each browser to
the call it started, so one caller cannot read another's claim.

## Layout

| file | |
| --- | --- |
| `protocol.py` | the Voice Agent protocol, shared by both clients |
| `agent.py` | terminal client: devices, speaker, Ctrl+C |
| `audio.py` | device selection, mic capture, playback |
| `review.py` | review panel, comparison view, browser call relay |
| `validators.py` | one pure function per field, three verdicts |
| `intake.py` | per-call state: what is recorded, what is still being asked |
| `policies.json` | the fake policy database |

Audio is proxied through the server, so the API key never reaches the browser.
There is a test asserting no credential appears in anything served.
