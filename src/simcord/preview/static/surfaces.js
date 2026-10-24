import { renderMessage } from "./messages.js";
import { disposeSurface } from "./dom.js";

/** @param {ParentNode} root @param {boolean} blocked */
export function updateActionControls(root, blocked) {
  for (const button of root.querySelectorAll("button.component-button:not(.button-style-5):not(.button-style-6)")) {
    if (button instanceof HTMLButtonElement) button.setAttribute("aria-disabled", String(blocked || button.disabled));
  }
}

/** @import {Snapshot, Profile, Message, ActionBody, AdmittedAction, Diagnostic, Dropdown, RenderOptions} from "./preview-types" */
/** @typedef {ReturnType<import("./select-drafts").initSelectDrafts>} Selections */
/** @typedef {ReturnType<import("./composer").initComposer>} Composer */
/** @typedef {ReturnType<import("./assets").createAssets>} Assets */

/** Message-local draft/reveal state and retained focused-surface DOM work.
 * @param {{root: HTMLElement, selections: Selections, composer: Composer, assets: Assets,
 * dispatch: (kind: string, body: ActionBody) => void, localRender: (force: boolean) => void,
 * addDiagnostic: (item: Diagnostic) => void, clearRenderDiagnostics: (owner: string | null) => void, premiumNotice: () => void}} options
 */
export function createSurfaces({ root, selections, composer, assets, dispatch, localRender, addDiagnostic, clearRenderDiagnostics, premiumNotice }) {
  /** @type {{contextId: string | null, profile: Partial<Profile>, dropdown: Readonly<Dropdown> | null,
   * spoilerState: Set<string>, pollDrafts: Map<string, string[]>, pollResults: Set<string>}} */
  const state = { contextId: null, profile: {}, dropdown: null, spoilerState: new Set(),
    pollDrafts: new Map(), pollResults: new Set() };
  /** @type {string | null} */
  let identity = null;
  /** @type {string | null} */
  let focusedKey = null;
  let focusedFingerprint = "";
  /** @type {Promise<unknown>[]} */
  let focusedPending = [];
  const { openDropdown, updateDraft, commitDropdown, cancelDropdown, navigateDropdown, clearSelection } = selections;
  const loadAsset = assets.load;
  /** @type {Required<Pick<RenderOptions, "isSpoilerRevealed" | "onRevealSpoiler">>} */
  const spoilerOptions = {
    isSpoilerRevealed: (key) => state.spoilerState.has(key),
    onRevealSpoiler: (key) => state.spoilerState.add(key),
  };
/** @param {Snapshot} snapshot @param {Promise<unknown>[]} pendingMedia @param {Message | null} message @param {boolean} channelLayout @returns {RenderOptions} */
function messageRenderOptions(snapshot, pendingMedia, message, channelLayout) {
  /** @param {Message} item */
  const pollKey = (item) => `poll:${state.contextId}:${item.id}`;
  const renderOwner = message ? `message:${message.id}` : null;
  return {
    ...selections.renderOptions(),
    candidates: snapshot.candidates || {},
    assets: snapshot.assets || {},
    contextId: state.contextId,
    viewerId: snapshot.viewerId,
    ...spoilerOptions,
    locale: state.profile.locale,
    timezone: state.profile.timezone,
    presentationTime: state.profile.presentationTime,
    mediaTime: state.profile.mediaTime,
    reducedMotion: state.profile.reducedMotion,
    onMediaCaptureTime: (assetId, mediaTime) => assets.recordTime(assetId, mediaTime),
    assetMetadata: assets.manifest,
    dropdown: state.dropdown,
    onDiagnostic: (diagnostic) => addDiagnostic({
      ...diagnostic,
      ...(renderOwner ? { id: `local:render:${renderOwner}:${diagnostic.code}`, renderOwner } : {}),
    }),
    onOpen: openDropdown,
    onDraft: updateDraft,
    onPremiumActivate: premiumNotice,
    onClick: (controlKey) => dispatch("click", { control_key: controlKey }),
    onCommit: commitDropdown,
    onCancel: cancelDropdown,
    onNavigate: navigateDropdown,
    onClear: clearSelection,
    loadAsset,
    onLocalRender: () => localRender(true),
    pendingMedia,
    channelLayout,
    onReply: channelLayout && snapshot.channel?.canSendMessages ? (message) => composer.setReplyTo(message) : null,
    onEdit: channelLayout ? (message) => composer.setEditMessage(message) : null,
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
    pollShowingResults: (item) => state.pollResults.has(pollKey(item)),
    onPollResults: (item, showing) => {
      if (showing) state.pollResults.add(pollKey(item));
      else state.pollResults.delete(pollKey(item));
      localRender(true);
    },
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
  function clearFocused() {
    disposeSurface(root);
    clearRenderDiagnostics(focusedKey ? `message:${focusedKey}` : null);
    focusedKey = null; focusedFingerprint = ""; focusedPending = [];
  }
  return {
    /** @param {boolean} blocked */
    admission: (blocked) => updateActionControls(root, blocked),
    options: messageRenderOptions, spoilerOptions,
    /** @param {Snapshot} snapshot @param {Readonly<Profile>} profile @param {Readonly<Dropdown> | null} dropdown */
    updateContext: (snapshot, profile, dropdown) => {
      const next = `${snapshot.context?.id}:${snapshot.viewerId}`;
      if (identity !== null && identity !== next) { state.spoilerState.clear(); state.pollDrafts.clear(); state.pollResults.clear(); }
      identity = next; state.contextId = snapshot.context?.id; state.profile = profile; state.dropdown = dropdown;
    },
    /** @param {Snapshot} snapshot @param {boolean} [force] */
    focused: (snapshot, force = false) => {
      const selected = snapshot.targetId ? snapshot.messages?.[String(snapshot.targetId)] || null : null;
      const key = selected ? String(selected.id) : null;
      const open = state.dropdown?.key;
      const loading = Boolean(open?.startsWith(`message:${key}:`) && selections.candidateLoading(open));
      const value = `${selected ? JSON.stringify(selected) : ""}:${selections.fingerprint(`message:${key}`)}:${state.profile.mediaTime ?? ""}:${loading}:${selected?.poll ? state.profile.presentationTime : ""}`;
      if (force || key !== focusedKey || value !== focusedFingerprint) {
        clearRenderDiagnostics(focusedKey ? `message:${focusedKey}` : null);
        clearRenderDiagnostics(key ? `message:${key}` : null);
        focusedKey = key; focusedFingerprint = value; focusedPending = [];
        renderMessage(root, selected, messageRenderOptions(snapshot, focusedPending, selected, false));
      }
      return focusedPending;
    },
    clearFocused,
    /** @param {AdmittedAction} action */
    outcome: (action) => {
      if (action.kind === "set_poll_votes") {
        state.pollDrafts.delete(`poll:${action.contextId}:${action.targetId}`);
        state.pollResults.delete(`poll:${action.contextId}:${action.targetId}`);
      }
    },
    reset: () => { clearFocused(); state.spoilerState.clear(); state.pollDrafts.clear(); state.pollResults.clear(); identity = null; },
  };
}
