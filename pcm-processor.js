// AudioWorklet: capture mic audio as PCM16 mono at 24 kHz.
//
// Safari ignores AudioContext({sampleRate: 24000}) and runs at the hardware
// rate, so sending its samples as if they were 24 kHz produces chipmunked
// audio. The context's real rate is passed in and resampled here, which makes
// the ratio exactly 1 on Chrome and Firefox and correct everywhere else.
class PCMProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const { inputSampleRate, targetSampleRate } = options.processorOptions;
    this.ratio = inputSampleRate / targetSampleRate;
  }

  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    const outLength = Math.floor(input.length / this.ratio);
    const pcm16 = new Int16Array(outLength);
    for (let i = 0; i < outLength; i++) {
      const sample = input[Math.floor(i * this.ratio)] ?? 0;
      pcm16[i] = Math.max(-32768, Math.min(32767, Math.round(sample * 32767)));
    }
    this.port.postMessage(pcm16.buffer, [pcm16.buffer]);
    return true;
  }
}

registerProcessor("pcm-processor", PCMProcessor);
