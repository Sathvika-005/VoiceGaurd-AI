/**
 * VoiceGuard AI — Offscreen Tab Audio Capture & 16-bit RIFF/WAV Encoder
 */

let activeCapture = null;

function mergeFloat32Chunks(chunks) {
  const totalLength = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const merged = new Float32Array(totalLength);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.length;
  }
  return merged;
}

function encodeWav(samples, sampleRate) {
  const numChannels = 1;
  const bitsPerSample = 16;
  const bytesPerSample = bitsPerSample / 8;
  const blockAlign = numChannels * bytesPerSample;
  const byteRate = sampleRate * blockAlign;
  const dataSize = samples.length * bytesPerSample;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);

  const writeAscii = (offset, text) => {
    for (let i = 0; i < text.length; i += 1) {
      view.setUint8(offset + i, text.charCodeAt(i));
    }
  };

  writeAscii(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeAscii(8, "WAVE");
  writeAscii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, byteRate, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bitsPerSample, true);
  writeAscii(36, "data");
  view.setUint32(40, dataSize, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i += 1) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    offset += 2;
  }
  return buffer;
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000;
  const parts = [];
  for (let i = 0; i < bytes.length; i += chunkSize) {
    parts.push(String.fromCharCode(...bytes.subarray(i, i + chunkSize)));
  }
  return btoa(parts.join(""));
}

async function startTabAudioCapture(streamId) {
  if (activeCapture) {
    await stopTabAudioCapture();
  }

  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      mandatory: {
        chromeMediaSource: "tab",
        chromeMediaSourceId: streamId,
      },
    },
    video: false,
  });

  const audioCtx = new AudioContext();
  if (audioCtx.state === "suspended") {
    await audioCtx.resume();
  }

  const source = audioCtx.createMediaStreamSource(stream);
  // Keep tab audio audible to the user while tapping PCM cleanly
  source.connect(audioCtx.destination);

  const processor = audioCtx.createScriptProcessor(4096, 1, 1);
  const muteGain = audioCtx.createGain();
  muteGain.gain.value = 0;

  const pcmChunks = [];
  let recording = true;

  processor.onaudioprocess = (event) => {
    if (!recording) return;
    const channel = event.inputBuffer.getChannelData(0);
    pcmChunks.push(new Float32Array(channel));
  };

  source.connect(processor);
  processor.connect(muteGain);
  muteGain.connect(audioCtx.destination);

  activeCapture = {
    stop: async () => {
      recording = false;
      processor.disconnect();
      source.disconnect();
      muteGain.disconnect();
      stream.getTracks().forEach((track) => track.stop());

      const sampleRate = audioCtx.sampleRate || 44100;
      await audioCtx.close().catch(() => {});

      const merged = mergeFloat32Chunks(pcmChunks);
      const durationSeconds = merged.length / sampleRate;
      let peakAmplitude = 0;
      let sumSquares = 0;
      for (let i = 0; i < merged.length; i += 1) {
        const sample = merged[i];
        const magnitude = Math.abs(sample);
        if (magnitude > peakAmplitude) peakAmplitude = magnitude;
        sumSquares += sample * sample;
      }
      const rmsAmplitude = merged.length ? Math.sqrt(sumSquares / merged.length) : 0;
      const wavBuffer = encodeWav(merged, sampleRate);
      return {
        wavBase64: arrayBufferToBase64(wavBuffer),
        durationSeconds: Number(durationSeconds.toFixed(2)),
        sampleRate,
        peakAmplitude: Number(peakAmplitude.toFixed(6)),
        rmsAmplitude: Number(rmsAmplitude.toFixed(6)),
      };
    },
  };
}

async function stopTabAudioCapture() {
  if (!activeCapture) {
    return null;
  }
  const handle = activeCapture;
  activeCapture = null;
  return handle.stop();
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || typeof message !== "object") return false;

  if (message.type === "OFFSCREEN_START_CAPTURE") {
    startTabAudioCapture(message.streamId)
      .then(() => sendResponse({ ok: true }))
      .catch((err) => sendResponse({ ok: false, error: err instanceof Error ? err.message : String(err) }));
    return true;
  }

  if (message.type === "OFFSCREEN_STOP_CAPTURE") {
    stopTabAudioCapture()
      .then((result) => sendResponse({ ok: true, result }))
      .catch((err) => sendResponse({ ok: false, error: err instanceof Error ? err.message : String(err) }));
    return true;
  }

  return false;
});
