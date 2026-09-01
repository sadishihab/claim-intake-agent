You are helping me integrate AssemblyAI into my codebase. Build the integration described below end to end, matching the official API shape, do not invent endpoints or parameters.

## Goal
Use case: Front office agent
Category: Voice Agent
What good looks like: Accurate transcription tuned for this use case.

## Recipe (recommended defaults)
- Product: Voice Agent
- Model: voice-agent-api
- Parameters:
  - system_prompt: "You are a friendly front-office receptionist. Greet the caller and figure out what they need: book an appointment, answer a question, or route them to the right person. When booking, use the book_meeting tool with the caller name, email, and ISO-8601 start time. Always confirm names, dates, and phone numbers by reading them back."
  - greeting: "Thanks for calling, how can I help you today?"
  - voice: "anna"
  - tools:
    - book_meeting:
      - description: "Book a meeting on the Cal.com calendar. Use this when the caller wants to schedule an appointment."
      - parameters: {"type":"object","properties":{"start":{"type":"string","format":"date-time","description":"Meeting start time in ISO-8601 with timezone offset."},"eventTypeId":{"type":"number","description":"Cal.com event type ID to book."},"attendee":{"type":"object","properties":{"name":{"type":"string","description":"Full name of the caller."},"email":{"type":"string","format":"email","description":"Caller email."},"timeZone":{"type":"string","description":"IANA time zone, e.g. America/New_York."}},"required":["name","email","timeZone"]}},"required":["start","eventTypeId","attendee"]}
      - http: {"url":"https://api.cal.com/v2/bookings","http_method":"POST","headers":[{"name":"Authorization","value":"Bearer YOUR_CAL_API_KEY"},{"name":"cal-api-version","value":"2026-02-25"}]}

## API & integration notes
- Transport: WebSocket at wss://agents.assemblyai.com/v1/ws with header Authorization: Bearer <API_KEY>. For browser / client-side, mint a temporary token first (GET https://agents.assemblyai.com/v1/token) and connect with ?token=<temp_token>.
- First message MUST be {"type": "session.update", "session": {...}} — either {"agent_id": "..."} referencing a stored agent (POST /v1/agents), or an inline config carrying system_prompt, greeting, input (format, turn_detection, keyterms), and output (voice, format). Wait for {"type": "session.ready"} before streaming audio.
- Send caller audio as {"type": "input.audio", "audio": "<base64 PCM16 mono 24kHz>"} events. Receive agent speech as {"type": "reply.audio", "data": "<base64 PCM>"} events (input uses the "audio" field, output uses "data"). Transcripts arrive as {"type": "transcript.user.delta"} / {"type": "transcript.user"} for the caller and {"type": "transcript.agent"} for the agent.
- system_prompt, input.turn_detection, input.keyterms, output.volume, and tools are MUTABLE after session.ready (send another session.update). greeting, output.voice, and output.format are IMMUTABLE.
- To hang up, send {"type": "session.end"} — the server emits session.ended and billing stops immediately. Just closing the socket keeps the session (and billing) alive for a 30s resume window.

## Reference docs
- Voice Agent overview: https://www.assemblyai.com/docs/voice-agents/voice-agent-api
- Configure your agent (session.update): https://www.assemblyai.com/docs/voice-agents/voice-agent-api/session-configuration
- Turn detection & interruptions: https://www.assemblyai.com/docs/voice-agents/voice-agent-api/turn-detection-and-interruptions
- Prompting guide: https://www.assemblyai.com/docs/voice-agents/voice-agent-api/prompting-guide
- Voices: https://www.assemblyai.com/docs/voice-agents/voice-agent-api/voices
- WebSocket reference: https://www.assemblyai.com/docs/api-reference/voice-agent-api/voice-agent-web-socket

## Deliverables
1. A working implementation in the language/framework of my project (ask me if unclear).
2. Config surfaced via environment variables, never hardcode the API key.
3. Error handling: auth failures, reconnection for streaming, poll/webhook for async.
4. A short README section explaining how to run it and how to tune the parameters above.
5. Minimal but realistic test/demo that I can run locally.

## Constraints
- Use the raw HTTP/WebSocket API (not the AssemblyAI SDK) unless I explicitly ask for the SDK.
- Keep dependencies minimal.
- Match my existing code style and project structure, read the repo first.
- Ask me clarifying questions about audio source, deployment target, and concurrency before writing code.
