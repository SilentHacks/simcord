/** @import {Snapshot, Profile, Presentation, Dimensions, FontStatus, AdmittedAction, ActionReport, ActionBody, Diagnostic} from "./preview-types" */
/** @import {PreviewUI} from "./app" */
/** @typedef {ReturnType<import("./diagnostics").createDiagnostics>} Diagnostics */
/** Logical viewport/profile, font observations, and private capture/support UI.
 * @param {{ui: PreviewUI, initialProfile: Readonly<Profile>,
 * changed: (report: {profile: Readonly<Profile>, host: Readonly<Dimensions>, fontStatus: FontStatus}) => void,
 * addDiagnostic: (item: Diagnostic) => void, diagnostics: Diagnostics, renderDiagnostics: () => void,
 * pageIntent: (kind: string, body: ActionBody) => void, drainIntents: () => void}} options
 */
export function createPresentation({ ui, initialProfile, changed, addDiagnostic, diagnostics, renderDiagnostics, pageIntent, drainIntents }) {
  /** @type {{profile: Profile, snapshot: Snapshot | null, initialPresentation: Dimensions | null,
   * host: Dimensions, pinnedCapture: boolean, queuedPageIntents: ActionReport["queuedPageIntents"],
   * pendingAction: AdmittedAction | null, fontStatus: FontStatus, captureViewport: Dimensions | null,
   * publishedRevision: number, renderGeneration: number}} */
  const state = { profile: { ...initialProfile }, snapshot: null, initialPresentation: null,
    host: { width: 0, height: 0 }, pinnedCapture: false, queuedPageIntents: {}, pendingAction: null,
    fontStatus: { loaded: [], missing: [], faces: [] }, captureViewport: null, publishedRevision: 0, renderGeneration: 0 };
  /** @type {ResizeObserver | null} */
  let observer = null;
  /** @template T @param {T} value @returns {T} */
  const clone = (value) => value == null ? value : JSON.parse(JSON.stringify(value));
  function publish() { changed(Object.freeze({ profile: Object.freeze({ ...state.profile }), host: Object.freeze({ ...state.host }), fontStatus: state.fontStatus })); }
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
/** @type {Promise<{family: string, css: string, sample: string, loaded: boolean, error?: string}[]> | undefined} */
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
  publish();
  if (missing.length) {
    const names = missing.map((face) => face.family).join(", ");
    throw new Error(`Packaged preview fonts failed to load: ${names}. Reinstall simcord[preview] and retry.`);
  }
  return faces;
}
/** @param {Snapshot} snapshot @returns {Profile} */
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
    mediaTime: typeof configured.mediaTime === "number" && Number.isFinite(configured.mediaTime) ? configured.mediaTime : null,
  };
}

function applyProfile() {
  /** @type {Partial<Presentation>} */
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
    const fields = /** @type {[HTMLInputElement, number][]} */ ([[ui.width, fixed ? exact.width : width], [ui.height, fixed ? exact.height : height]]);
    for (const [field, value] of fields) {
      if (!fixed || Number(field.value) === Number(value)) delete field.dataset.dirty;
      if (!field.dataset.dirty) field.value = String(value);
    }
    ui.display.value = presentation.display || "responsive";
    ui.layout.value = presentation.layout || state.snapshot?.layout || "message";
  }
  ui.width.readOnly = ui.display.value !== "fixed";
  ui.height.readOnly = ui.display.value !== "fixed";
  renderPresentationStatus();
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
    diagnostics: diagnostics.support(),
  };
}

/** @param {Dimensions | null} [dimensions] */
function updateCaptureRecipe(dimensions = null) {
  if (!state.snapshot || state.pinnedCapture) return;
  /** @type {Partial<Presentation>} */
  const presentation = state.snapshot.presentation || {};
  /** @type {Partial<Dimensions>} */
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
  ui.copyRecipe.disabled = false;
  ui.copyReport.disabled = !safeSupportReport();
}

function renderPresentationStatus() {
  if (!ui.viewportReadout) return;
  /** @type {Partial<Presentation>} */
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
function getHostDimensions() {
  return { width: Math.max(1, Math.floor(ui.stage.clientWidth)), height: Math.max(1, Math.floor(ui.stage.clientHeight)) };
}

function queuePresentation() {
  if (state.pinnedCapture) return;
  const host = getHostDimensions();
  state.host = host;
  publish();
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
/** @param {HTMLInputElement} field @param {number} minimum @param {number} maximum */
function updateViewport(field, minimum, maximum) {
  const value = Number(field.value);
  if (!Number.isInteger(value) || value < minimum || value > maximum
    || Number(ui.width.value) * Number(ui.height.value) > 32 * 1024 * 1024) {
    addDiagnostic({ code: "viewport-invalid", message: "Viewport dimensions must be bounded integers", complete: true });
    delete field.dataset.dirty;
    applyProfile();
    return;
  }
  diagnostics.clear("viewport-invalid");
  renderDiagnostics();
  ui.display.value = "fixed";
  queuePresentation();
}
ui.display.addEventListener("change", () => {
  const exact = state.snapshot?.presentation?.exactProfile || state.profile;
  ui.width.value = String(exact.width);
  ui.height.value = String(exact.height);
  queuePresentation();
});
ui.layout.addEventListener("change", queuePresentation);
ui.preset.addEventListener("change", () => {
  if (ui.preset.value === "custom") return;
  const [width, height] = ui.preset.value.split("x").map(Number);
  ui.width.value = String(width);
  ui.height.value = String(height);
  ui.display.value = "fixed";
  queuePresentation();
});
ui.useAvailable.addEventListener("click", () => {
  const host = getHostDimensions();
  ui.width.value = String(Math.max(240, host.width));
  ui.height.value = String(Math.max(180, host.height));
  ui.display.value = "fixed";
  queuePresentation();
});
ui.resetProfile.addEventListener("click", () => {
  if (!state.initialPresentation) return;
  ui.width.value = String(state.initialPresentation.width);
  ui.height.value = String(state.initialPresentation.height);
  ui.display.value = "fixed";
  queuePresentation();
});
ui.captureCurrent.addEventListener("click", () => {
  const rect = ui.app.getBoundingClientRect();
  state.captureViewport = { width: Math.round(rect.width), height: Math.round(rect.height) };
  updateCaptureRecipe();
});
/** @param {string} value @param {() => void} recovery */
async function copyText(value, recovery) {
  try { await navigator.clipboard.writeText(value); ui.captureStatus.textContent = "Copied."; }
  catch (_) { recovery(); }
}
ui.copyRecipe.addEventListener("click", () => copyText(ui.captureRecipe.value, () => {
  ui.captureRecipe.focus();
  ui.captureRecipe.select();
  ui.captureStatus.textContent = "Clipboard unavailable; the private recipe is selected for manual copying.";
}));
ui.copyReport.addEventListener("click", () => copyText(JSON.stringify(safeSupportReport(), null, 2), () => {
  ui.downloadReport.focus();
  ui.captureStatus.textContent = "Clipboard unavailable; use Download support report for the redacted JSON.";
}));
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
ui.width.addEventListener("input", () => { ui.width.dataset.dirty = "true"; });
ui.height.addEventListener("input", () => { ui.height.dataset.dirty = "true"; });
ui.width.addEventListener("change", () => updateViewport(ui.width, 240, 32768));
ui.height.addEventListener("change", () => updateViewport(ui.height, 180, 32768));
  return {
    /** @param {Snapshot} snapshot @param {number} generation */
    update: (snapshot, generation) => {
      state.snapshot = snapshot; state.pinnedCapture = snapshot.context?.id?.startsWith("capture_") || false;
      state.publishedRevision = snapshot.publishedRevision; state.renderGeneration = generation;
      state.initialPresentation ||= clone(snapshot.presentation?.exactProfile);
      state.profile = profileFromSnapshot(snapshot); publish(); applyProfile();
    },
    /** @param {ActionReport} report */
    setAdmission: (report) => { state.queuedPageIntents = report.queuedPageIntents; state.pendingAction = report.pendingAction; },
    /** @param {number | undefined} width @param {number | undefined} height */
    noteHost: (width, height) => { if (width === undefined || height === undefined) return; state.host = { width, height }; publish(); },
    observe: () => {
      observer?.disconnect();
      if (state.pinnedCapture) return;
      queuePresentation();
      observer = new ResizeObserver(() => {
        const dimensions = getHostDimensions();
        if (dimensions.width !== state.host.width || dimensions.height !== state.host.height) queuePresentation();
      });
      observer.observe(ui.stage);
    },
    reset: () => { state.snapshot = null; state.captureViewport = null; ui.captureRecipe.value = ""; },
    dispose: () => { observer?.disconnect(); observer = null; },
    fonts: loadRequiredFonts, apply: applyProfile, recipe: updateCaptureRecipe,
    dimensions: getHostDimensions, queue: queuePresentation,
  };
}
