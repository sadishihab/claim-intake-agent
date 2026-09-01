"""Audio I/O for the voice agent: device selection, mic capture, playback.

Nothing here knows about WebSockets or the Voice Agent protocol — it deals in
raw PCM16 bytes at RATE. Framing those into events is agent.py's job.
"""

import asyncio, queue, sys, threading

import numpy as np, sounddevice as sd

RATE = 24000  # PCM16 mono 24 kHz, both directions (the audio/pcm default)
BLOCK = 1200  # 50 ms of frames at 24 kHz — the chunk size the docs recommend


def suggested_pin(kind):
    """A stable named node, preferred over ALSA 'default' (a PipeWire alias)."""
    for d in sd.query_devices():
        if d[f"max_{kind}_channels"] > 0 and d["name"].startswith(f"alsa_{kind}."):
            return d["name"]
    return None


def list_devices():
    print(sd.query_devices())
    print("\nPin these in .env so PipeWire cannot re-select between runs:")
    for kind in ("input", "output"):
        if pin := suggested_pin(kind):
            print(f"  {kind.upper()}_DEVICE={pin}")


def resolve_device(spec, kind):
    """Resolve an index, a name substring, or the default — once, at startup.

    Monitor sources are never matched for input: they are a loopback of what is
    playing, so the agent would transcribe its own voice.
    """
    devices = sd.query_devices()
    if not spec:
        idx = sd.query_devices(kind=kind)["index"]
        print(f"  {kind:<6} [{idx}] {devices[idx]['name']}  <- unpinned, see --list-devices")
        return idx
    if spec.strip().isdigit():
        idx = int(spec)
    else:
        hits = [i for i, d in enumerate(devices)
                if spec.lower() in d["name"].lower()
                and d[f"max_{kind}_channels"] > 0
                and not (kind == "input" and d["name"].endswith(".monitor"))]
        if not hits:
            sys.exit(f"No {kind} device matches {spec!r}. Try --list-devices.")
        idx = hits[0]
    print(f"  {kind:<6} [{idx}] {devices[idx]['name']}")
    return idx


class Speaker:
    """Drains reply.audio chunks to the sound card on a dedicated thread.

    stream.write() blocks until the card has buffer room, so it stays off the
    event loop. Chunks go straight out; the hardware clock paces, never a sleep.
    """

    def __init__(self, device):
        self.q = queue.Queue()
        self.stream = sd.OutputStream(samplerate=RATE, channels=1, dtype="int16",
                                      device=device)
        self.stream.start()
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        while True:
            pcm = self.q.get()
            if pcm is None:
                return
            self.stream.write(pcm)

    def play(self, pcm_bytes):
        """Takes decoded PCM16; base64 is wire format and stays in agent.py."""
        self.q.put(np.frombuffer(pcm_bytes, dtype=np.int16))

    def flush(self):
        """Drop queued speech after a barge-in so stale audio isn't heard."""
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break
        self.stream.abort()
        self.stream.start()

    def close(self):
        self.q.put(None)
        self.stream.close()


async def mic_chunks(device):
    """Yield 50 ms PCM16 chunks from the mic until the consumer stops."""
    loop, chunks = asyncio.get_running_loop(), asyncio.Queue()

    def on_audio(indata, frames, time_info, status):
        loop.call_soon_threadsafe(chunks.put_nowait, bytes(indata))

    with sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16",
                           blocksize=BLOCK, device=device, callback=on_audio):
        print("mic live — speak, Ctrl+C to hang up\n")
        while True:
            yield await chunks.get()
