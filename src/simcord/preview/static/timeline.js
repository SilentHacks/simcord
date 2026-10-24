import { renderMessage } from "./messages.js";
import { disposeSurface } from "./dom.js";
import { updateActionControls } from "./surfaces.js";

/** @import {Snapshot, Profile, Message, ActionBody, RenderOptions} from "./preview-types" */
/** @import {PreviewUI} from "./app" */
/** @typedef {{policy: string, targetId?: string, anchorId?: string, anchorTop?: number, oldScrollTop?: number, survivingIds?: string[]}} ScrollIntent */
/** @param {{ui: Pick<PreviewUI, "messageList" | "timeline" | "newMessages" | "channelEmpty" | "historyOlder" | "historyNewer" | "channel">,
 * candidateLoading: (key: string) => boolean, candidateFingerprint: (scope: string) => string,
 * fingerprint: (value: unknown) => string, clearRenderDiagnostics: (owner: string | null) => void,
 * messageRenderOptions: (snapshot: Snapshot, pending: Promise<unknown>[], message: Message | null, channel: boolean) => RenderOptions,
 * dispatch: (kind: string, body: ActionBody) => void, onScrollLayout: () => void}} options */

export function initTimeline({ ui, candidateLoading, candidateFingerprint, fingerprint, clearRenderDiagnostics, messageRenderOptions, dispatch, onScrollLayout }) {
  /** @type {{profile: Partial<Profile>, messageNodes: Map<string, {element: HTMLElement, fingerprint: string, pendingMedia: Promise<unknown>[]}>,
   * dayNodes: Map<string, HTMLElement>, scrollIntent: ScrollIntent | null, dropdown: {key: string} | null, ready: boolean}} */
  const state = { profile: {}, messageNodes: new Map(), dayNodes: new Map(), scrollIntent: null, dropdown: null, ready: false };
  /** @type {number | null} */
  let correction = null;
  const observer = new ResizeObserver(() => {
    if (correction !== null) return;
    correction = requestAnimationFrame(() => {
      correction = null;
      reconcileScrollIntent();
      onScrollLayout();
    });
  });
  observer.observe(ui.messageList);
  observer.observe(ui.timeline);
  ui.timeline.addEventListener("scroll", () => {
    if (!state.ready && state.scrollIntent?.policy === "target") return;
    state.scrollIntent = captureScrollIntent();
    if (state.scrollIntent.policy === "bottom") ui.newMessages.hidden = true;
  }, { passive: true });
  ui.newMessages.addEventListener("click", () => {
    state.scrollIntent = { policy: "bottom" };
    ui.newMessages.hidden = true;
    dispatch("history", { direction: "latest" });
  });
  /** @param {Message} message */
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
        dateStyle: "long",
      }).format(date),
    };
  }

  /** @param {HTMLElement[]} nodes */
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

  /** @param {Snapshot} snapshot @param {string | null} previousTargetId @param {boolean} [force] */
  function renderChannelTimeline(snapshot, previousTargetId, force = false) {
    const ids = (snapshot.timeline || []).map(String);
    const active = new Set(ids);
    const previousIds = [...state.messageNodes.keys()];
    const lastPrevious = previousIds.reduce((last, id) => BigInt(id) > BigInt(last) ? id : last, "0");
    const hasNewTail = previousIds.length > 0 && ids.some((id) => !state.messageNodes.has(id) && BigInt(id) > BigInt(lastPrevious));
    const desired = [];
    const dayKeys = new Set();
    const pendingMedia = [];
    /** @type {string | null} */
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
        record = { element, fingerprint: "", pendingMedia: [] };
        state.messageNodes.set(id, record);
      }
      const openKey = state.dropdown?.key;
      const loading = Boolean(openKey?.startsWith(`message:${id}:`) && candidateLoading(openKey));
      const value = `${fingerprint(message)}:${candidateFingerprint(`message:${id}`)}:${state.profile.mediaTime ?? ""}:${loading}:${message.poll ? state.profile.presentationTime : ""}`;
      if (force || record.fingerprint !== value) {
        record.fingerprint = value;
        clearRenderDiagnostics(`message:${id}`);
        record.pendingMedia = [];
        renderMessage(
          record.element,
          message,
          messageRenderOptions(snapshot, record.pendingMedia, message, true),
        );
      }
      pendingMedia.push(...record.pendingMedia);
      record.element.classList.toggle("message-surface", id === String(snapshot.targetId || ""));
      desired.push(record.element);
    }

    for (const [id, record] of state.messageNodes) {
      if (!active.has(id)) {
        disposeSurface(record.element);
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

  /** @returns {ScrollIntent} */
  function captureScrollIntent() {
    const bounds = ui.timeline.getBoundingClientRect();
    const elements = [...ui.messageList.querySelectorAll("[data-message-id]")].filter((item) => item instanceof HTMLElement);
    const anchor = elements.find((item) => item.getBoundingClientRect().bottom > bounds.top);
    return { policy: ui.timeline.scrollHeight - ui.timeline.scrollTop - ui.timeline.clientHeight <= 1 ? "bottom" : "anchor",
      anchorId: anchor?.dataset.messageId, anchorTop: anchor ? anchor.getBoundingClientRect().top - bounds.top : 0,
      oldScrollTop: ui.timeline.scrollTop, survivingIds: elements.map((item) => item.dataset.messageId || "") };
  }
  function reconcileScrollIntent() {
    const intent = state.scrollIntent;
    if (!intent || ui.channel.hidden) return;
    const bounds = ui.timeline.getBoundingClientRect();
    if (intent.policy === "bottom") {
      ui.timeline.scrollTop = ui.timeline.scrollHeight - ui.timeline.clientHeight;
    } else if (intent.policy === "target") {
      const target = state.messageNodes.get(intent.targetId || "")?.element;
      if (!target) return;
      const rect = target.getBoundingClientRect();
      if (rect.height > bounds.height || rect.top < bounds.top) ui.timeline.scrollTop += rect.top - bounds.top;
      else if (rect.bottom > bounds.bottom) ui.timeline.scrollTop += rect.bottom - bounds.bottom;
    } else {
      const id = state.messageNodes.has(intent.anchorId || "") ? intent.anchorId
        : intent.survivingIds?.find((value) => state.messageNodes.has(value));
      const anchor = state.messageNodes.get(id || "")?.element;
      if (anchor) ui.timeline.scrollTop += anchor.getBoundingClientRect().top - bounds.top - (intent.anchorTop || 0);
      else ui.timeline.scrollTop = intent.oldScrollTop || 0;
    }
  }
  function reset() {
    for (const [id, record] of state.messageNodes) {
      disposeSurface(record.element);
      clearRenderDiagnostics(`message:${id}`);
    }
    state.messageNodes.clear();
    state.dayNodes.clear();
    state.scrollIntent = null;
    ui.messageList.replaceChildren();
  }
  return {
    /** @param {boolean} blocked */
    admission: (blocked) => { for (const record of state.messageNodes.values()) updateActionControls(record.element, blocked); },
    /** @param {{profile: Readonly<Profile>, openKey: string | null | undefined}} context */
    updateContext: ({ profile, openKey }) => { state.profile = profile; state.dropdown = openKey ? { key: openKey } : null; },
    begin: () => {
      state.ready = false;
      if (state.scrollIntent?.policy !== "target") state.scrollIntent = captureScrollIntent();
    },
    ready: () => {
      reconcileScrollIntent();
      if (state.scrollIntent?.policy === "target") state.scrollIntent = captureScrollIntent();
      state.ready = true;
    },
    /** @param {Pick<DOMRect, "x" | "y" | "width" | "height">} crop */
    visibleIds: (crop) => [...state.messageNodes].filter(([, record]) => {
      const rect = record.element.getBoundingClientRect();
      return rect.bottom > crop.y && rect.top < crop.y + crop.height
        && rect.right > crop.x && rect.left < crop.x + crop.width;
    }).map(([id]) => id),
    reset, dispose: () => { observer.disconnect(); if (correction !== null) cancelAnimationFrame(correction); reset(); },
    renderChannelTimeline, captureScrollIntent, reconcileScrollIntent,
  };
}
