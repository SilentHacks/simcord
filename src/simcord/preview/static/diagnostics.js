/** @import {Snapshot, Diagnostic} from "./preview-types" */
/** @import {PreviewUI} from "./app" */
/** Sanitized diagnostics and inspector rendering; never stores private backend text.
 * @param {{ui: Pick<PreviewUI, "inspectorCount" | "inspectorTabCount" | "inspectorToggle" | "diagnostics" | "diagnosticFilter">,
 * announceStatus: (message: string, urgent: boolean) => void, viewActivityMessage: (id: string) => void,
 * changed: (report: {diagnostics: readonly Diagnostic[], complete: boolean}) => void}} options
 */
export function createDiagnostics({ ui, announceStatus, viewActivityMessage, changed }) {
  /** @type {{snapshot: Snapshot | null, publishedRevision: number, closed: boolean, complete: boolean,
   * localDiagnostics: Diagnostic[], diagnostics: Diagnostic[], diagnosticFilter: string}} */
  const state = { snapshot: null, publishedRevision: 0, closed: false, complete: true,
    localDiagnostics: [], diagnostics: [], diagnosticFilter: "all" };
/** @type {Record<string, [string, string, string]>} */
const LOCAL_DIAGNOSTICS = {
  "preview-render": ["rendering", "Preview rendering is incomplete.", "Inspect supported components and offline assets."],
  "preview-fonts": ["rendering", "Packaged fonts or assets could not be rendered.", "Reinstall simcord[preview] and reload."],
  "modal-validation": ["validation", "Correct the highlighted modal field.", "Review the associated field guidance; your draft is retained."],
  "viewport-invalid": ["presentation", "Viewport dimensions are invalid.", "Use bounded dimensions within the capture raster limit."],
  "missing-capability": ["authorization", "This page has no preview capability.", "Open the active Preview URL."],
  "access-denied": ["authorization", "Preview access was revoked.", "Restore authorization and explicitly refresh."],
  "bootstrap-failed": ["transport", "The authorized page could not be opened.", "Reload the active Preview URL."],
  "state-poll-unavailable": ["transport", "A preview read failed.", "Wait for a healthy read; actions are not retried."],
  "state-refresh-unavailable": ["transport", "The snapshot read failed after an action.", "Wait for a healthy read before another action."],
  "action-response-unavailable": ["transport", "The action outcome is uncertain.", "Inspect the action receipt; do not automatically retry."],
  "action-receipt-unavailable": ["transport", "The action receipt could not be verified.", "Inspect the page before another action."],
  "premium-sku-icon-unavailable": ["rendering", "The offline premium icon is unavailable.", "Supply the icon's offline asset."],
  "invalid-link": ["rendering", "A component link is unavailable.", "Use an absolute safe HTTP(S) URL."],
  "unsupported-component": ["rendering", "This component is not supported.", "Review the supported-component contract."],
  "unsupported-modal-component": ["rendering", "This modal component is not supported.", "Review the supported-modal contract."],
  "media-rejected": ["rendering", "An offline media asset was rejected.", "Inspect private asset diagnostics and supported limits."],
  "media-unavailable": ["rendering", "An offline media asset is unavailable.", "Supply the required offline media asset."],
  "unsupported-media-type": ["rendering", "This media type cannot be rendered.", "Use a supported offline media format."],
  "media-process-memory-limit-unavailable": ["rendering", "The platform media-process memory ceiling is unavailable.", "Use a supported Linux runtime for adversarial-media checks."],
  "sticker-asset-unavailable": ["rendering", "A sticker asset is unavailable.", "Supply the required offline sticker asset."],
};
/** @type {Record<string, [string, string, boolean, string, string]>} */
let backendDiagnostics = {};
/** @param {Diagnostic} diagnostic */
function addDiagnostic(diagnostic) {
  const severity = ["info", "warning", "error"].includes(diagnostic.severity || "") ? diagnostic.severity : "warning";
  const code = typeof diagnostic.code === "string" ? diagnostic.code : "preview-render";
  const known = Object.hasOwn(LOCAL_DIAGNOSTICS, code) || Object.hasOwn(backendDiagnostics, code)
    ? code : "preview-render";
  const catalog = Object.hasOwn(LOCAL_DIAGNOSTICS, known) ? LOCAL_DIAGNOSTICS[known] : null;
  const backend = Object.hasOwn(backendDiagnostics, known) ? backendDiagnostics[known] : null;
  const item = {
    id: diagnostic.id || `local:${code}`, code,
    category: catalog?.[0] || backend?.[0], severity: backend?.[1] || severity,
    state: diagnostic.state === "recovered" ? "recovered" : "current",
    complete: backend ? backend[2] : diagnostic.complete !== false,
    message: catalog?.[1] || backend?.[3],
    remediation: catalog?.[2] || backend?.[4],
    ...(diagnostic.renderOwner ? { renderOwner: diagnostic.renderOwner } : {}),
  };
  state.localDiagnostics = state.localDiagnostics.filter((entry) => entry.id !== item.id);
  state.localDiagnostics.push(item);
  // Active failures belong to the current surface, not bounded diagnostic history.
  const history = state.localDiagnostics.filter((entry) =>
    entry.state === "recovered" || entry.complete !== false).slice(-20);
  state.localDiagnostics = state.localDiagnostics.filter((entry) =>
    (entry.state !== "recovered" && entry.complete === false) || history.includes(entry));
  renderDiagnostics();
}

/** @param {string | null} owner */
function clearRenderDiagnostics(owner) {
  if (!owner) return;
  const retained = state.localDiagnostics.filter((item) => item.renderOwner !== owner);
  if (retained.length === state.localDiagnostics.length) return;
  state.localDiagnostics = retained;
  renderDiagnostics();
}
/** @param {Diagnostic} item @returns {Diagnostic} */
function backendDiagnostic(item) {
  const code = typeof item.code === "string" ? item.code : "internal-error";
  const approved = Object.hasOwn(backendDiagnostics, code) ? backendDiagnostics[code] : null;
  const recovered = item.state === "recovered";
  const { messageId, controlKey, commandOption } = item.subject || {};
  /** @type {Diagnostic["subject"]} */
  let subject;
  if (typeof messageId === "string" && messageId.length >= 1 && messageId.length <= 20
    && !/[^0-9]/.test(messageId)) {
    subject = { messageId };
    if (typeof controlKey === "string" && [...controlKey].length <= 160) subject.controlKey = controlKey;
  } else if (typeof commandOption === "string" && /^[\p{L}\p{N}\p{M}_'-]{1,32}$/u.exec(commandOption)?.[0] === commandOption) {
    subject = { commandOption };
  }
  return {
    id: typeof item.id === "string" ? item.id : `backend:${code}`, code,
    category: approved?.[0] || "rendering",
    severity: approved?.[1] || (["error", "warning", "info"].includes(item.severity || "") ? item.severity : "warning"),
    complete: recovered || (approved?.[2] ?? false), message: approved?.[3] || "Preview diagnostics are unavailable.",
    remediation: approved?.[4] || "Reload the active Preview URL.",
    state: recovered ? "recovered" : "current",
    ...(subject ? { subject } : {}),
    ...(item.correlation ? { correlation: item.correlation } : {}),
  };
}
function renderDiagnostics() {
  const previous = state.diagnostics;
  const diagnostics = [...[...(state.snapshot?.diagnostics || []),
    ...(state.snapshot?.activity || []).flatMap((receipt) => receipt.diagnostics || [])]
    .map(backendDiagnostic), ...state.localDiagnostics];
  const seen = new Set();
  state.diagnostics = diagnostics.filter((item) => {
    const key = item.id || JSON.stringify(item);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  const count = state.diagnostics.filter((item) =>
    ["error", "warning"].includes(item.severity || "") && item.state !== "recovered").length;
  for (const badge of [ui.inspectorCount, ui.inspectorTabCount]) {
    badge.hidden = count === 0;
    badge.textContent = count ? String(count) : "";
  }
  ui.inspectorToggle.setAttribute("aria-label", count
    ? `Inspector, ${count} active warning${count === 1 ? "" : "s"} or error${count === 1 ? "" : "s"}`
    : "Inspector");
  const error = state.diagnostics.find((item) => item.severity === "error" && item.state !== "recovered"
    && item.code !== "modal-validation"
    && !previous.some((old) => old.id === item.id && old.state !== "recovered"));
  if (error) {
    announceStatus(error.message || "Preview error.", true);
  }
  if (!state.closed) state.complete = !state.diagnostics.some((item) => item.state !== "recovered" && item.complete === false);
  changed(Object.freeze({ diagnostics: Object.freeze(state.diagnostics.map((item) => Object.freeze({ ...item }))), complete: state.complete }));
  ui.diagnostics.replaceChildren();
  const visible = state.diagnostics.filter((item) =>
    state.diagnosticFilter === "all" || item.severity === state.diagnosticFilter);
  if (!visible.length) {
    ui.diagnostics.append(Object.assign(document.createElement("p"), {
      textContent: state.diagnostics.length ? "No diagnostics match this filter." : "No warnings or errors.",
    }));
    return;
  }
  const list = document.createElement("ul");
  visible.forEach((item) => {
    const row = document.createElement("li");
    row.className = `diagnostic-${item.severity || "warning"}`;
    row.append(Object.assign(document.createElement("p"), {
      className: "diagnostic-message",
      textContent: item.message || "Preview diagnostic.",
    }));
    if (item.remediation) {
      row.append(Object.assign(document.createElement("p"), {
        className: "diagnostic-remediation",
        textContent: item.remediation,
      }));
    }
    if (item.subject?.messageId) {
      const view = Object.assign(document.createElement("button"), { type: "button", textContent: "View affected message" });
      const messageId = item.subject.messageId;
      view.addEventListener("click", () => viewActivityMessage(messageId));
      row.append(view);
    }
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "Technical details";
    details.append(summary, Object.assign(document.createElement("p"), {
      textContent: `${item.code} · ${item.category} · ${item.severity} · ${item.state} · revision ${state.publishedRevision}`,
    }));
    row.append(details);
    list.append(row);
  });
  ui.diagnostics.append(list);
}
  ui.diagnosticFilter.addEventListener("change", () => {
    state.diagnosticFilter = ui.diagnosticFilter.value; renderDiagnostics();
  });
  return {
    add: addDiagnostic, clearRender: clearRenderDiagnostics,
    /** @param {Snapshot | null} snapshot @param {number} revision @param {boolean} closed */
    update: (snapshot, revision, closed) => { state.snapshot = snapshot; state.publishedRevision = revision; state.closed = closed; renderDiagnostics(); },
    /** @param {string} code */
    clear: (code) => { state.localDiagnostics = state.localDiagnostics.filter((item) => item.code !== code); },
    reset: () => { state.localDiagnostics = []; state.diagnostics = []; state.snapshot = null; },
    /** @param {Diagnostic[]} items */
    replace: (items) => { state.localDiagnostics = items; renderDiagnostics(); },
    /** @param {Set<string>} codes @param {string} message */
    recover: (codes, message) => {
      state.localDiagnostics = state.localDiagnostics.map((item) => codes.has(item.code) && item.category === "transport" && item.state === "current"
        ? { ...item, state: "recovered", severity: "warning", complete: true, message } : item);
      renderDiagnostics();
    },
    /** @param {unknown} value */
    catalog: (value) => {
      if (!value || typeof value !== "object" || Array.isArray(value)
        || Object.values(value).some((entry) => !Array.isArray(entry) || entry.length !== 5
          || typeof entry[0] !== "string" || !["info", "warning", "error"].includes(entry[1])
          || typeof entry[2] !== "boolean" || typeof entry[3] !== "string" || typeof entry[4] !== "string")) throw new Error("Invalid preview diagnostic metadata");
      backendDiagnostics = /** @type {Record<string, [string, string, boolean, string, string]>} */ (value);
    },
    support: () => state.diagnostics.slice(-40).map((item) => ({
      code: Object.hasOwn(backendDiagnostics, item.code) || Object.hasOwn(LOCAL_DIAGNOSTICS, item.code) ? item.code : "internal-error",
      severity: ["error", "warning", "info"].includes(item.severity || "") ? item.severity : "warning",
      state: item.state === "recovered" ? "recovered" : "current",
      remediation: Object.hasOwn(LOCAL_DIAGNOSTICS, item.code) ? LOCAL_DIAGNOSTICS[item.code][2]
        : Object.hasOwn(backendDiagnostics, item.code) ? backendDiagnostics[item.code][4]
        : "Inspect authorized local diagnostics and Env.errors; correct the issue before continuing.",
      ...((state.snapshot?.activity || []).some((receipt) => receipt.correlation === item.correlation)
        && /^c_[A-Za-z0-9_-]{8,64}$/.test(item.correlation || "") ? { correlation: item.correlation } : {}),
    })),
  };
}
