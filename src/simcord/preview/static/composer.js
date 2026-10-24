import { icon } from "./dom.js";

/** @import {Snapshot, ActionBody, AdmittedAction, Receipt} from "./preview-types" */
/** Composer owns text drafts, versions, reply/edit mode, and native input lifecycle.
 * @param {{form: HTMLFormElement, input: HTMLTextAreaElement, inputContainer?: HTMLElement | null,
 * sendButton: HTMLButtonElement, replyContext: HTMLElement, replyLabel: HTMLElement, replyCancel: HTMLButtonElement,
 * channelLabel: (snapshot: Snapshot) => {name: string, glyph: string}, onDispatch: (kind: string, body: ActionBody) => void,
 * onModeChange?: () => void, onBeforeEdit?: () => void, rememberFocus?: () => void,
 * setFocusKey?: (key: string | null) => void, onBeforeKey?: (event: KeyboardEvent, input: HTMLElement) => boolean}} options
 */
export function initComposer({
  form,
  input,
  inputContainer,
  sendButton,
  replyContext,
  replyLabel,
  replyCancel,
  channelLabel,
  onDispatch,
  onModeChange,
  onBeforeEdit,
  rememberFocus,
  setFocusKey,
  onBeforeKey,
}) {
  /** @type {{contextId: string | null, targetId: string | null, authorized: boolean, pinnedCapture: boolean,
   * pendingAction: boolean, replyToId: string | null, editTargetId: string | null,
   * drafts: Map<string, string>, draftVersions: Map<string, number>}} */
  const state = {
    contextId: null, targetId: null, authorized: false, pinnedCapture: false,
    pendingAction: false, replyToId: null, editTargetId: null,
    drafts: new Map(), draftVersions: new Map(),
  };
  /** @type {HTMLElement} */
  let currentInput = input;
  let keyInterceptor = onBeforeKey || null;
  let composing = false;
  const row = inputContainer || input.parentElement || form;
  /** @type {Snapshot | null} */
  let lastSnapshot = null;

  function value() {
    return currentInput instanceof HTMLTextAreaElement ? currentInput.value : currentInput.textContent || "";
  }

  /** @param {string} next */
  function setValue(next) {
    if ("value" in currentInput) currentInput.value = next;
    else currentInput.textContent = next;
  }

  function resizeComposer() {
    if (form.hidden || !currentInput.style || typeof currentInput.scrollHeight !== "number") return;
    currentInput.style.height = "auto";
    if (currentInput.dataset.commandMode === "true") return;
    currentInput.style.height = `${Math.min(160, Math.max(44, currentInput.scrollHeight))}px`;
  }

  function onInput() {
    if (currentInput.dataset.commandMode === "true") return;
    const key = currentInput.dataset.controlKey;
    if (!key) return;
    state.drafts.set(key, value());
    state.draftVersions.set(key, (state.draftVersions.get(key) || 0) + 1);
    resizeComposer();
    rememberFocus?.();
  }

  /** @param {KeyboardEvent} event */
  function onKeydown(event) {
    if (keyInterceptor?.(event, currentInput) === true) {
      event.preventDefault();
      return;
    }
    if (event.target instanceof HTMLButtonElement) return;
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && !composing && event.keyCode !== 229) {
      event.preventDefault();
      form.requestSubmit();
    }
  }

  function onCompositionStart() {
    composing = true;
  }

  function onCompositionEnd() {
    composing = false;
  }

  /** @param {HTMLElement} element */
  function attachInput(element) {
    element.addEventListener("input", onInput);
    element.addEventListener("keydown", onKeydown);
    element.addEventListener("compositionstart", onCompositionStart);
    element.addEventListener("compositionend", onCompositionEnd);
  }

  /** @param {HTMLElement} element */
  function detachInput(element) {
    element.removeEventListener("input", onInput);
    element.removeEventListener("keydown", onKeydown);
    element.removeEventListener("compositionstart", onCompositionStart);
    element.removeEventListener("compositionend", onCompositionEnd);
  }

  /** @param {Snapshot} snapshot */
  function update(snapshot) {
    lastSnapshot = snapshot;
    if (snapshot.layout !== "channel") return;
    const editKey = state.editTargetId ? `edit:${state.contextId}:${state.editTargetId}` : null;
    const key = editKey || `composer:${state.contextId}`;
    currentInput.dataset.controlKey = key;
    const editMessage = state.editTargetId ? snapshot.messages?.[state.editTargetId] : null;
    if (!state.drafts.has(key)) state.drafts.set(key, editMessage?.content || "");
    if (currentInput.dataset.commandMode !== "true" && value() !== state.drafts.get(key)) setValue(state.drafts.get(key) || "");
    sendButton.disabled = Boolean(state.pendingAction) || !state.authorized || state.pinnedCapture || form.hidden
      || (!state.editTargetId && !(currentInput.dataset.commandMode === "true"
        ? snapshot.channel?.canUseApplicationCommands : snapshot.channel?.canSendMessages));
    const sendLabel = state.editTargetId ? "Save" : "Send";
    sendButton.setAttribute("aria-label", sendLabel);
    sendButton.title = `${sendLabel} message (Enter)`;
    if (sendButton.dataset.mode !== sendLabel) {
      sendButton.replaceChildren(icon(state.editTargetId ? "check" : "send"));
      sendButton.dataset.mode = sendLabel;
    }
    resizeComposer();
    const channel = channelLabel(snapshot);
    if ("placeholder" in currentInput) {
      currentInput.placeholder = state.editTargetId ? "Edit message"
        : !snapshot.channel?.canSendMessages && snapshot.channel?.canUseApplicationCommands
          ? "You can use application commands here"
          : `Message ${channel.glyph}${channel.name}`;
    }
    const messageRows = state.authorized ? snapshot.messageIndex || [] : [];
    const reply = state.replyToId
      ? messageRows.find((item) => String(item.id) === state.replyToId)
      : null;
    replyContext.hidden = !state.replyToId && !state.editTargetId;
    replyLabel.textContent = state.editTargetId
      ? `Editing message ${state.editTargetId}`
      : state.replyToId
        ? reply
          ? `Replying to ${reply.author?.name || "Unknown author"}: ${String(reply.excerpt || "").slice(0, 100)}`
          : `Replying to message ${state.replyToId}`
        : "";
    replyCancel.setAttribute("aria-label", state.editTargetId ? "Cancel edit" : "Cancel reply");
  }

  function focusAtEnd() {
    currentInput.focus();
    if (currentInput instanceof HTMLTextAreaElement) {
      const end = value().length;
      currentInput.setSelectionRange(end, end);
    }
  }

  /** @param {{id: string | number}} message */
  function setReplyTo(message) {
    state.editTargetId = null;
    state.replyToId = String(message.id);
    onModeChange?.();
    const key = `composer:${state.contextId}`;
    setFocusKey?.(key);
    focusAtEnd();
  }

  /** @param {{id: string | number, content?: string}} message */
  function setEditMessage(message) {
    onBeforeEdit?.();
    state.replyToId = null;
    state.editTargetId = String(message.id);
    const key = `edit:${state.contextId}:${state.editTargetId}`;
    if (!state.drafts.has(key)) state.drafts.set(key, String(message.content || ""));
    onModeChange?.();
    setFocusKey?.(key);
    focusAtEnd();
  }

  function cancelMode() {
    state.replyToId = null;
    state.editTargetId = null;
    onModeChange?.();
    currentInput.focus();
  }

  /** @param {AdmittedAction | null} action @param {Receipt} receipt */
  function completeActionDrafts(action, receipt) {
    if (!action || receipt.rejected || receipt.settlement !== "settled") return;
    const { kind, contextId, targetId } = action;
    const draftKey = kind === "send_message" ? `composer:${contextId}` : `edit:${contextId}:${targetId}`;
    const unchanged = (state.draftVersions.get(draftKey) || 0) === action.draftVersion;
    if ((["send_message", "edit_message"].includes(kind) && unchanged) || kind === "delete_message") {
      state.drafts.delete(draftKey);
    }
    if (contextId !== state.contextId) return;
    if ((kind === "send_message" && unchanged) || (kind === "delete_message" && state.replyToId === String(targetId))) {
      state.replyToId = null;
    }
    if (((kind === "edit_message" && unchanged) || kind === "delete_message") && state.editTargetId === String(targetId)) {
      state.editTargetId = null;
    }
  }

  /** @param {SubmitEvent} event */
  function onSubmit(event) {
    event.preventDefault();
    const content = value();
    if (state.editTargetId) {
      onDispatch("edit_message", { target_id: state.editTargetId, content });
    } else if (content.trim()) {
      onDispatch("send_message", {
        target_id: state.targetId,
        content,
        reply_to_id: state.replyToId,
      });
    }
  }

  attachInput(currentInput);
  replyCancel.addEventListener("click", cancelMode);
  form.addEventListener("submit", onSubmit);

  /** @param {HTMLElement} element */
  function replaceInput(element) {
    if (!(element instanceof HTMLElement)) throw new TypeError("composer input must be an HTMLElement");
    if (element === currentInput) return currentInput;
    const previous = currentInput;
    detachInput(previous);
    if (previous.parentNode) previous.replaceWith(element);
    if (element.dataset && previous.dataset?.controlKey) element.dataset.controlKey = previous.dataset.controlKey;
    if (element instanceof HTMLTextAreaElement && previous instanceof HTMLTextAreaElement) element.value = previous.value;
    currentInput = element;
    composing = false;
    attachInput(currentInput);
    resizeComposer();
    return currentInput;
  }

  return {
    /** @param {{blocked: boolean, authorized: boolean, pinnedCapture: boolean}} admission */
    setAdmission: ({blocked, authorized, pinnedCapture}) => {
      state.pendingAction = blocked; state.authorized = authorized; state.pinnedCapture = pinnedCapture;
      if (lastSnapshot) update(lastSnapshot); else sendButton.disabled = true;
    },
    /** @param {{contextId: string | null, targetId: string | null, authorized: boolean, pinnedCapture: boolean, pending: boolean}} context */
    setContext: ({ contextId, targetId, authorized, pinnedCapture, pending }) => {
      Object.assign(state, { contextId, targetId, authorized, pinnedCapture, pendingAction: pending });
    },
    report: () => Object.freeze({ editTargetId: state.editTargetId, replyToId: state.replyToId }),
    /** @param {string} kind @param {string | null} contextId @param {string | null | undefined} targetId */
    draftVersion: (kind, contextId, targetId) => state.draftVersions.get(
      kind === "send_message" ? `composer:${contextId}` : `edit:${contextId}:${targetId}`,
    ) || 0,
    /** @param {boolean} enabled @param {string | null | undefined} replyToId */
    commandMode: (enabled, replyToId) => {
      state.replyToId = enabled ? null : replyToId || null;
    },
    reset: () => {
      state.drafts.clear();
      state.draftVersions.clear();
      state.replyToId = null;
      state.editTargetId = null;
    },
    clear: () => {
      lastSnapshot = null;
      composing = false;
      setValue("");
      delete currentInput.dataset.controlKey;
      if ("placeholder" in currentInput) currentInput.placeholder = "";
      replyLabel.textContent = "";
      replyContext.hidden = true;
      resizeComposer();
    },
    update,
    resize: resizeComposer,
    input: () => currentInput,
    inputContainer: row,
    replaceInput,
    /** @param {((event: KeyboardEvent, input: HTMLElement) => boolean) | null} interceptor */
    setKeyInterceptor: (interceptor) => { keyInterceptor = interceptor; },
    setReplyTo,
    setEditMessage,
    cancelMode,
    completeActionDrafts,
  };
}
