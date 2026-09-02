# Project: voice claim intake agent

## AssemblyAI rules
For anything AssemblyAI related, use the assemblyai-docs MCP tools first.
Do not rely on training data. The API has changed.
Also fetch https://www.assemblyai.com/docs/llms.txt before writing AssemblyAI code.
See recipe.md in this folder for the verified API shape.

## Hard-won facts (do not "correct" these)
- Voice Agent API needs `Authorization: Bearer <key>`.
- Every OTHER AssemblyAI API takes the raw key with NO Bearer prefix.
- Voice Agent audio is PCM16 mono 24000 Hz, base64 inside JSON events.
- Input audio uses the field `audio`. Output audio uses the field `data`.
- Never sleep-schedule playback. Write chunks straight to the output stream.
- Always send {"type": "session.end"} when done or billing continues 30s.

## Style
Small files. Minimal dependencies. Raw WebSocket, not the SDK.
Explain what you are doing before you do it — I am learning this stack.

- reply.audio uses `data`, not `audio` — the message-sequence doc page is
  wrong about this. Agent transcript field is `text`, not `transcript`.
  Verified against the AsyncAPI schema and the live API.
- The assemblyai-docs MCP is deprecated. Replacement at
  https://www.assemblyai.com/docs/mcp

## Recognizer independence (learned the hard way)
Never bias the recognizer toward the answer key when the recognizer's output
is the evidence being validated.

We set `input.keyterms` to the policy numbers in policies.json. Transcription
accuracy improved on clean speech, and on a real call the recognizer revised
its own answer onto a keyterm: the partials show "...is C411." then "...is
KD4-1188." — a real policy, which then passed exact-match validation. I had
deliberately read a number that is not on file; that part is recollection, the
audio is not kept. Three sessions ran with keyterms and all three recorded
KD4-1188, but only this one caught the revision in its partials — do not cite
the other two as the same finding.

Exact match against policies.json was only ever evidence because the
recognizer knew nothing about policies.json. keyterms wired the answer key
into the recognizer and the match stopped meaning anything, while every
downstream guard still passed. A wrong value that arrives already valid is
invisible to a validator.

So: no keyterms for policy numbers, and policy_number is never accepted on a
match alone — the caller has to hear the NATO readback and agree, via
`confirmed` on record_field, which ClaimRecord enforces (a `confirmed` that
was never read back does not count).
