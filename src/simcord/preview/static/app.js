import { renderModal } from "./components.js";
import { renderMessage } from "./messages.js";
import { closeLightbox } from "./media.js";

const $ = (id) => document.getElementById(id);
const ui = {
  app: $("preview-app"),
  workspace: $("preview-workspace"),
  stage: $("preview-stage"),
  inspector: $("inspector"),
  toolbar: $("toolbar"),
  surface: $("focused-content"),
  modal: $("modal-root"),
  diagnostics: $("diagnostics"),
  panel: $("inspector-panel"),
  capturePanel: $("capture-panel"),
  captureRecipe: $("capture-recipe"),
  captureStatus: $("capture-status"),
  copyRecipe: $("copy-capture-recipe"),
  copyReport: $("copy-support-report"),
  activity: $("activity-list"),
  liveStatus: $("live-status"),
  downloadReport: $("download-support-report"),
  diagnosticFilter: $("diagnostic-filter"),
  transportStatus: $("transport-status"),
  empty: $("message-picker-empty"),
  channel: $("channel-layout"),
  channelName: $("channel-name"),
  channelTopic: $("channel-topic"),
  timeline: $("channel-timeline"),
  messageList: $("channel-message-list"),
  channelEmpty: $("channel-empty"),
  composerForm: $("channel-composer"),
  composer: $("channel-composer-input"),
  send: $("send-message"),
  replyContext: $("reply-context"),
  replyLabel: $("reply-label"),
  replyCancel: $("reply-cancel"),
  historyOlder: $("history-older"),
  historyNewer: $("history-newer"),
  newMessages: $("new-messages"),
  viewer: $("viewer-picker"),
  message: $("message-picker"),
  searchForm: $("message-search-form"),
  search: $("message-search"),
  previous: $("message-previous"),
  next: $("message-next"),
  pageStatus: $("message-page-status"),
  exactForm: $("message-exact-form"),
  exactId: $("message-exact-id"),
  display: $("display-mode"),
  layout: $("layout-mode"),
  preset: $("viewport-preset"),
  width: $("viewport-width"),
  height: $("viewport-height"),
  viewportReadout: $("viewport-readout"),
  useAvailable: $("use-available"),
  resetProfile: $("reset-profile"),
  captureCurrent: $("capture-current"),
  refresh: $("refresh"),
  close: $("close"),
  backActivity: $("back-activity"),
  selectActions: $("select-draft-actions"),
  selectApply: $("select-apply"),
  selectCancel: $("select-cancel"),
  action: $("action-status"),
};

const hash = window.location.hash.startsWith("#") ? window.location.hash.slice(1) : "";
const state = {
  capability: hash,
  snapshot: null,
  protocolCompatible: true,
  contextId: null,
  contextGeneration: 0,
  botGeneration: 0,
  publishedRevision: 0,
  renderGeneration: 0,
  ready: false,
  complete: true,
  diagnostics: [],
  localDiagnostics: [],
  lastAction: null,
  externalNotice: null,
  pendingAction: null,
  pendingQuery: null,
  queuedQuery: null,
  queuedQueries: new Map(),
  queuedPageIntents: {},
  queryIntent: 0,
  queryIntents: new Map(),
  observedRevision: 0,
  selectQueryTimers: new Map(),
  sequence: 0,
  profile: { theme: "dark", width: 960, height: 720, locale: "en-US", timezone: "UTC", deviceScale: 1, reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches, mediaTime: null },
  calibration: { status: "uncalibrated", reason: "No legitimate Discord reference fixture is bundled for this slice" },
  initialPresentation: null,
  host: { width: 0, height: 0 },
  awaitingRevision: null,
  queryResultRevision: null,
  drafts: new Map(),
  selectDrafts: new Map(),
  selectStatuses: new Map(),
  candidateIdentities: new Map(),
  candidateQueries: new Map(),
  selectValidationRevisions: new Map(),
  pendingSelectValidations: new Map(),
  selectVerifiedValues: new Map(),
  modalDrafts: new Map(),
  modalTouched: new Set(),
  modalHandle: null,
  dismissedModal: null,
  modalError: null,
  modalErrorHandle: null,
  dropdown: null,
  candidateFingerprints: new Map(),
  lastMessageKey: null,
  lastMessageFingerprint: "",
  pendingMedia: [],
  targetId: null,
  objectUrls: new Map(),
  assetLoads: new Map(),
  assetEpoch: 0,
  spoilerState: new Set(),
  spoilerContext: null,
  messageNodes: new Map(),
  dayNodes: new Map(),
  replyToId: null,
  editTargetId: null,
  pollDrafts: new Map(),
  closed: false,
  authorized: true,
  pinnedCapture: false,
  modalOpenerFocusKey: null,
  focusKey: null,
  focusSelection: null,
  focusInModal: false,
  contextReleased: false,
  statusFingerprint: "",
  fontStatus: { loaded: [], missing: [], faces: [] },
  mediaCaptureTimes: {},
  mediaMetadata: {},
  transport: { state: "idle", failures: 0, recoveries: 0, uncertainRequestId: null, uncertainSequence: null, uncertainCloseAttemptFor: null, history: [] },
  activityBackStack: [],
  activityReturnKey: null,
  diagnosticFilter: "all",
  lastCaptureRecipe: "",
  captureViewport: null,
  resizeObserver: null,
  scrollIntent: null,
  scrollObserver: null,
  scrollCorrection: null,
  lastAnnouncement: "",
};

function clone(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value));
}
function freeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.values(value).forEach(freeze);
  return Object.freeze(value);
}

function ownerGeometry(element) {
  if (!element) return null;
  const rect = element.getBoundingClientRect();
  const app = ui.app.getBoundingClientRect(), stage = ui.stage.getBoundingClientRect();
  const x = Math.max(rect.left, app.left, stage.left, 0);
  const y = Math.max(rect.top, app.top, stage.top, 0);
  const right = Math.min(rect.right, app.right, stage.right, innerWidth);
  const bottom = Math.min(rect.bottom, app.bottom, stage.bottom, innerHeight);
  return {
    rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
    viewport: { width: element.clientWidth, height: element.clientHeight },
    contentExtent: { width: element.scrollWidth, height: element.scrollHeight },
    visibleCrop: { x, y, width: Math.max(0, right - x), height: Math.max(0, bottom - y) },
    scrollOffset: { x: element.scrollLeft, y: element.scrollTop },
    overflow: { horizontal: element.scrollWidth > element.clientWidth, vertical: element.scrollHeight > element.clientHeight },
    visible: element.checkVisibility(),
  };
}

function visibleMessageIds() {
  if (!state.authorized || state.modalHandle) return [];
  if (state.snapshot?.layout !== "channel") {
    return state.targetId && ui.surface.checkVisibility() ? [String(state.targetId)] : [];
  }
  const crop = ownerGeometry(ui.timeline).visibleCrop;
  if (!crop.width || !crop.height) return [];
  return [...state.messageNodes].filter(([, record]) => {
    const rect = record.element.getBoundingClientRect();
    return rect.bottom > crop.y && rect.top < crop.y + crop.height
      && rect.right > crop.x && rect.left < crop.x + crop.width;
  }).map(([id]) => id);
}

function selectStates() {
  return Object.fromEntries([...document.querySelectorAll(".preview-select")].map((element) => {
    const key = element.dataset.controlKey;
    const values = state.pendingSelectValidations.has(key) ? [] : [...(state.selectDrafts.get(key) || [])];
    const minimum = Number(element.dataset.minimum), maximum = Number(element.dataset.maximum);
    const optional = element.dataset.optional === "true";
    const touched = state.modalTouched.has(key);
    const receipt = [...(state.snapshot?.activity || [])].reverse().find((item) =>
      item.target?.controlKey === key && item.acknowledgement !== "not_applicable");
    return [key, {
      values, minimum, maximum, optional,
      valid: (optional && !touched && !values.length) || (values.length >= minimum && values.length <= maximum),
      editing: state.dropdown?.key === key, touched,
      pending: state.pendingAction?.controlKey === key,
      commit: receipt ? {
        requestId: receipt.requestId, sequence: receipt.sequence, dispatch: receipt.dispatch,
        settlement: receipt.settlement, uncertain: receipt.uncertain,
      } : null,
    }];
  }));
}

function statusObject() {
  const renderState = {
    openPopupKey: state.dropdown?.key || null,
    modalHandle: state.modalHandle,
    validationPaths: state.modalError ? [state.modalError.controlId] : [],
    mediaCaptureTimes: { ...state.mediaCaptureTimes },
    mediaMetadata: { ...state.mediaMetadata },
  };
  const value = {
    schemaVersion: 1,
    protocolVersion: 3,
    contextId: state.contextId,
    contextGeneration: state.contextGeneration,
    botGeneration: state.botGeneration,
    viewerId: state.viewerId || null,
    targetId: state.targetId,
    activeControlKey: state.authorized ? state.focusKey : null,
    visibleMessageIds: visibleMessageIds(),
    projectedMessageIds: [...(state.snapshot?.timeline || [])],
    publishedRevision: state.publishedRevision,
    publication: clone(state.snapshot?.publication || null),
    navigation: clone(state.snapshot?.navigation || null),
    presentation: clone(state.snapshot?.presentation || null),
    renderGeneration: state.renderGeneration,
    renderState,
    selectDrafts: Object.fromEntries([...state.selectDrafts].map(([key, values]) => [
      key, state.pendingSelectValidations.has(key) ? [] : [...values],
    ])),
    selectStates: selectStates(),
    lastAction: state.authorized ? clone(state.lastAction) : null,
    pendingAction: state.pendingAction && !state.authorized
      ? { ...clone(state.pendingAction), controlKey: null, targetId: null } : clone(state.pendingAction),
    pendingQuery: state.authorized ? clone(state.pendingQuery) : null,
    ready: state.ready,
    awaitingRevision: state.awaitingRevision,
    queryResultRevision: state.queryResultRevision,
    geometry: (() => {
      const rect = ui.app.getBoundingClientRect();
      return {
        constraint: state.snapshot?.presentation?.constraint || null,
        app: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
        host: getHostDimensions(),
        owners: {
          stage: ownerGeometry(ui.stage), focused: ownerGeometry(ui.surface),
          timeline: ownerGeometry(ui.timeline), modalBody: ownerGeometry(ui.modal.querySelector(".modal-body")),
        },
      };
    })(),
    complete: state.complete,
    diagnostics: clone(state.diagnostics),
    activity: clone(state.snapshot?.activity || []),
    transport: clone(state.transport),
    authorized: state.authorized,
    calibration: { ...state.calibration },
    profile: { ...state.profile, fontStatus: clone(state.fontStatus) },
  };
  return freeze(value);
}
Object.defineProperty(window, "simcordPreview", { configurable: false, enumerable: true, get: statusObject });

function rememberFocus() {
  const active = document.activeElement;
  const owner = active instanceof HTMLElement ? active.closest("[data-control-key]") : null;
  state.focusKey = owner?.dataset.controlKey || (active instanceof HTMLElement ? active.dataset.controlKey || null : null);
  state.focusInModal = Boolean(active instanceof HTMLElement && active.closest(".modal-dialog"));
  state.focusVisible = active instanceof HTMLElement
    && (active.matches(":focus-visible") || active.classList.contains("focus-visible"));
  state.focusSelection = active && typeof active.selectionStart === "number"
    ? { start: active.selectionStart, end: active.selectionEnd }
    : null;
}

function focusTarget(key) {
  if (!key) return null;
  const owner = [...document.querySelectorAll("[data-control-key]")].find(
    (item) => item.dataset.controlKey === key && item.tabIndex >= 0,
  ) || [...document.querySelectorAll("[data-control-key]")].find(
    (item) => item.dataset.controlKey === key,
  );
  if (!(owner instanceof HTMLElement)) return null;
  return owner.matches("input, textarea, select, button, [role=listbox]")
    ? owner
    : owner.querySelector("input, textarea, select, button, [role=listbox]") || owner;
}

function restoreFocus(key = state.focusKey) {
  let target = focusTarget(key);
  if (target?.getAttribute("role") === "listbox") {
    target = target.querySelector(".select-option.is-highlighted")
      || target.closest(".preview-select")?.querySelector(".select-trigger");
  }
  if (!(target instanceof HTMLElement) || target.inert || target.matches(":disabled") || !target.checkVisibility()) return false;
  target.focus({ preventScroll: true });
  if (state.focusSelection && typeof target.setSelectionRange === "function") {
    try { target.setSelectionRange(state.focusSelection.start, state.focusSelection.end); } catch (_) {}
  }
  if (state.focusVisible && !target.matches(":focus-visible")) {
    target.classList.add("focus-visible");
    target.addEventListener("blur", () => target.classList.remove("focus-visible"), { once: true });
  }
  return true;
}

function setModalIsolation(open) {
  [ui.channel, ui.empty, ui.surface, ui.inspector, ui.panel].forEach((element) => {
    if (element) element.inert = open;
  });
  ui.modal.setAttribute("aria-hidden", String(!open));
}

function revokeAssets() {
  closeLightbox();
  document.querySelectorAll(".media-player").forEach((wrapper) => wrapper._previewMediaPlayer?.dispose?.());
  state.assetEpoch += 1;
  state.assetLoads.clear();
  state.objectUrls.forEach((url) => URL.revokeObjectURL(url));
  state.objectUrls.clear();
  state.mediaCaptureTimes = {};
  state.mediaMetadata = {};
}

function pruneAssetUrls(assets) {
  for (const [key, url] of state.objectUrls) {
    const id = key.slice(0, key.indexOf(":"));
    if (assets[id]) continue;
    URL.revokeObjectURL(url);
    state.objectUrls.delete(key);
    delete state.mediaCaptureTimes[id];
    delete state.mediaMetadata[id];
  }
}

function beginRender() {
  closeLightbox();
  state.renderGeneration += 1;
  state.ready = false;
  ui.app.setAttribute("aria-busy", "true");
  return state.renderGeneration;
}

function nextFrames() {
  return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
}

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
const BACKEND_DIAGNOSTIC_CODES = new Set([
  "bad-envelope", "unsupported-protocol", "stale-sequence", "sequence-gap", "conflicting-request",
  "busy", "stale-context", "stale-generation", "stale-revision", "unknown-kind", "validation-failed",
  "query-invalid", "stale-cursor", "control-unavailable", "context-unavailable", "asset-unavailable",
  "access-denied", "action-callback-error", "action-timeout", "action-cancelled", "message-type-unknown",
  "premium-sku-metadata-missing", "sticker-asset-unavailable", "target-unavailable", "font-platform-inspection", "internal-error",
]);
function addDiagnostic(diagnostic) {
  const severity = ["info", "warning", "error"].includes(diagnostic.severity) ? diagnostic.severity : "warning";
  const code = Object.hasOwn(LOCAL_DIAGNOSTICS, diagnostic.code) ? diagnostic.code
    : BACKEND_DIAGNOSTIC_CODES.has(diagnostic.code) ? diagnostic.code : "preview-render";
  const catalog = LOCAL_DIAGNOSTICS[code];
  const item = {
    id: diagnostic.id || `local:${code}`, code,
    category: catalog?.[0] || diagnostic.category || "rendering", severity,
    state: diagnostic.state === "recovered" ? "recovered" : "current",
    complete: diagnostic.complete !== false,
    message: catalog?.[1] || diagnostic.message,
    remediation: catalog?.[2] || diagnostic.remediation,
    ...(diagnostic.renderOwner ? { renderOwner: diagnostic.renderOwner } : {}),
  };
  state.localDiagnostics = state.localDiagnostics.filter((entry) => entry.id !== item.id);
  state.localDiagnostics.push(item);
  state.localDiagnostics = state.localDiagnostics.slice(-20);
  renderDiagnostics();
}

function clearRenderDiagnostics(owner) {
  if (!owner) return;
  const retained = state.localDiagnostics.filter((item) => item.renderOwner !== owner);
  if (retained.length === state.localDiagnostics.length) return;
  state.localDiagnostics = retained;
  renderDiagnostics();
}

function recoverActionTransportDiagnostics(message = "The matching action receipt was observed; transport failure remains in history.") {
  const codes = new Set(["action-response-unavailable", "action-receipt-unavailable"]);
  state.localDiagnostics = state.localDiagnostics.map((item) => codes.has(item.code)
    && item.category === "transport" && item.state === "current"
    ? { ...item, state: "recovered", severity: "warning", complete: true, message }
    : item);
}

function renderDiagnostics() {
  const previous = state.diagnostics;
  const diagnostics = [...(state.snapshot?.diagnostics || []),
    ...(state.snapshot?.activity || []).flatMap((receipt) => receipt.diagnostics || []), ...state.localDiagnostics];
  const seen = new Set();
  state.diagnostics = diagnostics.filter((item) => {
    const key = item.id || JSON.stringify(item);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  const error = state.diagnostics.find((item) => item.severity === "error" && item.state !== "recovered"
    && item.code !== "modal-validation" // Its associated field already owns the alert.
    && !previous.some((old) => old.id === item.id && old.state !== "recovered"));
  if (error) {
    state.lastAnnouncement = null;
    announceStatus(error.message || "Preview error.", true);
  }
  state.complete = !state.diagnostics.some((item) => item.state !== "recovered" && item.complete === false);
  ui.diagnostics.replaceChildren();
  ui.diagnostics.hidden = !state.diagnostics.length;
  if (!state.diagnostics.length) return;
  const heading = document.createElement("h2");
  heading.textContent = "Diagnostics";
  ui.diagnostics.append(heading);
  const list = document.createElement("ul");
  state.diagnostics.filter((item) => state.diagnosticFilter === "all" || item.severity === state.diagnosticFilter).forEach((item) => {
    const row = document.createElement("li");
    row.className = `diagnostic-${item.severity || "warning"}`;
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = `${item.message || item.code} (${item.severity}, ${item.state})`;
    details.append(summary);
    details.append(Object.assign(document.createElement("p"), {
      textContent: `${item.code} · ${item.category} · revision ${state.publishedRevision}`,
    }));
    if (item.remediation) details.append(Object.assign(document.createElement("p"), { textContent: item.remediation }));
    if (item.subject?.messageId) {
      const view = Object.assign(document.createElement("button"), { type: "button", textContent: "View affected message" });
      view.addEventListener("click", () => viewActivityMessage(String(item.subject.messageId)));
      details.append(view);
    }
    row.append(details);
    list.append(row);
  });
  ui.diagnostics.append(list);
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
function announceStatus(message, urgent = false) {
  if (message === state.lastAnnouncement) return;
  state.lastAnnouncement = message;
  ui.liveStatus.setAttribute("role", urgent ? "alert" : "status");
  ui.liveStatus.setAttribute("aria-live", urgent ? "assertive" : "polite");
  ui.liveStatus.textContent = message;
}

const FONT_REQUIREMENTS = [
  { family: "Noto Sans", css: 'normal 16px "Noto Sans"', sample: "Discord Preview" },
  { family: "Noto Sans", css: 'italic 16px "Noto Sans"', sample: "Italic Preview" },
  { family: "Noto Sans Mono", css: 'normal 16px "Noto Sans Mono"', sample: "const x = 1;" },
  { family: "Noto Color Emoji", css: 'normal 16px "Noto Color Emoji"', sample: "👩🏽‍💻❤️‍🔥" },
  { family: "Noto Sans Arabic", css: 'normal 16px "Noto Sans Arabic"', sample: "مرحبا بالعالم" },
  { family: "Noto Sans Hebrew", css: 'normal 16px "Noto Sans Hebrew"', sample: "שלום עולם" },
  { family: "Noto Sans Devanagari", css: 'normal 16px "Noto Sans Devanagari"', sample: "नमस्ते दुनिया" },
  { family: "Noto Sans SC", css: 'normal 16px "Noto Sans SC"', sample: "你好世界" },
];
let requiredFontsPromise;

async function loadRequiredFonts() {
  if (!document.fonts?.load || !document.fonts?.check) {
    throw new Error("This browser does not expose the CSS Font Loading API.");
  }
  if (!requiredFontsPromise) {
    requiredFontsPromise = Promise.all(FONT_REQUIREMENTS.map(async (requirement) => {
      try {
        await document.fonts.load(requirement.css, requirement.sample);
        const loaded = document.fonts.check(requirement.css, requirement.sample);
        return { ...requirement, loaded };
      } catch {
        return { ...requirement, loaded: false, error: "font load failed" };
      }
    }));
  }
  const faces = await requiredFontsPromise;
  const missing = faces.filter((face) => !face.loaded).map((face) => ({
    family: face.family,
    sample: face.sample,
    error: face.error || "font face or certified glyphs are unavailable",
  }));
  state.fontStatus = {
    loaded: faces.filter((face) => face.loaded).map((face) => face.family),
    missing,
    faces: faces.map((face) => ({ family: face.family, sample: face.sample, loaded: face.loaded })),
  };
  if (missing.length) {
    const names = missing.map((face) => face.family).join(", ");
    throw new Error(`Packaged preview fonts failed to load: ${names}. Reinstall simcord[preview] and retry.`);
  }
  return faces;
}

async function waitReady(generation, pendingMedia) {
  let fontError = null;
  try {
    await Promise.all(pendingMedia || []);
    await loadRequiredFonts();
    await nextFrames();
  } catch (error) {
    fontError = error;
  }
  if (generation !== state.renderGeneration || state.closed) return;
  if (fontError) {
    addDiagnostic({
      code: "preview-fonts",
      category: "rendering",
      severity: "error",
      message: "Preview assets or fonts failed to load.",
      remediation: "Install the preview assets with `pip install simcord[preview]`, then reload the Preview URL.",
      complete: false,
    });
  }
  reconcileScrollIntent();
  state.ready = true;
  ui.app.setAttribute("aria-busy", "false");
}

function profileFromSnapshot(snapshot) {
  const configured = snapshot?.profile || {};
  return {
    ...state.profile,
    width: Number(configured.width ?? 960),
    height: Number(configured.height ?? 720),
    locale: configured.locale || state.profile.locale || "en-US",
    timezone: configured.timezone || state.profile.timezone || "UTC",
    presentationTime: configured.presentationTime || state.profile.presentationTime || null,
    reducedMotion: configured.reducedMotion ?? state.profile.reducedMotion,
    mediaTime: Number.isFinite(configured.mediaTime) ? configured.mediaTime : null,
  };
}

function applyProfile() {
  const presentation = state.snapshot?.presentation || {};
  const viewport = presentation.viewport || { width: state.profile.width, height: state.profile.height };
  const width = Number(viewport.width) || state.profile.width;
  const height = Number(viewport.height) || state.profile.height;
  const fixed = presentation.display === "fixed";
  ui.app.classList.toggle("presentation-fixed", fixed);
  ui.app.classList.toggle("presentation-responsive", !fixed);
  ui.app.style.setProperty("--preview-width", `${width}px`);
  ui.app.style.setProperty("--preview-height", `${height}px`);
  ui.app.style.width = `${width}px`;
  ui.app.style.height = `${height}px`;
  const exact = presentation.exactProfile || { width, height };
  if (!state.queuedPageIntents.configure_presentation && state.pendingAction?.kind !== "configure_presentation") {
    ui.width.value = String(fixed ? exact.width : width);
    ui.height.value = String(fixed ? exact.height : height);
    ui.display.value = presentation.display || "responsive";
    ui.layout.value = presentation.layout || state.snapshot?.layout || "message";
  }
  ui.width.readOnly = ui.display.value !== "fixed";
  ui.height.readOnly = ui.display.value !== "fixed";
  renderPresentationStatus();
}

function loadAsset(assetId, options = {}) {
  const { download = false, capture = false, poster = false, mediaTime = null } = options;
  const params = new URLSearchParams();
  if (download) params.set("download", "1");
  if (capture) params.set("capture", "1");
  if (poster) params.set("poster", "1");
  if (mediaTime !== null && mediaTime !== undefined) params.set("media_time", String(mediaTime));
  const suffix = params.size ? `?${params.toString()}` : "";
  const key = `${assetId}:${suffix}`;
  const cached = state.objectUrls.get(key);
  if (cached) return Promise.resolve(cached);
  const epoch = state.assetEpoch;
  const requestKey = `${epoch}:${key}`;
  const pending = state.assetLoads.get(requestKey);
  if (pending) return pending;
  const request = fetch(`/api/assets/${encodeURIComponent(assetId)}${suffix}`, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) throw new Error(`asset request failed (${response.status})`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    if (epoch !== state.assetEpoch || !state.snapshot?.assets?.[assetId] || state.closed) {
      URL.revokeObjectURL(url);
      throw new Error("stale asset generation");
    }
    const manifest = state.snapshot?.assets?.[assetId];
    if (!download && manifest) {
      const metadata = response.headers.get("X-Simcord-Media-Metadata");
      if (metadata) Object.assign(manifest, JSON.parse(metadata));
      const width = Number(response.headers.get("X-Display-Width"));
      const height = Number(response.headers.get("X-Display-Height"));
      if (width > 0 && height > 0) {
        manifest.displayReady = true;
        manifest.displayWidth = width;
        manifest.displayHeight = height;
      }
      if (capture && mediaTime !== null && mediaTime !== undefined) {
        state.mediaCaptureTimes[assetId] = Number(response.headers.get("X-Media-Time") || mediaTime);
      }
      state.mediaMetadata[assetId] = {
        kind: manifest.mediaKind,
        duration: manifest.duration,
        sourceCodecs: manifest.sourceCodecs,
        displayCodecs: manifest.displayCodecs,
        transformation: manifest.transformation,
        qualityDifferences: manifest.qualityDifferences,
        effectiveMediaTime: manifest.effectiveMediaTime,
        workerMemoryLimited: manifest.workerMemoryLimited,
      };
    }
    state.objectUrls.set(key, url);
    return url;
  }).finally(() => {
    if (state.assetLoads.get(requestKey) === request) state.assetLoads.delete(requestKey);
  });
  state.assetLoads.set(requestKey, request);
  return request;
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

function fingerprint(value) {
  return value ? JSON.stringify(value) : "";
}

function messageSummary(message) {
  const author = message.author?.name || "Unknown author";
  const excerpt = [...new Intl.Segmenter(state.profile.locale, { granularity: "grapheme" })
    .segment(String(message.excerpt || ""))].slice(0, 70).map((item) => item.segment).join("");
  const labels = (message.components || []).map((item) => item.label).filter(Boolean);
  const kinds = message.contentKinds || [];
  const details = [
    excerpt || kinds.join(", ") || "(no text preview)",
    labels.length ? `components: ${labels.join(", ")}` : "",
    message.attachments?.count ? `${message.attachments.count} attachment(s)` : "",
    message.ephemeral ? "ephemeral" : "",
  ].filter(Boolean);
  return `${author} · ${message.createdAt || "time unavailable"} · ${message.id}: ${details.join(" · ")}`;
}

function updatePickers(snapshot) {
  ui.viewer.replaceChildren();
  const viewers = Array.isArray(snapshot.viewers) && snapshot.viewers.length ? snapshot.viewers : [{ id: snapshot.viewerId }];
  viewers.forEach((viewer) => {
    const id = typeof viewer === "object" ? viewer.id : viewer;
    const option = document.createElement("option");
    option.value = String(id || "");
    option.textContent = typeof viewer === "object" && viewer.name ? String(viewer.name) : `Viewer ${id || "unavailable"}`;
    ui.viewer.append(option);
  });
  ui.viewer.value = snapshot.viewerId || "";
  const messageRows = [...(snapshot.messageIndex || [])];
  const targetId = String(snapshot.targetId || "");
  ui.message.replaceChildren();
  messageRows.forEach((message) => {
    const option = document.createElement("option");
    option.value = String(message.id);
    option.textContent = messageSummary(message);
    ui.message.append(option);
  });
  ui.message.value = targetId;
  ui.message.disabled = !messageRows.length || Boolean(state.pendingAction || state.queuedQueries.size);
  const navigation = snapshot.navigation || {};
  if (document.activeElement !== ui.search && !state.queuedQuery) ui.search.value = navigation.query || "";
  ui.previous.disabled = !navigation.hasPrevious || Boolean(state.pendingAction);
  ui.next.disabled = !navigation.hasNext || Boolean(state.pendingAction);
  ui.pageStatus.textContent = [
    navigation.hasPrevious ? "Earlier results available" : "",
    navigation.hasNext ? "More results available" : "",
    navigation.filter ? `Filter: ${navigation.filter}` : "",
  ].filter(Boolean).join(" · ") || "Current authorized page";
  const channelLayout = snapshot.layout === "channel";
  const target = targetId ? snapshot.messages?.[targetId] : null;
  ui.channel.hidden = !channelLayout;
  ui.channelName.textContent = snapshot.channel?.name || "Unavailable channel";
  ui.channelTopic.textContent = snapshot.channel?.topic || "";
  ui.channelTopic.hidden = !snapshot.channel?.topic;
  ui.composerForm.hidden = !channelLayout || !(snapshot.channel?.canSendMessages || state.editTargetId);
  ui.surface.classList.toggle("message-surface", !channelLayout);
  ui.empty.hidden = channelLayout || Boolean(target);
  ui.surface.hidden = channelLayout || !target;
  if (channelLayout) {
    const editKey = state.editTargetId ? `edit:${state.contextId}:${state.editTargetId}` : null;
    const key = editKey || `composer:${state.contextId}`;
    ui.composer.dataset.controlKey = key;
    const editMessage = state.editTargetId ? snapshot.messages?.[state.editTargetId] : null;
    if (!state.drafts.has(key)) state.drafts.set(key, editMessage?.content || "");
    if (ui.composer.value !== state.drafts.get(key)) ui.composer.value = state.drafts.get(key);
    ui.send.disabled = Boolean(state.pendingAction) || !state.authorized || state.pinnedCapture;
    ui.send.textContent = state.editTargetId ? "Save" : "Send";
    ui.composer.placeholder = state.editTargetId ? "Edit message" : "Message";
    const reply = state.replyToId
      ? messageRows.find((item) => String(item.id) === state.replyToId)
      : null;
    ui.replyContext.hidden = !state.replyToId && !state.editTargetId;
    ui.replyLabel.textContent = state.editTargetId
      ? `Editing message ${state.editTargetId}`
      : state.replyToId
        ? reply
          ? `Replying to ${reply.author?.name || "Unknown author"}: ${String(reply.excerpt || "").slice(0, 100)}`
          : `Replying to message ${state.replyToId}`
        : "";
    ui.replyCancel.setAttribute("aria-label", state.editTargetId ? "Cancel edit" : "Cancel reply");
  }
}

function setReplyTo(message) {
  state.editTargetId = null;
  state.replyToId = String(message.id);
  updatePickers(state.snapshot);
  state.focusKey = `composer:${state.contextId}`;
  ui.composer.focus();
  ui.composer.setSelectionRange(ui.composer.value.length, ui.composer.value.length);
}

function setEditMessage(message) {
  state.replyToId = null;
  state.editTargetId = String(message.id);
  const key = `edit:${state.contextId}:${state.editTargetId}`;
  if (!state.drafts.has(key)) state.drafts.set(key, String(message.content || ""));
  updatePickers(state.snapshot);
  state.focusKey = key;
  ui.composer.focus();
  ui.composer.setSelectionRange(ui.composer.value.length, ui.composer.value.length);
}

function localMessageDay(message) {
  const date = new Date(message.timestamp);
  if (!Number.isFinite(date.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: state.profile.timezone || "UTC",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return {
    key: `${value.year}-${value.month}-${value.day}`,
    label: new Intl.DateTimeFormat(state.profile.locale || "en-US", {
      timeZone: state.profile.timezone || "UTC",
      dateStyle: "full",
    }).format(date),
  };
}

function messageRenderOptions(snapshot, generation, pendingMedia, message, channelLayout) {
  const pollKey = (item) => `poll:${state.contextId}:${item.id}`;
  const renderOwner = message ? `message:${message.id}` : null;
  return {
    drafts: state.drafts,
    candidates: snapshot.candidates || {},
    assets: snapshot.assets || {},
    contextId: state.contextId,
    viewerId: snapshot.viewerId,
    spoilerState: state.spoilerState,
    locale: state.profile.locale,
    timezone: state.profile.timezone,
    presentationTime: state.profile.presentationTime,
    mediaTime: state.profile.mediaTime,
    reducedMotion: state.profile.reducedMotion,
    onMediaCaptureTime: (assetId, mediaTime) => {
      state.mediaCaptureTimes[assetId] = mediaTime;
      const manifest = snapshot.assets?.[assetId];
      if (manifest) {
        manifest.effectiveMediaTime = mediaTime;
        state.mediaMetadata[assetId] = {
          kind: manifest.mediaKind,
          duration: manifest.duration,
          sourceCodecs: manifest.sourceCodecs,
          displayCodecs: manifest.displayCodecs,
          transformation: manifest.transformation,
          qualityDifferences: manifest.qualityDifferences,
          effectiveMediaTime: mediaTime,
          workerMemoryLimited: manifest.workerMemoryLimited,
        };
      }
    },
    dropdown: state.dropdown,
    onDiagnostic: (diagnostic) => addDiagnostic({
      ...diagnostic,
      ...(renderOwner ? { id: `local:render:${renderOwner}:${diagnostic.code}`, renderOwner } : {}),
    }),
    onInit: (key, value) => initSelectDraft(key, value, state.drafts),
    identityEntries,
    identityPending: (key) => state.pendingSelectValidations.has(key),
    candidateQuery: (key) => state.candidateQueries.get(key),
    onCandidateQuery: queueCandidateQuery,
    selectionStatus: (key) => state.selectStatuses.get(key)?.message || "",
    selectionInvalid: (key) => state.selectStatuses.get(key)?.invalid === true,
    candidateLoading,
    onOpen: openDropdown,
    onDraft: updateDraft,
    onPremiumActivate: () => {
      state.externalNotice = "Premium purchases are handled by Discord outside this preview. No purchase was started.";
      updateActionStatus();
    },
    onClick: (controlKey) => dispatch("click", { control_key: controlKey }),
    onCommit: commitDropdown,
    onCancel: cancelDropdown,
    onNavigate: navigateDropdown,
    onClear: clearSelection,
    loadAsset,
    isCurrent: () => generation === state.renderGeneration,
    onLocalRender: () => localRender(true),
    pendingMedia,
    channelLayout,
    onReply: channelLayout && snapshot.channel?.canSendMessages ? setReplyTo : null,
    onEdit: channelLayout ? setEditMessage : null,
    onDelete: channelLayout ? (item) => {
      if (window.confirm("Delete this message? This cannot be undone.")) {
        dispatch("delete_message", { target_id: String(item.id), confirmed: true });
      }
    } : null,
    onPin: channelLayout ? (item) => dispatch("set_pinned", {
      target_id: String(item.id),
      pinned: !item.pinned,
    }) : null,
    onReaction: (item, reaction) => {
      const emoji = reaction.emoji || {};
      const key = emoji.id ? `${emoji.name || ""}:${emoji.id}` : String(emoji.name || "");
      dispatch("set_reaction", {
        target_id: String(item.id),
        emoji: key,
        reacted: !reaction.viewer_reacted,
      });
    },
    pollAnswers: (item) => state.pollDrafts.get(pollKey(item)),
    onPollDraft: (item, answers) => {
      state.pollDrafts.set(pollKey(item), answers.map(String));
      localRender(true);
    },
    onPollSubmit: (item, answers) => dispatch("set_poll_votes", {
      target_id: String(item.id),
      answer_ids: answers.map(String),
    }),
  };
}

function setMessageOrder(nodes) {
  let current = ui.messageList.firstElementChild;
  for (const element of nodes) {
    if (element === current) current = current.nextElementSibling;
    else ui.messageList.insertBefore(element, current);
  }
  while (current) {
    const next = current.nextElementSibling;
    current.remove();
    current = next;
  }
}

function renderChannelTimeline(snapshot, generation, previousTargetId) {
  const ids = (snapshot.timeline || []).map(String);
  const active = new Set(ids);
  const previousIds = [...state.messageNodes.keys()];
  const lastPrevious = previousIds.reduce((last, id) => BigInt(id) > BigInt(last) ? id : last, "0");
  const hasNewTail = previousIds.length > 0 && ids.some((id) => !state.messageNodes.has(id) && BigInt(id) > BigInt(lastPrevious));
  const desired = [];
  const dayKeys = new Set();
  const pendingMedia = [];
  let previousDay = null;

  for (const id of ids) {
    const message = snapshot.messages?.[id];
    if (!message) continue;
    const day = localMessageDay(message);
    if (day && day.key !== previousDay) {
      dayKeys.add(day.key);
      let divider = state.dayNodes.get(day.key);
      if (!divider) {
        divider = document.createElement("div");
        divider.className = "timeline-day-divider";
        divider.setAttribute("role", "separator");
        state.dayNodes.set(day.key, divider);
      }
      divider.textContent = day.label;
      desired.push(divider);
    }
    previousDay = day?.key || null;

    let record = state.messageNodes.get(id);
    if (!record) {
      const element = document.createElement("article");
      element.className = "channel-message";
      element.dataset.messageId = id;
      record = { element, fingerprint: "" };
      state.messageNodes.set(id, record);
    }
    const value = `${fingerprint(message)}:${state.candidateFingerprints.get(`message:${id}`) || ""}:${state.profile.mediaTime ?? ""}`;
    if (record.fingerprint !== value) {
      record.fingerprint = value;
      clearRenderDiagnostics(`message:${id}`);
      renderMessage(
        record.element,
        message,
        messageRenderOptions(snapshot, generation, pendingMedia, message, true),
      );
    }
    record.element.classList.toggle("message-surface", id === String(snapshot.targetId || ""));
    desired.push(record.element);
  }

  for (const [id, record] of state.messageNodes) {
    if (!active.has(id)) {
      record.element.remove();
      clearRenderDiagnostics(`message:${id}`);
      state.messageNodes.delete(id);
    }
  }
  for (const [key, divider] of state.dayNodes) {
    if (!dayKeys.has(key)) state.dayNodes.delete(key);
    else if (!desired.includes(divider)) divider.remove();
  }
  setMessageOrder(desired);
  ui.channelEmpty.hidden = ids.length > 0;
  state.scrollIntent = snapshot.targetId && String(snapshot.targetId) !== String(previousTargetId || "")
    ? { policy: "target", targetId: String(snapshot.targetId) } : state.scrollIntent || captureScrollIntent();
  if ((snapshot.history?.hasAfter || hasNewTail) && state.scrollIntent?.policy === "anchor") ui.newMessages.hidden = false;
  else if (state.scrollIntent?.policy === "bottom") ui.newMessages.hidden = true;
  ui.historyOlder.hidden = !snapshot.history?.hasBefore;
  ui.historyNewer.hidden = !snapshot.history?.hasAfter;
  return pendingMedia;
}

function updateActionStatus() {
  const waiting = Number.isInteger(state.awaitingRevision) && state.awaitingRevision > state.publishedRevision;
  const blocked = Boolean(state.pendingAction || waiting || state.transport.uncertainRequestId);
  ui.send.disabled = blocked || !state.authorized || state.pinnedCapture || ui.composerForm.hidden;
  ui.message.disabled = blocked || !state.authorized || !ui.message.options.length || state.pinnedCapture;
  ui.previous.disabled = blocked || !state.snapshot?.navigation?.hasPrevious;
  ui.next.disabled = blocked || !state.snapshot?.navigation?.hasNext;
  if (ui.selectActions) {
    ui.selectActions.hidden = !state.dropdown || state.pinnedCapture;
    ui.selectApply.disabled = blocked || !state.dropdown;
    ui.selectCancel.disabled = !state.dropdown;
  }
  ui.backActivity.hidden = !state.activityBackStack.length || state.pinnedCapture;
  const publication = state.snapshot?.publication;
  const action = state.lastAction;
  const actionText = action
    ? [
      action.dispatch || "Action",
      action.acknowledgement || "acknowledgement pending",
      action.settlement || "settlement pending",
      action.uncertain ? "outcome uncertain" : "",
    ].filter(Boolean).join(" · ")
    : "";
  const pendingText = state.pendingAction ? `${state.pendingAction.kind}…` : "";
  const queuedText = [
    state.queuedQueries.size ? "query queued" : "",
    Object.keys(state.queuedPageIntents).length ? "page change queued" : "",
    waiting ? `awaiting revision ${state.awaitingRevision}` : "",
  ].filter(Boolean).join(" · ");
  const publicationText = publication
    ? `Published ${publication.publishedRevision ?? state.publishedRevision}${publication.reason ? ` · ${publication.reason}` : ""}`
    : `Revision ${state.publishedRevision}`;
  ui.action.textContent = state.externalNotice
    || [pendingText || actionText, queuedText, publicationText, state.transport.state === "uncertain" ? "action outcome uncertain; not retried" : ""]
      .filter(Boolean).join(" — ");
}

function viewActivityMessage(messageId, source = null) {
  if (!/^\d+$/.test(messageId)) return;
  const current = String(source?.messageId || state.targetId || "");
  if (current && /^\d+$/.test(current) && current !== messageId) {
    state.activityBackStack.push({ targetId: current, controlKey: source?.controlKey || state.focusKey });
    state.activityBackStack = state.activityBackStack.slice(-20);
  }
  state.activityReturnKey = null;
  updateActionStatus();
  dispatch("focus", { target_id: messageId });
}

function backActivity() {
  const target = state.activityBackStack.pop();
  if (!target) return;
  state.activityReturnKey = target.controlKey;
  updateActionStatus();
  dispatch("focus", { target_id: target.targetId });
}
function renderActivity() {
  ui.activity.replaceChildren();
  const receipts = state.snapshot?.activity || [];
  if (!receipts.length) {
    ui.activity.append(Object.assign(document.createElement("li"), { textContent: "No activity receipts" }));
    return;
  }
  receipts.slice(-20).forEach((receipt) => {
    const row = document.createElement("li");
    const summary = document.createElement("p");
    const fields = [
      `Request ${receipt.requestId || "unavailable"}`,
      Number.isInteger(receipt.sequence) ? `sequence ${receipt.sequence}` : "",
      receipt.dispatch || "",
      receipt.acknowledgement || "",
      receipt.settlement || "",
      receipt.uncertain ? "outcome uncertain" : "",
    ].filter(Boolean);
    summary.textContent = fields.join(" · ");
    row.append(summary);
    if (receipt.target?.messageId || receipt.target?.controlKey) {
      const target = document.createElement("p");
      target.textContent = [
        receipt.target.messageId ? `Target message ${receipt.target.messageId}` : "",
        receipt.target.controlKey ? `Control ${receipt.target.controlKey}` : "",
      ].filter(Boolean).join(" · ");
      row.append(target);
    }
    const outcomes = document.createElement("ul");
    (receipt.outcomes || []).forEach((outcome) => {
      const item = document.createElement("li");
      const kind = ["response", "followup", "source_edit", "message", "modal", "no_output", "deferred", "unavailable"].includes(outcome.kind)
        ? outcome.kind
        : "unavailable";
      item.append(document.createTextNode(kind));
      if (["response", "followup", "source_edit", "message"].includes(kind) && outcome.messageId) {
        const view = document.createElement("button");
        view.type = "button";
        view.textContent = "View response";
        view.addEventListener("click", () => viewActivityMessage(String(outcome.messageId), receipt.target));
        item.append(" ", view);
      }
      outcomes.append(item);
    });
    if (outcomes.childElementCount) row.append(outcomes);
    ui.activity.append(row);
  });
}

function safeSupportReport() {
  const snapshot = state.snapshot;
  const presentation = snapshot?.presentation;
  if (!snapshot || typeof snapshot.runtimeVersion !== "string" || !presentation) return null;
  return {
    schemaVersion: 1,
    runtimeVersion: snapshot.runtimeVersion,
    protocolVersion: 3,
    rendererVersion: "3",
    display: presentation.display,
    dimensions: {
      viewport: clone(presentation.viewport),
      host: clone(presentation.host),
    },
    publicationRevision: state.publishedRevision,
    diagnostics: state.diagnostics.slice(-40).map((item) => ({
      code: BACKEND_DIAGNOSTIC_CODES.has(item.code) || Object.hasOwn(LOCAL_DIAGNOSTICS, item.code) ? item.code : "internal-error",
      severity: ["error", "warning", "info"].includes(item.severity) ? item.severity : "warning",
      state: item.state === "recovered" ? "recovered" : "current",
      remediation: LOCAL_DIAGNOSTICS[item.code]?.[2] || "Inspect authorized local diagnostics and Env.errors; correct the issue before continuing.",
      ...((state.snapshot?.activity || []).some((receipt) => receipt.correlation === item.correlation)
        && /^c_[A-Za-z0-9_-]{8,64}$/.test(item.correlation) ? { correlation: item.correlation } : {}),
    })),
  };
}

function updateCaptureRecipe(dimensions = null) {
  if (!state.snapshot || state.pinnedCapture) return;
  const presentation = state.snapshot.presentation || {};
  const viewport = dimensions || state.captureViewport || presentation.viewport || {};
  const viewer = String(state.snapshot.viewerId || "");
  const target = String(state.snapshot.targetId || "");
  if (!/^\d+$/.test(viewer) || !/^\d+$/.test(target) || !Number.isInteger(Number(viewport.width)) || !Number.isInteger(Number(viewport.height))) {
    ui.captureRecipe.value = "Capture recipe unavailable until an authorized viewer, target, and viewport are available.";
    ui.copyRecipe.disabled = true;
    return;
  }
  const layout = presentation.layout || state.snapshot.layout;
  const modalNote = state.snapshot.modal
    ? "# Modal state is available only on the live page; this recipe identifies its authorized source message."
    : "";
  ui.captureRecipe.value = [
    "# Run inside this active scenario, with preview in scope. This recipe contains private identifiers.",
    modalNote,
    `# Live-page checkpoint: ready, revision ${state.publishedRevision}, renderGeneration ${state.renderGeneration}; inspect pendingAction/lastAction.`,
    `capture = await preview.screenshot(viewer=${viewer}, target=${target}, viewport=(${Number(viewport.width)}, ${Number(viewport.height)}), layout="${layout}", mode="viewport", media_time=0)`,
  ].filter(Boolean).join("\n");
  state.lastCaptureRecipe = ui.captureRecipe.value;
  ui.copyRecipe.disabled = false;
  ui.copyReport.disabled = !safeSupportReport();
}

function renderPresentationStatus() {
  if (!ui.viewportReadout) return;
  const presentation = state.snapshot?.presentation || {};
  const actual = ui.app.getBoundingClientRect();
  const host = presentation.host || state.host;
  const viewport = presentation.viewport || { width: actual.width, height: actual.height };
  ui.viewportReadout.textContent = [
    `Actual ${Math.round(actual.width)} × ${Math.round(actual.height)}`,
    `viewport ${viewport.width} × ${viewport.height}`,
    `host ${host.width} × ${host.height}`,
    presentation.constraint ? `constraint: ${presentation.constraint}` : "",
  ].filter(Boolean).join(" · ");
  const presets = ["320x700", "640x700", "960x720", "1280x900"];
  const exact = presentation.exactProfile || state.profile;
  const key = `${exact.width}x${exact.height}`;
  ui.preset.value = presets.includes(key) ? key : "custom";
}

function currentDrafts(key) {
  return state.modalDrafts.has(key) || (state.dropdown?.key === key && state.dropdown.modal)
    ? state.modalDrafts
    : state.drafts;
}

function initDraft(key, value, drafts = currentDrafts(key)) {
  if (!drafts.has(key)) drafts.set(key, value);
}

function initSelectDraft(key, value, drafts = currentDrafts(key)) {
  initDraft(key, value, drafts);
  const current = drafts.get(key);
  if (Array.isArray(current)) {
    state.selectDrafts.set(key, current.map(String));
    if (state.snapshot?.candidates?.[key] && !state.selectValidationRevisions.has(key)) {
      state.selectValidationRevisions.set(key, state.publishedRevision);
      state.selectVerifiedValues.set(key, current.map(String));
    }
  }
}

function identityEntries(key) {
  return [...(state.candidateIdentities.get(key)?.values() || [])];
}
function reconcileCandidateSelection(key, selected, requested, revision) {
  const authorized = new Map(selected.map((entry) => [String(entry.value ?? entry.id), entry]));
  const values = requested.filter((value) => authorized.has(String(value))).map(String);
  currentDrafts(key).set(key, values);
  state.selectDrafts.set(key, values);
  const identities = new Map(values.map((id) => [id, authorized.get(id)]));
  if (identities.size) state.candidateIdentities.set(key, identities);
  else state.candidateIdentities.delete(key);
  state.selectValidationRevisions.set(key, Number(revision));
  state.selectVerifiedValues.set(key, values);
  state.pendingSelectValidations.delete(key);
  if (values.length !== requested.length) {
    state.selectStatuses.set(key, {
      fingerprint: JSON.stringify(["authorization", values]),
      message: "One or more selected options are no longer available.",
      authorization: true,
    });
  } else if (state.selectStatuses.get(key)?.authorization) {
    state.selectStatuses.delete(key);
  }
}
function clearModalDrafts() {
  for (const [timerKey, timer] of state.selectQueryTimers) {
    if (timerKey.includes("modal:")) {
      clearTimeout(timer);
      state.selectQueryTimers.delete(timerKey);
    }
  }
  for (const key of state.modalDrafts.keys()) {
    state.selectDrafts.delete(key);
    state.selectStatuses.delete(key);
    state.candidateIdentities.delete(key);
    state.candidateQueries.delete(key);
    state.selectValidationRevisions.delete(key);
    state.selectVerifiedValues.delete(key);
    state.pendingSelectValidations.delete(key);
  }
  state.modalDrafts.clear();
  state.modalTouched.clear();
}

function localRender(renderDom = true) {
  rememberFocus();
  const generation = beginRender();
  if (renderDom && state.snapshot) renderSnapshot(state.snapshot, generation, true);
  else {
    renderDiagnostics();
    updateActionStatus();
    waitReady(generation, []);
  }
}

function captureScrollIntent() {
  const bounds = ui.timeline.getBoundingClientRect();
  const elements = [...ui.messageList.querySelectorAll("[data-message-id]")];
  const anchor = elements.find((item) => item.getBoundingClientRect().bottom > bounds.top);
  return { policy: ui.timeline.scrollHeight - ui.timeline.scrollTop - ui.timeline.clientHeight <= 1 ? "bottom" : "anchor",
    anchorId: anchor?.dataset.messageId, anchorTop: anchor ? anchor.getBoundingClientRect().top - bounds.top : 0,
    oldScrollTop: ui.timeline.scrollTop, survivingIds: elements.map((item) => item.dataset.messageId) };
}
function reconcileScrollIntent() {
  const intent = state.scrollIntent;
  if (!intent || ui.channel.hidden) return;
  const bounds = ui.timeline.getBoundingClientRect();
  if (intent.policy === "bottom") {
    ui.timeline.scrollTop = ui.timeline.scrollHeight - ui.timeline.clientHeight;
  } else if (intent.policy === "target") {
    const target = state.messageNodes.get(intent.targetId)?.element;
    if (!target) return;
    const rect = target.getBoundingClientRect();
    if (rect.height > bounds.height || rect.top < bounds.top) ui.timeline.scrollTop += rect.top - bounds.top;
    else if (rect.bottom > bounds.bottom) ui.timeline.scrollTop += rect.bottom - bounds.bottom;
  } else {
    const id = state.messageNodes.has(intent.anchorId) ? intent.anchorId
      : intent.survivingIds?.find((value) => state.messageNodes.has(value));
    const anchor = state.messageNodes.get(id)?.element;
    if (anchor) ui.timeline.scrollTop += anchor.getBoundingClientRect().top - bounds.top - intent.anchorTop;
    else ui.timeline.scrollTop = intent.oldScrollTop || 0;
  }
}
function scheduleScrollCorrection() {
  if (state.scrollCorrection !== null) return;
  state.scrollCorrection = requestAnimationFrame(() => {
    state.scrollCorrection = null;
    reconcileScrollIntent();
    fitOpenDropdowns();
  });
}
function openDropdown(key, selected, multi, minimum, maximum, highlight, entries = [], modal = false) {
  rememberFocus();
  if (state.dropdown?.key === key) {
    if (multi) commitDropdown(key);
    else cancelDropdown(key);
    return;
  }
  if (state.dropdown) {
    const previousKey = state.dropdown.key;
    if (state.dropdown.multi) {
      if (!commitDropdown(previousKey)) return;
    } else cancelDropdown(previousKey);
  }
  const firstAvailable = entries.find((entry) => (
    !multi || selected.includes(String(entry.value ?? entry.id ?? "")) || selected.length < maximum
  ));
  state.dropdown = {
    key,
    selected: [...selected],
    multi,
    minimum,
    maximum,
    modal,
    highlight: highlight ?? selected[0] ?? firstAvailable?.value ?? firstAvailable?.id ?? null,
  };
  const descriptor = state.snapshot?.candidates?.[key];
  if (descriptor) queueCandidateQuery(key, state.candidateQueries.get(key) ?? descriptor.query ?? "", null, modal ? state.modalHandle : null);
  localRender(true);
  state.focusKey = key;
  const trigger = [...document.querySelectorAll(".select-trigger")].find(
    (item) => item.dataset.controlKey === key,
  );
  trigger?.focus();
}

function announceSelectStatus(key, fingerprint, message) {
  if (state.selectStatuses.get(key)?.fingerprint === fingerprint) return;
  state.selectStatuses.set(key, { fingerprint, message });
  const wrap = [...document.querySelectorAll(".preview-select")].find((item) => item.dataset.controlKey === key);
  const status = wrap?.querySelector(".select-guidance");
  if (status) status.textContent = message;
}

function announceInvalidSelection(key, values, minimum, maximum) {
  const fingerprint = JSON.stringify([values, minimum, maximum]);
  const message = values.length < minimum
    ? `Choose at least ${minimum} option${minimum === 1 ? "" : "s"}.`
    : `Choose no more than ${maximum} options.`;
  announceSelectStatus(key, fingerprint, message);
  const status = state.selectStatuses.get(key);
  if (status) status.invalid = true;
  const wrap = [...document.querySelectorAll(".preview-select")].find((item) => item.dataset.controlKey === key);
  wrap?.querySelector(".select-trigger")?.setAttribute("aria-invalid", "true");
}

function updateDraft(key, value, multi, minimum, maximum, selected, entry) {
  const drafts = currentDrafts(key);
  const current = Array.isArray(drafts.get(key)) ? [...drafts.get(key)] : [...selected];
  const id = String(value);
  const next = multi ? (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]) : [id];
  if (next.length > maximum) {
    announceInvalidSelection(key, next, minimum, maximum);
    return;
  }
  drafts.set(key, next);
  state.selectDrafts.set(key, [...next]);
  if (state.snapshot?.candidates?.[key]) {
    const identities = state.candidateIdentities.get(key) || new Map();
    if (entry && next.includes(id)) identities.set(id, entry);
    for (const candidateId of identities.keys()) {
      if (!next.includes(candidateId)) identities.delete(candidateId);
    }
    if (identities.size) state.candidateIdentities.set(key, identities);
    else state.candidateIdentities.delete(key);
  }
  state.selectStatuses.delete(key);
  if (state.dropdown) state.dropdown.highlight = id;
  localRender(true);
}

function navigateDropdown(key, highlight) {
  if (state.dropdown?.key !== key) return;
  state.dropdown.highlight = highlight;
  localRender(true);
}

function clearSelection(key, minimum = 1) {
  const modal = state.modalDrafts.has(key) || (state.dropdown?.key === key && state.dropdown.modal);
  if (modal) {
    clearModalValidation();
    state.modalTouched.add(key);
  }
  currentDrafts(key).set(key, []);
  state.selectDrafts.set(key, []);
  if (state.snapshot?.candidates?.[key]) {
    state.selectValidationRevisions.set(key, state.publishedRevision);
    state.selectVerifiedValues.set(key, []);
  }
  state.pendingSelectValidations.delete(key);
  state.selectStatuses.delete(key);
  state.candidateIdentities.delete(key);
  if (state.dropdown?.key === key) state.dropdown = null;
  localRender(true);
  if (!modal && minimum <= 0) dispatch("select", { control_key: key, values: [] });
}

function commitDropdown(key) {
  const dropdown = state.dropdown;
  if (!dropdown || dropdown.key !== key) return false;
  const values = [...(currentDrafts(key).get(key) || [])].map(String);
  if (state.pendingAction) {
    announceSelectStatus(
      key,
      JSON.stringify(["pending", state.pendingAction.requestId, values]),
      "Wait for the current action to finish before applying.",
    );
    return false;
  }
  if (values.length < dropdown.minimum || values.length > dropdown.maximum) {
    announceInvalidSelection(key, values, dropdown.minimum, dropdown.maximum);
    return false;
  }
  const previous = dropdown.selected || [];
  const changed = values.length !== previous.length || values.some((value, index) => value !== previous[index]);
  state.selectStatuses.delete(key);
  state.dropdown = null;
  const wrap = [...document.querySelectorAll(".preview-select")].find((item) => item.dataset.controlKey === key);
  const list = wrap?.querySelector(".select-list");
  const trigger = wrap?.querySelector(".select-trigger");
  if (list) {
    if (typeof list.hidePopover === "function" && list.matches(":popover-open")) list.hidePopover();
    list.hidden = true;
  }
  if (trigger) {
    trigger.setAttribute("aria-expanded", "false");
    trigger.removeAttribute("aria-activedescendant");
  } else localRender(true);
  wrap?.classList.remove("is-open", "opens-up");
  if (!dropdown.modal && !state.modalDrafts.has(key) && changed) dispatch("select", { control_key: key, values });
  return true;
}

function cancelDropdown(key) {
  const dropdown = state.dropdown;
  if (!dropdown || dropdown.key !== key) return;
  const values = [...dropdown.selected];
  currentDrafts(key).set(key, values);
  state.selectDrafts.set(key, values);
  state.selectStatuses.delete(key);
  const identities = state.candidateIdentities.get(key);
  if (identities) for (const candidateId of identities.keys()) {
    if (!values.includes(candidateId)) identities.delete(candidateId);
  }
  state.dropdown = null;
  localRender(true);
}

function clearModalValidation() {
  const error = state.modalError;
  if (error) {
    const field = [...ui.modal.querySelectorAll(".modal-field")]
      .find((item) => item.dataset.customId === error.controlId);
    const message = field?.querySelector(".field-error");
    if (message) {
      field.querySelectorAll("[aria-invalid='true']").forEach((target) => {
        target.removeAttribute("aria-invalid");
        const ids = (target.getAttribute("aria-describedby") || "").split(/\s+/).filter((id) => id && id !== message.id);
        if (ids.length) target.setAttribute("aria-describedby", ids.join(" "));
        else target.removeAttribute("aria-describedby");
      });
      message.remove();
    }
  }
  state.modalError = null;
  state.localDiagnostics = state.localDiagnostics.filter((item) => item.code !== "modal-validation");
}
document.addEventListener("pointerdown", (event) => {
  const activeWrap = event.target instanceof Element ? event.target.closest(".preview-select") : null;
  if (!state.dropdown || activeWrap?.dataset.controlKey === state.dropdown.key) return;
  const key = state.dropdown.key;
  if (state.dropdown.multi) {
    commitDropdown(key);
    return;
  }
  setTimeout(() => cancelDropdown(key), 0);
});

function submitModal(values) {
  const modal = state.snapshot?.modal;
  if (!modal || modal.handle !== state.modalHandle) return;
  const error = validateModalValues(modal.payload || {}, values);
  if (error) {
    state.modalError = error;
    state.modalErrorHandle = modal.handle;
    state.localDiagnostics = state.localDiagnostics.filter((item) => item.code !== "modal-validation");
    addDiagnostic({
      code: "modal-validation",
      severity: "error",
      message: `${error.controlId}: ${error.message}`,
      complete: false,
    });
    state.lastModalFingerprint = "";
    localRender(true);
    return;
  }
  clearModalValidation();
  state.modalErrorHandle = null;
  dispatch("modal_submit", { modal_handle: modal.handle, values });
}

function renderSnapshot(snapshot, generation, force = false) {
  if (generation !== state.renderGeneration || state.closed) return;
  if (state.snapshot?.layout === "channel") state.scrollIntent = captureScrollIntent();
  state.snapshot = snapshot;
  state.authorized = snapshot.status !== "access_denied";
  if (state.authorized) {
    state.localDiagnostics = state.localDiagnostics.filter((item) => item.code !== "access-denied");
  }
  if (!state.authorized) {
    revokeAssets();
    state.localDiagnostics = [];
    addDiagnostic({ code: "access-denied", severity: "error", complete: false });
    state.spoilerState.clear();
    resetInteractionState();
    state.pollDrafts.clear();
    state.editTargetId = null;
    state.replyToId = null;
    state.dropdown = null;
    state.dismissedModal = null;
    state.modalHandle = null;
    state.modalOpenerFocusKey = null;
    ui.surface.replaceChildren();
    ui.messageList.replaceChildren();
    state.messageNodes.clear();
    state.dayNodes.clear();
  }
  const nextContextId = snapshot.context?.id || state.contextId;
  const nextGeneration = Number(snapshot.context?.generation || 0);
  const nextRevision = Number(snapshot.publishedRevision || 0);
  const nextViewer = snapshot.viewerId || null;
  const spoilerContext = `${nextContextId || ""}:${nextViewer || ""}`;
  if (state.spoilerContext && state.spoilerContext !== spoilerContext) state.spoilerState.clear();
  state.spoilerContext = spoilerContext;
  state.contextId = nextContextId;
  const previousTargetId = state.targetId;
  pruneAssetUrls(snapshot.assets || {});
  if (nextViewer !== state.viewerId) {
    resetInteractionState();
    state.localDiagnostics = [];
    state.lastAction = null;
  }
  if (nextViewer !== state.viewerId || snapshot.botGeneration !== state.botGeneration) revokeAssets();
  state.contextGeneration = nextGeneration;
  state.botGeneration = Number(snapshot.botGeneration || 0);
  state.publishedRevision = nextRevision;
  state.observedRevision = Math.max(state.observedRevision, nextRevision);
  state.initialPresentation ||= clone(snapshot.presentation?.exactProfile);
  state.viewerId = nextViewer;
  state.targetId = snapshot.targetId ?? null;
  const previousMediaTime = state.profile.mediaTime;
  state.profile = profileFromSnapshot(snapshot);
  if (previousMediaTime !== state.profile.mediaTime) {
    state.mediaCaptureTimes = {};
    state.mediaMetadata = {};
  }
  applyProfile();
  updatePickers(snapshot);
  const pendingMedia = [];
  if (snapshot.layout === "channel") {
    pendingMedia.push(...renderChannelTimeline(snapshot, generation, previousTargetId));
  } else {
    ui.messageList.replaceChildren();
    state.messageNodes.clear();
    state.dayNodes.clear();
    const selected = snapshot.targetId ? snapshot.messages?.[String(snapshot.targetId)] || null : null;
    const selectedKey = selected ? String(selected.id) : null;
    const selectedFingerprint = `${fingerprint(selected)}:${state.candidateFingerprints.get(`message:${selectedKey}`) || ""}:${state.profile.mediaTime ?? ""}`;
    const shouldRenderMessage =
      force || selectedKey !== state.lastMessageKey || selectedFingerprint !== state.lastMessageFingerprint;
    if (shouldRenderMessage) {
      clearRenderDiagnostics(`message:${state.lastMessageKey}`);
      clearRenderDiagnostics(`message:${selectedKey}`);
      state.lastMessageKey = selectedKey;
      state.lastMessageFingerprint = selectedFingerprint;
      renderMessage(ui.surface, selected, messageRenderOptions(snapshot, generation, pendingMedia, selected, false));
      state.pendingMedia = pendingMedia;
    } else {
      pendingMedia.push(...state.pendingMedia);
    }
  }
  const modal = snapshot.modal && snapshot.modal.handle !== state.dismissedModal ? snapshot.modal : null;
  const modalKey = snapshot.modal ? `${fingerprint(snapshot.modal)}:${state.candidateFingerprints.get(`modal:${snapshot.modal.handle}`) || ""}` : "";
  if (modal && state.modalErrorHandle !== modal.handle) {
    clearModalValidation();
    state.modalErrorHandle = modal.handle;
  } else if (!modal) {
    clearModalValidation();
    state.modalErrorHandle = null;
  }
  const nextHandle = modal?.handle || null;
  const previousHandle = state.modalHandle;
  if (nextHandle && !previousHandle) state.modalOpenerFocusKey = state.focusKey;
  if (nextHandle !== state.modalHandle) {
    clearModalDrafts();
  }
  setModalIsolation(Boolean(nextHandle));
  if (force || modalKey !== state.lastModalFingerprint) {
    const scrollTop = ui.modal.querySelector(".modal-body")?.scrollTop || 0;
    clearRenderDiagnostics(state.modalHandle ? `modal:${state.modalHandle}` : null);
    if (modal) clearRenderDiagnostics(`modal:${modal.handle}`);
    const rendered = renderModal(ui.modal, modal?.payload, {
      drafts: state.modalDrafts,
      dropdown: state.dropdown,
      candidates: snapshot.candidates || {},
      modalHandle: modal?.handle || "",
      isTouched: (key) => state.modalTouched.has(key),
      loadAsset,
      assets: snapshot.assets || {},
      contextId: state.contextId,
      spoilerState: state.spoilerState,
      validationError: state.modalError,
      scrollTop,
      locale: state.profile.locale,
      presentationTime: state.profile.presentationTime,
      pendingMedia,
      isCurrent: () => generation === state.renderGeneration,
      onInit: (key, value) => initSelectDraft(key, value, state.modalDrafts),
      identityEntries,
      identityPending: (key) => state.pendingSelectValidations.has(key),
      candidateQuery: (key) => state.candidateQueries.get(key),
      onCandidateQuery: queueCandidateQuery,
      selectionStatus: (key) => state.selectStatuses.get(key)?.message || "",
      selectionInvalid: (key) => state.selectStatuses.get(key)?.invalid === true,
      candidateLoading,
      onSelectOpen: openDropdown,
      onSelectDraft: (key, value, multi, minimum, maximum, selected, entry) => {
        clearModalValidation();
        state.modalTouched.add(key);
        updateDraft(key, value, multi, minimum, maximum, selected, entry);
      },
      onSelectCommit: commitDropdown,
      onSelectCancel: cancelDropdown,
      onNavigate: navigateDropdown,
      onClear: clearSelection,
      onDraft: (key, value) => {
        clearModalValidation();
        state.modalDrafts.set(key, value);
        state.modalTouched.add(key);
        localRender(false);
      },
      onFiles: (key, files) => {
        clearModalValidation();
        state.modalDrafts.set(key, files);
        state.modalTouched.add(key);
        localRender(false);
      },
      onCancel: () => {
        state.dropdown = null;
        clearModalValidation();
        state.dismissedModal = state.modalHandle;
        clearModalDrafts();
        state.modalErrorHandle = null;
        localRender(true);
      },
      onSubmit: submitModal,
      onDiagnostic: (diagnostic) => addDiagnostic({
        ...diagnostic,
        id: `local:render:modal:${modal.handle}:${diagnostic.code}`,
        renderOwner: `modal:${modal.handle}`,
      }),
    });
    if (nextHandle && state.modalError && rendered.errorTarget) {
      rendered.errorTarget.scrollIntoView({ block: "nearest" });
      rendered.errorTarget.focus({ preventScroll: true });
    } else if (nextHandle && !previousHandle) requestAnimationFrame(() => rendered.focus?.focus());
    else if (nextHandle) restoreFocus();
    else if (previousHandle) {
      if (!restoreFocus(state.modalOpenerFocusKey)) {
        const fallback = ui.message.checkVisibility() && !ui.message.disabled
          ? ui.message : ui.inspector.querySelector("summary");
        fallback.focus();
      }
    }
  }
  state.modalHandle = nextHandle;
  state.lastModalFingerprint = modalKey;
  renderDiagnostics();
  updateActionStatus();
  renderActivity();
  renderTransportStatus();
  updateCaptureRecipe();
  fitOpenDropdowns();
  waitReady(generation, pendingMedia).then(() => {
    if (generation !== state.renderGeneration || !state.activityReturnKey) return;
    const key = state.activityReturnKey;
    state.activityReturnKey = null;
    restoreFocus(key);
  });
  if (!nextHandle && !previousHandle) requestAnimationFrame(() => restoreFocus());
}

function fitOpenDropdowns() {
  const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
  const viewportHeight = document.documentElement.clientHeight || window.innerHeight;
  for (const wrap of document.querySelectorAll(".preview-select.is-open")) {
    const trigger = wrap.querySelector(".select-trigger");
    const list = wrap.querySelector(".select-list");
    if (!trigger || !list) continue;
    if (typeof list.showPopover === "function" && !list.matches(":popover-open")) list.showPopover();
    const rect = trigger.getBoundingClientRect();
    const modal = wrap.closest(".modal-dialog");
    const boundary = modal?.getBoundingClientRect();
    const footer = modal?.querySelector(".modal-actions")?.getBoundingClientRect();
    const top = Math.max(0, boundary?.top || 0);
    const bottom = Math.min(viewportHeight, footer?.top ?? viewportHeight);
    if (rect.bottom <= 0 || rect.top >= viewportHeight) {
      cancelDropdown(wrap.dataset.controlKey);
      continue;
    }
    const gutter = 8, gap = 4;
    const width = Math.max(0, Math.min(rect.width, viewportWidth - gutter * 2));
    const left = Math.max(gutter, Math.min(rect.left, viewportWidth - width - gutter));
    const below = Math.max(0, bottom - rect.bottom - gap - gutter);
    const above = Math.max(0, rect.top - top - gap - gutter);
    const desired = Math.min(list.scrollHeight, 220);
    const opensUp = below < desired && above > below;
    const available = opensUp ? above : below;
    const height = Math.min(220, available);
    list.style.left = `${left}px`;
    list.style.top = `${opensUp ? Math.max(top + gutter, rect.top - gap - height) : Math.max(top + gutter, Math.min(bottom - gutter - height, rect.bottom + gap))}px`;
    list.style.width = `${width}px`;
    list.style.maxHeight = `${height}px`;
    wrap.classList.toggle("opens-up", opensUp);
    const active = list.querySelector(".select-option.is-highlighted");
    if (active) {
      if (active.offsetTop < list.scrollTop) list.scrollTop = active.offsetTop;
      else if (active.offsetTop + active.offsetHeight > list.scrollTop + list.clientHeight) {
        list.scrollTop = active.offsetTop + active.offsetHeight - list.clientHeight;
      }
    }
  }
}
window.addEventListener("resize", fitOpenDropdowns);
document.addEventListener("scroll", fitOpenDropdowns, true);

async function installSnapshot(snapshot, force = false) {
  if (!snapshot || state.closed) return false;
  const compatible = Number(snapshot.protocolVersion) === 3
    && snapshot.context && typeof snapshot.context.id === "string"
    && Number.isInteger(snapshot.context.generation)
    && Array.isArray(snapshot.messageIndex) && snapshot.messageIndex.length <= 50
    && snapshot.messages && typeof snapshot.messages === "object" && !Array.isArray(snapshot.messages)
    && Array.isArray(snapshot.timeline)
    && snapshot.history && typeof snapshot.history === "object"
    && snapshot.navigation && typeof snapshot.navigation === "object"
    && snapshot.presentation && typeof snapshot.presentation === "object"
    && snapshot.publication && typeof snapshot.publication === "object"
    && snapshot.candidates && typeof snapshot.candidates === "object" && !Array.isArray(snapshot.candidates)
    && Array.isArray(snapshot.activity)
    && Number.isInteger(snapshot.publishedRevision);
  if (snapshot.context?.id === state.contextId && snapshot.context?.generation === state.contextGeneration
    && snapshot.publishedRevision < state.publishedRevision) return false;
  if (!compatible) {
    state.protocolCompatible = false;
    state.authorized = false;
    state.snapshot = null;
    state.localDiagnostics = [{
      id: "local:protocol-mismatch",
      code: "protocol-mismatch",
      category: "protocol",
      severity: "error",
      state: "current",
      message: "Preview protocol 3 is required; reload this page.",
      remediation: "Reload the Preview URL to receive a compatible snapshot.",
      complete: false,
    }];
    renderDiagnostics();
    ui.app.setAttribute("aria-busy", "false");
    return false;
  }
  state.protocolCompatible = true;
  state.pinnedCapture = snapshot.context.id.startsWith("capture_");
  if (state.pinnedCapture) document.body.dataset.managedCapture = "true";
  else delete document.body.dataset.managedCapture;
  const groups = new Map();
  for (const [key, descriptor] of Object.entries(snapshot.candidates || {})) {
    const scope = key.split(":").slice(0, 2).join(":");
    if (!groups.has(scope)) groups.set(scope, []);
    groups.get(scope).push([key, descriptor]);
  }
  for (const [key, values] of state.selectDrafts) {
    if (!state.selectValidationRevisions.has(key)) continue;
    const selected = snapshot.candidates?.[key]?.selected;
    const verified = state.selectVerifiedValues.get(key);
    const selectedValues = Array.isArray(selected)
      ? new Set(selected.map((entry) => String(entry.value ?? entry.id)))
      : null;
    const selectedCoversDraft = selectedValues
      && values.every((value) => selectedValues.has(String(value)));
    if (!values.length) {
      state.selectValidationRevisions.set(key, Number(snapshot.publishedRevision));
      state.selectVerifiedValues.set(key, []);
      state.pendingSelectValidations.delete(key);
    } else if (Array.isArray(selected)
      && (selectedCoversDraft || (Array.isArray(verified) && fingerprint(verified) === fingerprint(values)))) {
      reconcileCandidateSelection(key, selected, values, snapshot.publishedRevision);
    } else {
      state.pendingSelectValidations.set(key, Number(snapshot.publishedRevision));
      if (state.dropdown?.key === key) state.dropdown = null;
    }
  }
  state.candidateFingerprints = new Map([...groups].map(([scope, entries]) => [
    scope,
    fingerprint(entries.map(([key, descriptor]) => [
      key, descriptor, state.pendingSelectValidations.has(key),
    ])),
  ]));
  rememberFocus();
  if (!state.pendingAction && "lastAction" in snapshot) state.lastAction = snapshot.lastAction;
  reconcileUncertainAction(snapshot);
  const generation = beginRender();
  renderSnapshot(snapshot, generation, force);
  queuePendingCandidateValidations();
  return true;
}

function validateModalValues(modal, values) {
  const controls = {};
  const uploadLimit = 10 * 1024 * 1024;
  let totalUploadBytes = 0;
  const walk = (node) => {
    if (!node || typeof node !== "object") return;
    if (typeof node.custom_id === "string") controls[node.custom_id] = node;
    Object.values(node).forEach((value) => Array.isArray(value) ? value.forEach(walk) : walk(value));
  };
  (modal.components || []).forEach(walk);
  for (const [id, component] of Object.entries(controls)) {
    const type = Number(component.type);
    const supplied = Object.prototype.hasOwnProperty.call(values, id);
    const value = values[id];
    const required = component.required === true
      || (component.required === undefined && (type === 4 || [3, 5, 6, 7, 8].includes(type)));
    const invalid = (message) => ({ controlId: id, message });
    if (type === 4) {
      const text = String(value ?? component.value ?? component.default ?? "");
      const length = Array.from(text).length;
      if (required && !text) return invalid("This field is required.");
      if (component.min_length !== undefined && length < Number(component.min_length)) {
        return invalid(`Enter at least ${component.min_length} characters.`);
      }
      if (component.max_length !== undefined && length > Number(component.max_length)) {
        return invalid(`Enter no more than ${component.max_length} characters.`);
      }
    } else if ([3, 5, 6, 7, 8, 19, 22].includes(type)) {
      const selected = Array.isArray(value) ? value : [];
      const minimum = Number(component.min_values ?? 1);
      const maximum = Number(component.max_values ?? (type === 22 ? component.options?.length || 1 : 1));
      if (required && selected.length === 0) return invalid("Choose at least one value.");
      if ((supplied || required) && selected.length < minimum) {
        return invalid(`Choose at least ${minimum} value(s).`);
      }
      if (selected.length > maximum) return invalid(`Choose no more than ${maximum} value(s).`);
      if (type === 19) {
        for (const file of selected) {
          if (!file || typeof file.size !== "number") return invalid("Choose a valid file.");
          if (file.size > uploadLimit) return invalid(`${file.name} exceeds the 10 MiB per-file limit.`);
          totalUploadBytes += file.size;
          if (totalUploadBytes > 25 * 1024 * 1024) {
            return invalid("Uploads exceed the 25 MiB aggregate limit.");
          }
        }
      }
    } else if (type === 21) {
      if (required && !value) return invalid("Choose one option.");
      if (value != null && !(component.options || []).some((item) => String(item.value) === String(value))) {
        return invalid("Choose an available option.");
      }
    } else if (type === 23 && supplied && typeof value !== "boolean") {
      return invalid("Choose a valid checkbox value.");
    }
  }
  return null;
}
function normalizeSearch(value) {
  return Array.from(String(value || "").normalize("NFC")).slice(0, 128).join("");
}

function pageIntent(kind, body) {
  state.queuedPageIntents[kind] = body;
  updateActionStatus();
}

function queueQuery(key, kind, body) {
  const intent = { key, kind, body, scope: `${state.viewerId}:${state.contextGeneration}`, token: ++state.queryIntent };
  state.queryIntents.set(key, intent.token);
  state.queuedQueries.set(key, intent);
  state.queuedQuery = intent;
  updateActionStatus();
  drainIntents();
}

function candidateLoading(controlKey) {
  const intentKey = `candidate:${controlKey}:${state.modalHandle || ""}`;
  return state.pendingQuery?.controlKey === controlKey
    || state.queuedQueries.has(intentKey) || state.selectQueryTimers.has(intentKey);
}

function queueCandidateQuery(controlKey, query, cursor = null, modalHandle = null) {
  if (state.pinnedCapture || typeof controlKey !== "string") return;
  const normalized = normalizeSearch(query);
  state.candidateQueries.set(controlKey, normalized);
  const key = `candidate:${controlKey}:${modalHandle || ""}`;
  clearTimeout(state.selectQueryTimers.get(key));
  const body = {
    control_key: controlKey,
    query: normalized,
    cursor: typeof cursor === "string" ? cursor : null,
    ...(modalHandle ? { modal_handle: modalHandle } : {}),
    selected_values: [...(state.selectDrafts.get(controlKey) || [])],
  };
  if (cursor == null) {
    const timer = window.setTimeout(() => {
      state.selectQueryTimers.delete(key);
      queueQuery(key, "browse_candidates", body);
    }, 180);
    state.selectQueryTimers.set(key, timer);
  } else {
    state.selectQueryTimers.delete(key);
    queueQuery(key, "browse_candidates", body);
  }
}
function queuePendingCandidateValidations() {
  for (const [key, revision] of state.pendingSelectValidations) {
    if (revision !== state.publishedRevision || !state.snapshot?.candidates?.[key] || candidateLoading(key)) continue;
    const modalHandle = state.modalHandle && key.startsWith(`modal:${state.modalHandle}:`)
      ? state.modalHandle : null;
    queueCandidateQuery(key, state.candidateQueries.get(key) ?? state.snapshot.candidates[key].query ?? "", null, modalHandle);
  }
}

function queueMessageQuery(query, cursor = null) {
  if (state.pinnedCapture) return;
  const normalized = normalizeSearch(query);
  ui.search.value = normalized;
  queueQuery("messages", "browse_messages", {
    query: normalized,
    filter: "all",
    cursor: typeof cursor === "string" ? cursor : null,
  });
}

function getHostDimensions() {
  return { width: Math.max(1, Math.floor(ui.stage.clientWidth)), height: Math.max(1, Math.floor(ui.stage.clientHeight)) };
}

function queuePresentation() {
  if (state.pinnedCapture) return;
  const host = getHostDimensions();
  state.host = host;
  pageIntent("configure_presentation", {
    layout: ui.layout.value || "message",
    display: ui.display.value || "responsive",
    width: ui.display.value === "fixed" ? Number(ui.width.value) : (state.snapshot?.presentation?.exactProfile.width || state.profile.width),
    height: ui.display.value === "fixed" ? Number(ui.height.value) : (state.snapshot?.presentation?.exactProfile.height || state.profile.height),
    host_width: host.width,
    host_height: host.height,
  });
  drainIntents();
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
    state.lastAction = receipt;
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

function resetInteractionState() {
  for (const timer of state.selectQueryTimers.values()) clearTimeout(timer);
  state.selectQueryTimers.clear();
  state.queuedQueries.clear();
  state.queryIntents.clear();
  delete state.queuedPageIntents.focus;
  state.focusKey = null;
  state.focusSelection = null;
  state.dropdown = null;
  state.drafts.clear();
  state.selectDrafts.clear();
  state.selectStatuses.clear();
  state.candidateIdentities.clear();
  state.candidateQueries.clear();
  state.selectValidationRevisions.clear();
  state.selectVerifiedValues.clear();
  state.pendingSelectValidations.clear();
  state.pollDrafts.clear();
  clearModalDrafts();
  state.editTargetId = null;
  state.replyToId = null;
  state.dismissedModal = null;
  state.activityBackStack = [];
  state.activityReturnKey = null;
}

function redactPrivateView() {
  revokeAssets();
  resetInteractionState();
  state.snapshot = null;
  state.lastAction = null;
  state.viewerId = null;
  state.targetId = null;
  ui.viewer.replaceChildren();
  ui.message.replaceChildren();
  ui.captureRecipe.value = "";
  ui.exactId.value = "";
  ui.search.value = "";
  state.lastMessageKey = null;
  state.lastMessageFingerprint = "";
  state.messageNodes.clear();
  state.dayNodes.clear();
  state.spoilerState.clear();
  ui.surface.replaceChildren();
  ui.messageList.replaceChildren();
  renderActivity();
}

function rememberReceipt(receipt) {
  receipt = { ...receipt };
  delete receipt.result;
  state.lastAction = receipt;
  if (!state.snapshot) return;
  const activity = (state.snapshot.activity || []).filter((item) => item.requestId !== receipt.requestId);
  activity.push(receipt);
  state.snapshot = { ...state.snapshot, lastAction: receipt, activity: activity.slice(-20) };
  renderActivity();
}


function queryStatusFingerprint(snapshot) {
  return JSON.stringify({
    status: snapshot.status,
    diagnostics: snapshot.diagnostics,
    botGeneration: snapshot.botGeneration,
    context: snapshot.context,
    publication: snapshot.publication,
    navigation: snapshot.navigation,
    messageIndex: snapshot.messageIndex,
    candidates: snapshot.candidates,
    activity: snapshot.activity,
    lastAction: snapshot.lastAction,
    presentation: snapshot.presentation,
  });
}
function drainIntents() {
  const uncertain = state.transport.uncertainRequestId;
  const canClose = Boolean(uncertain && "close" in state.queuedPageIntents
    && state.transport.uncertainCloseAttemptFor !== uncertain);
  if (
    state.pendingAction || state.closed || state.pinnedCapture
    || !state.protocolCompatible || !state.contextId || (uncertain && !canClose)
    || ((Number.isInteger(state.awaitingRevision) && state.awaitingRevision > state.observedRevision) && !canClose)
  ) return;
  for (const kind of ["close", "viewer", "refresh", "focus", "configure_presentation"]) {
    if (!(kind in state.queuedPageIntents)) continue;
    if (uncertain && kind !== "close") continue;
    if (!state.authorized && !["close", "viewer", "refresh"].includes(kind)) continue;
    const body = state.queuedPageIntents[kind];
    delete state.queuedPageIntents[kind];
    dispatch(kind, body);
    return;
  }
  if (!state.authorized || uncertain) return;
  const next = state.queuedQueries.entries().next().value;
  if (!next) return;
  const [key, intent] = next;
  state.queuedQueries.delete(key);
  state.queuedQuery = state.queuedQueries.values().next().value || null;
  if (intent.scope !== `${state.viewerId}:${state.contextGeneration}`) { drainIntents(); return; }
  performAction(intent.kind, intent.body, intent);
}

function pageAction(kind) {
  return ["close", "refresh", "focus", "viewer", "configure_presentation"].includes(kind);
}
async function performAction(kind, extra, queryIntentValue = null) {
  const requestId = globalThis.crypto?.randomUUID?.() || `preview-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const sequence = state.sequence + 1;
  const body = {
    protocol_version: 3,
    sequence,
    request_id: requestId,
    generation: state.contextGeneration,
    bot_generation: state.botGeneration,
    published_revision: state.observedRevision || state.publishedRevision,
    kind,
    ...extra,
  };
  if (["click", "select"].includes(kind)) {
    body.target_id = /^message:(\d+):component:/.exec(extra.control_key)?.[1] ?? state.targetId;
  }
  state.pendingAction = {
    kind, requestId, sequence, controlKey: extra.control_key || null, targetId: body.target_id || null,
    closeProbe: kind === "close" && state.transport.uncertainCloseAttemptFor === state.transport.uncertainRequestId
      && Boolean(state.transport.uncertainRequestId),
  };
  if (queryIntentValue) {
    state.pendingQuery = { kind, key: queryIntentValue.key, controlKey: extra.control_key || null, requestId, sequence, query: extra.query };
  }
  localRender(false);
  let receipt;
  try {
    receipt = await requestAction(body);
  } catch (_) {
    state.pendingAction = null;
    state.pendingQuery = null;
    noteTransportFailure("action-response-unavailable", requestId, sequence);
    localRender(false);
    return;
  }
  if (!receipt || typeof receipt !== "object" || receipt.requestId !== requestId) {
    state.pendingAction = null;
    state.pendingQuery = null;
    noteTransportFailure("action-receipt-unavailable", requestId, sequence);
    localRender(false);
    return;
  }
  noteTransportHealthy();
  if (Number.isInteger(receipt.expectedSequence)) state.sequence = receipt.expectedSequence;
  if (Number.isInteger(receipt.revision) && receipt.revision > state.publishedRevision) {
    state.awaitingRevision = receipt.revision;
  }
  if (Array.isArray(receipt.diagnostics)) receipt.diagnostics.forEach((item) => addDiagnostic(item));
  if (kind === "close" && receipt.rejected && state.pendingAction?.closeProbe) {
    state.queuedPageIntents.close = extra;
  }
  if (kind === "close" && !receipt.rejected && receipt.settlement === "settled"
    && Number.isInteger(state.transport.uncertainSequence)
    && receipt.sequence === state.transport.uncertainSequence) {
    state.transport.uncertainRequestId = null;
    state.transport.uncertainSequence = null;
    state.transport.uncertainCloseAttemptFor = null;
    state.transport.state = "recovered";
    state.transport.recoveries += 1;
    state.transport.history.push({ state: "recovered", code: "action-not-admitted-by-close", at: Date.now() });
    state.transport.history = state.transport.history.slice(-20);
    recoverActionTransportDiagnostics("The accepted Close proved the uncertain action was not admitted; transport failure remains in history.");
    renderTransportStatus();
    renderDiagnostics();
  }
  if (kind === "close" && !receipt.rejected && receipt.settlement === "settled") {
    state.closed = true;
    state.authorized = false;
    state.pendingAction = null;
    state.pendingQuery = null;
    state.ready = false;
    state.queuedPageIntents = {};
    state.queuedQueries.clear();
    state.awaitingRevision = null;
    state.externalNotice = "Preview session closed.";
    announceStatus(state.externalNotice);
    redactPrivateView();
    updateActionStatus();
    return;
  }
  if (!queryIntentValue && !receipt.rejected && receipt.settlement === "settled") {
    if (kind === "send_message") {
      state.drafts.delete(`composer:${state.contextId}`);
      state.replyToId = null;
    } else if (kind === "edit_message") {
      state.drafts.delete(`edit:${state.contextId}:${extra.target_id}`);
      if (state.editTargetId === String(extra.target_id)) state.editTargetId = null;
    } else if (kind === "delete_message") {
      state.drafts.delete(`edit:${state.contextId}:${extra.target_id}`);
      if (state.editTargetId === String(extra.target_id)) state.editTargetId = null;
      if (state.replyToId === String(extra.target_id)) state.replyToId = null;
    } else if (kind === "set_poll_votes") {
      state.pollDrafts.delete(`poll:${state.contextId}:${extra.target_id}`);
    }
  }
  rememberReceipt(receipt);
  try {
    const snapshot = await request("/api/state");
    noteTransportHealthy();
    state.statusFingerprint = queryStatusFingerprint(snapshot);
    if (snapshot.viewerId !== state.viewerId || snapshot.botGeneration !== state.botGeneration) {
      resetInteractionState();
    }
    state.observedRevision = Number(snapshot.publishedRevision);
    if (!queryIntentValue || (state.queryIntents.get(queryIntentValue.key) === queryIntentValue.token
      && queryIntentValue.scope === `${state.viewerId}:${state.contextGeneration}`)) {
      if (kind === "browse_candidates" && !receipt.rejected && receipt.settlement === "settled"
        && (!extra.modal_handle || snapshot.modal?.handle === extra.modal_handle)
        && Array.isArray(extra.selected_values)
        && fingerprint(extra.selected_values) === fingerprint(state.selectDrafts.get(extra.control_key) || [])) {
        const selected = snapshot.candidates?.[extra.control_key]?.selected;
        if (Array.isArray(selected)) {
          reconcileCandidateSelection(
            extra.control_key, selected, extra.selected_values, snapshot.publishedRevision,
          );
        }
      }
      await installSnapshot(snapshot, false);
    }
  } catch (_) {
    // A receipt is not a render snapshot. Keep the last installed projection until a healthy read.
    state.pendingAction = null;
    state.pendingQuery = null;
    noteTransportFailure("state-refresh-unavailable");
    localRender(Boolean(queryIntentValue));
  }
  state.pendingAction = null;
  state.pendingQuery = null;
  queuePendingCandidateValidations();
  updateActionStatus();
  drainIntents();
}
function dispatch(kind, extra = {}) {
  if (["browse_messages", "browse_candidates"].includes(kind)) {
    const key = kind === "browse_messages"
      ? "messages"
      : `candidate:${extra.control_key}:${extra.modal_handle || ""}`;
    queueQuery(key, kind, extra);
    return;
  }
  if (state.closed || !state.contextId || !state.protocolCompatible || state.pinnedCapture) return;
  if (!state.authorized && !["close", "viewer", "refresh"].includes(kind)) return;
  if (state.pendingAction || (state.transport.uncertainRequestId && kind !== "close")) {
    if (pageAction(kind)) pageIntent(kind, extra);
    return;
  }
  if (state.transport.uncertainRequestId) {
    if (state.transport.uncertainCloseAttemptFor === state.transport.uncertainRequestId) {
      pageIntent(kind, extra);
      return;
    }
    state.transport.uncertainCloseAttemptFor = state.transport.uncertainRequestId;
  }
  if (kind !== "close" && Number.isInteger(state.awaitingRevision)
    && state.awaitingRevision > state.publishedRevision) {
    if (pageAction(kind)) pageIntent(kind, extra);
    return;
  }
  state.externalNotice = null;
  if (kind === "viewer") redactPrivateView();
  if (kind === "configure_presentation") state.host = { width: extra.host_width, height: extra.host_height };
  performAction(kind, extra);
}

async function poll() {
  if (state.closed || !state.contextId || !state.protocolCompatible) return;
  try {
    const snapshot = await request("/api/state");
    noteTransportHealthy();
    reconcileUncertainAction(snapshot);
    state.observedRevision = Math.max(state.observedRevision, Number(snapshot.publishedRevision || 0));
    const stale = snapshot.context?.id === state.contextId
      && snapshot.context?.generation === state.contextGeneration
      && snapshot.publishedRevision < state.publishedRevision;
    if (!stale && !state.pendingQuery && !state.queuedQueries.size) {
      const statusFingerprint = queryStatusFingerprint(snapshot);
      if (
        snapshot.publishedRevision !== state.publishedRevision
        || snapshot.context?.generation !== state.contextGeneration
        || snapshot.viewerId !== state.viewerId
        || fingerprint(snapshot.modal) !== fingerprint(state.snapshot?.modal)
        || statusFingerprint !== state.statusFingerprint
      ) {
        state.statusFingerprint = statusFingerprint;
        await installSnapshot(snapshot, false);
      } else {
        renderActivity();
        updateActionStatus();
      }
    }
    queuePendingCandidateValidations();
    drainIntents();
  } catch (_) {
    if (!state.closed) noteTransportFailure("state-poll-unavailable");
  } finally {
    if (!state.closed) window.setTimeout(poll, 500);
  }
}
function releaseContext() {
  if (state.contextReleased || state.closed || !state.contextId || !state.capability) return;
  state.contextReleased = true;
  fetch(`/api/pages/${encodeURIComponent(state.contextId)}`, {
    method: "DELETE",
    headers: authHeaders(),
    cache: "no-store",
    keepalive: true,
  }).catch(() => {});
}

window.addEventListener("pagehide", (event) => {
  if (!event.persisted) releaseContext();
});
window.addEventListener("beforeunload", () => releaseContext());
window.addEventListener("pageshow", (event) => {
  if (event.persisted && !state.closed) poll();
});

async function bootstrap() {
  applyProfile();
  renderDiagnostics();
  if (!state.capability) {
    addDiagnostic({ code: "missing-capability", severity: "error", message: "Open this page from a Preview URL fragment", complete: false });
    ui.app.setAttribute("aria-busy", "false");
    return;
  }
  try {
    const snapshot = await request("/api/pages", "POST", {}, null);
    state.contextReleased = false;
    noteTransportHealthy();
    state.statusFingerprint = queryStatusFingerprint(snapshot);
    await installSnapshot(snapshot, true);
    if (!state.pinnedCapture) {
      queuePresentation();
      state.resizeObserver = new ResizeObserver(() => {
        const dimensions = getHostDimensions();
        if (dimensions.width !== state.host.width || dimensions.height !== state.host.height) queuePresentation();
      });
      state.resizeObserver.observe(ui.stage);
    }
    poll();
  } catch (_) {
    addDiagnostic({ code: "bootstrap-failed", severity: "error", message: "The authorized preview could not be opened.", remediation: "Reload the active Preview URL.", complete: false });
    ui.app.setAttribute("aria-busy", "false");
  }
}

ui.viewer.addEventListener("change", () => dispatch("viewer", { viewer_id: ui.viewer.value }));
ui.message.addEventListener("change", () => dispatch("focus", { target_id: ui.message.value }));
function updateViewport(field, minimum, maximum) {
  const value = Number(field.value);
  if (!Number.isInteger(value) || value < minimum || value > maximum
    || Number(ui.width.value) * Number(ui.height.value) > 32 * 1024 * 1024) {
    addDiagnostic({ code: "viewport-invalid", message: "Viewport dimensions must be bounded integers", complete: true });
    applyProfile();
    return;
  }
  ui.display.value = "fixed";
  queuePresentation();
}
ui.display.addEventListener("change", () => {
  const exact = state.snapshot?.presentation?.exactProfile || state.profile;
  ui.width.value = exact.width;
  ui.height.value = exact.height;
  queuePresentation();
});
ui.layout.addEventListener("change", queuePresentation);
ui.preset.addEventListener("change", () => {
  if (ui.preset.value === "custom") return;
  const [width, height] = ui.preset.value.split("x").map(Number);
  ui.width.value = width;
  ui.height.value = height;
  ui.display.value = "fixed";
  queuePresentation();
});
ui.useAvailable.addEventListener("click", () => {
  const host = getHostDimensions();
  ui.width.value = Math.max(240, host.width);
  ui.height.value = Math.max(180, host.height);
  ui.display.value = "fixed";
  queuePresentation();
});
ui.resetProfile.addEventListener("click", () => {
  ui.width.value = state.initialPresentation.width;
  ui.height.value = state.initialPresentation.height;
  ui.display.value = "fixed";
  queuePresentation();
});
ui.captureCurrent.addEventListener("click", () => {
  const rect = ui.app.getBoundingClientRect();
  state.captureViewport = { width: Math.round(rect.width), height: Math.round(rect.height) };
  updateCaptureRecipe();
});
ui.searchForm.addEventListener("submit", (event) => { event.preventDefault(); queueMessageQuery(ui.search.value); });
ui.previous.addEventListener("click", () => queueMessageQuery(state.snapshot.navigation.query, state.snapshot.navigation.previousCursor));
ui.next.addEventListener("click", () => queueMessageQuery(state.snapshot.navigation.query, state.snapshot.navigation.nextCursor));
ui.exactForm.addEventListener("submit", (event) => {
  event.preventDefault();
  dispatch("focus", { target_id: ui.exactId.value });
});
ui.backActivity.addEventListener("click", backActivity);
ui.selectApply.addEventListener("click", () => state.dropdown && commitDropdown(state.dropdown.key));
ui.selectCancel.addEventListener("click", () => state.dropdown && cancelDropdown(state.dropdown.key));
async function copyText(value) {
  try { await navigator.clipboard.writeText(value); ui.captureStatus.textContent = "Copied."; }
  catch (_) { ui.captureStatus.textContent = "Clipboard unavailable; select the text and copy manually."; }
}
ui.copyRecipe.addEventListener("click", () => copyText(ui.captureRecipe.value));
ui.copyReport.addEventListener("click", () => copyText(JSON.stringify(safeSupportReport(), null, 2)));
ui.downloadReport.addEventListener("click", () => {
  const report = safeSupportReport();
  if (!report) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "simcord-support.json";
  link.click();
  URL.revokeObjectURL(url);
});
ui.panel.addEventListener("toggle", () => {
  if (!ui.panel.open) ui.panel.querySelector("summary").focus({ preventScroll: true });
});
ui.panel.addEventListener("keydown", (event) => {
  if (event.key === "Escape") { ui.panel.open = false; event.preventDefault(); }
});
ui.timeline.addEventListener("scroll", () => {
  state.scrollIntent = captureScrollIntent();
  if (state.scrollIntent.policy === "bottom") ui.newMessages.hidden = true;
}, { passive: true });
ui.newMessages.addEventListener("click", () => {
  state.scrollIntent = { policy: "bottom" };
  ui.newMessages.hidden = true;
  dispatch("history", { direction: "latest" });
});
ui.diagnosticFilter.addEventListener("change", () => {
  state.diagnosticFilter = ui.diagnosticFilter.value;
  renderDiagnostics();
});
state.scrollObserver = new ResizeObserver(scheduleScrollCorrection);
state.scrollObserver.observe(ui.messageList);
state.scrollObserver.observe(ui.timeline);
ui.width.addEventListener("change", () => updateViewport(ui.width, 240, 32768));
ui.height.addEventListener("change", () => updateViewport(ui.height, 180, 32768));
ui.refresh.addEventListener("click", () => dispatch("refresh"));
ui.close.addEventListener("click", () => dispatch("close"));
ui.historyOlder.addEventListener("click", () => dispatch("history", { direction: "older" }));
ui.historyNewer.addEventListener("click", () => dispatch("history", { direction: "newer" }));
ui.replyCancel.addEventListener("click", () => {
  state.replyToId = null;
  state.editTargetId = null;
  updatePickers(state.snapshot);
  ui.composer.focus();
});
ui.composer.addEventListener("input", () => {
  state.drafts.set(ui.composer.dataset.controlKey, ui.composer.value);
  rememberFocus();
});
ui.composer.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    ui.composerForm.requestSubmit();
  }
});
ui.composerForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const content = ui.composer.value;
  if (state.editTargetId) {
    dispatch("edit_message", { target_id: state.editTargetId, content });
  } else if (content.trim()) {
    dispatch("send_message", {
      target_id: state.targetId,
      content,
      reply_to_id: state.replyToId,
    });
  }
});
ui.modal.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    if (state.dropdown) { event.preventDefault(); cancelDropdown(state.dropdown.key); return; }
    if (state.modalHandle) {
      event.preventDefault();
      clearModalValidation();
      state.dismissedModal = state.modalHandle;
      clearModalDrafts();
      state.modalErrorHandle = null;
      localRender(true);
    }
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = [...ui.modal.querySelectorAll("button, input, textarea, [tabindex]:not([tabindex='-1'])")]
    .filter((item) => !item.disabled && item.offsetParent !== null);
  if (!focusable.length) {
    event.preventDefault();
    ui.modal.querySelector(".modal-dialog")?.focus();
    return;
  }
  const index = focusable.indexOf(document.activeElement);
  const next = event.shiftKey
    ? (index <= 0 ? focusable.length - 1 : index - 1)
    : (index < 0 || index === focusable.length - 1 ? 0 : index + 1);
  event.preventDefault();
  focusable[next].focus();
});

bootstrap();
