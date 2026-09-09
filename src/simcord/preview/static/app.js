import { renderMessage, renderModal } from "./components.js";

const $ = (id) => document.getElementById(id);
const ui = {
  app: $("preview-app"),
  surface: $("message-surface"),
  modal: $("modal-root"),
  diagnostics: $("diagnostics"),
  empty: $("message-picker-empty"),
  viewer: $("viewer-picker"),
  message: $("message-picker"),
  theme: $("theme-toggle"),
  width: $("viewport-width"),
  height: $("viewport-height"),
  refresh: $("refresh"),
  close: $("close"),
  action: $("action-status"),
};

const hash = window.location.hash.startsWith("#") ? window.location.hash.slice(1) : "";
const state = {
  capability: hash,
  snapshot: null,
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
  pendingAction: null,
  sequence: 0,
  profile: { theme: "dark", width: 960, height: 720, locale: "en-US", timezone: "UTC", deviceScale: 1, reducedMotion: true },
  calibration: { status: "uncalibrated", reason: "No legitimate Discord reference fixture is bundled for this slice" },
  drafts: new Map(),
  modalDrafts: new Map(),
  modalControls: {},
  modalHandle: null,
  dismissedModal: null,
  dropdown: null,
  lastMessageKey: null,
  lastMessageFingerprint: "",
  lastModalFingerprint: "",
  objectUrls: new Set(),
  pollTimer: null,
  closed: false,
  focusKey: null,
};

function freeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.values(value).forEach(freeze);
  return Object.freeze(value);
}

function statusObject() {
  return freeze({
    protocolVersion: state.snapshot?.protocolVersion ?? 1,
    contextId: state.contextId,
    contextGeneration: state.contextGeneration,
    botGeneration: state.botGeneration,
    publishedRevision: state.publishedRevision,
    renderGeneration: state.renderGeneration,
    lastAction: state.lastAction,
    ready: state.ready,
    complete: state.complete,
    calibration: state.calibration,
    diagnostics: [...state.diagnostics],
    profile: { ...state.profile },
  });
}
Object.defineProperty(window, "simcordPreview", { configurable: false, enumerable: true, get: statusObject });

function rememberFocus() {
  const active = document.activeElement;
  state.focusKey = active instanceof HTMLElement ? active.dataset.controlKey || null : null;
}

function restoreFocus() {
  if (!state.focusKey) return;
  const controls = [...document.querySelectorAll("[data-control-key]")];
  const target = controls.find((item) => item.dataset.controlKey === state.focusKey);
  if (target instanceof HTMLElement) target.focus();
}

function revokeAssets() {
  state.objectUrls.forEach((url) => URL.revokeObjectURL(url));
  state.objectUrls.clear();
}

function beginRender() {
  revokeAssets();
  state.renderGeneration += 1;
  state.ready = false;
  ui.app.setAttribute("aria-busy", "true");
  return state.renderGeneration;
}

function nextFrames() {
  return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
}

function addDiagnostic(diagnostic) {
  const item = { severity: "warning", complete: false, ...diagnostic };
  const fingerprint = JSON.stringify(item);
  if (!state.localDiagnostics.some((entry) => JSON.stringify(entry) === fingerprint)) state.localDiagnostics.push(item);
  renderDiagnostics();
}

function renderDiagnostics() {
  const diagnostics = [...(state.snapshot?.diagnostics || []), ...state.localDiagnostics];
  const seen = new Set();
  state.diagnostics = diagnostics.filter((item) => {
    const key = JSON.stringify(item);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  state.complete = !state.diagnostics.some((item) => item.complete === false || item.severity === "error");
  ui.diagnostics.replaceChildren();
  if (!state.diagnostics.length) {
    ui.diagnostics.append(Object.assign(document.createElement("p"), { textContent: "No diagnostics" }));
    return;
  }
  const heading = document.createElement("h2");
  heading.textContent = "Diagnostics";
  ui.diagnostics.append(heading);
  const list = document.createElement("ul");
  state.diagnostics.forEach((item) => {
    const row = document.createElement("li");
    row.className = `diagnostic-${item.severity || "warning"}`;
    row.textContent = `${item.code ? `${item.code}: ` : ""}${item.message || ""}`;
    list.append(row);
  });
  ui.diagnostics.append(list);
}

async function waitReady(generation, pendingMedia) {
  try {
    await Promise.all(pendingMedia || []);
    if (document.fonts?.ready) await document.fonts.ready;
    await nextFrames();
  } catch (_) {
    // Media failures are represented by a diagnostic and an unavailable tile.
  }
  if (generation !== state.renderGeneration || state.closed) return;
  state.ready = true;
  ui.app.setAttribute("aria-busy", "false");
}

function profileFromSnapshot(snapshot) {
  return {
    ...state.profile,
    theme: state.profile.theme || "dark",
    width: Number(state.profile.width || 960),
    height: Number(state.profile.height || 720),
    locale: snapshot?.profile?.locale || state.profile.locale || "en-US",
    timezone: snapshot?.profile?.timezone || state.profile.timezone || "UTC",
  };
}

function applyProfile() {
  document.documentElement.dataset.theme = state.profile.theme;
  ui.app.style.setProperty("--preview-width", `${state.profile.width}px`);
  ui.app.style.setProperty("--preview-height", `${state.profile.height}px`);
  ui.theme.textContent = state.profile.theme === "dark" ? "Light theme" : "Dark theme";
  ui.width.value = String(state.profile.width);
  ui.height.value = String(state.profile.height);
}

function loadAsset(assetId, generation) {
  return fetch(`/api/assets/${encodeURIComponent(assetId)}`, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) throw new Error(`asset request failed (${response.status})`);
    const url = URL.createObjectURL(await response.blob());
    if (generation !== state.renderGeneration || state.closed) {
      URL.revokeObjectURL(url);
      throw new Error("stale asset generation");
    }
    state.objectUrls.add(url);
    return url;
  });
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
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(detail || `preview request failed (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}

function messageFingerprint(message) {
  return message ? JSON.stringify(message) : "";
}

function modalFingerprint(modal) {
  return modal ? JSON.stringify(modal) : "";
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
  ui.message.replaceChildren();
  (snapshot.messages || []).forEach((message) => {
    const option = document.createElement("option");
    option.value = String(message.id);
    option.textContent = `${message.author?.name || "Unknown"}: ${(message.content || "").slice(0, 70) || "(component message)"}`;
    ui.message.append(option);
  });
  ui.message.value = snapshot.targetId || "";
  ui.message.disabled = !(snapshot.messages || []).length;
  ui.empty.hidden = Boolean(snapshot.selected);
  ui.surface.hidden = !snapshot.selected;
}

function updateActionStatus() {
  if (state.pendingAction) {
    ui.action.textContent = `${state.pendingAction.kind}…`;
    return;
  }
  const action = state.lastAction;
  ui.action.textContent = action ? `${action.dispatch || "not dispatched"} · ${action.settlement || "pending"}` : "";
}

function currentDrafts(key) {
  return key.startsWith("modal:") ? state.modalDrafts : state.drafts;
}

function initDraft(key, value) {
  const drafts = currentDrafts(key);
  if (!drafts.has(key)) drafts.set(key, value);
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

function openDropdown(key, selected, multi, minimum, maximum, highlight) {
  rememberFocus();
  if (state.dropdown?.key === key) {
    state.dropdown = null;
  } else {
    state.dropdown = { key, selected: [...selected], multi, minimum, maximum, highlight: highlight ?? selected[0] };
  }
  localRender(true);
}

function updateDraft(key, value, multi, minimum, maximum, selected) {
  const drafts = currentDrafts(key);
  const current = Array.isArray(drafts.get(key)) ? [...drafts.get(key)] : [...selected];
  let next = multi ? (current.includes(String(value)) ? current.filter((item) => item !== String(value)) : [...current, String(value)]) : [String(value)];
  if (multi && next.length > maximum) {
    addDiagnostic({ code: "select-max", message: `Selection cannot exceed ${maximum} values` });
    localRender(false);
    return;
  }
  if (multi && next.length < minimum) {
    addDiagnostic({ code: "select-min", message: `Select at least ${minimum} values before applying` });
    localRender(false);
    return;
  }
  drafts.set(key, next);
  if (state.dropdown) state.dropdown.highlight = String(value);
  localRender(true);
}

function commitDropdown(key) {
  const dropdown = state.dropdown;
  const values = [...(currentDrafts(key).get(key) || [])];
  if (dropdown?.multi && (values.length < dropdown.minimum || values.length > dropdown.maximum)) {
    addDiagnostic({ code: "select-invalid", message: "Selection was not dispatched because its count is invalid" });
    localRender(false);
    return;
  }
  state.dropdown = null;
  localRender(true);
  if (!key.startsWith("modal:")) dispatch("select", { custom_id: key.slice("message:".length), values });
}

function cancelDropdown(key) {
  const dropdown = state.dropdown;
  if (!dropdown || dropdown.key !== key) return;
  currentDrafts(key).set(key, [...dropdown.selected]);
  state.dropdown = null;
  localRender(true);
}

defaults: {
  // A named block keeps the event wiring grouped without an extra helper class.
  document.addEventListener("pointerdown", (event) => {
    if (state.dropdown && !(event.target instanceof Element && event.target.closest(".preview-select"))) cancelDropdown(state.dropdown.key);
  });
}

function submitModal(values) {
  const modal = state.snapshot?.modal;
  if (!modal || modal.handle !== state.modalHandle) return;
  const error = validateModalValues(modal.payload || {}, values);
  if (error) {
    addDiagnostic({ code: "modal-validation", severity: "error", message: error, complete: false });
    localRender(false);
    return;
  }
  dispatch("modal_submit", { modal_handle: modal.handle, values });
}

function renderSnapshot(snapshot, generation, force = false) {
  if (generation !== state.renderGeneration || state.closed) return;
  state.snapshot = snapshot;
  state.contextId = snapshot.context?.id || state.contextId;
  state.contextGeneration = Number(snapshot.context?.generation || 0);
  state.botGeneration = Number(snapshot.botGeneration || 0);
  state.publishedRevision = Number(snapshot.publishedRevision || 0);
  state.profile = profileFromSnapshot(snapshot);
  applyProfile();
  updatePickers(snapshot);
  const selected = snapshot.selected;
  const selectedKey = selected ? String(selected.id) : null;
  const selectedFingerprint = messageFingerprint(selected);
  const shouldRenderMessage = force || selectedKey !== state.lastMessageKey || selectedFingerprint !== state.lastMessageFingerprint;
  let pendingMedia = [];
  if (shouldRenderMessage) {
    state.lastMessageKey = selectedKey;
    state.lastMessageFingerprint = selectedFingerprint;
    const options = {
      drafts: state.drafts,
      candidates: snapshot.candidates || {},
      dropdown: state.dropdown,
      onInit: initDraft,
      onOpen: openDropdown,
      onClick: (customId) => dispatch("click", { custom_id: customId }),
      onCommit: commitDropdown,
      onCancel: cancelDropdown,
      loadAsset: (id) => loadAsset(id, generation),
      isCurrent: () => generation === state.renderGeneration,
      onDiagnostic: addDiagnostic,
      pendingMedia,
    };
    renderMessage(ui.surface, selected, options);
  }
  const modal = snapshot.modal && snapshot.modal.handle !== state.dismissedModal ? snapshot.modal : null;
  const modalKey = modal ? modalFingerprint(modal) : "";
  if (force || modalKey !== state.lastModalFingerprint) {
    const rendered = renderModal(ui.modal, modal?.payload, {
      drafts: state.modalDrafts,
      candidates: snapshot.candidates || {},
      onInit: initDraft,
      onDraft: (key, value) => { state.modalDrafts.set(key, value); localRender(false); },
      onCancel: () => {
        if (state.dropdown) {
          cancelDropdown(state.dropdown.key);
          return;
        }
        state.dismissedModal = state.modalHandle;
        state.modalControls = {};
        localRender(true);
      },
      onSubmit: submitModal,
      onDiagnostic: addDiagnostic,
    });
    state.modalControls = rendered.controls;
    state.modalHandle = modal?.handle || null;
  }
  renderDiagnostics();
  updateActionStatus();
  waitReady(generation, pendingMedia);
  requestAnimationFrame(restoreFocus);
}

async function installSnapshot(snapshot, force = false) {
  if (!snapshot || state.closed) return;
  const generation = beginRender();
  renderSnapshot(snapshot, generation, force);
}

function validateModalValues(modal, values) {
  const controls = {};
  const walk = (node) => {
    if (!node || typeof node !== "object") return;
    if (typeof node.custom_id === "string") controls[node.custom_id] = node;
    Object.values(node).forEach((value) => Array.isArray(value) ? value.forEach(walk) : walk(value));
  };
  (modal.components || []).forEach(walk);
  for (const [id, component] of Object.entries(controls)) {
    const type = Number(component.type);
    const value = values[id];
    if (type === 4) {
      const text = String(value ?? "");
      if (component.required !== false && !text) return `Required modal control '${id}' cannot be empty`;
      if (component.min_length !== undefined && text.length < Number(component.min_length)) return `${id} is shorter than its minimum length`;
      if (component.max_length !== undefined && text.length > Number(component.max_length)) return `${id} exceeds its maximum length`;
    } else if ([3, 5, 6, 7, 8, 19, 22].includes(type)) {
      const count = Array.isArray(value) ? value.length : 0;
      const minimum = Number(component.min_values ?? 1);
      const maximum = Number(component.max_values ?? (type === 22 ? component.options?.length || 1 : 1));
      if (component.required !== false && count < minimum) return `${id} requires at least ${minimum} value(s)`;
      if (count > maximum) return `${id} accepts at most ${maximum} value(s)`;
    } else if (type === 21 && component.required !== false && !value) return `${id} requires one choice`;
  }
  return null;
}

async function dispatch(kind, extra = {}) {
  if (state.pendingAction || state.closed || !state.contextId) return;
  const requestId = globalThis.crypto?.randomUUID?.() || `preview-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const sequence = state.sequence + 1;
  state.sequence = sequence;
  const body = {
    sequence,
    request_id: requestId,
    generation: state.contextGeneration,
    bot_generation: state.botGeneration,
    kind,
    ...extra,
  };
  state.pendingAction = { kind, requestId, sequence };
  localRender(false);
  try {
    const result = await request("/api/action", "POST", body);
    state.lastAction = result;
    state.pendingAction = null;
    if (Array.isArray(result.diagnostics)) result.diagnostics.forEach((item) => addDiagnostic(item));
    if (kind === "close") {
      state.closed = true;
      state.ready = false;
      updateActionStatus();
      return;
    }
    const snapshot = await request("/api/state");
    if (snapshot.context?.generation !== state.contextGeneration) {
      state.dropdown = null;
      state.modalDrafts.clear();
    }
    state.dismissedModal = null;
    await installSnapshot(snapshot, true);
  } catch (error) {
    state.pendingAction = null;
    state.lastAction = { requestId, sequence, dispatched: false, settlement: "rejected", diagnostics: [{ type: "PreviewRequestError", message: String(error) }] };
    addDiagnostic({ code: "action-failed", severity: "error", message: String(error), complete: false });
    localRender(false);
  }
}

async function poll() {
  if (state.closed || !state.contextId) return;
  try {
    const snapshot = await request("/api/state");
    if (snapshot.publishedRevision !== state.publishedRevision || snapshot.context?.generation !== state.contextGeneration || modalFingerprint(snapshot.modal) !== state.lastModalFingerprint) {
      await installSnapshot(snapshot, false);
    }
  } catch (error) {
    if (!state.closed) addDiagnostic({ code: "state-poll", severity: "error", message: String(error), complete: false });
  } finally {
    if (!state.closed) state.pollTimer = window.setTimeout(poll, 500);
  }
}

async function bootstrap() {
  applyProfile();
  renderDiagnostics();
  if (!state.capability) {
    addDiagnostic({ code: "missing-capability", severity: "error", message: "Open this page from a Preview URL fragment", complete: false });
    ui.app.setAttribute("aria-busy", "false");
    return;
  }
  try {
    const snapshot = await request("/api/pages", "POST", {} , null);
    await installSnapshot(snapshot, true);
    poll();
  } catch (error) {
    addDiagnostic({ code: "bootstrap-failed", severity: "error", message: String(error), complete: false });
    ui.app.setAttribute("aria-busy", "false");
  }
}

ui.viewer.addEventListener("change", () => dispatch("viewer", { viewer_id: ui.viewer.value }));
ui.message.addEventListener("change", () => dispatch("focus", { target_id: ui.message.value }));
ui.theme.addEventListener("click", () => {
  state.profile.theme = state.profile.theme === "dark" ? "light" : "dark";
  localRender(true);
});
function updateViewport(field, minimum, maximum) {
  const value = Number(field.value);
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    addDiagnostic({ code: "viewport-invalid", message: "Viewport dimensions must be bounded integers" });
    field.value = String(state.profile[field === ui.width ? "width" : "height"]);
    return;
  }
  state.profile[field === ui.width ? "width" : "height"] = value;
  localRender(true);
}
ui.width.addEventListener("change", () => updateViewport(ui.width, 240, 32768));
ui.height.addEventListener("change", () => updateViewport(ui.height, 180, 32768));
ui.refresh.addEventListener("click", () => dispatch("refresh"));
ui.close.addEventListener("click", () => dispatch("close"));

bootstrap();
