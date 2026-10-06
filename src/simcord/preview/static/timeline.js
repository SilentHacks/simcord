import { renderMessage } from "./messages.js";

export function initTimeline({ state, ui, candidateLoading, fingerprint, clearRenderDiagnostics, messageRenderOptions }) {
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

  function renderChannelTimeline(snapshot, generation, previousTargetId, force = false) {
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
        record = { element, fingerprint: "", pendingMedia: [] };
        state.messageNodes.set(id, record);
      }
      const openKey = state.dropdown?.key;
      const loading = Boolean(openKey?.startsWith(`message:${id}:`) && candidateLoading(openKey));
      const value = `${fingerprint(message)}:${state.candidateFingerprints.get(`message:${id}`) || ""}:${state.profile.mediaTime ?? ""}:${loading}:${message.poll ? state.profile.presentationTime : ""}`;
      if (force || record.fingerprint !== value) {
        record.fingerprint = value;
        clearRenderDiagnostics(`message:${id}`);
        record.pendingMedia = [];
        renderMessage(
          record.element,
          message,
          messageRenderOptions(snapshot, generation, record.pendingMedia, message, true),
        );
      }
      pendingMedia.push(...record.pendingMedia);
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
  return { renderChannelTimeline, captureScrollIntent, reconcileScrollIntent };
}
