import { icon } from "./dom.js";

/**
 * Wire a conversation composer. The app remains the owner of state and action
 * dispatch; inputContainer exposes the composer row for picker integrations.
 */
export function initComposer({
  form,
  input,
  inputContainer,
  sendButton,
  replyContext,
  replyLabel,
  replyCancel,
  state,
  channelLabel,
  onDispatch,
  onModeChange,
  onBeforeEdit,
  rememberFocus,
  setFocusKey,
  onBeforeKey,
}) {
  let currentInput = input;
  let keyInterceptor = onBeforeKey || null;
  let composing = false;
  const row = inputContainer || input.parentElement;

  function value() {
    return typeof currentInput.value === "string" ? currentInput.value : currentInput.textContent || "";
  }

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
    state.drafts.set(key, value());
    state.draftVersions.set(key, (state.draftVersions.get(key) || 0) + 1);
    resizeComposer();
    rememberFocus?.();
  }

  function onKeydown(event) {
    if (keyInterceptor?.(event, currentInput) === true) {
      event.preventDefault();
      return;
    }
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

  function attachInput(element) {
    element.addEventListener("input", onInput);
    element.addEventListener("keydown", onKeydown);
    element.addEventListener("compositionstart", onCompositionStart);
    element.addEventListener("compositionend", onCompositionEnd);
  }

  function detachInput(element) {
    element.removeEventListener("input", onInput);
    element.removeEventListener("keydown", onKeydown);
    element.removeEventListener("compositionstart", onCompositionStart);
    element.removeEventListener("compositionend", onCompositionEnd);
  }

  function update(snapshot) {
    if (snapshot.layout !== "channel") return;
    const editKey = state.editTargetId ? `edit:${state.contextId}:${state.editTargetId}` : null;
    const key = editKey || `composer:${state.contextId}`;
    currentInput.dataset.controlKey = key;
    const editMessage = state.editTargetId ? snapshot.messages?.[state.editTargetId] : null;
    if (!state.drafts.has(key)) state.drafts.set(key, editMessage?.content || "");
    if (currentInput.dataset.commandMode !== "true" && value() !== state.drafts.get(key)) setValue(state.drafts.get(key));
    sendButton.disabled = Boolean(state.pendingAction) || !state.authorized || state.pinnedCapture
      || (!state.editTargetId && !snapshot.channel?.canSendMessages);
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
    if (typeof currentInput.setSelectionRange === "function") {
      const end = value().length;
      currentInput.setSelectionRange(end, end);
    }
  }

  function setReplyTo(message) {
    state.editTargetId = null;
    state.replyToId = String(message.id);
    onModeChange?.();
    const key = `composer:${state.contextId}`;
    setFocusKey?.(key);
    focusAtEnd();
  }

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

  function replaceInput(element) {
    if (!(element instanceof HTMLElement)) throw new TypeError("composer input must be an HTMLElement");
    if (element === currentInput) return currentInput;
    const previous = currentInput;
    detachInput(previous);
    if (previous.parentNode) previous.replaceWith(element);
    if (element.dataset && previous.dataset?.controlKey) element.dataset.controlKey = previous.dataset.controlKey;
    if (typeof element.value === "string" && typeof previous.value === "string") element.value = previous.value;
    currentInput = element;
    composing = false;
    attachInput(currentInput);
    resizeComposer();
    return currentInput;
  }

  return {
    update,
    resize: resizeComposer,
    input: () => currentInput,
    inputContainer: row,
    replaceInput,
    setKeyInterceptor: (interceptor) => { keyInterceptor = interceptor; },
    setReplyTo,
    setEditMessage,
    cancelMode,
    completeActionDrafts,
  };
}
