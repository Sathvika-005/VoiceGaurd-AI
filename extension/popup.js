(() => {
  "use strict";

  const backendUrlInput = document.getElementById("backend-url-input");
  const defaultBackendUrl = globalThis.VOICEGUARD_CONFIG.backendUrl;
  backendUrlInput.value = defaultBackendUrl;
  const langSelect = document.getElementById("language-select");
  const screenTabBtn = document.getElementById("screen-tab-btn");
  const captureTimer = document.getElementById("capture-timer");
  const tabStatusText = document.getElementById("tab-status-text");

  const stepperCard = document.getElementById("stepper-card");
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
  const threatLevelText = document.getElementById("threat-level-text");
  const threatHeadline = document.getElementById("threat-headline");

  const aletheiaTiming = document.getElementById("aletheia-timing");
  const aletheiaBody = document.getElementById("aletheia-body");
  const geminiTiming = document.getElementById("gemini-timing");
  const geminiBody = document.getElementById("gemini-body");
  const fusionCard = document.getElementById("fusion-card");
  const fusionReasons = document.getElementById("fusion-reasons");
  const fusionActions = document.getElementById("fusion-actions");

  let currentStatus = "idle";
  let timerInterval = null;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clearChildren(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function formatSeconds(sec) {
    const total = Math.max(0, Math.round(sec));
    const m = Math.floor(total / 60);
    const s = total % 60;
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }

  function addKV(grid, label, value) {
    grid.appendChild(el("div", "kv-label", label));
    grid.appendChild(el("div", "kv-value", value));
  }

  function renderAletheia(data) {
    clearChildren(aletheiaBody);
    if (!data) {
      aletheiaTiming.textContent = "—";
      aletheiaBody.appendChild(el("p", "summary-text", "Running DetectifAI Aletheia..."));
      return;
    }
    aletheiaTiming.textContent = `${data.elapsed_ms ?? 0} ms`;
    const grid = el("div", "kv-grid");
    if (data.status === "ok") {
      addKV(grid, "State", data.state);
      addKV(grid, "Verdict", data.raw_verdict || "—");
      if (typeof data.score === "number") {
        addKV(grid, "Score", `${data.score.toFixed(3)} (0=genuine, 1=synthetic)`);
      }
      if (Array.isArray(data.flagged_ranges) && data.flagged_ranges.length) {
        addKV(grid, "Flagged", data.flagged_ranges.join(", "));
      }
    } else {
      addKV(grid, "Status", "Unavailable");
      addKV(grid, "Reason", data.error_message || "Service unavailable");
    }
    aletheiaBody.appendChild(grid);
  }

  function renderGemini(data) {
    clearChildren(geminiBody);
    if (!data) {
      geminiTiming.textContent = "—";
      geminiBody.appendChild(el("p", "summary-text", "Running Gemini Native Audio..."));
      return;
    }
    geminiTiming.textContent = `${data.elapsed_ms ?? 0} ms`;
    const grid = el("div", "kv-grid");
    if (data.status !== "ok" || !data.analysis) {
      addKV(grid, "Status", "Unavailable");
      addKV(grid, "Reason", data.error_message || "Service unavailable");
      geminiBody.appendChild(grid);
      return;
    }

    const a = data.analysis;
    addKV(grid, "Risk Level", a.risk_level || "—");
    addKV(grid, "Confidence", a.confidence || "—");
    addKV(grid, "Language", a.language_detected || "—");
    geminiBody.appendChild(grid);

    if (a.summary) {
      geminiBody.appendChild(el("p", "summary-text", a.summary));
    }
    if (Array.isArray(a.indicators) && a.indicators.length) {
      const ul = el("ul", "bullet-list");
      a.indicators.slice(0, 3).forEach((ind) => {
        const cat = String(ind.category || "other").replace(/_/g, " ");
        ul.appendChild(el("li", "", `${cat}: “${ind.evidence || ""}”`));
      });
      geminiBody.appendChild(ul);
    }
  }

  function renderState(state) {
    if (!state) return;
    currentStatus = state.status || "idle";

    if (state.backendUrl && document.activeElement !== backendUrlInput) {
      backendUrlInput.value = state.backendUrl;
    }
    if (state.language && document.activeElement !== langSelect) {
      langSelect.value = state.language;
    }

    if (timerInterval) {
      clearInterval(timerInterval);
      timerInterval = null;
    }

    if (currentStatus === "capturing") {
      screenTabBtn.textContent = "Stop & Analyse Tab Audio";
      screenTabBtn.classList.add("capturing");
      captureTimer.classList.remove("hidden");
      const updateTimer = () => {
        const elapsed = (Date.now() - (state.captureStartedAt || Date.now())) / 1000;
        captureTimer.textContent = formatSeconds(elapsed);
      };
      updateTimer();
      timerInterval = setInterval(updateTimer, 250);
      tabStatusText.textContent = `Capturing live audio from: ${state.tabTitle || "Active Tab"}`;
    } else {
      screenTabBtn.textContent = "Screen Active Tab Audio";
      screenTabBtn.classList.remove("capturing");
      captureTimer.classList.add("hidden");
      if (currentStatus === "analyzing") {
        tabStatusText.textContent = "Running Aletheia ∥ Gemini...";
      } else if (currentStatus === "done") {
        tabStatusText.textContent = `Screened ${state.durationSeconds || 0} s from ${state.tabTitle || "tab"}.`;
      } else if (currentStatus === "error") {
        tabStatusText.textContent = "Screening stopped with an error.";
      } else {
        tabStatusText.textContent = "Ready to capture audio playing in the active tab (minimum 4 s).";
      }
    }

    const showStepper = currentStatus !== "idle";
    stepperCard.classList.toggle("hidden", !showStepper);
    if (state.steps) {
      stepValidate.dataset.state = state.steps.validate?.state || "idle";
      stepValidateMeta.textContent = state.steps.validate?.meta || "Waiting";
      stepEngines.dataset.state = state.steps.engines?.state || "idle";
      stepEnginesMeta.textContent = state.steps.engines?.meta || "Parallel engines";
      stepFusion.dataset.state = state.steps.fusion?.state || "idle";
      stepFusionMeta.textContent = state.steps.fusion?.meta || "Waiting";
    }

    if (state.error) {
      errorMessage.textContent = state.error;
      errorBanner.classList.remove("hidden");
    } else {
      errorBanner.classList.add("hidden");
    }

    const showResults =
      currentStatus === "analyzing" || currentStatus === "done" || Boolean(state.report);
    resultsArea.classList.toggle("hidden", !showResults);

    if (showResults) {
      renderAletheia(state.aletheia);
      renderGemini(state.gemini);

      if (state.report && state.report.fusion) {
        const fu = state.report.fusion;
        threatBanner.dataset.level = fu.threat_level || "UNDETERMINED";
        threatLevelText.textContent = fu.threat_level || "UNDETERMINED";
        threatHeadline.textContent = fu.headline || "Assessment complete";

        clearChildren(fusionReasons);
        (fu.reasons || []).forEach((r) => fusionReasons.appendChild(el("li", "", r)));
        clearChildren(fusionActions);
        (fu.actions || []).forEach((a) => fusionActions.appendChild(el("li", "", a)));
        fusionCard.classList.remove("hidden");
      } else {
        threatBanner.dataset.level = "UNDETERMINED";
        threatLevelText.textContent = "SCREENING";
        threatHeadline.textContent = "Running VoiceGuard dual-engine analysis...";
        fusionCard.classList.add("hidden");
      }
    }
  }

  async function ensureHostPermission(urlString) {
    try {
      const origin = new URL(urlString).origin + "/*";
      const has = await chrome.permissions.contains({ origins: [origin] });
      if (!has) {
        await chrome.permissions.request({ origins: [origin] });
      }
    } catch {
      // Ignore if already covered or invalid URL (background fetch reports clear error)
    }
  }

  screenTabBtn.addEventListener("click", async () => {
    const backendUrl = backendUrlInput.value.trim() || defaultBackendUrl;
    const language = langSelect.value || "en";

    if (currentStatus === "capturing") {
      chrome.runtime.sendMessage({ type: "STOP_TAB_CAPTURE" });
      return;
    }

    await ensureHostPermission(backendUrl);
    chrome.runtime.sendMessage(
      { type: "START_TAB_CAPTURE", backendUrl, language },
      (resp) => {
        if (resp && resp.state) renderState(resp.state);
      }
    );
  });

  const persistSettings = () => {
    chrome.runtime.sendMessage({
      type: "UPDATE_SETTINGS",
      backendUrl: backendUrlInput.value.trim(),
      language: langSelect.value,
    });
  };
  backendUrlInput.addEventListener("change", persistSettings);
  langSelect.addEventListener("change", persistSettings);

  chrome.runtime.onMessage.addListener((message) => {
    if (message && message.type === "VG_STATE_UPDATE") {
      renderState(message.state);
    }
  });

  chrome.runtime.sendMessage({ type: "GET_STATUS" }, (resp) => {
    if (resp && resp.ok && resp.state) {
      renderState(resp.state);
    }
  });
})();
