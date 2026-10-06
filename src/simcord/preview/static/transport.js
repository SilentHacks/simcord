export function initTransport({ state, ui, addDiagnostic, renderDiagnostics, updateActionStatus, announceStatus, completeActionDrafts, composer, commandPicker }) {
  function recoverActionTransportDiagnostics(message = "The matching action receipt was observed; transport failure remains in history.") {
    const codes = new Set(["action-response-unavailable", "action-receipt-unavailable"]);
    state.localDiagnostics = state.localDiagnostics.map((item) => codes.has(item.code)
      && item.category === "transport" && item.state === "current"
      ? { ...item, state: "recovered", severity: "warning", complete: true, message }
      : item);
  }


  function noteTransportFailure(code, requestId = null, sequence = null) {
    state.transport.failures += 1;
    const uncertain = Boolean(requestId || state.transport.uncertainRequestId);
    state.transport.state = uncertain ? "uncertain" : "error";
    if (requestId && !state.transport.uncertainRequestId) {
      state.transport.uncertainRequestId = requestId;
      state.transport.uncertainSequence = Number.isInteger(sequence) ? sequence : null;
    }
    state.transport.history.push({ state: state.transport.state, code, at: Date.now() });
    state.transport.history = state.transport.history.slice(-20);
    addDiagnostic({
      id: `local:transport:${code}`,
      code,
      category: "transport",
      severity: "error",
      state: "current",
      complete: false,
      message: "A preview network request did not complete.",
      remediation: "Wait for a successful state refresh before continuing.",
    });
    renderTransportStatus();
    updateActionStatus();
    announceStatus(uncertain ? "Action outcome is uncertain; it will not be retried." : "Connection lost; polling will continue.");
  }

  function noteTransportHealthy() {
    if (state.transport.state === "error") {
      state.transport.recoveries += 1;
      state.transport.state = "recovered";
      state.transport.history.push({ state: "recovered", code: "transport-restored", at: Date.now() });
      const recovered = new Set(["state-poll-unavailable", "state-refresh-unavailable", "bootstrap-failed"]);
      state.localDiagnostics = state.localDiagnostics.map((item) => item.category === "transport"
        && recovered.has(item.code) && item.state === "current"
        ? { ...item, state: "recovered", severity: "warning", complete: true, message: "Transport recovered; failure remains in history." }
        : item);
      renderDiagnostics();
    } else if (state.transport.state === "idle") {
      state.transport.state = "healthy";
    }
    renderTransportStatus();
    state.transport.lastSuccessfulRead = new Date().toISOString();
    if (state.transport.state === "recovered") announceStatus("Connection restored.");
    updateActionStatus();
  }

  function renderTransportStatus() {
    if (!ui.transportStatus) return;
    const stateText = {
      idle: "Transport has not checked in yet.",
      healthy: "Transport is healthy.",
      recovered: "Transport recovered; previous failure remains in history.",
      error: "Transport unavailable. The preview will continue polling.",
      uncertain: "Action outcome is uncertain. It will not be retried.",
    }[state.transport.state];
    ui.transportStatus.replaceChildren(Object.assign(document.createElement("p"), {
      textContent: `${stateText} Published snapshot; backend changes require Refresh. Publication: ${state.snapshot?.publication?.publishedAt || "unknown"}. Presentation time: ${state.profile.presentationTime || "unknown"}.`,
    }));
    if (state.transport.history.length) {
      const list = document.createElement("ol");
      state.transport.history.forEach((item) => {
        const row = document.createElement("li");
        row.textContent = `${item.state}: ${item.code}`;
        list.append(row);
      });
      ui.transportStatus.append(list);
    }
  }

  function authHeaders(context = state.contextId) {
    const headers = { "X-Simcord-Capability": state.capability };
    if (context) headers["X-Simcord-Context"] = context;
    return headers;
  }

  async function request(path, method = "GET", body, context = state.contextId) {
    const headers = { ...authHeaders(context) };
    const init = { method, headers, cache: "no-store" };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    const response = await fetch(path, init);
    if (!response.ok) throw new Error("preview request failed");
    return response.status === 204 ? null : response.json();
  }

  async function requestAction(body) {
    if (body.kind === "run_command") {
      const files = state.commandUploads || {};
      const entries = Object.entries(files).filter(([, file]) => file instanceof File);
      if (!entries.length) return request("/api/action", "POST", body);
      const form = new FormData();
      form.append("payload", JSON.stringify(body));
      entries.forEach(([name, file]) => form.append(`file:${name}`, file, file.name));
      const response = await fetch("/api/action", { method: "POST", headers: authHeaders(), body: form, cache: "no-store" });
      if (!response.ok) throw new Error("preview request failed");
      return response.json();
    }
    const values = body.values;
    const uploads = [];
    const payload = { ...body, values: { ...(values || {}) } };
    Object.entries(payload.values || {}).forEach(([id, value]) => {
      if (!Array.isArray(value) || !value.some((item) => item instanceof File)) return;
      payload.values[id] = [];
      value.forEach((item) => { if (item instanceof File) uploads.push([id, item]); });
    });
    if (!uploads.length) return request("/api/action", "POST", body);
    const form = new FormData();
    form.append("payload", JSON.stringify(payload));
    uploads.forEach(([id, file]) => form.append(`file:${id}`, file, file.name));
    const response = await fetch("/api/action", { method: "POST", headers: authHeaders(), body: form, cache: "no-store" });
    if (!response.ok) throw new Error("preview request failed");
    return response.json();
  }

  function receiptFor(snapshot, requestId) {
    if (!requestId) return null;
    return [snapshot?.lastAction, ...(snapshot?.activity || [])]
      .find((receipt) => receipt?.requestId === requestId) || null;
  }

  function reconcileUncertainAction(snapshot) {
    const requestId = state.transport.uncertainRequestId;
    const receipt = receiptFor(snapshot, requestId);
    const terminal = receipt?.rejected === true
      || (typeof receipt?.settlement === "string" && receipt.settlement !== "pending");
    if (receipt && terminal) {
      const action = state.uncertainAction;
      state.lastAction = receipt;
      if (action?.kind === "run_command") {
        commandPicker.receiveAction("run_command", receipt, action.commandEnvelope, { draftId: action.commandDraftId });
        state.commandUploads = {};
      }
      completeActionDrafts(action, receipt);
      if (action?.contextId === state.contextId && snapshot.context?.id === state.contextId) composer.update(snapshot);
      state.lastActionKind = action?.kind || state.lastActionKind;
      state.uncertainAction = null;
      if (Number.isInteger(receipt.expectedSequence)) state.sequence = receipt.expectedSequence;
      state.transport.uncertainRequestId = null;
      state.transport.uncertainSequence = null;
      state.transport.uncertainCloseAttemptFor = null;
      state.transport.state = "recovered";
      state.transport.recoveries += 1;
      state.transport.history.push({ state: "recovered", code: "action-receipt-observed", at: Date.now() });
      state.transport.history = state.transport.history.slice(-20);
      recoverActionTransportDiagnostics();
      renderTransportStatus();
      updateActionStatus();
      renderDiagnostics();
    }
    if (Number.isInteger(state.awaitingRevision) && snapshot.publishedRevision >= state.awaitingRevision) {
      state.awaitingRevision = null;
      state.queryResultRevision = null;
    }
  }
  return { recoverActionTransportDiagnostics, noteTransportFailure, noteTransportHealthy, renderTransportStatus, authHeaders, request, requestAction, reconcileUncertainAction };
}
