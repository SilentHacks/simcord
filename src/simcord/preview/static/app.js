import { renderMessage, renderModal } from "./components.js";

const $ = (id) => document.getElementById(id);
const ui = {
  app: $("preview-app"),
  toolbar: $("toolbar"),
  surface: $("message-surface"),
  modal: $("modal-root"),
  diagnostics: $("diagnostics"),
  empty: $("message-picker-empty"),
  viewer: $("viewer-picker"),
  message: $("message-picker"),
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
  pendingAction: null,
  sequence: 0,
  profile: { theme: "dark", width: 960, height: 720, locale: "en-US", timezone: "UTC", deviceScale: 1, reducedMotion: true },
  profileCustomized: { width: false, height: false },
  calibration: { status: "uncalibrated", reason: "No legitimate Discord reference fixture is bundled for this slice" },
  drafts: new Map(),
  modalDrafts: new Map(),
  modalTouched: new Set(),
  modalHandle: null,
  dismissedModal: null,
  modalError: null,
  modalErrorHandle: null,
  dropdown: null,
  lastMessageKey: null,
  lastMessageFingerprint: "",
  targetId: null,
  objectUrls: new Map(),
  closed: false,
  authorized: true,
  modalOpenerFocusKey: null,
  focusKey: null,
  focusSelection: null,
  focusInModal: false,
  contextReleased: false,
  statusFingerprint: "",
  fontStatus: { loaded: [], missing: [], faces: [] },
};
function freeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.values(value).forEach(freeze);
  return Object.freeze(value);
}

function statusObject() {
  const renderState = {
    openPopupKey: state.dropdown?.key || null,
    modalHandle: state.modalHandle,
    validationPaths: state.modalError ? [state.modalErrorHandle] : [],
    mediaCaptureTimes: {},
  };
  const value = {
    schemaVersion: state.snapshot?.protocolVersion ?? 2,
    protocolVersion: state.snapshot?.protocolVersion ?? 2,
    contextId: state.contextId,
    contextGeneration: state.contextGeneration,
    botGeneration: state.botGeneration,
    viewerId: state.viewerId,
    targetId: state.targetId,
    activeControlKey: state.focusKey,
    visibleMessageIds: [...(state.snapshot?.timeline || [])],
    publishedRevision: state.publishedRevision,
    renderGeneration: state.renderGeneration,
    renderState,
    lastAction: state.lastAction ? JSON.parse(JSON.stringify(state.lastAction)) : null,
    pendingAction: state.pendingAction ? JSON.parse(JSON.stringify(state.pendingAction)) : null,
    ready: state.ready,
    complete: state.complete,
    diagnostics: JSON.parse(JSON.stringify(state.diagnostics)),
    authorized: state.authorized,
    calibration: { ...state.calibration },
    profile: { ...state.profile, fontStatus: JSON.parse(JSON.stringify(state.fontStatus)) },
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
  const target = focusTarget(key);
  if (!(target instanceof HTMLElement) || target.inert || target.matches(":disabled")) return false;
  target.focus();
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
  [ui.toolbar, ui.empty, ui.surface, ui.diagnostics].forEach((element) => {
    if (element) element.inert = open;
  });
  ui.modal.setAttribute("aria-hidden", String(!open));
}

function revokeAssets() {
  state.objectUrls.forEach((url) => URL.revokeObjectURL(url));
  state.objectUrls.clear();
}

function beginRender() {
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
      } catch (error) {
        return { ...requirement, loaded: false, error: String(error?.message || error) };
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
  if (fontError) {
    addDiagnostic({
      code: "preview-fonts",
      severity: "error",
      message: fontError.message || String(fontError),
      remediation: "Install the preview assets with `pip install simcord[preview]`, then reload the Preview URL.",
      complete: false,
    });
  }
  if (generation !== state.renderGeneration || state.closed) return;
  state.ready = true;
  ui.app.setAttribute("aria-busy", "false");
}

function profileFromSnapshot(snapshot) {
  const configured = snapshot?.profile || {};
  return {
    ...state.profile,
    width: state.profileCustomized.width ? state.profile.width : Number(configured.width || 960),
    height: state.profileCustomized.height ? state.profile.height : Number(configured.height || 720),
    locale: configured.locale || state.profile.locale || "en-US",
    timezone: configured.timezone || state.profile.timezone || "UTC",
    presentationTime: configured.presentationTime || state.profile.presentationTime || null,
  };
}

function applyProfile() {
  ui.app.style.setProperty("--preview-width", `${state.profile.width}px`);
  ui.app.style.setProperty("--preview-height", `${state.profile.height}px`);
  ui.width.value = String(state.profile.width);
  ui.height.value = String(state.profile.height);
}

function loadAsset(assetId, { download = false } = {}) {
  // download=1 serves the original bytes for explicit save/open actions;
  // display loads always take the normalized form under the plain key.
  const key = download ? `${assetId}:download` : assetId;
  const cached = state.objectUrls.get(key);
  if (cached) return Promise.resolve(cached);
  const generation = state.contextGeneration;
  return fetch(`/api/assets/${encodeURIComponent(assetId)}${download ? "?download=1" : ""}`, { headers: authHeaders() }).then(async (response) => {
    if (!response.ok) throw new Error(`asset request failed (${response.status})`);
    const url = URL.createObjectURL(await response.blob());
    if (generation !== state.contextGeneration || state.closed) {
      URL.revokeObjectURL(url);
      throw new Error("stale asset generation");
    }
    state.objectUrls.set(key, url);
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
  if (!response.ok) throw new Error((await response.text().catch(() => "")) || `preview request failed (${response.status})`);
  return response.json();
}

function fingerprint(value) {
  return value ? JSON.stringify(value) : "";
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
  (snapshot.messageIndex || []).forEach((message) => {
    const option = document.createElement("option");
    option.value = String(message.id);
    option.textContent = `${message.author_name || "Unknown"}: ${String(message.excerpt ?? "").slice(0, 70) || "(component message)"}`;
    ui.message.append(option);
  });
  ui.message.value = snapshot.targetId || "";
  ui.message.disabled = !(snapshot.messageIndex || []).length;
  const target = snapshot.targetId ? snapshot.messages?.[String(snapshot.targetId)] : null;
  ui.empty.hidden = Boolean(target);
  ui.surface.hidden = !target;
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
    cancelDropdown(key);
    return;
  }
  state.dropdown = {
    key,
    selected: [...selected],
    multi,
    minimum,
    maximum,
    highlight: highlight ?? selected[0] ?? null,
  };
  localRender(true);
  state.focusKey = `${key}:list`;
  const list = [...document.querySelectorAll("[data-control-key]")].find(
    (item) => item.dataset.controlKey === `${key}:list`,
  );
  if (list instanceof HTMLElement) list.focus();
}

function updateDraft(key, value, multi, minimum, maximum, selected) {
  const drafts = currentDrafts(key);
  const current = Array.isArray(drafts.get(key)) ? [...drafts.get(key)] : [...selected];
  const next = multi ? (current.includes(String(value)) ? current.filter((item) => item !== String(value)) : [...current, String(value)]) : [String(value)];
  if (multi && next.length > maximum) {
    addDiagnostic({ code: "select-max", message: `Selection cannot exceed ${maximum} values` });
    localRender(false);
    return;
  }
  drafts.set(key, next);
  if (state.dropdown) state.dropdown.highlight = String(value);
  localRender(true);
}

function navigateDropdown(key, highlight) {
  if (state.dropdown?.key !== key) return;
  state.dropdown.highlight = highlight;
  localRender(true);
}

function clearSelection(key) {
  if (key.startsWith("modal:")) state.modalTouched.add(key);
  currentDrafts(key).set(key, []);
  if (state.dropdown?.key === key) {
    state.dropdown.selected = [];
    state.dropdown.highlight = null;
    state.dropdown = null;
  }
  localRender(true);
  if (!key.startsWith("modal:")) dispatch("select", { control_key: key, values: [] });
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
  if (!key.startsWith("modal:")) dispatch("select", { control_key: key, values });
}

function cancelDropdown(key) {
  const dropdown = state.dropdown;
  if (!dropdown || dropdown.key !== key) return;
  currentDrafts(key).set(key, [...dropdown.selected]);
  state.dropdown = null;
  localRender(true);
}

document.addEventListener("pointerdown", (event) => {
  if (state.dropdown && !(event.target instanceof Element && event.target.closest(".preview-select"))) cancelDropdown(state.dropdown.key);
});

function submitModal(values) {
  const modal = state.snapshot?.modal;
  if (!modal || modal.handle !== state.modalHandle) return;
  const error = validateModalValues(modal.payload || {}, values);
  if (error) {
    state.modalError = error;
    state.modalErrorHandle = modal.handle;
    addDiagnostic({ code: "modal-validation", severity: "error", message: error, complete: false });
    state.lastModalFingerprint = "";
    localRender(true);
    return;
  }
  state.modalError = null;
  state.modalErrorHandle = null;
  state.modalDrafts.clear();
  state.modalTouched.clear();
  dispatch("modal_submit", { modal_handle: modal.handle, values });
}

function renderSnapshot(snapshot, generation, force = false) {
  if (generation !== state.renderGeneration || state.closed) return;
  state.snapshot = snapshot;
  state.authorized = snapshot.status !== "access_denied";
  if (state.authorized) {
    state.localDiagnostics = state.localDiagnostics.filter((item) => item.code !== "access-denied");
  }
  if (!state.authorized) {
    revokeAssets();
    state.localDiagnostics = [{
      code: "access-denied",
      severity: "error",
      message: "Preview access was revoked; refresh after authorization is restored.",
      complete: false,
    }];
    state.drafts.clear();
    state.modalDrafts.clear();
    state.modalTouched.clear();
    state.dropdown = null;
    state.dismissedModal = null;
    state.modalHandle = null;
    state.modalOpenerFocusKey = null;
    ui.surface.replaceChildren();
  }
  state.contextId = snapshot.context?.id || state.contextId;
  const nextGeneration = Number(snapshot.context?.generation || 0);
  const nextRevision = Number(snapshot.publishedRevision || 0);
  const nextViewer = snapshot.viewerId || null;
  if (state.authorized && (nextGeneration !== state.contextGeneration || nextRevision !== state.publishedRevision)) state.localDiagnostics = [];
  if (nextGeneration !== state.contextGeneration || nextViewer !== state.viewerId) revokeAssets();
  state.contextGeneration = nextGeneration;
  state.botGeneration = Number(snapshot.botGeneration || 0);
  state.publishedRevision = nextRevision;
  state.viewerId = nextViewer;
  state.targetId = snapshot.targetId ?? null;
  state.profile = profileFromSnapshot(snapshot);
  applyProfile();
  updatePickers(snapshot);
  const selected = snapshot.targetId ? snapshot.messages?.[String(snapshot.targetId)] || null : null;
  const selectedKey = selected ? String(selected.id) : null;
  const selectedFingerprint = fingerprint(selected);
  const shouldRenderMessage = force || selectedKey !== state.lastMessageKey || selectedFingerprint !== state.lastMessageFingerprint;
  const pendingMedia = [];
  if (shouldRenderMessage) {
    state.lastMessageKey = selectedKey;
    state.lastMessageFingerprint = selectedFingerprint;
    renderMessage(ui.surface, selected, {
      drafts: state.drafts,
      candidates: snapshot.candidates || {},
      assets: snapshot.assets || {},
      mentions: {
        ...(selected?.mention_names || {}),
        ...(selected?.mention_channel_names || {}),
      },
      locale: state.profile.locale,
      timezone: state.profile.timezone,
      presentationTime: state.profile.presentationTime,
      dropdown: state.dropdown,
      onInit: initDraft,
      onOpen: openDropdown,
      onDraft: updateDraft,
      onClick: (controlKey) => dispatch("click", { control_key: controlKey }),
      onCommit: commitDropdown,
      onCancel: cancelDropdown,
      onNavigate: navigateDropdown,
      onClear: clearSelection,
      loadAsset,
      isCurrent: () => generation === state.renderGeneration,
      onDiagnostic: addDiagnostic,
      onLocalRender: () => localRender(true),
      pendingMedia,
    });
  }
  const modal = snapshot.modal && snapshot.modal.handle !== state.dismissedModal ? snapshot.modal : null;
  const modalKey = modal ? fingerprint(modal) : "";
  if (modal && state.modalErrorHandle !== modal.handle) {
    state.modalError = null;
    state.modalErrorHandle = modal.handle;
  } else if (!modal) {
    state.modalError = null;
    state.modalErrorHandle = null;
  }
  const nextHandle = modal?.handle || null;
  const previousHandle = state.modalHandle;
  if (nextHandle && !previousHandle) state.modalOpenerFocusKey = state.focusKey;
  if (nextHandle !== state.modalHandle) {
    state.modalDrafts.clear();
    state.modalTouched.clear();
  }
  setModalIsolation(Boolean(nextHandle));
  if (force || modalKey !== state.lastModalFingerprint) {
    const rendered = renderModal(ui.modal, modal?.payload, {
      drafts: state.modalDrafts,
      dropdown: state.dropdown,
      candidates: snapshot.candidates || {},
      modalHandle: modal?.handle || "",
      isTouched: (key) => state.modalTouched.has(key),
      loadAsset,
      assets: snapshot.assets || {},
      validationError: state.modalError,
      locale: state.profile.locale,
      presentationTime: state.profile.presentationTime,
      pendingMedia,
      isCurrent: () => generation === state.renderGeneration,
      onInit: initDraft,
      onSelectOpen: openDropdown,
      onSelectDraft: (key, value, multi, minimum, maximum, selected) => {
        state.modalTouched.add(key);
        updateDraft(key, value, multi, minimum, maximum, selected);
      },
      onSelectCommit: commitDropdown,
      onSelectCancel: cancelDropdown,
      onNavigate: navigateDropdown,
      onClear: clearSelection,
      onDraft: (key, value) => {
        state.modalDrafts.set(key, value);
        state.modalTouched.add(key);
        if (state.modalError) {
          state.modalError = null;
          state.lastModalFingerprint = fingerprint(state.snapshot?.modal);
          document.querySelectorAll(".field-error").forEach((error) => error.remove());
        }
        localRender(false);
      },
      onFiles: (key, files) => {
        state.modalDrafts.set(key, files);
        state.modalTouched.add(key);
        state.modalError = null;
        state.lastModalFingerprint = fingerprint(state.snapshot?.modal);
        document.querySelectorAll(".field-error").forEach((error) => error.remove());
        localRender(false);
      },
      onCancel: () => {
        if (state.dropdown) { cancelDropdown(state.dropdown.key); return; }
        state.dismissedModal = state.modalHandle;
        state.modalDrafts.clear();
        state.modalTouched.clear();
        state.modalError = null;
        state.modalErrorHandle = null;
        localRender(true);
      },
      onSubmit: submitModal,
      onDiagnostic: addDiagnostic,
    });
    if (nextHandle && !previousHandle) requestAnimationFrame(() => rendered.focus?.focus());
    else if (nextHandle) restoreFocus();
    else if (previousHandle) restoreFocus(state.modalOpenerFocusKey) || (ui.message.disabled ? ui.viewer : ui.message).focus();
  }
  state.modalHandle = nextHandle;
  state.lastModalFingerprint = modalKey;
  renderDiagnostics();
  updateActionStatus();
  fitOpenDropdowns();
  waitReady(generation, pendingMedia);
  if (!nextHandle && !previousHandle) requestAnimationFrame(() => restoreFocus());
}

function fitOpenDropdowns() {
  for (const wrap of document.querySelectorAll(".preview-select.is-open")) {
    const trigger = wrap.querySelector(".select-trigger");
    const list = wrap.querySelector(".select-list");
    if (!trigger || !list) continue;
    wrap.classList.remove("opens-up");
    const needed = Math.min(list.scrollHeight, 220) + 8;
    if (window.innerHeight - trigger.getBoundingClientRect().bottom < needed) wrap.classList.add("opens-up");
  }
}

async function installSnapshot(snapshot, force = false) {
  if (!snapshot || state.closed) return false;
  const compatible = Number(snapshot.protocolVersion) === 2
    && snapshot.context && typeof snapshot.context.id === "string"
    && Number.isInteger(snapshot.context.generation)
    && Array.isArray(snapshot.messageIndex)
    && snapshot.messages && typeof snapshot.messages === "object" && !Array.isArray(snapshot.messages)
    && Array.isArray(snapshot.timeline)
    && snapshot.history && typeof snapshot.history === "object";
  if (!compatible) {
    state.protocolCompatible = false;
    state.authorized = false;
    state.snapshot = null;
    state.localDiagnostics = [{
      code: "protocol-mismatch",
      severity: "error",
      message: "Preview protocol 2 is required; reload this page.",
      remediation: "Reload the Preview URL to receive a compatible snapshot.",
      complete: false,
    }];
    renderDiagnostics();
    ui.app.setAttribute("aria-busy", "false");
    return false;
  }
  state.protocolCompatible = true;
  rememberFocus();
  if (!state.pendingAction && "lastAction" in snapshot) state.lastAction = snapshot.lastAction;
  const generation = beginRender();
  renderSnapshot(snapshot, generation, force);
  return true;
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
  if (
    state.pendingAction
    || state.closed
    || !state.authorized
    || !state.protocolCompatible
    || !state.contextId
  ) return;
  const requestId = globalThis.crypto?.randomUUID?.() || `preview-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const sequence = state.sequence + 1;
  const body = {
    sequence,
    request_id: requestId,
    generation: state.contextGeneration,
    bot_generation: state.botGeneration,
    kind,
    published_revision: state.publishedRevision,
    ...extra,
  };
  if (["click", "select"].includes(kind)) body.target_id = state.targetId;
  state.pendingAction = { kind, requestId, sequence };
  localRender(false);
  try {
    const result = await requestAction(body);
    if (result && typeof result.expectedSequence === "number") state.sequence = result.expectedSequence;
    state.pendingAction = null;
    state.lastAction = result;
    if (Array.isArray(result.diagnostics)) result.diagnostics.forEach((item) => addDiagnostic(item));
    if (kind === "close") { state.closed = true; state.ready = false; revokeAssets(); updateActionStatus(); return; }
    const snapshot = await request("/api/state");
    if (snapshot.context?.generation !== state.contextGeneration) {
      state.dropdown = null;
      state.drafts.clear();
      state.modalDrafts.clear();
      state.modalTouched.clear();
      state.dismissedModal = null;
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
  if (state.closed || !state.contextId || !state.protocolCompatible) return;
  try {
    const snapshot = await request("/api/state");
    const statusFingerprint = JSON.stringify({
      status: snapshot.status,
      diagnostics: snapshot.diagnostics,
      botGeneration: snapshot.botGeneration,
      context: snapshot.context,
    });
    if (
      snapshot.publishedRevision !== state.publishedRevision
      || snapshot.context?.generation !== state.contextGeneration
      || fingerprint(snapshot.modal) !== state.lastModalFingerprint
      || statusFingerprint !== state.statusFingerprint
    ) {
      state.statusFingerprint = statusFingerprint;
      await installSnapshot(snapshot, false);
    }
  } catch (error) {
    if (!state.closed) addDiagnostic({ code: "state-poll", severity: "error", message: String(error), complete: false });
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
    state.statusFingerprint = JSON.stringify({
      status: snapshot.status,
      diagnostics: snapshot.diagnostics,
      botGeneration: snapshot.botGeneration,
      context: snapshot.context,
    });
    await installSnapshot(snapshot, true);
    poll();
  } catch (error) {
    addDiagnostic({
      code: String(error).includes("expired") ? "context-expired" : "bootstrap-failed",
      severity: "error",
      message: String(error),
      complete: false,
    });
    ui.app.setAttribute("aria-busy", "false");
  }
}

ui.viewer.addEventListener("change", () => dispatch("viewer", { viewer_id: ui.viewer.value }));
ui.message.addEventListener("change", () => dispatch("focus", { target_id: ui.message.value }));
function updateViewport(field, minimum, maximum) {
  const value = Number(field.value);
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    addDiagnostic({ code: "viewport-invalid", message: "Viewport dimensions must be bounded integers" });
    field.value = String(state.profile[field === ui.width ? "width" : "height"]);
    return;
  }
  state.profile[field === ui.width ? "width" : "height"] = value;
  state.profileCustomized[field === ui.width ? "width" : "height"] = true;
  localRender(true);
}
ui.width.addEventListener("change", () => updateViewport(ui.width, 240, 32768));
ui.height.addEventListener("change", () => updateViewport(ui.height, 180, 32768));
ui.refresh.addEventListener("click", () => dispatch("refresh"));
ui.close.addEventListener("click", () => dispatch("close"));
ui.modal.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    if (state.dropdown) { event.preventDefault(); cancelDropdown(state.dropdown.key); return; }
    if (state.modalHandle) {
      event.preventDefault();
      state.dismissedModal = state.modalHandle;
      state.modalDrafts.clear();
      state.modalTouched.clear();
      localRender(true);
    }
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = [...ui.modal.querySelectorAll("button, input, textarea, [tabindex]:not([tabindex='-1'])")].filter((item) => !item.disabled && item.offsetParent !== null);
  if (focusable.length < 2) return;
  const index = focusable.indexOf(document.activeElement);
  const next = event.shiftKey ? (index <= 0 ? focusable.length - 1 : index - 1) : (index === focusable.length - 1 ? 0 : index + 1);
  event.preventDefault();
  focusable[next].focus();
});

bootstrap();
