/** @import {Snapshot, Profile, ActionBody, Receipt, Identity} from "./preview-types" */
/** @import {PreviewUI} from "./app" */
/** @typedef {{profile: Readonly<Profile>, authorized: boolean, pinnedCapture: boolean, pendingAction: boolean, queuedQuery: boolean, editTargetId: string | null}} WorkbenchContext */
/** Workbench owns drawers, inspector navigation, activity return paths, and sidebar DOM.
 * @param {{ui: PreviewUI, dispatch: (kind: string, body: ActionBody) => void,
 * updateActionStatus: () => void, focusKey: () => string | null}} options
 */
export function initWorkbench({ ui, dispatch, updateActionStatus, focusKey }) {
  /** @type {WorkbenchContext & {snapshot: Snapshot | null, targetId: string | null,
   * activityBackStack: {targetId: string, controlKey: string | null}[], activityReturnKey: string | null}} */
  const state = { snapshot: null, profile: {width:960, height:720, locale:"en-US", timezone:"UTC", mediaTime:null, reducedMotion:false}, authorized: false, pinnedCapture: false,
    pendingAction: false, queuedQuery: false, editTargetId: null, targetId: null,
    activityBackStack: [], activityReturnKey: null };
  let modalOpen = false;
  /** @type {HTMLElement | null} */
  let drawerOpener = null;
  /** @type {HTMLElement | null} */
  let panelOpener = null;
  const inspectorTabs = [...document.querySelectorAll("[data-inspector-tab]")].filter((item) => item instanceof HTMLButtonElement);
  /** @type {Record<string, HTMLElement | null>} */
  const inspectorPanels = {
    activity: document.getElementById("activity-panel"),
    diagnostics: document.getElementById("diagnostics-panel"),
    capture: document.getElementById("capture-panel"),
  };

  function isMobileWorkbench() {
    return window.matchMedia("(max-width: 760px)").matches;
  }

  function updateWorkbenchIsolation(open = modalOpen) {
    modalOpen = open;
    const mobile = isMobileWorkbench();
    if (mobile) ui.shell.classList.remove("messages-closed");
    else ui.shell.classList.remove("messages-open");
    const drawerOpen = ui.shell.classList.contains("messages-open");
    const sidebarClosed = mobile ? !drawerOpen : ui.shell.classList.contains("messages-closed");
    const inspectorOpen = !ui.panel.hidden;
    ui.toolbar.inert = modalOpen;
    ui.sidebar.inert = modalOpen || sidebarClosed || (mobile && inspectorOpen);
    ui.sidebar.setAttribute("aria-hidden", String(modalOpen || sidebarClosed || (mobile && inspectorOpen)));
    ui.panel.inert = modalOpen || !inspectorOpen;
    ui.stage.inert = !modalOpen && mobile && (drawerOpen || inspectorOpen);
    ui.drawerScrim.hidden = !mobile || !drawerOpen || modalOpen;
    ui.messagesToggle.setAttribute("aria-expanded", String(!sidebarClosed));
    ui.inspectorToggle.setAttribute("aria-expanded", String(inspectorOpen));
  }

  /** @param {string | undefined} name */
  function setInspectorTab(name) {
    inspectorTabs.forEach((button) => {
      const selected = button.dataset.inspectorTab === name;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
      const panel = inspectorPanels[button.dataset.inspectorTab || ""];
      if (panel) panel.hidden = !selected;
    });
  }

  function closeMessagesDrawer(restore = true) {
    if (!ui.shell.classList.contains("messages-open")) return;
    ui.shell.classList.remove("messages-open");
    updateWorkbenchIsolation();
    if (restore) (drawerOpener?.isConnected ? drawerOpener : ui.messagesToggle).focus({ preventScroll: true });
    drawerOpener = null;
  }

  /** @param {string} [name] @param {Element | null} [opener] */
  function openInspector(name = "activity", opener = document.activeElement) {
    if (ui.panel.hidden) panelOpener = opener instanceof HTMLElement ? opener : ui.inspectorToggle;
    closeMessagesDrawer(false);
    ui.panel.hidden = false;
    ui.shell.classList.add("inspector-open");
    setInspectorTab(name);
    updateWorkbenchIsolation();
    requestAnimationFrame(() => inspectorTabs.find((button) => button.dataset.inspectorTab === name)?.focus());
  }

  function closeInspector(restore = true) {
    if (ui.panel.hidden) return;
    ui.panel.hidden = true;
    ui.shell.classList.remove("inspector-open");
    updateWorkbenchIsolation();
    if (restore) {
      const target = panelOpener instanceof HTMLElement && panelOpener.isConnected
        && !panelOpener.inert ? panelOpener : ui.inspectorToggle;
      target.focus({ preventScroll: true });
    }
    panelOpener = null;
  }

  function openMessagesDrawer() {
    closeInspector(false);
    if (isMobileWorkbench()) {
      drawerOpener = ui.messagesToggle;
      ui.shell.classList.add("messages-open");
      updateWorkbenchIsolation();
      ui.search.focus({ preventScroll: true });
    } else {
      ui.shell.classList.remove("messages-closed");
      updateWorkbenchIsolation();
    }
  }

  ui.messagesToggle.addEventListener("click", () => {
    const closed = isMobileWorkbench()
      ? !ui.shell.classList.contains("messages-open")
      : ui.shell.classList.contains("messages-closed");
    if (closed) openMessagesDrawer();
    else if (isMobileWorkbench()) closeMessagesDrawer();
    else {
      ui.shell.classList.add("messages-closed");
      updateWorkbenchIsolation();
      ui.messagesToggle.focus({ preventScroll: true });
    }
  });
  ui.drawerScrim.addEventListener("click", () => closeMessagesDrawer());
  ui.inspectorToggle.addEventListener("click", () => {
    if (ui.panel.hidden) openInspector();
    else closeInspector();
  });
  ui.captureOpen.addEventListener("click", () => openInspector("capture", ui.captureOpen));
  ui.inspectorClose.addEventListener("click", () => closeInspector());
  inspectorTabs.forEach((button, index) => {
    button.addEventListener("click", () => setInspectorTab(button.dataset.inspectorTab));
    button.addEventListener("keydown", (event) => {
      let next = index;
      if (event.key === "ArrowRight") next = (index + 1) % inspectorTabs.length;
      else if (event.key === "ArrowLeft") next = (index + inspectorTabs.length - 1) % inspectorTabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = inspectorTabs.length - 1;
      else return;
      event.preventDefault();
      setInspectorTab(inspectorTabs[next].dataset.inspectorTab);
      inspectorTabs[next].focus();
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || event.defaultPrevented || modalOpen) return;
    if (!ui.panel.hidden) {
      event.preventDefault();
      closeInspector();
    } else if (ui.shell.classList.contains("messages-open")) {
      event.preventDefault();
      closeMessagesDrawer();
    }
  });
  window.addEventListener("resize", () => updateWorkbenchIsolation());
  updateWorkbenchIsolation();
/** @param {string | undefined} createdAt */
function messageTime(createdAt) {
  if (!createdAt) return "Time unavailable";
  const date = new Date(createdAt);
  if (!Number.isFinite(date.getTime())) return "Time unavailable";
  try {
    return new Intl.DateTimeFormat(state.profile.locale, {
      hour: "numeric", minute: "2-digit", timeZone: state.profile.timezone,
    }).format(date);
  } catch (_) {
    return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }
}

/** @param {Snapshot} snapshot */
function channelLabel(snapshot) {
  if (!state.authorized) return { name: "Unavailable channel", glyph: "#" };
  const recipient = snapshot.channel?.recipient;
  if (recipient) return { name: recipient.name || "Unavailable channel", glyph: "@" };
  return { name: snapshot.channel?.name || "Unavailable channel", glyph: "#" };
}

/** @param {Snapshot} snapshot */
function updatePickers(snapshot) {
  ui.viewer.replaceChildren();
  /** @type {(Identity | string)[]} */
  const viewers = Array.isArray(snapshot.viewers) && snapshot.viewers.length ? snapshot.viewers : [{ id: snapshot.viewerId || undefined }];
  viewers.forEach((viewer) => {
    const id = typeof viewer === "object" ? viewer.id : viewer;
    const option = document.createElement("option");
    option.value = String(id || "");
    option.textContent = typeof viewer === "object" && viewer.name ? String(viewer.name) : `Viewer ${id || "unavailable"}`;
    ui.viewer.append(option);
  });
  ui.viewer.value = snapshot.viewerId || "";
  const messageRows = state.authorized ? [...(snapshot.messageIndex || [])] : [];
  const targetId = String(snapshot.targetId || "");
  ui.message.replaceChildren();
  messageRows.forEach((message) => {
    const id = String(message.id);
    const item = document.createElement("li");
    const row = document.createElement("button");
    row.type = "button";
    row.className = "message-row";
    row.dataset.messageId = id;
    row.dataset.controlKey = `message:${id}`;
    row.setAttribute("aria-disabled", String(!state.authorized || state.pinnedCapture));
    if (id === targetId) row.setAttribute("aria-current", "page");

    const heading = document.createElement("span");
    heading.className = "message-row-heading";
    heading.append(
      Object.assign(document.createElement("strong"), {
        className: "message-row-author",
        textContent: message.author?.name || "Unknown author",
      }),
      Object.assign(document.createElement("time"), {
        className: "message-row-time",
        dateTime: message.createdAt || "",
        textContent: messageTime(message.createdAt),
      }),
    );
    row.append(heading, Object.assign(document.createElement("span"), {
      className: "message-row-excerpt",
      textContent: String(message.excerpt || "") || (message.contentKinds || []).join(", ") || "(No text preview)",
    }));
    const indicators = document.createElement("span");
    indicators.className = "message-row-indicators";
    if (message.attachments?.count) {
      const count = Number(message.attachments.count);
      indicators.append(Object.assign(document.createElement("span"), {
        className: "message-indicator",
        textContent: `${count} attachment${count === 1 ? "" : "s"}`,
      }));
    }
    if (message.components?.length) {
      const count = message.components.length;
      indicators.append(Object.assign(document.createElement("span"), {
        className: "message-indicator",
        textContent: `${count} component${count === 1 ? "" : "s"}`,
      }));
    }
    if (message.ephemeral) indicators.append(Object.assign(document.createElement("span"), {
      className: "message-indicator",
      textContent: "Ephemeral",
    }));
    indicators.append(Object.assign(document.createElement("span"), {
      className: "message-indicator",
      textContent: `ID ${id}`,
    }));
    const metadata = [...indicators.children].map((element) => element.textContent).join(" · ");
    row.setAttribute("aria-label", [[...heading.children].map((element) => element.textContent).join(" "), row.querySelector(".message-row-excerpt")?.textContent || "", metadata].join(" · "));
    row.title = metadata;
    if (indicators.childElementCount) row.append(indicators);
    row.addEventListener("click", () => {
      if (row.getAttribute("aria-disabled") !== "true") dispatch("focus", { target_id: id });
    });
    item.append(row);
    ui.message.append(item);
  });
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
  const channel = channelLabel(snapshot);
  ui.channelContextHash.textContent = channel.glyph;
  ui.channelHash.textContent = channel.glyph;
  ui.channelContextName.textContent = channel.name;
  ui.layoutLabel.textContent = channelLayout ? "Conversation" : "Isolate message";
  ui.channel.hidden = !channelLayout;
  ui.channelName.textContent = channel.name;
  ui.channelTopic.textContent = state.authorized ? snapshot.channel?.topic || "" : "";
  ui.channelTopic.hidden = !state.authorized || !snapshot.channel?.topic;
  ui.composerForm.hidden = !channelLayout || !(state.authorized
    && (snapshot.channel?.canSendMessages || snapshot.channel?.canUseApplicationCommands || state.editTargetId));
  ui.surface.classList.toggle("message-surface", !channelLayout);
  ui.surface.hidden = channelLayout || !target;
  ui.empty.hidden = channelLayout || Boolean(target);
  if (!ui.empty.hidden) ui.empty.textContent = messageRows.length
    ? "Choose a message from Messages."
    : "No visible messages.";
  ui.pickerEmpty.hidden = Boolean(messageRows.length);
  ui.pickerEmpty.textContent = "No visible messages.";
}
function renderActivity() {
  ui.activity.replaceChildren();
  const receipts = state.snapshot?.activity || [];
  if (!receipts.length) {
    ui.activity.append(Object.assign(document.createElement("li"), { textContent: "No activity yet." }));
    return;
  }
  /** @type {Record<string, string>} */
  const outcomeLabels = {
    response: "Response",
    followup: "Follow-up",
    source_edit: "Source message updated",
    message: "Message",
    modal: "Form opened",
    no_output: "No response",
    deferred: "Response deferred",
    unavailable: "Response unavailable",
  };
  receipts.slice(-20).forEach((receipt) => {
    const row = document.createElement("li");
    const status = receipt.uncertain ? "Outcome uncertain"
      : receipt.rejected || ["rejected", "failed", "timeout", "cancelled"].includes(receipt.settlement)
        || receipt.acknowledgement === "unacknowledged" ? "Action was not completed"
        : receipt.settlement === "settled"
          ? receipt.dispatched ? "Callback completed" : "Preview updated"
          : "Action is awaiting a confirmed result";
    row.append(Object.assign(document.createElement("p"), {
      className: "activity-result",
      textContent: status,
    }));
    const details = document.createElement("details");
    details.append(Object.assign(document.createElement("summary"), { textContent: "Technical details" }));
    const fields = [
      `Request ${receipt.requestId || "unavailable"}`,
      Number.isInteger(receipt.sequence) ? `sequence ${receipt.sequence}` : "",
      receipt.dispatch || "",
      receipt.acknowledgement || "",
      receipt.settlement || "",
      receipt.uncertain ? "outcome uncertain" : "",
      receipt.target?.messageId ? `Target message ${receipt.target.messageId}` : "",
      receipt.target?.controlKey ? `Control ${receipt.target.controlKey}` : "",
    ].filter(Boolean);
    details.append(Object.assign(document.createElement("p"), { textContent: fields.join(" · ") }));
    row.append(details);
    const outcomes = document.createElement("ul");
    (receipt.outcomes || []).forEach((outcome) => {
      const item = document.createElement("li");
      const kind = Object.hasOwn(outcomeLabels, outcome.kind) ? outcome.kind : "unavailable";
      item.append(document.createTextNode(outcomeLabels[kind]));
      if (["response", "followup", "source_edit", "message", "deferred"].includes(kind) && outcome.messageId) {
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

/** @param {string} messageId @param {Receipt["target"]} [source] */
function viewActivityMessage(messageId, source = null) {
  if (!/^\d+$/.test(messageId)) return;
  const current = String(source?.messageId || state.targetId || "");
  if (current && /^\d+$/.test(current) && current !== messageId) {
    state.activityBackStack.push({ targetId: current, controlKey: source?.controlKey || focusKey() });
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
  return {
    /** @param {string | undefined} text @param {string | null} [cursor] */
    browseMessages: (text, cursor = null) => {
      if (state.pinnedCapture) return;
      const query = Array.from(String(text || "").normalize("NFC")).slice(0, 128).join("");
      ui.search.value = query;
      dispatch("browse_messages", {query, filter: "all", cursor: typeof cursor === "string" ? cursor : null});
    },
    /** @param {{blocked: boolean, authorized: boolean, pinnedCapture: boolean, receipt: Receipt | null}} admission */
    admission: ({blocked, authorized, pinnedCapture, receipt}) => {
      state.authorized = authorized; state.pinnedCapture = pinnedCapture;
      for (const row of ui.message.querySelectorAll(".message-row")) row.setAttribute("aria-disabled", String(!authorized || pinnedCapture));
      ui.previous.disabled = blocked || !state.snapshot?.navigation?.hasPrevious;
      ui.next.disabled = blocked || !state.snapshot?.navigation?.hasNext;
      ui.backActivity.hidden = !state.activityBackStack.length || pinnedCapture;
      const output = receipt?.outcomes?.find((item) => ["response", "followup", "message", "deferred"].includes(item.kind) && item.messageId);
      ui.viewActionOutput.hidden = !output || pinnedCapture || !authorized;
      ui.viewActionOutput.disabled = blocked;
    },
    channelLabel, viewActivityMessage, backActivity,
    /** @param {Snapshot} snapshot @param {WorkbenchContext} context */
    update: (snapshot, context) => { Object.assign(state, context); state.snapshot = snapshot; state.targetId = snapshot.targetId; updatePickers(snapshot); },
    /** @param {Snapshot | null} snapshot */
    activity: (snapshot) => { state.snapshot = snapshot; renderActivity(); },
    report: () => Object.freeze({ backAvailable: state.activityBackStack.length > 0 }),
    returnKey: () => { const key = state.activityReturnKey; state.activityReturnKey = null; return key; },
    resetNavigation: () => { state.activityBackStack = []; state.activityReturnKey = null; state.snapshot = null; state.targetId = null; },
    updateWorkbenchIsolation, resetWorkbench: () => {
    closeMessagesDrawer(false);
    closeInspector(false);
    panelOpener = null;
    drawerOpener = null;
    updateWorkbenchIsolation(false);
  } };
}
