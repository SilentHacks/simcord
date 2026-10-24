import { createModal } from "./modal.js";
import { createSurfaces } from "./surfaces.js";
import { closeLightbox, reconcileLightbox } from "./media.js";
import { initComposer } from "./composer.js";
import { createCommandPicker } from "./commands.js";
import { icon } from "./dom.js";
import { initWorkbench } from "./workbench.js";
import { initSelectDrafts } from "./select-drafts.js";
import { initTimeline } from "./timeline.js";
import { createTransport } from "./transport.js";
import { initConnection } from "./connection.js";
import { createSession } from "./session.js";
import { ownerGeometry as measureOwner } from "./geometry.js";
import { createAssets } from "./assets.js";
import { createActions } from "./actions.js";
import { createDiagnostics } from "./diagnostics.js";
import { createFocus } from "./focus.js";
import { createPresentation } from "./presentation.js";

/** @import {Snapshot, ActionContext, ActionBody, ActionMetadata, ActionReport, AdmittedAction, Receipt, Diagnostic, Profile, Dimensions, Dropdown, FontStatus, TransportReport, AssetOptions} from "./preview-types" */
/** @typedef {Record<"app" | "shell" | "workspace" | "stage" | "toolbar" | "sidebar" | "drawerScrim" | "inspectorCount" | "inspectorTabCount" | "channelContextName" | "channelContextHash" | "channelHash" | "layoutLabel" | "surface" | "modal" | "diagnostics" | "diagnosticsPanel" | "panel" | "capturePanel" | "captureStatus" | "activity" | "liveStatus" | "transportStatus" | "empty" | "pickerEmpty" | "channel" | "channelName" | "channelTopic" | "timeline" | "messageList" | "channelEmpty" | "replyContext" | "replyLabel" | "message" | "pageStatus" | "viewportReadout" | "action", HTMLElement>
 * & Record<"messagesToggle" | "inspectorToggle" | "inspectorClose" | "captureOpen" | "copyRecipe" | "copyReport" | "downloadReport" | "send" | "replyCancel" | "historyOlder" | "historyNewer" | "newMessages" | "previous" | "next" | "useAvailable" | "resetProfile" | "captureCurrent" | "refresh" | "close" | "backActivity" | "viewActionOutput", HTMLButtonElement>
 * & Record<"composerForm" | "searchForm" | "exactForm", HTMLFormElement>
 * & Record<"composer" | "captureRecipe", HTMLTextAreaElement>
 * & Record<"search" | "exactId" | "width" | "height", HTMLInputElement>
 * & Record<"viewer" | "display" | "layout" | "preset" | "diagnosticFilter", HTMLSelectElement>} PreviewUI */
/** @typedef {ActionContext & ActionReport & {capability: string, snapshot: Snapshot | null,
 * renderGeneration: number, rendering: boolean, ready: boolean, complete: boolean,
 * diagnostics: readonly Diagnostic[], lastAction: Receipt | null, lastActionKind: string | null, externalNotice: string | null,
 * profile: Readonly<Profile>, host: Readonly<Dimensions>, calibration: {status: string, reason: string},
 * modalHandle: string | null, dropdown: Readonly<Dropdown> | null, pickerPendingMedia: Promise<unknown>[],
 * renderPendingMedia: Promise<unknown>[], statusFingerprint: string, fontStatus: FontStatus,
 * transport: Readonly<TransportReport>, lastAnnouncement: string | null}} PageState */

/** @template {HTMLElement} T @param {string} id @returns {T} */
const $ = (id) => /** @type {T} */ (document.getElementById(id));
/** @type {PreviewUI} */
const ui = {
  app: $("preview-app"),
  shell: $("preview-shell"),
  workspace: $("preview-workspace"),
  stage: $("preview-stage"),
  toolbar: $("toolbar"),
  sidebar: $("messages-sidebar"),
  drawerScrim: $("drawer-scrim"),
  messagesToggle: $("messages-toggle"),
  inspectorToggle: $("inspector-toggle"),
  inspectorClose: $("inspector-close"),
  inspectorCount: $("inspector-count"),
  inspectorTabCount: $("inspector-tab-count"),
  captureOpen: $("capture-open"),
  channelContextName: $("channel-context-name"),
  channelContextHash: /** @type {HTMLElement} */ (document.querySelector(".channel-context-hash")),
  channelHash: /** @type {HTMLElement} */ (document.querySelector(".channel-hash")),
  layoutLabel: $("layout-label"),
  surface: $("focused-content"),
  modal: $("modal-root"),
  diagnostics: $("diagnostics"),
  diagnosticsPanel: $("diagnostics-panel"),
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
  empty: $("preview-empty"),
  pickerEmpty: $("message-picker-empty"),
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
  viewActionOutput: $("view-action-output"),
  action: $("action-status"),
};

const toolbarIcons = /** @type {[string, Parameters<typeof icon>[0]][]} */ ([["messages-toggle", "messages"], ["refresh", "refresh"], ["capture-open", "capture"], ["inspector-toggle", "inspector"]]);
for (const [id, name] of toolbarIcons) {
  $(id).prepend(icon(name));
}

const hash = window.location.hash.startsWith("#") ? window.location.hash.slice(1) : "";
window.addEventListener("hashchange", () => window.location.reload());
/** @type {PageState} */
const state = {
  capability: hash,
  snapshot: null,
  protocolCompatible: true,
  contextId: null,
  contextGeneration: 0,
  botGeneration: 0,
  publishedRevision: 0,
  renderGeneration: 0,
  rendering: false,
  viewerId: null,
  ready: false,
  complete: true,
  diagnostics: [],
  lastAction: null,
  lastActionKind: null,
  externalNotice: null,
  pendingAction: null,
  uncertainAction: null,
  pendingQuery: null,
  queuedQuery: null,
  queuedQueryCount: 0,
  queuedPageIntents: {},
  observedRevision: 0,
  profile: { theme: "dark", width: 960, height: 720, locale: "en-US", timezone: "UTC", deviceScale: 1, reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches, mediaTime: null },
  calibration: { status: "uncalibrated", reason: "No legitimate Discord reference fixture is bundled for this slice" },
  host: { width: 0, height: 0 },
  awaitingRevision: null,
  queryResultRevision: null,
  modalHandle: null,
  dropdown: null,
  pickerPendingMedia: [],
  renderPendingMedia: [],
  targetId: null,
  closed: false,
  authorized: false,
  pinnedCapture: false,
  statusFingerprint: "",
  fontStatus: { loaded: [], missing: [], faces: [] },
  transport: { state: "idle", failures: 0, recoveries: 0, uncertainRequestId: null, uncertainSequence: null, history: [] },

  lastAnnouncement: "",
};

const focus = createFocus();
const transport = createTransport(hash);
const { authHeaders, request, requestAction } = transport;
const assets = createAssets({ headers: () => authHeaders() });
const composer = initComposer({
  form: ui.composerForm,
  input: ui.composer,
  inputContainer: ui.composer.parentElement,
  sendButton: ui.send,
  replyContext: ui.replyContext,
  replyLabel: ui.replyLabel,
  replyCancel: ui.replyCancel,
  channelLabel,
  onDispatch: (kind, extra) => dispatch(kind, extra),
  onModeChange: () => { if (state.snapshot) updatePickers(state.snapshot); },
  onBeforeEdit: () => commandPicker.exit(),
  rememberFocus,
  setFocusKey: focus.setKey,
});
const commandPicker = createCommandPicker({
  form: ui.composerForm,
  input: ui.composer,
  composer,
  dispatch: (kind, extra, metadata) => dispatch(kind, extra, metadata),
  run: (body, files, metadata) => {
    dispatch("run_command", body, { commandDraftId: metadata?.draftId ?? null,
      commandDraftVersion: metadata?.draftVersion ?? null, uploads: files });
  },
  fetchCatalog: () => request("/api/commands"),
  announce: (message) => announceStatus(message),
  onCommandMode: (enabled, replyToId) => {
    composer.commandMode(enabled, replyToId);
    if (state.snapshot) composer.update(state.snapshot);
    updateActionStatus();
  },
  loadAsset,
  onPendingMedia: (pending) => {
    if (state.closed) return;
    state.pickerPendingMedia = pending;
    if (state.rendering) return;
    const generation = beginRender();
    void waitReady(generation, state.renderPendingMedia);
  },
});

/** @template T @param {T} value @returns {T} */
function clone(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value));
}
/** @template T @param {T} value @returns {Readonly<T>} */
function freeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.values(value).forEach(freeze);
  return Object.freeze(value);
}

/** @param {Element | null} element */
function ownerGeometry(element) {
  return measureOwner(element, ui.app, ui.stage);
}

function visibleMessageIds() {
  if (!state.authorized || state.modalHandle) return [];
  if (state.snapshot?.layout !== "channel") {
    return state.targetId && ui.surface.checkVisibility() ? [String(state.targetId)] : [];
  }
  const crop = ownerGeometry(ui.timeline)?.visibleCrop;
  if (!crop || !crop.width || !crop.height) return [];
  return timeline.visibleIds(crop);
}

function selectStates() {
  return Object.fromEntries([...document.querySelectorAll(".preview-select")].filter((element) => element instanceof HTMLElement).map((element) => {
    const key = element.dataset.controlKey || "";
    const values = selections.isPending(key) ? [] : selections.values(key);
    const minimum = Number(element.dataset.minimum), maximum = Number(element.dataset.maximum);
    const optional = element.dataset.optional === "true";
    const touched = selections.isTouched(key);
    const receipt = [...(state.snapshot?.activity || [])].reverse().find((item) =>
      item.target?.controlKey === key && item.acknowledgement !== "not_applicable");
    return [key, {
      values, minimum, maximum, optional,
      valid: (optional && !values.length) || (values.length >= minimum && values.length <= maximum),
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
  const activeControl = document.activeElement?.closest("[data-control-key]");
  const renderState = {
    openPopupKey: state.dropdown?.key || null,
    modalHandle: state.modalHandle,
    validationPaths: modal.report().validationPaths,
    ...assets.report(),
  };
  const value = {
    schemaVersion: 1,
    protocolVersion: 3,
    contextId: state.contextId,
    contextGeneration: state.contextGeneration,
    botGeneration: state.botGeneration,
    viewerId: state.viewerId || null,
    targetId: state.targetId,
    activeControlKey: state.authorized && activeControl instanceof HTMLElement ? activeControl.dataset.controlKey || null : null,
    visibleMessageIds: visibleMessageIds(),
    projectedMessageIds: [...(state.snapshot?.timeline || [])],
    publishedRevision: state.publishedRevision,
    publication: clone(state.snapshot?.publication || null),
    navigation: clone(state.snapshot?.navigation || null),
    presentation: clone(state.snapshot?.presentation || null),
    renderGeneration: state.renderGeneration,
    renderState,
    selectDrafts: selections.report(),
    selectStates: selectStates(),
    commandPicker: commandPicker.status(),
    lastAction: state.authorized ? clone(state.lastAction) : null,
    pendingAction: state.pendingAction ? {
      kind: state.pendingAction.kind,
      requestId: state.pendingAction.requestId,
      sequence: state.pendingAction.sequence,
      controlKey: state.authorized ? state.pendingAction.controlKey : null,
      targetId: state.authorized ? state.pendingAction.targetId : null,
    } : null,
    pendingQuery: state.authorized ? clone(state.pendingQuery) : null,
    ready: state.ready && !state.pendingAction && !Object.keys(state.queuedPageIntents).length
      && !commandPicker.isCatalogLoadingOpen(),
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
    diagnostics: state.diagnostics.map(({ renderOwner, ...diagnostic }) => clone(diagnostic)),
    activity: clone(state.snapshot?.activity || []),
    transport: {
      state: state.transport.state,
      failures: state.transport.failures,
      recoveries: state.transport.recoveries,
      uncertainRequestId: state.transport.uncertainRequestId,
      ...(state.transport.lastSuccessfulRead ? { lastSuccessfulRead: state.transport.lastSuccessfulRead } : {}),
      history: clone(state.transport.history),
    },
    authorized: state.authorized,
    calibration: { ...state.calibration },
    profile: { ...state.profile, fontStatus: clone(state.fontStatus) },
  };
  return freeze(value);
}
Object.defineProperty(window, "simcordPreview", { configurable: false, enumerable: true, get: statusObject });

function rememberFocus() { focus.remember(); }
/** @param {string | null} [key] */
function restoreFocus(key) { return focus.restore(key); }

/** @param {boolean} open */
function setModalIsolation(open) {
  [ui.channel, ui.empty, ui.surface].forEach((element) => {
    if (element) element.inert = open;
  });
  updateWorkbenchIsolation(open);
  ui.modal.setAttribute("aria-hidden", String(!open));
}
/** @param {Snapshot} snapshot */
function channelLabel(snapshot) { return workbench.channelLabel(snapshot); }
/** @param {Snapshot} snapshot */
function updatePickers(snapshot) {
  workbench.update(snapshot, { authorized: state.authorized, pinnedCapture: state.pinnedCapture,
    profile: state.profile, pendingAction: Boolean(state.pendingAction), queuedQuery: Boolean(state.queuedQuery),
    editTargetId: composer.report().editTargetId });
  composer.setContext({ contextId: state.contextId, targetId: state.targetId,
    authorized: state.authorized, pinnedCapture: state.pinnedCapture, pending: Boolean(state.pendingAction) });
  composer.update(snapshot);
  commandPicker.setContext({ authorized: state.authorized, pendingKind: state.pendingAction?.kind || null });
  commandPicker.update(snapshot);
  commandPicker.receiveSnapshot(snapshot);
}

function revokeAssets() {
  closeLightbox();
  document.querySelectorAll(".media-player").forEach((wrapper) => { if (wrapper instanceof HTMLElement) wrapper._previewMediaPlayer?.dispose(); });
  assets.reset();
}

/** @param {Snapshot["assets"]} manifests */
function pruneAssetUrls(manifests) {
  assets.update(manifests);
}

function beginRender() {
  state.renderGeneration += 1;
  state.ready = false;
  ui.app.setAttribute("aria-busy", "true");
  return state.renderGeneration;
}

/** @returns {Promise<void>} */
function nextFrames() {
  return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
}


function renderActivity() { workbench.activity(state.snapshot); }
/** @param {string} id @param {Receipt["target"]} [source] */
function viewActivityMessage(id, source) { workbench.viewActivityMessage(id, source); }
const diagnostics = createDiagnostics({
  ui, viewActivityMessage,
  announceStatus: (message, urgent) => { state.lastAnnouncement = null; announceStatus(message, urgent); },
  changed: (report) => { state.diagnostics = report.diagnostics; if (!state.closed) state.complete = report.complete; },
});
/** @param {Diagnostic} item */
function addDiagnostic(item) { diagnostics.add(item); }
/** @param {string | null} owner */
function clearRenderDiagnostics(owner) { diagnostics.clearRender(owner); }
function renderDiagnostics() { diagnostics.update(state.snapshot, state.publishedRevision, state.closed); }
const presentation = createPresentation({
  ui, initialProfile: state.profile, changed: (report) => Object.assign(state, report),
  addDiagnostic, diagnostics, renderDiagnostics, pageIntent, drainIntents,
});
function getHostDimensions() { return presentation.dimensions(); }
function queuePresentation() { presentation.queue(); }
function updateCaptureRecipe() { presentation.recipe(); }
const connection = initConnection({
  element: ui.transportStatus, changed: (report) => { state.transport = report; updateActionStatus(); },
  addDiagnostic, announceStatus,
  recoverDiagnostics: (codes, message) => diagnostics.recover(codes, message),
});
const { noteTransportFailure, noteTransportHealthy, renderTransportStatus } = connection;
/** @param {string} message @param {boolean} [urgent] */
function announceStatus(message, urgent = false) {
  if (message === state.lastAnnouncement) return;
  state.lastAnnouncement = message;
  ui.liveStatus.setAttribute("role", urgent ? "alert" : "status");
  ui.liveStatus.setAttribute("aria-live", urgent ? "assertive" : "polite");
  ui.liveStatus.textContent = message;
}


/** @param {number} generation @param {Promise<unknown>[]} pendingMedia */
async function waitReady(generation, pendingMedia) {
  let fontError = null;
  try {
    await Promise.all([...(pendingMedia || []), ...state.pickerPendingMedia]);
    await presentation.fonts();
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
  timeline.ready();
  state.ready = true;
  ui.app.setAttribute("aria-busy", "false");
}


/** @param {string} id @param {AssetOptions} [options] */
function loadAsset(id, options) { return assets.load(id, options); }



/** @param {unknown} value */
function fingerprint(value) {
  return value ? JSON.stringify(value) : "";
}




/** @param {Parameters<ReturnType<typeof createSurfaces>["options"]>} args */
function messageRenderOptions(...args) { return surfaces.options(...args); }
const timeline = initTimeline({
  ui, candidateLoading, fingerprint, clearRenderDiagnostics, messageRenderOptions, dispatch,
  candidateFingerprint: (scope) => selections.fingerprint(scope),
  onScrollLayout: () => fitOpenDropdowns(),
});
const { renderChannelTimeline } = timeline;

function updateActionStatus() {
  syncActionContext();
  const blocked = actions.admissionBlocked() || Boolean(state.transport.uncertainRequestId);
  commandPicker.setContext({ authorized: state.authorized, pendingKind: state.pendingAction?.kind || null });
  composer.setAdmission({ blocked, authorized: state.authorized, pinnedCapture: state.pinnedCapture });
  selections.admission({ blocked, pendingAction: state.pendingAction, pinnedCapture: state.pinnedCapture });
  surfaces.admission(blocked);
  timeline.admission(blocked);
  modal.admission(blocked);
  workbench.admission({ blocked, authorized: state.authorized, pinnedCapture: state.pinnedCapture, receipt: state.lastAction });
  ui.action.textContent = actions.message({
    receipt: state.lastAction, kind: state.lastActionKind, externalNotice: state.externalNotice,
    ready: state.ready, transport: state.transport,
  });
}


const selections = initSelectDrafts({
  rememberFocus, restoreFocus, onRender: localRender, queueQuery, dispatch, clearModalValidation,
  setFocusKey: focus.setKey,
  changed: (report) => { state.dropdown = report.dropdown; },
  queryPending: (controlKey, key) => state.pendingQuery?.controlKey === controlKey || actions.hasQuery(key),
});
const { clearModalDrafts, openDropdown, updateDraft, navigateDropdown, clearSelection, commitDropdown, cancelDropdown, fitOpenDropdowns } = selections;
function syncSelectionContext() {
  selections.updateContext({ pendingAction: state.pendingAction, modalHandle: state.modalHandle, pinnedCapture: state.pinnedCapture });
}
/** @param {string} key */
function candidateLoading(key) { syncSelectionContext(); return selections.candidateLoading(key); }
/** @param {Parameters<ReturnType<typeof initSelectDrafts>["queueCandidateQuery"]>} args */
function queueCandidateQuery(...args) { syncSelectionContext(); selections.queueCandidateQuery(...args); }
function queuePendingCandidateValidations() { syncSelectionContext(); selections.queuePendingCandidateValidations(); }

function localRender(renderDom = true) {
  rememberFocus();
  const generation = beginRender();
  if (renderDom && state.snapshot) renderSnapshot(state.snapshot, generation, true);
  else {
    renderDiagnostics();
    updateActionStatus();
    waitReady(generation, state.renderPendingMedia);
  }
}


const surfaces = createSurfaces({
  root: ui.surface, selections, composer, assets, dispatch, localRender, addDiagnostic, clearRenderDiagnostics,
  premiumNotice: () => {
    state.externalNotice = "Premium purchases are handled by Discord outside this preview. No purchase was started.";
    updateActionStatus();
  },
});
const modal = createModal({
  root: ui.modal, selections, loadAsset, assetMetadata: assets.manifest, dispatch, render: localRender,
  diagnostic: addDiagnostic, clearRenderDiagnostics, isolate: setModalIsolation, restoreFocus,
  fallbackFocus: () => ui.messagesToggle.focus(),
  changed: (report) => { state.modalHandle = report.modalHandle; syncSelectionContext(); },
  clearDiagnostic: () => diagnostics.clear("modal-validation"),
});
function clearModalValidation() { modal.clearValidation(); }


/** @param {Snapshot} snapshot @param {number} generation @param {boolean} [force] */
function renderSnapshot(snapshot, generation, force = false) {
  if (generation !== state.renderGeneration || state.closed) return;
  state.rendering = true;
  if (state.snapshot?.layout === "channel") timeline.begin();
  state.snapshot = snapshot;
  state.authorized = snapshot.status !== "access_denied";
  if (state.authorized) {
    diagnostics.clear("access-denied");
  }
  if (!state.authorized) {
    redactPrivateView();
    state.snapshot = snapshot;
    addDiagnostic({ code: "access-denied", severity: "error", complete: false });
  }
  const nextContextId = snapshot.context?.id || state.contextId;
  const nextGeneration = Number(snapshot.context?.generation || 0);
  const nextRevision = Number(snapshot.publishedRevision || 0);
  const nextViewer = snapshot.viewerId || null;
  state.contextId = nextContextId;
  transport.setContext(nextContextId);
  const previousTargetId = state.targetId;
  if (nextViewer !== state.viewerId) {
    diagnostics.reset();
    state.lastAction = null;
  }
  if (nextViewer !== state.viewerId || snapshot.botGeneration !== state.botGeneration) {
    revokeAssets();
    force = true;
  }
  pruneAssetUrls(snapshot.assets || {});
  state.contextGeneration = nextGeneration;
  state.botGeneration = Number(snapshot.botGeneration || 0);
  state.publishedRevision = nextRevision;
  state.observedRevision = Math.max(state.observedRevision, nextRevision);
  state.viewerId = nextViewer;
  state.targetId = snapshot.targetId ?? null;
  const previousMediaTime = state.profile.mediaTime;
  presentation.update(snapshot, generation);
  if (previousMediaTime !== state.profile.mediaTime) {
    assets.clearTimes();
  }
  updatePickers(snapshot);
  const openKey = state.dropdown?.key;
  const pendingMedia = [];
  surfaces.updateContext(snapshot, state.profile, state.dropdown);
  timeline.updateContext({ profile: state.profile, openKey });
  if (snapshot.layout === "channel") {
    surfaces.clearFocused();
    pendingMedia.push(...renderChannelTimeline(snapshot, previousTargetId, force));
  } else {
    timeline.reset();
    pendingMedia.push(...surfaces.focused(snapshot, force));
  }
  const previousHandle = state.modalHandle;
  pendingMedia.push(...modal.update(snapshot, {
    profile: state.profile, dropdown: state.dropdown, focusKey: focus.report().key, spoilerOptions: surfaces.spoilerOptions,
  }, force));
  const nextHandle = state.modalHandle;
  renderDiagnostics();
  updateActionStatus();
  renderActivity();
  connection.update(snapshot.publication?.publishedAt, state.profile.presentationTime);
  renderTransportStatus();
  updateCaptureRecipe();
  fitOpenDropdowns();
  reconcileLightbox();
  state.renderPendingMedia = pendingMedia;
  state.rendering = false;
  waitReady(generation, pendingMedia).then(() => {
    if (generation !== state.renderGeneration) return;
    const key = workbench.returnKey();
    if (!key) return;
    restoreFocus(key);
  });
  if (!nextHandle && !previousHandle) {
    requestAnimationFrame(() => {
      const active = document.activeElement;
      if (!active || active === document.body || !active.isConnected) restoreFocus();
    });
  }
}

window.addEventListener("resize", () => fitOpenDropdowns());
document.addEventListener("scroll", (event) => {
  if (!(event.target instanceof Element) || !event.target.closest(".select-list")) fitOpenDropdowns();
}, true);

/** @param {unknown} snapshot @param {boolean} [force] @returns {Promise<boolean>} */
async function installSnapshot(snapshot, force = false) {
  if (!snapshot || state.closed) return false;
  if (!session.compatible(snapshot)) {
    terminateSession("Preview protocol 3 is required; reload this page.");
    state.protocolCompatible = false;
    diagnostics.replace([{
      id: "local:protocol-mismatch",
      code: "protocol-mismatch",
      category: "protocol",
      severity: "error",
      state: "current",
      message: "Preview protocol 3 is required; reload this page.",
      remediation: "Reload the Preview URL to receive a compatible snapshot.",
      complete: false,
    }]);
    renderDiagnostics();
    ui.app.setAttribute("aria-busy", "false");
    return false;
  }
  if (!session.accept(snapshot)) return false;
  state.protocolCompatible = true;
  state.pinnedCapture = snapshot.context.id.startsWith("capture_");
  if (state.pinnedCapture) document.body.dataset.managedCapture = "true";
  else delete document.body.dataset.managedCapture;
  const validatedControl = actions.validatedCandidate(snapshot, selections.values);
  if (snapshot.viewerId !== state.viewerId || snapshot.botGeneration !== state.botGeneration) resetInteractionState();
  selections.observe(snapshot, validatedControl);
  state.observedRevision = snapshot.publishedRevision;
  state.statusFingerprint = queryStatusFingerprint(snapshot);
  rememberFocus();
  if (!state.pendingAction && "lastAction" in snapshot) {
    if (state.lastAction?.requestId !== snapshot.lastAction?.requestId) state.lastActionKind = null;
    state.lastAction = snapshot.lastAction || null;
  }
  reconcileUncertainAction(snapshot);
  const generation = beginRender();
  renderSnapshot(snapshot, generation, force);
  syncActionContext();
  queuePendingCandidateValidations();
  return true;
}


/** @param {string} kind @param {ActionBody} body */
function pageIntent(kind, body) { syncActionContext(); actions.pageIntent(kind, body); }
/** @param {string} key @param {string} kind @param {ActionBody} body @param {ActionMetadata | null} [metadata] */
function queueQuery(key, kind, body, metadata = null) {
  syncActionContext(); actions.queueQuery(key, kind, body, metadata);
}


/** @param {string | undefined} query @param {string | null} [cursor] */
function queueMessageQuery(query, cursor = null) { workbench.browseMessages(query, cursor); }



/** @param {AdmittedAction | null} action @param {Receipt} receipt */
function completeActionDrafts(action, receipt) {
  if (!action || receipt.rejected || receipt.settlement !== "settled") return;
  composer.completeActionDrafts(action, receipt);
  surfaces.outcome(action);
}


function resetInteractionState() {
  commandPicker.clear();
  selections.reset();
  actions.resetInteraction();
  focus.reset();
  state.pickerPendingMedia = [];
  selections.closeDropdown();
  composer.reset();
  surfaces.reset();
  clearModalDrafts();
  modal.reset();
  state.lastActionKind = null;
  workbench.resetNavigation();
}

function redactPrivateView() {
  revokeAssets();
  surfaces.reset();
  timeline.reset();
  modal.reset();
  diagnostics.reset();
  state.diagnostics = [];
  state.complete = false;
  state.ready = false;
  state.renderGeneration += 1;
  actions.redact();
  resetInteractionState();
  composer.clear();
  resetWorkbench();
  presentation.reset();
  state.renderPendingMedia = [];
  state.snapshot = null;
  state.lastAction = null;
  state.viewerId = null;
  state.targetId = null;
  ui.viewer.replaceChildren();
  ui.message.replaceChildren();
  ui.captureRecipe.value = "";
  ui.exactId.value = "";
  ui.search.value = "";
  ui.diagnostics.replaceChildren();
  ui.channelName.textContent = "Unavailable channel";
  ui.channelContextName.textContent = "Unavailable channel";
  ui.channelContextHash.textContent = "#";
  ui.channelHash.textContent = "#";
  ui.channelTopic.textContent = "";
  ui.channelTopic.hidden = true;
  ui.channel.hidden = true;
  ui.composerForm.hidden = true;
  ui.empty.hidden = true;
  ui.pickerEmpty.hidden = false;
  ui.pickerEmpty.textContent = "No visible messages.";
  ui.surface.replaceChildren();
  ui.messageList.replaceChildren();
  renderActivity();
}

/** @param {Receipt} receipt */
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


/** @param {Snapshot} snapshot */
function queryStatusFingerprint(snapshot) {
  return JSON.stringify(snapshot);
}
function syncActionContext() {
  actions.setContext({
    contextId: state.contextId, contextGeneration: state.contextGeneration,
    botGeneration: state.botGeneration, publishedRevision: state.publishedRevision,
    observedRevision: state.observedRevision, viewerId: state.viewerId, targetId: state.targetId,
    closed: state.closed, authorized: state.authorized, protocolCompatible: state.protocolCompatible,
    pinnedCapture: state.pinnedCapture,
  });
}
const actions = createActions({
  session: {
    beginAction: (invalidate) => session.beginAction(invalidate),
    endAction: (rejected) => session.endAction(rejected),
    readState: () => session.readState(),
  },
  send: requestAction,
  readResult: (action) => request(`/api/action-result?request_id=${encodeURIComponent(action.requestId)}&sequence=${action.sequence}`),
  changed: (report) => { Object.assign(state, report); presentation.setAdmission(report); syncSelectionContext(); updateActionStatus(); },
  render: () => { if (state.pendingAction?.kind === "run_command") commandPicker.setPending(); localRender(false); },
  healthy: noteTransportHealthy, failure: noteTransportFailure, diagnostic: addDiagnostic,
  outcome: (action, receipt, body, metadata) => commandPicker.receiveAction(action.kind, receipt, body, metadata),
  completeDraft: completeActionDrafts,
  idle: () => { queuePendingCandidateValidations(); commandPicker.resume(); updateActionStatus(); },
  rememberReceipt: (receipt, kind) => { rememberReceipt(receipt); state.lastActionKind = kind; },
  terminate: terminateSession,
  beforePage: (kind, extra) => {
    state.externalNotice = null;
    if (kind === "viewer") redactPrivateView();
    if (kind === "configure_presentation") presentation.noteHost(extra.host_width, extra.host_height);
  },
  acceptedClose: () => {
    session.stop(); presentation.dispose(); timeline.dispose();
    state.closed = true; state.authorized = false; state.ready = false;
    state.externalNotice = "Preview session closed."; announceStatus(state.externalNotice);
    redactPrivateView(); state.complete = !state.transport.uncertainRequestId; updateActionStatus();
  },
  recovered: (own) => connection.recovered(own),
});
function drainIntents() { syncActionContext(); actions.drain(); }
/** @param {Snapshot} snapshot */
function reconcileUncertainAction(snapshot) { syncActionContext(); actions.observe(snapshot); }
/** @param {string} kind @param {ActionBody} [extra] @param {ActionMetadata | null} [metadata] */
function dispatch(kind, extra = {}, metadata = null) {
  syncActionContext();
  actions.dispatch(kind, extra, { ...metadata,
    ...(["send_message", "edit_message"].includes(kind)
      ? { draftVersion: composer.draftVersion(kind, state.contextId, extra.target_id),
        composerModeVersion: composer.modeVersion() } : {}),
  });
}

/** @param {string} message */
function terminateSession(message) {
  session.stop();
  if (state.pendingAction && !state.transport.uncertainRequestId) {
    noteTransportFailure("action-response-unavailable", state.pendingAction.requestId, state.pendingAction.sequence);
  }
  state.closed = true;
  state.authorized = false;
  actions.stop();
  state.externalNotice = message;
  presentation.dispose();
  timeline.dispose();
  redactPrivateView();
  ui.app.setAttribute("aria-busy", "false");
  updateActionStatus();
}

/** @type {ReturnType<typeof createSession>} */
const session = createSession({
  read: (signal) => request("/api/state", "GET", undefined, state.contextId, signal),
  /** @param {unknown} snapshot @returns {Promise<boolean>} */
  install: async (snapshot) => {
    const completingQuery = state.pendingAction?.queryIntent && !state.pendingQuery;
    if (session.compatible(snapshot) && !completingQuery && queryStatusFingerprint(snapshot) === state.statusFingerprint) {
      if (!session.accept(snapshot)) return false;
      reconcileUncertainAction(snapshot);
      return true;
    }
    return installSnapshot(snapshot);
  },
  tick: () => {
    noteTransportHealthy();
    syncActionContext(); actions.tick();
    queuePendingCandidateValidations();
    drainIntents();
  },
  failure: (error) => {
    if (error instanceof Error && "status" in error && (error.status === 401 || error.status === 410)) terminateSession("Preview session is unavailable. Reload the active Preview URL.");
    else noteTransportFailure("state-poll-unavailable");
  },
});
window.addEventListener("pagehide", (event) => {
  session.pause();
  if (!event.persisted && state.contextId && !state.closed && !state.pinnedCapture) {
    // Lease expiry covers an undeliverable release; BFCache pages retain their context.
    void fetch(`/api/pages/${encodeURIComponent(state.contextId)}`, {
      method: "DELETE", headers: authHeaders(), keepalive: true,
    }).catch(() => {});
  }
});
window.addEventListener("pageshow", (event) => { if (event.persisted) session.resume(); });

async function bootstrap() {
  presentation.apply();
  renderDiagnostics();
  if (!state.capability) {
    addDiagnostic({ code: "missing-capability", severity: "error", message: "Open this page from a Preview URL fragment", complete: false });
    ui.app.setAttribute("aria-busy", "false");
    return;
  }
  try {
    const metadata = await fetch("/backend-diagnostics.json", { cache: "no-store" });
    if (!metadata.ok) throw new Error("Preview diagnostic metadata unavailable");
    diagnostics.catalog(await metadata.json());
    /** @type {Snapshot} */
    const snapshot = await request("/api/pages", "POST", {}, null);
    noteTransportHealthy();
    state.statusFingerprint = "";
    await installSnapshot(snapshot, true);
    presentation.observe();
    session.resume();
  } catch (_) {
    addDiagnostic({ code: "bootstrap-failed", severity: "error", message: "The authorized preview could not be opened.", remediation: "Reload the active Preview URL.", complete: false });
    ui.app.setAttribute("aria-busy", "false");
  }
}

ui.viewer.addEventListener("change", () => dispatch("viewer", { viewer_id: ui.viewer.value }));
const workbench = initWorkbench({ ui, dispatch, updateActionStatus, focusKey: () => focus.report().key });
const { updateWorkbenchIsolation, resetWorkbench } = workbench;
ui.searchForm.addEventListener("submit", (event) => { event.preventDefault(); queueMessageQuery(ui.search.value); });
ui.previous.addEventListener("click", () => { if (state.snapshot) queueMessageQuery(state.snapshot.navigation.query, state.snapshot.navigation.previousCursor); });
ui.next.addEventListener("click", () => { if (state.snapshot) queueMessageQuery(state.snapshot.navigation.query, state.snapshot.navigation.nextCursor); });
ui.exactForm.addEventListener("submit", (event) => {
  event.preventDefault();
  dispatch("focus", { target_id: ui.exactId.value });
});
ui.backActivity.addEventListener("click", workbench.backActivity);
ui.viewActionOutput.addEventListener("click", () => {
  const output = (state.lastAction?.outcomes || []).find((outcome) =>
    ["response", "followup", "message", "deferred"].includes(outcome.kind) && outcome.messageId);
  if (output) viewActivityMessage(String(output.messageId), state.lastAction?.target);
});
ui.refresh.addEventListener("click", () => dispatch("refresh"));
ui.close.addEventListener("click", () => dispatch("close"));
ui.historyOlder.addEventListener("click", () => dispatch("history", { direction: "older" }));
ui.historyNewer.addEventListener("click", () => dispatch("history", { direction: "newer" }));

bootstrap();
