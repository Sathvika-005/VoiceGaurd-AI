(() => {
  "use strict";

  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("file-input");
  const recordBtn = document.getElementById("record-btn");
  const recordBtnLabel = document.getElementById("record-btn-label");
  const recordTimer = document.getElementById("record-timer");
  const langSelect = document.getElementById("language-select");
  const filePreview = document.getElementById("file-preview");
  const previewFilename = document.getElementById("preview-filename");
  const previewDetails = document.getElementById("preview-details");
  const audioPlayer = document.getElementById("audio-player");
  const clearBtn = document.getElementById("clear-btn");
  const analyzeBtn = document.getElementById("analyze-btn");
  const statusText = document.getElementById("pipeline-status-text");

  const stepperSection = document.getElementById("stepper-section");
  const stepValidate = document.getElementById("step-validate");
  const stepValidateMeta = document.getElementById("step-validate-meta");
  const stepEngines = document.getElementById("step-engines");
  const stepEnginesMeta = document.getElementById("step-engines-meta");
  const stepFusion = document.getElementById("step-fusion");
  const stepFusionMeta = document.getElementById("step-fusion-meta");

  const errorBanner = document.getElementById("error-banner");
  const errorMessage = document.getElementById("error-message");
  const resultsArea = document.getElementById("results-area");

  const threatBanner = document.getElementById("threat-banner");
  const threatIcon = document.getElementById("threat-icon");
  const threatLevelText = document.getElementById("threat-level-text");
  const threatHeadline = document.getElementById("threat-headline");
  const partialBanner = document.getElementById("partial-banner");
  const cacheBadge = document.getElementById("cache-badge");
  const timingSummary = document.getElementById("timing-summary");

  const aletheiaTiming = document.getElementById("aletheia-timing");
  const aletheiaBody = document.getElementById("aletheia-body");
  const aletheiaTimelineWrap = document.getElementById("aletheia-timeline-wrap");
  const aletheiaTimeline = document.getElementById("aletheia-timeline");
  const aletheiaTimelineLegend = document.getElementById("aletheia-timeline-legend");
  const aletheiaSegmentCount = document.getElementById("aletheia-segment-count");
  const aletheiaEvidenceNote = document.getElementById("aletheia-evidence-note");

  const geminiTiming = document.getElementById("gemini-timing");
  const geminiBody = document.getElementById("gemini-body");

  const indicatorsCard = document.getElementById("indicators-card");
  const indicatorsCount = document.getElementById("indicators-count");
  const indicatorsList = document.getElementById("indicators-list");

  const fusionDetailsCard = document.getElementById("fusion-details-card");
  const fusionReasons = document.getElementById("fusion-reasons");
  const fusionActions = document.getElementById("fusion-actions");

  const copySummaryBtn = document.getElementById("copy-summary-btn");
  const printBtn = document.getElementById("print-btn");
  const downloadJsonBtn = document.getElementById("download-json-btn");
  const copyFeedback = document.getElementById("copy-feedback");

  let selectedFile = null;
  let selectedFileDuration = null;
  let objectUrl = null;
  let latestReport = null;

  let activeRecState = null;
  let recordTimerId = null;
  let recordStartMs = 0;

  const THREAT_ICONS = {
    LOW: "✓",
    MEDIUM: "!",
    HIGH: "▲",
    CRITICAL: "⛔",
    UNDETERMINED: "?",
  };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clearChildren(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  }

  function formatSeconds(sec) {
    const total = Math.max(0, Math.round(sec));
    const m = Math.floor(total / 60);
    const s = total % 60;
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }

  async function decodeAudioDuration(file) {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx || !file) return null;
    const ctx = new AudioCtx();
    try {
      const arrayBuffer = await file.arrayBuffer();
      const audioBuffer = await ctx.decodeAudioData(arrayBuffer);
      return Number.isFinite(audioBuffer.duration) && audioBuffer.duration > 0
        ? audioBuffer.duration
        : null;
    } catch {
      return null;
    } finally {
      ctx.close().catch(() => {});
    }
  }

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

    function writeAscii(offset, text) {
      for (let i = 0; i < text.length; i += 1) {
        view.setUint8(offset + i, text.charCodeAt(i));
      }
    }

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

  function encodeAudioBufferToWav(audioBuffer) {
    const length = audioBuffer.length;
    const channels = audioBuffer.numberOfChannels || 1;
    const mono = new Float32Array(length);
    for (let ch = 0; ch < channels; ch += 1) {
      const data = audioBuffer.getChannelData(ch);
      for (let i = 0; i < length; i += 1) {
        mono[i] += data[i] / channels;
      }
    }
    return encodeWav(mono, audioBuffer.sampleRate);
  }

  function setSelectedFile(file) {
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
      objectUrl = null;
    }
    selectedFile = file;
    selectedFileDuration = null;
    if (!file) {
      filePreview.classList.add("hidden");
      audioPlayer.removeAttribute("src");
      analyzeBtn.disabled = true;
      statusText.textContent = "Ready.";
      return;
    }

    objectUrl = URL.createObjectURL(file);
    audioPlayer.src = objectUrl;
    previewFilename.textContent = file.name || "recording";
    previewDetails.textContent = `${formatBytes(file.size)} · loading duration...`;
    filePreview.classList.remove("hidden");
    analyzeBtn.disabled = false;
    statusText.textContent = "Audio ready for analysis.";

    audioPlayer.onloadedmetadata = async () => {
      if (Number.isFinite(audioPlayer.duration) && audioPlayer.duration > 0) {
        selectedFileDuration = audioPlayer.duration;
        previewDetails.textContent = `${formatBytes(file.size)} · ${audioPlayer.duration.toFixed(1)} s`;
      } else {
        const fallbackDuration = await decodeAudioDuration(file);
        if (selectedFile !== file) return;
        if (Number.isFinite(fallbackDuration) && fallbackDuration > 0) {
          selectedFileDuration = fallbackDuration;
          previewDetails.textContent = `${formatBytes(file.size)} · ${fallbackDuration.toFixed(1)} s`;
        } else {
          previewDetails.textContent = formatBytes(file.size);
        }
      }
    };
  }

  function resetResultsUI() {
    errorBanner.classList.add("hidden");
    errorMessage.textContent = "";
    resultsArea.classList.add("hidden");
    indicatorsCard.classList.add("hidden");
    fusionDetailsCard.classList.add("hidden");
    aletheiaTimelineWrap.classList.add("hidden");
    aletheiaEvidenceNote.classList.add("hidden");
    partialBanner.classList.add("hidden");
    cacheBadge.classList.add("hidden");
    timingSummary.classList.add("hidden");
    copyFeedback.textContent = "";
    latestReport = null;

    aletheiaTiming.textContent = "—";
    geminiTiming.textContent = "—";
    clearChildren(aletheiaBody);
    aletheiaBody.appendChild(el("p", "muted", "Running DetectifAI Aletheia..."));
    clearChildren(geminiBody);
    geminiBody.appendChild(el("p", "muted", "Running Gemini native audio..."));

    setStep(stepValidate, stepValidateMeta, "idle", "Waiting");
    setStep(stepEngines, stepEnginesMeta, "idle", "Parallel engines");
    setStep(stepFusion, stepFusionMeta, "idle", "Waiting");
  }

  function setStep(stepEl, metaEl, state, metaText) {
    stepEl.dataset.state = state;
    metaEl.textContent = metaText;
  }

  function showError(msg) {
    errorMessage.textContent = msg;
    errorBanner.classList.remove("hidden");
    statusText.textContent = "Error.";
  }

  function addKV(grid, label, value) {
    grid.appendChild(el("div", "kv-label", label));
    grid.appendChild(el("div", "kv-value", value));
  }

  function renderAletheia(data) {
    clearChildren(aletheiaBody);
    aletheiaTiming.textContent = `${data.elapsed_ms ?? 0} ms`;
    aletheiaEvidenceNote.textContent = data.evidence_note || "A detection score is evidence, not proof.";
    aletheiaEvidenceNote.classList.remove("hidden");

    const grid = el("div", "kv-grid");
    if (data.status === "ok") {
      addKV(grid, "State", data.state);
      addKV(grid, "Raw Verdict", data.raw_verdict || "—");
      if (typeof data.score === "number") {
        addKV(grid, "Score", `${data.score.toFixed(3)} (${data.score_meaning || "0=genuine, 1=synthetic"})`);
      } else {
        addKV(grid, "Score", "See per-segment timeline (scores are not averaged)");
      }
      if (Array.isArray(data.flagged_ranges) && data.flagged_ranges.length > 0) {
        addKV(grid, "Flagged Ranges", data.flagged_ranges.join(", "));
      }
      aletheiaBody.appendChild(grid);
    } else {
      console.error("[VoiceGuard][Aletheia] Failed:", data.error_message, data);
      addKV(grid, "Status", data.status === "unrecognised" ? "Unrecognised response" : "Unavailable");
      addKV(grid, "Reason", data.error_message || "Service unavailable");
      aletheiaBody.appendChild(grid);
    }

    const segments = Array.isArray(data.segments) ? data.segments : [];
    if (segments.length > 1) {
      clearChildren(aletheiaTimeline);
      clearChildren(aletheiaTimelineLegend);
      aletheiaSegmentCount.textContent = `${segments.length} segments`;
      const totalSpan = segments.reduce((acc, s) => acc + Math.max(1, (s.end || 0) - (s.start || 0)), 0);

      segments.forEach((seg) => {
        const dur = Math.max(1, (seg.end || 0) - (seg.start || 0));
        const flexRatio = Math.max(1, Math.round((dur / totalSpan) * 100));
        const segEl = el("div", "timeline-seg");
        segEl.setAttribute("role", "listitem");
        segEl.dataset.state = seg.state || "UNAVAILABLE";
        segEl.style.flexGrow = String(flexRatio);
        const scorePart = typeof seg.score === "number" ? ` · ${seg.score.toFixed(2)}` : "";
        const verdictPart = seg.raw_verdict || seg.state;
        segEl.textContent = `${seg.time_range} (${verdictPart}${scorePart})`;
        segEl.title = `Segment ${seg.index + 1}: ${seg.time_range} | ${seg.state}${scorePart}`;
        aletheiaTimeline.appendChild(segEl);
      });

      aletheiaTimelineLegend.textContent =
        "Each block represents a non-overlapping window analysed independently by Aletheia.";
      aletheiaTimelineWrap.classList.remove("hidden");
    } else {
      aletheiaTimelineWrap.classList.add("hidden");
    }
  }

  function renderGemini(data) {
    clearChildren(geminiBody);
    geminiTiming.textContent = `${data.elapsed_ms ?? 0} ms`;

    if (data.status !== "ok" || !data.analysis) {
      const grid = el("div", "kv-grid");
      addKV(grid, "Status", "Unavailable");
      addKV(grid, "Reason", data.error_message || "Gemini analysis unavailable");
      geminiBody.appendChild(grid);
      return;
    }

    const a = data.analysis;
    const grid = el("div", "kv-grid");
    addKV(grid, "Risk Level", a.risk_level || "—");
    addKV(grid, "Confidence", a.confidence || "—");
    addKV(grid, "Speech Clarity", a.speech_clarity || "—");
    addKV(grid, "Language Heard", a.language_detected || "—");
    geminiBody.appendChild(grid);

    if (a.summary) {
      geminiBody.appendChild(el("p", "summary-text", a.summary));
    }
    if (a.limitations) {
      geminiBody.appendChild(el("p", "limitations-note", `Limitations: ${a.limitations}`));
    }

    const indicators = Array.isArray(a.indicators) ? a.indicators : [];
    clearChildren(indicatorsList);
    if (indicators.length > 0) {
      indicatorsCount.textContent = `${indicators.length} indicator(s)`;
      indicators.forEach((ind) => {
        const li = el("li", "indicator-item");
        const top = el("div", "indicator-top");
        const catName = String(ind.category || "other").replace(/_/g, " ");
        top.appendChild(el("span", "ind-cat", catName));

        const sevTag = el("span", "ind-tag", `severity: ${ind.severity || "low"}`);
        sevTag.dataset.sev = ind.severity || "low";
        top.appendChild(sevTag);

        if (ind.timestamp) {
          top.appendChild(el("span", "ind-tag mono", `~${ind.timestamp}`));
        }
        li.appendChild(top);
        li.appendChild(el("p", "ind-evidence", `“${ind.evidence || ""}”`));
        if (ind.explanation) {
          li.appendChild(el("p", "ind-explanation", ind.explanation));
        }
        indicatorsList.appendChild(li);
      });
      indicatorsCard.classList.remove("hidden");
    } else {
      indicatorsCard.classList.add("hidden");
    }
  }

  function renderReport(report) {
    latestReport = report;
    const fusion = report.fusion || {};
    const level = fusion.threat_level || "UNDETERMINED";

    threatBanner.dataset.level = level;
    threatIcon.textContent = THREAT_ICONS[level] || "?";
    threatLevelText.textContent = level;
    threatHeadline.textContent = fusion.headline || "Assessment complete";

    if (fusion.banner) {
      partialBanner.textContent = fusion.banner;
      partialBanner.classList.remove("hidden");
    } else {
      partialBanner.classList.add("hidden");
    }

    if (report.cached) {
      cacheBadge.textContent = `Cached · ${report.cache_age_seconds ?? 0}s ago`;
      cacheBadge.classList.remove("hidden");
    } else {
      cacheBadge.classList.add("hidden");
    }

    const t = report.timings_ms || {};
    timingSummary.textContent = `Total ${t.total ?? 0} ms (Aletheia ${t.aletheia ?? 0} ms · Gemini ${t.gemini ?? 0} ms)`;
    timingSummary.classList.remove("hidden");

    clearChildren(fusionReasons);
    (fusion.reasons || []).forEach((r) => {
      fusionReasons.appendChild(el("li", "", r));
    });

    clearChildren(fusionActions);
    (fusion.actions || []).forEach((a) => {
      fusionActions.appendChild(el("li", "", a));
    });

    fusionDetailsCard.classList.remove("hidden");
    resultsArea.classList.remove("hidden");
  }

  async function runAnalysis() {
    if (!selectedFile) return;
    resetResultsUI();
    stepperSection.classList.remove("hidden");
    resultsArea.classList.remove("hidden");
    analyzeBtn.disabled = true;
    statusText.textContent = "Validating audio...";
    setStep(stepValidate, stepValidateMeta, "active", "Reading duration...");

    let resolvedDuration =
      Number.isFinite(audioPlayer.duration) && audioPlayer.duration > 0
        ? audioPlayer.duration
        : selectedFileDuration;
    if (!Number.isFinite(resolvedDuration) || resolvedDuration <= 0) {
      resolvedDuration = await decodeAudioDuration(selectedFile);
      if (Number.isFinite(resolvedDuration) && resolvedDuration > 0) {
        selectedFileDuration = resolvedDuration;
        previewDetails.textContent = `${formatBytes(selectedFile.size)} · ${resolvedDuration.toFixed(1)} s`;
      }
    }

    const validLabel =
      Number.isFinite(resolvedDuration) && resolvedDuration > 0
        ? `Valid audio (${resolvedDuration.toFixed(1)} s)`
        : "Probe complete";
    setStep(stepValidate, stepValidateMeta, "done", validLabel);
    setStep(stepEngines, stepEnginesMeta, "active", "Running Aletheia ∥ Gemini...");
    statusText.textContent = "Running Aletheia & Gemini in parallel...";

    const formData = new FormData();
    formData.append("file", selectedFile);
    formData.append("language", langSelect.value);

    const ENGINE_TIMEOUT_MS = 15000;
    const controller = new AbortController();
    let timeoutId = null;
    const resetAbortTimer = () => {
      if (timeoutId !== null) clearTimeout(timeoutId);
      timeoutId = setTimeout(() => {
        controller.abort();
      }, ENGINE_TIMEOUT_MS);
    };
    resetAbortTimer();

    let aletheiaDone = false;
    let geminiDone = false;
    let reportReceived = false;
    let errorReceived = false;

    try {
      const resp = await fetch("/api/analyze", {
        method: "POST",
        body: formData,
        signal: controller.signal,
      });

      const contentType = resp.headers.get("content-type") || "";
      if (!resp.ok || contentType.includes("application/json")) {
        const errJson = await resp.json().catch(() => ({}));
        const msg = errJson.error || errJson.detail || `Server returned HTTP ${resp.status}`;
        setStep(stepEngines, stepEnginesMeta, "error", "Failed");
        resultsArea.classList.add("hidden");
        showError(msg);
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        resetAbortTimer();
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
            if (evt.stage === "validate") {
              if (stepValidate.dataset.state !== "done") {
                setStep(stepValidate, stepValidateMeta, "active", evt.message || "Probing...");
              }
            } else if (evt.stage === "preprocess") {
              setStep(stepValidate, stepValidateMeta, "done", validLabel);
              setStep(stepEngines, stepEnginesMeta, "active", evt.message || "Preparing...");
            } else if (evt.stage === "engines") {
              setStep(stepValidate, stepValidateMeta, "done", validLabel);
              setStep(stepEngines, stepEnginesMeta, "active", evt.message || "Running in parallel...");
            } else if (evt.stage === "fusion") {
              setStep(stepFusion, stepFusionMeta, "active", "Combining verdicts...");
            }
          } else if (evt.type === "aletheia") {
            aletheiaDone = true;
            renderAletheia(evt.result || {});
            setStep(
              stepEngines,
              stepEnginesMeta,
              geminiDone ? "done" : "active",
              geminiDone ? "Both finished" : "Aletheia done; waiting on Gemini"
            );
          } else if (evt.type === "gemini") {
            geminiDone = true;
            renderGemini(evt.result || {});
            setStep(
              stepEngines,
              stepEnginesMeta,
              aletheiaDone ? "done" : "active",
              aletheiaDone ? "Both finished" : "Gemini done; waiting on Aletheia"
            );
          } else if (evt.type === "report") {
            reportReceived = true;
            setStep(stepValidate, stepValidateMeta, "done", validLabel);
            setStep(stepEngines, stepEnginesMeta, "done", "Completed");
            setStep(stepFusion, stepFusionMeta, "done", "Assessment ready");
            renderReport(evt.report || {});
            statusText.textContent = "Analysis complete.";
          } else if (evt.type === "error") {
            errorReceived = true;
            setStep(stepEngines, stepEnginesMeta, "error", "Failed");
            resultsArea.classList.add("hidden");
            showError(evt.message || "Analysis failed.");
          }
        }
      }

      if (!reportReceived && !errorReceived) {
        setStep(stepEngines, stepEnginesMeta, "error", "Incomplete stream");
        showError("Analysis stream ended before a final report was received.");
      }
    } catch (err) {
      const isTimeout = err && err.name === "AbortError";
      const errMsg = isTimeout
        ? "Analysis request timed out after 15 seconds."
        : "Network error while streaming analysis results.";
      setStep(stepEngines, stepEnginesMeta, "error", isTimeout ? "Timed out (15 s)" : "Network error");
      if (!aletheiaDone) {
        renderAletheia({ status: "unavailable", elapsed_ms: 15000, error_message: errMsg });
      }
      if (!geminiDone) {
        renderGemini({ status: "unavailable", elapsed_ms: 15000, error_message: errMsg });
      }
      showError(errMsg);
    } finally {
      if (timeoutId !== null) clearTimeout(timeoutId);
      analyzeBtn.disabled = !selectedFile;
    }
  }

  function buildPlainSummary(report) {
    const f = report.fusion || {};
    const a = report.aletheia || {};
    const g = (report.gemini && report.gemini.analysis) || null;
    const lines = [
      "VOICEGUARD AI — SECURITY REPORT",
      `Threat Level: ${f.threat_level || "UNDETERMINED"} — ${f.headline || ""}`,
      a.status === "ok"
        ? `Voice Authenticity (Aletheia): ${a.raw_verdict || a.state}${typeof a.score === "number" ? ` (score ${a.score.toFixed(3)})` : ""}`
        : `Voice Authenticity (Aletheia): Unavailable (${a.error_message || ""})`,
      g
        ? `Communication Risk (Gemini): ${g.risk_level} (confidence ${g.confidence}) — ${g.summary}`
        : `Communication Risk (Gemini): Unavailable (${(report.gemini && report.gemini.error_message) || ""})`,
    ];
    if (Array.isArray(f.actions) && f.actions.length) {
      lines.push("Recommended Actions:");
      f.actions.forEach((act) => lines.push(`- ${act}`));
    }
    lines.push("Note: Automated assessment, not proof of fraud or voice cloning. Verify independently.");
    return lines.join("\n");
  }

  // Drag & drop + file browse
  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fileInput.click();
    }
  });
  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("drag-over");
  });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag-over"));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("drag-over");
    const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (file) setSelectedFile(file);
  });

  fileInput.addEventListener("change", () => {
    const file = fileInput.files && fileInput.files[0];
    if (file) setSelectedFile(file);
  });

  clearBtn.addEventListener("click", () => {
    fileInput.value = "";
    setSelectedFile(null);
    resetResultsUI();
    stepperSection.classList.add("hidden");
  });

  analyzeBtn.addEventListener("click", runAnalysis);

  // In-browser WAV microphone recorder (avoids Chrome MediaRecorder WebM missing duration metadata)
  recordBtn.addEventListener("click", async () => {
    if (activeRecState) {
      await activeRecState.stop();
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      showError("In-browser microphone recording is not supported in this browser.");
      return;
    }

    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      showError("Microphone permission was denied or no microphone was found.");
      return;
    }

    const finishUiReset = () => {
      clearInterval(recordTimerId);
      recordTimerId = null;
      recordBtn.classList.remove("recording");
      recordBtnLabel.textContent = "Record Mic";
      recordTimer.classList.add("hidden");
      activeRecState = null;
    };

    if (AudioCtx && typeof AudioCtx.prototype.createScriptProcessor === "function") {
      try {
        const audioCtx = new AudioCtx();
        if (audioCtx.state === "suspended") {
          await audioCtx.resume();
        }
        const source = audioCtx.createMediaStreamSource(stream);
        const processor = audioCtx.createScriptProcessor(4096, 1, 1);
        const muteGain = audioCtx.createGain();
        muteGain.gain.value = 0;

        const pcmChunks = [];
        let recordingActive = true;

        processor.onaudioprocess = (event) => {
          if (!recordingActive) return;
          const inputData = event.inputBuffer.getChannelData(0);
          pcmChunks.push(new Float32Array(inputData));
        };

        source.connect(processor);
        processor.connect(muteGain);
        muteGain.connect(audioCtx.destination);

        activeRecState = {
          stop: async () => {
            recordingActive = false;
            finishUiReset();
            processor.disconnect();
            source.disconnect();
            muteGain.disconnect();
            stream.getTracks().forEach((t) => t.stop());

            const sampleRate = audioCtx.sampleRate || 44100;
            await audioCtx.close().catch(() => {});

            const merged = mergeFloat32Chunks(pcmChunks);
            const wavBuffer = encodeWav(merged, sampleRate);
            const wavBlob = new Blob([wavBuffer], { type: "audio/wav" });
            const recordedFile = new File([wavBlob], "mic-recording.wav", { type: "audio/wav" });
            setSelectedFile(recordedFile);
          },
        };

        recordStartMs = Date.now();
        recordTimer.textContent = "00:00";
        recordTimer.classList.remove("hidden");
        recordBtn.classList.add("recording");
        recordBtnLabel.textContent = "Stop Recording";
        recordTimerId = setInterval(() => {
          recordTimer.textContent = formatSeconds((Date.now() - recordStartMs) / 1000);
        }, 250);
        return;
      } catch {
        stream.getTracks().forEach((t) => t.stop());
        showError("Unable to initialize microphone audio encoder.");
        return;
      }
    }

    // Fallback: MediaRecorder + AudioContext.decodeAudioData() -> WAV
    if (typeof MediaRecorder === "undefined") {
      stream.getTracks().forEach((t) => t.stop());
      showError("In-browser microphone recording is not supported in this browser.");
      return;
    }
    try {
      const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : MediaRecorder.isTypeSupported("audio/webm")
        ? "audio/webm"
        : "";
      const recordChunks = [];
      const mediaRecorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);

      mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) recordChunks.push(e.data);
      };
      mediaRecorder.onstop = async () => {
        finishUiReset();
        stream.getTracks().forEach((t) => t.stop());

        const blobType = mediaRecorder.mimeType || "audio/webm";
        const rawBlob = new Blob(recordChunks, { type: blobType });
        if (AudioCtx) {
          const decodeCtx = new AudioCtx();
          try {
            const audioBuffer = await decodeCtx.decodeAudioData(await rawBlob.arrayBuffer());
            const wavBuffer = encodeAudioBufferToWav(audioBuffer);
            const wavBlob = new Blob([wavBuffer], { type: "audio/wav" });
            setSelectedFile(new File([wavBlob], "mic-recording.wav", { type: "audio/wav" }));
            return;
          } catch {
            // fall back to raw blob if decode fails
          } finally {
            decodeCtx.close().catch(() => {});
          }
        }
        const ext = blobType.includes("ogg") ? "ogg" : "webm";
        setSelectedFile(new File([rawBlob], `mic-recording.${ext}`, { type: blobType }));
      };

      activeRecState = {
        stop: async () => {
          if (mediaRecorder.state === "recording") {
            mediaRecorder.stop();
          }
        },
      };

      mediaRecorder.start(250);
      recordStartMs = Date.now();
      recordTimer.textContent = "00:00";
      recordTimer.classList.remove("hidden");
      recordBtn.classList.add("recording");
      recordBtnLabel.textContent = "Stop Recording";
      recordTimerId = setInterval(() => {
        recordTimer.textContent = formatSeconds((Date.now() - recordStartMs) / 1000);
      }, 250);
    } catch {
      stream.getTracks().forEach((t) => t.stop());
      showError("Unable to start microphone recording.");
    }
  });

  copySummaryBtn.addEventListener("click", async () => {
    if (!latestReport) return;
    try {
      await navigator.clipboard.writeText(buildPlainSummary(latestReport));
      copyFeedback.textContent = "Copied to clipboard.";
    } catch {
      copyFeedback.textContent = "Clipboard access unavailable.";
    }
  });

  printBtn.addEventListener("click", () => {
    window.print();
  });

  downloadJsonBtn.addEventListener("click", () => {
    if (!latestReport) return;
    const blob = new Blob([JSON.stringify(latestReport, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "voiceguard-report.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  });
})();
