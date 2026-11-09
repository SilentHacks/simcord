/** @import {Diagnostic, TransportReport} from "./preview-types" */
/** Connection health/history, separate from HTTP and admitted action arbitration.
 * @param {{element: HTMLElement | null, changed: (report: Readonly<TransportReport>) => void,
 * addDiagnostic: (item: Diagnostic) => void, recoverDiagnostics: (codes: Set<string>, message: string) => void,
 * announceStatus: (message: string) => void}} options
 */
export function initConnection({ element, changed, addDiagnostic, recoverDiagnostics, announceStatus }) {
  /** @type {Omit<TransportReport, "history"> & {history: {state: string, code: string, at: number}[]}} */
  const state = { state: "idle", failures: 0, recoveries: 0, uncertainRequestId: null, uncertainSequence: null, history: [], lastSuccessfulRead: null };
  let publicationTime = "unknown";
  let presentationTime = "unknown";
  function report() { return Object.freeze({ ...state, history: Object.freeze(state.history.map((item) => Object.freeze({ ...item }))) }); }
  function publish() { changed(report()); renderTransportStatus(); }
  /** @param {string} status @param {string} code */
  function record(status, code) {
    state.history.push({ state: status, code, at: Date.now() });
    state.history = state.history.slice(-20);
  }
  function recoverActionTransportDiagnostics(message = "The matching action receipt was observed; transport failure remains in history.") {
    recoverDiagnostics(new Set(["action-response-unavailable", "action-receipt-unavailable"]), message);
  }
  /** @param {string} code @param {string | null} [requestId] @param {number | null} [sequence] */
  function noteTransportFailure(code, requestId = null, sequence = null) {
    state.failures += 1;
    const uncertain = Boolean(requestId || state.uncertainRequestId);
    state.state = uncertain ? "uncertain" : "error";
    if (requestId && !state.uncertainRequestId) {
      state.uncertainRequestId = requestId;
      state.uncertainSequence = Number.isInteger(sequence) ? sequence : null;
    }
    record(state.state, code);
    addDiagnostic({ id: `local:transport:${code}`, code, category: "transport", severity: "error", state: "current", complete: false });
    publish();
    announceStatus(uncertain ? "Action outcome is uncertain; it will not be retried." : "Connection lost; polling will continue.");
  }
  function noteTransportHealthy() {
    if (state.state === "error") {
      state.recoveries += 1;
      state.state = "recovered";
      record("recovered", "transport-restored");
      recoverDiagnostics(new Set(["state-poll-unavailable", "state-refresh-unavailable", "bootstrap-failed"]), "Transport recovered; failure remains in history.");
    } else if (state.state === "idle") state.state = "healthy";
    state.lastSuccessfulRead = new Date().toISOString();
    publish();
    if (state.state === "recovered") announceStatus("Connection restored.");
  }
  /** @param {boolean} own */
  function recovered(own) {
    state.uncertainRequestId = null; state.uncertainSequence = null;
    state.state = "recovered"; state.recoveries += 1;
    record("recovered", own ? "action-receipt-observed" : "action-not-admitted-by-close");
    recoverActionTransportDiagnostics();
    publish();
  }
  function renderTransportStatus() {
    if (!element) return;
    const text = /** @type {Record<string, string>} */ ({ idle: "Transport has not checked in yet.", healthy: "Transport is healthy.",
      recovered: "Transport recovered; previous failure remains in history.", error: "Transport unavailable. The preview will continue polling.",
      uncertain: "Action outcome is uncertain. It will not be retried." })[state.state];
    element.replaceChildren(Object.assign(document.createElement("p"), {
      textContent: `${text} Published snapshot; backend changes require Refresh. Publication: ${publicationTime}. Presentation time: ${presentationTime}.`,
    }));
    if (state.history.length) {
      const list = document.createElement("ol");
      for (const item of state.history) list.append(Object.assign(document.createElement("li"), { textContent: `${item.state}: ${item.code}` }));
      element.append(list);
    }
  }
  return { noteTransportFailure, noteTransportHealthy, renderTransportStatus, recovered, report,
    /** @param {string | undefined} publication @param {string | null | undefined} presentation */
    update: (publication, presentation) => { publicationTime = publication || "unknown"; presentationTime = presentation || "unknown"; },
  };
}
