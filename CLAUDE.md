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
