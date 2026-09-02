// AudioWorklet: capture mic audio as PCM16 mono at 24 kHz.
//
// The context runs at the device rate (48 kHz here), not a forced 24 kHz:
// Firefox builds one MediaTrackGraph per sample rate and only feeds the
// default graph's output to the echo canceller, so a 24 kHz context would
// leave agent playback uncancelled. Resampling therefore happens here, which
// also covers Safari, where AudioContext({sampleRate}) is ignored outright.
//
// Samples are accumulated into ~50 ms chunks before posting. process() runs
// every 128 frames, which at 48 kHz would otherwise emit a websocket frame
// every 2.7 ms.
class PCMProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const { inputSampleRate, targetSampleRate, chunkSamples } = options.processorOptions;
    this.ratio = inputSampleRate / targetSampleRate;
    this.chunk = chunkSamples || 1200;
    this.buffer = new Int16Array(this.chunk);
    this.filled = 0;
  }

  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    const outLength = Math.floor(input.length / this.ratio);
    for (let i = 0; i < outLength; i++) {
      const sample = input[Math.floor(i * this.ratio)] ?? 0;
      this.buffer[this.filled++] =
        Math.max(-32768, Math.min(32767, Math.round(sample * 32767)));
      if (this.filled === this.chunk) {
        const out = this.buffer.slice();          // copy: the transfer detaches it
        this.port.postMessage(out.buffer, [out.buffer]);
        this.filled = 0;
      }
    }
    return true;
  }
}

registerProcessor("pcm-processor", PCMProcessor);
