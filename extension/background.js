/**
 * VoiceGuard AI — Chrome Extension Background Service Worker (MV3)
 *
 * Coordinates chrome.tabCapture + Offscreen WAV encoding and streams the
 * captured audio through VoiceGuard AI's /api/analyze NDJSON pipeline.
 */

importScripts("config.js");

const DEFAULT_BACKEND_URL = globalThis.VOICEGUARD_CONFIG.backendUrl;
const STORAGE_KEY = "vgExtensionState";
const ANALYSIS_TIMEOUT_MS = 120000;

const initialState = {
  status: "idle", // idle | capturing | analyzing | done | error
  backendUrl: DEFAULT_BACKEND_URL,
  language: "en",
  tabTitle: "",
  captureStartedAt: 0,
  durationSeconds: 0,
  steps: {
    validate: { state: "idle", meta: "Waiting" },
    engines: { state: "idle", meta: "Parallel engines" },
    fusion: { state: "idle", meta: "Waiting" },
  },
  aletheia: null,
  gemini: null,
  report: null,
  error: null,
};

async function getState() {
  const data = await chrome.storage.local.get(STORAGE_KEY);
  return { ...initialState, ...(data[STORAGE_KEY] || {}) };
}

async function updateState(patch) {
  const current = await getState();
  const next = {
    ...current,
    ...patch,
    steps: {
      ...current.steps,
      ...(patch.steps || {}),
    },
  };
  await chrome.storage.local.set({ [STORAGE_KEY]: next });
  chrome.runtime.sendMessage({ type: "VG_STATE_UPDATE", state: next }).catch(() => {});
  return next;
}

async function ensureOffscreenDocument() {
  const offscreenUrl = chrome.runtime.getURL("offscreen.html");
  if (typeof chrome.runtime.getContexts === "function") {
    const contexts = await chrome.runtime.getContexts({
      contextTypes: ["OFFSCREEN_DOCUMENT"],
      documentUrls: [offscreenUrl],
    });
    if (contexts.length > 0) return;
  }
  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["USER_MEDIA"],
    justification: "Capture active browser tab audio and encode to WAV for scam/deepfake screening.",
  });
}

function base64ToUint8Array(base64) {
  const binary = atob(base64);
  const len = binary.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

function normalizeBackendUrl(raw) {
  const trimmed = (raw || "").trim().replace(/\/+$/, "");
  if (!trimmed) return DEFAULT_BACKEND_URL;
  return trimmed;
}

async function analyzeCapturedWav(wavBytes, durationSeconds, backendUrl, language) {
  const validLabel =
    durationSeconds > 0 ? `Valid WAV (${durationSeconds.toFixed(1)} s)` : "Probe complete";

  await updateState({
    status: "analyzing",
    durationSeconds,
    error: null,
    aletheia: null,
    gemini: null,
    report: null,
    steps: {
      validate: { state: "done", meta: validLabel },
      engines: { state: "active", meta: "Running Aletheia ∥ Gemini..." },
      fusion: { state: "idle", meta: "Waiting" },
    },
  });

  const formData = new FormData();
  const wavBlob = new Blob([wavBytes], { type: "audio/wav" });
  formData.append("file", wavBlob, "tab-capture.wav");
  formData.append("language", language || "en");

  const controller = new AbortController();
  let timeoutId = null;
  const resetTimer = () => {
    if (timeoutId !== null) clearTimeout(timeoutId);
    timeoutId = setTimeout(() => controller.abort(), ANALYSIS_TIMEOUT_MS);
  };
  resetTimer();

  let aletheiaDone = false;
  let geminiDone = false;
  let reportReceived = false;
  let errorReceived = false;

  try {
    const resp = await fetch(`${normalizeBackendUrl(backendUrl)}/api/analyze`, {
      method: "POST",
      body: formData,
      signal: controller.signal,
    });

    const contentType = resp.headers.get("content-type") || "";
    if (!resp.ok || contentType.includes("application/json")) {
      const errJson = await resp.json().catch(() => ({}));
      const msg = errJson.error || errJson.detail || `Server returned HTTP ${resp.status}`;
      await updateState({
        status: "error",
        error: msg,
        steps: {
          validate: { state: "done", meta: validLabel },
          engines: { state: "error", meta: "Failed" },
          fusion: { state: "idle", meta: "Stopped" },
        },
      });
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      resetTimer();
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        let evt;
        try {
          evt = JSON.parse(trimmed);
        } catch {
          continue;
        }

        if (evt.type === "stage") {
          if (evt.stage === "preprocess" || evt.stage === "engines") {
            await updateState({
              steps: {
                engines: { state: "active", meta: evt.message || "Running in parallel..." },
              },
            });
          } else if (evt.stage === "fusion") {
            await updateState({
              steps: {
                fusion: { state: "active", meta: "Combining verdicts..." },
              },
            });
          }
        } else if (evt.type === "aletheia") {
          aletheiaDone = true;
          await updateState({
            aletheia: evt.result || null,
            steps: {
              engines: {
                state: geminiDone ? "done" : "active",
                meta: geminiDone ? "Both finished" : "Aletheia done; waiting on Gemini",
              },
            },
          });
        } else if (evt.type === "gemini") {
          geminiDone = true;
          await updateState({
            gemini: evt.result || null,
            steps: {
              engines: {
                state: aletheiaDone ? "done" : "active",
                meta: aletheiaDone ? "Both finished" : "Gemini done; waiting on Aletheia",
              },
            },
          });
        } else if (evt.type === "report") {
          reportReceived = true;
          await updateState({
            status: "done",
            report: evt.report || null,
            steps: {
              validate: { state: "done", meta: validLabel },
              engines: { state: "done", meta: "Completed" },
              fusion: { state: "done", meta: "Assessment ready" },
            },
          });
        } else if (evt.type === "error") {
          errorReceived = true;
          await updateState({
            status: "error",
            error: evt.message || "Analysis failed.",
            steps: {
              engines: { state: "error", meta: "Failed" },
            },
          });
        }
      }
    }

    if (!reportReceived && !errorReceived) {
      await updateState({
        status: "error",
        error: "Analysis stream ended before a final report was received.",
      });
    }
  } catch (err) {
    const isTimeout = err && err.name === "AbortError";
    await updateState({
      status: "error",
      error: isTimeout
        ? "Analysis timed out after 120 seconds. Check that both analysis services are responding, then try again."
        : `Could not reach VoiceGuard backend (${normalizeBackendUrl(backendUrl)}).`,
      steps: {
        engines: { state: "error", meta: isTimeout ? "Timed out (120 s)" : "Network error" },
      },
    });
  } finally {
    if (timeoutId !== null) clearTimeout(timeoutId);
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || typeof message !== "object") return false;

  if (message.type === "GET_STATUS") {
    getState()
      .then((state) => sendResponse({ ok: true, state }))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }

  if (message.type === "UPDATE_SETTINGS") {
    updateState({
      backendUrl: normalizeBackendUrl(message.backendUrl),
      language: message.language || "en",
    })
      .then((state) => sendResponse({ ok: true, state }))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }

  if (message.type === "START_TAB_CAPTURE") {
    (async () => {
      const backendUrl = normalizeBackendUrl(message.backendUrl);
      const language = message.language || "en";
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab || typeof tab.id !== "number") {
        throw new Error("No active browser tab found to capture audio from.");
      }

      const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
      await ensureOffscreenDocument();

      const startResp = await chrome.runtime.sendMessage({
        type: "OFFSCREEN_START_CAPTURE",
        streamId,
      });
      if (!startResp || !startResp.ok) {
        throw new Error((startResp && startResp.error) || "Failed to start offscreen tab audio capture.");
      }

      const state = await updateState({
        status: "capturing",
        backendUrl,
        language,
        tabTitle: tab.title || tab.url || "Active Tab",
        captureStartedAt: Date.now(),
        error: null,
        aletheia: null,
        gemini: null,
        report: null,
        steps: {
          validate: { state: "active", meta: "Capturing tab audio (min 4 s)..." },
          engines: { state: "idle", meta: "Parallel engines" },
          fusion: { state: "idle", meta: "Waiting" },
        },
      });
      sendResponse({ ok: true, state });
    })().catch(async (err) => {
      const msg = err instanceof Error ? err.message : String(err);
      await updateState({ status: "error", error: msg });
      sendResponse({ ok: false, error: msg });
    });
    return true;
  }

  if (message.type === "STOP_TAB_CAPTURE") {
    (async () => {
      const stopResp = await chrome.runtime.sendMessage({ type: "OFFSCREEN_STOP_CAPTURE" });
      if (!stopResp || !stopResp.ok || !stopResp.result) {
        throw new Error((stopResp && stopResp.error) || "No active tab recording found.");
      }
      const { wavBase64, durationSeconds, peakAmplitude = 0, rmsAmplitude = 0 } = stopResp.result;
      if (durationSeconds < 4) {
        await updateState({
          status: "error",
          error: `Captured audio is ${durationSeconds.toFixed(1)} s. Please capture at least 4 seconds for DetectifAI Aletheia.`,
          steps: {
            validate: { state: "error", meta: "Too short (< 4 s)" },
            engines: { state: "idle", meta: "Parallel engines" },
            fusion: { state: "idle", meta: "Waiting" },
          },
        });
        sendResponse({ ok: false, error: "Captured audio is shorter than 4 seconds." });
        return;
      }

      if (peakAmplitude < 0.001 && rmsAmplitude < 0.0001) {
        const error = "No audio was captured. Make sure Instagram is actively playing sound, the tab is not muted, and your device volume is up, then try again.";
        await updateState({
          status: "error",
          error,
          steps: {
            validate: { state: "error", meta: "No tab audio detected" },
            engines: { state: "idle", meta: "Skipped to avoid an empty analysis" },
            fusion: { state: "idle", meta: "Waiting" },
          },
        });
        sendResponse({ ok: false, error });
        return;
      }

      const current = await getState();
      const wavBytes = base64ToUint8Array(wavBase64);
      sendResponse({ ok: true });
      await analyzeCapturedWav(wavBytes, durationSeconds, current.backendUrl, current.language);
    })().catch(async (err) => {
      const msg = err instanceof Error ? err.message : String(err);
      await updateState({ status: "error", error: msg });
      sendResponse({ ok: false, error: msg });
    });
    return true;
  }

  return false;
});
