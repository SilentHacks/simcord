import { createListbox } from "./listbox.js";
import { node, renderIdentityAvatar, presenceDot } from "./dom.js";
import { createCommandCatalog } from "./command-catalog.js";
import { createCommandDrafts } from "./command-drafts.js";

/** @import {Snapshot, CommandOption, CommandEntry, CommandCatalog, CommandDraft, Choice, Identity, Primitive, Asset, AssetOptions, ActionBody, ActionMetadata, Receipt} from "./preview-types" */
/** @typedef {ReturnType<import("./composer").initComposer>} Composer */
/** @typedef {{present: boolean, valid: boolean, error: string | null, wire?: string | number | boolean | File}} OptionResult */

const INTEGER_INPUT = /^-?(?:0|[1-9][0-9]*)$/;
const INTEGER_LIMIT = 9007199254740991n;
const NUMBER_LIMIT = 2 ** 53;
const NUMBER_INPUT = /^-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?$/;
const ENTITY_TYPES = new Set(/** @type {(string | number)[]} */ (["user", "channel", "role", "mentionable"]));
/** @type {Record<string, string[]>} */
const FILE_TYPE_GROUPS = {
  image: [".png", ".gif", ".jpg", ".jpeg", ".jfif", ".webp", ".avif"],
  video: [".mp4", ".mov", ".qt", ".webm"],
  audio: [".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac"],
};

/** @param {string[] | undefined} types */
function allowedFileExtensions(types) {
  return (types || []).flatMap((type) => FILE_TYPE_GROUPS[String(type).toLowerCase()] || [String(type).toLowerCase()]);
}

/** @param {string | number} type @returns {string} */
function typeName(type) {
  return (/** @type {Record<string, string>} */ ({ 3: "string", 4: "integer", 5: "boolean", 6: "user", 7: "channel", 8: "role", 9: "mentionable", 10: "number", 11: "attachment" }))[String(type)] || String(type);
}

/** Browser mirror of parse_option_input/validate_option_value.
 * @param {CommandOption} option @param {unknown} raw
 * @returns {{code: string, value?: undefined} | {value: string | number | boolean | File, code?: undefined}}
 */
export function validateOptionInput(option, raw) {
  const type = typeName(option.type);
  /** @type {string | number | boolean} */
  let value;
  if (type === "string") {
    if (typeof raw !== "string") return { code: "option-type" };
    value = raw;
    const length = [...raw].length;
    if (option.minLength != null && length < Number(option.minLength)) return { code: "option-length" };
    if (option.maxLength != null && length > Number(option.maxLength)) return { code: "option-length" };
  } else if (type === "integer") {
    if (typeof raw !== "string" || !INTEGER_INPUT.test(raw)) return { code: "option-type" };
    let exact;
    try { exact = BigInt(raw); } catch (_) { return { code: "option-type" }; }
    if (exact > INTEGER_LIMIT || exact < -INTEGER_LIMIT) return { code: "option-integer-range" };
    value = Number(exact);
    if (!Number.isSafeInteger(value)) return { code: "option-integer-range" };
  } else if (type === "number") {
    if (typeof raw !== "string" || !NUMBER_INPUT.test(raw)) return { code: "option-type" };
    value = Number(raw);
    if (!Number.isFinite(value)) return { code: "option-type" };
    if (Math.abs(value) > NUMBER_LIMIT) return { code: "option-integer-range" };
  } else if (type === "boolean") {
    if (typeof raw !== "boolean") return { code: "option-type" };
    value = raw;
  } else if (ENTITY_TYPES.has(type)) {
    if (typeof raw !== "string" || !/^\d+$/.test(raw)) return { code: "option-type" };
    value = raw;
  } else if (type === "attachment") {
    if (!(raw instanceof File)) return { code: "option-type" };
    const allowed = allowedFileExtensions(option.fileTypes || option.file_types);
    const suffix = /\.[^.]+$/.exec(raw.name.replaceAll("\\", "/").split("/").pop() || "")?.[0]?.toLowerCase() || "";
    if (allowed.length && !allowed.includes(suffix)) return { code: "option-file-type" };
    return { value: raw };
  } else return { code: "option-type" };

  if (typeof value === "number" && ["integer", "number"].includes(type)) {
    if (option.minValue != null && value < Number(option.minValue)) return { code: "option-range" };
    if (option.maxValue != null && value > Number(option.maxValue)) return { code: "option-range" };
  }
  const choices = Array.isArray(option.choices) ? option.choices : [];
  if (choices.length > 25) return { code: "option-choice" };
  if (choices.length && !option.autocomplete && ["string", "integer", "number"].includes(type)
    && !choices.some((choice) => choice.value === value || String(choice.value) === String(value))) {
    return { code: "option-choice" };
  }
  return { value };
}

/** @param {readonly CommandEntry[]} entries @param {string | null | undefined} query */
export function matchCommands(entries, query) {
  const needle = String(query || "").trim().toLocaleLowerCase();
  const ranked = [];
  for (const entry of entries || []) {
    const invocation = String(entry.invocation || "");
    const lowered = invocation.toLocaleLowerCase();
    let rank = 3;
    if (!needle || lowered.startsWith(needle)) rank = 0;
    else if (invocation.split(" ").some((part) => part.toLocaleLowerCase().startsWith(needle))) rank = 1;
    else if (lowered.includes(needle)) rank = 2;
    else continue;
    ranked.push({ entry, rank });
  }
  return ranked.sort((a, b) => a.rank - b.rank
    || String(a.entry.invocation).localeCompare(String(b.entry.invocation))
    || String(a.entry.commandId).localeCompare(String(b.entry.commandId))).map(({ entry }) => entry);
}

/** @param {string | number} type */
const entityCaption = (type) => (/** @type {Record<string, string>} */ ({ user: "MEMBERS", mentionable: "MEMBERS", role: "ROLES", channel: "CHANNELS" }))[String(type)] || "OPTIONS";
/** @param {string | number} type */
const entityMark = (type) => (/** @type {Record<string, string>} */ ({ user: "@", mentionable: "@", role: "@", channel: "#" }))[String(type)] || "";

/** @param {{form: HTMLFormElement, input: HTMLTextAreaElement, composer: Composer,
 * dispatch: (kind: string, body: ActionBody, metadata?: ActionMetadata | null) => void,
 * run: (body: ActionBody, files: Record<string, File>, metadata: ActionMetadata) => void,
 * fetchCatalog: () => Promise<CommandCatalog>, announce?: (message: string) => void,
 * onCommandMode?: (enabled: boolean, reply?: string | null) => void,
 * loadAsset?: (id: string, options?: AssetOptions) => Promise<string>,
 * onPendingMedia?: (pending: Promise<unknown>[]) => void}} options */
export function createCommandPicker({
  form, input, composer, dispatch, run, fetchCatalog, announce,
  onCommandMode, loadAsset, onPendingMedia,
}) {
  /** @type {Readonly<Record<string, Asset>>} */
  let assets = {};
  let authorized = false;
  /** @type {string | null} */
  let pendingKind = null;
  /** @type {CommandCatalog | null} */
  let catalog = null;
  let catalogState = "idle";
  /** @type {string | null} */
  let catalogFingerprint = null;
  /** @type {number | null} */
  let contextGeneration = null;
  /** @type {string | null} */
  let contextId = null;
  /** @type {string | null} */
  let viewerId = null;
  let mode = "closed";
  /** @type {string | null} */
  let query = null;
  /** @type {CommandEntry[]} */
  let matches = [];
  /** @type {string | null} */
  let activeKey = null;
  /** @type {Readonly<CommandDraft> | null} */
  let draft = null;
  const drafts = createCommandDrafts((value) => { draft = value; });
  /** @type {ReturnType<typeof createListbox> | null} */
  let listbox = null;
  /** @type {ReturnType<typeof createListbox> | null} */
  let suggestionListbox = null;
  /** @type {HTMLDivElement | null} */
  let popup = null;
  /** @type {HTMLDivElement | null} */
  let contextBar = null;
  /** @type {HTMLDivElement | null} */
  let commandRow = null;
  /** @type {{option: string | null, state: string, choiceCount: number}} */
  let autocomplete = { option: null, state: "idle", choiceCount: 0 };
  /** @type {Map<string, Choice[]>} */
  let autocompleteResults = new Map();
  /** @type {Map<string, ReturnType<typeof setTimeout>>} */
  let timers = new Map();
  /** @type {string | null} */
  let detachedReply = null;
  /** @type {Snapshot | null} */
  let lastSnapshot = null;
  /** @type {HTMLElement | null} */
  let currentPopupOwner = null;
  /** @type {Promise<unknown>[]} */
  let popupPending = [];
  /** @type {Promise<unknown>[]} */
  let composePending = [];
  function reportPendingMedia() {
    onPendingMedia?.([...popupPending, ...composePending]);
  }
  const row = composer.inputContainer;
  function currentDraft() { if (!draft) throw new Error("No active command draft"); return draft; }
  function commandElement() { if (!commandRow) throw new Error("No active command row"); return commandRow; }
  function popupElement() { if (!popup) throw new Error("No active command popup"); return popup; }
  function contextElement() { if (!contextBar) throw new Error("No active command context"); return contextBar; }
  /** @template {HTMLElement} T @param {ParentNode | null} root @param {string} selector @returns {T | null} */
  function queryElement(root, selector) { return /** @type {T | null} */ (root?.querySelector(selector) || null); }

  /** @param {Snapshot | null} [snapshot] */
  function canOpen(snapshot = lastSnapshot) {
    return Boolean(snapshot?.layout === "channel" && !composer.report().editTargetId
      && authorized && snapshot.channel?.canUseApplicationCommands
      && snapshot.commands?.state === "available");
  }
  function entryForDraft() {
    return catalog?.entries?.find((item) => item.commandId === draft?.commandId
      && item.path.join("\u0000") === draft?.path.join("\u0000")) || null;
  }
  /** @param {CommandEntry | null} [entry] */
  function optionsFor(entry = entryForDraft()) { return entry?.options || []; }
  /** @param {CommandOption} option */
  function controlKey(option) {
    return `command:${currentDraft().commandId}:${currentDraft().path.join(".")}:option:${option.name}`;
  }
  /** @param {CommandOption} option */
  function valueExists(option) {
    const value = draft?.values?.[option.name];
    return Boolean(value && (value.file || value.raw !== "" && value.raw != null));
  }
  /** @param {CommandOption} option @returns {OptionResult} */
  function optionValue(option) {
    const value = currentDraft().values[option.name];
    if (!valueExists(option)) return { present: false, valid: !option.required, error: option.required ? "This field is required." : null };
    if (value.file) {
      const validated = validateOptionInput(option, value.file);
      return { present: true, valid: !validated.code, error: validated.code || null, wire: validated.value };
    }
    if (ENTITY_TYPES.has(option.type)) {
      const raw = value.entity?.id || value.raw;
      return { present: true, valid: typeof raw === "string" && /^\d+$/.test(raw), error: null, wire: raw };
    }
    const raw = option.type === "boolean" ? value.raw : String(value.raw ?? "");
    const validated = validateOptionInput(option, raw);
    return { present: true, valid: !validated.code, error: validated.code || null, wire: validated.value };
  }
  function checkedOptions() {
    /** @type {Record<string, Primitive | {upload: number}>} */
    const values = {};
    /** @type {{option: CommandOption, result: OptionResult}[]} */
    const invalid = [];
    let uploadIndex = 0;
    for (const option of optionsFor()) {
      const result = optionValue(option);
      if (!result.present) continue;
      if (!result.valid) { invalid.push({ option, result }); continue; }
      if (option.type === "attachment") {
        values[option.name] = { upload: uploadIndex++ };
      } else if (option.type === "integer" || option.type === "number") {
        values[option.name] = String(result.wire);
      } else if (result.wire !== undefined && !(result.wire instanceof File)) values[option.name] = result.wire;
    }
    return { values, invalid };
  }
  function missingRequired() {
    return optionsFor().filter((option) => option.required && !valueExists(option)).map((option) => option.name);
  }
  function submittable() {
    const { invalid } = checkedOptions();
    return draft?.available !== false && missingRequired().length === 0 && invalid.length === 0;
  }
  /** @param {string | null | undefined} code */
  function errorText(code) {
    return (/** @type {Record<string, string>} */ ({
      "option-length": "Value length is outside the allowed range.",
      "option-range": "Value is outside the allowed range.",
      "option-choice": "Choose an available option.",
      "option-type": "Enter a valid value.",
      "option-integer-range": "Integer is outside the safe range.",
      "option-file-type": "This file type is not allowed.",
    }))[code || ""] || "Enter a valid value.";
  }
  /** @param {CommandOption} option @param {HTMLElement} field */
  function clearOptionError(option, field) {
    drafts.error(option.name, null);
    const pill = field.closest(".command-pill");
    pill?.classList.remove("has-error");
    pill?.querySelector(".command-error")?.remove();
    field.removeAttribute("aria-invalid");
    field.setAttribute("aria-describedby", "command-context-description");
  }
  /** @param {CommandOption} option @param {HTMLElement} field @param {string} message */
  function showOptionError(option, field, message) {
    drafts.error(option.name, message);
    const pill = field.closest(".command-pill");
    if (!pill) return;
    pill.classList.add("has-error");
    field.setAttribute("aria-invalid", "true");
    const errorId = `command-error-${option.name}`;
    field.setAttribute("aria-describedby", `command-context-description ${errorId}`);
    let error = pill.querySelector(".command-error");
    if (!error) {
      error = node("span", "command-error");
      error.id = errorId;
      pill.append(error);
    }
    error.textContent = message;
  }
  /** @param {CommandOption} option @param {HTMLElement} field */
  function validateOnBlur(option, field) {
    const result = optionValue(option);
    if (!result.present && !option.required) return;
    if (!result.valid) {
      showOptionError(option, field, result.error === "This field is required."
        ? result.error
        : errorText(result.error));
    } else clearOptionError(option, field);
  }
  /** @param {CommandOption} option */
  function optionStatus(option) {
    const result = optionValue(option);
    const raw = draft?.values?.[option.name];
    const display = raw?.file ? { filename: raw.file.name, size: raw.file.size }
      : raw?.entity ? `${entityMark(option.type)}${raw.entity.name || raw.entity.label || raw.entity.id}`
        : raw?.raw ?? null;
    return { name: option.name, type: option.type, required: Boolean(option.required), present: result.present,
      valid: result.valid, error: result.error, display };
  }
  function status() {
    const state = mode === "composing" && pendingKind === "run_command" ? "pending" : mode;
    return {
      state: !canOpen(lastSnapshot) && !draft ? "unavailable" : state,
      catalog: { fingerprint: catalogFingerprint, state: catalogState, truncated: Boolean(catalog?.truncated) },
      query: mode === "browsing" ? query : null,
      entryKeys: mode === "browsing" ? matches.slice(0, 50).map((item) => item.key) : [],
      activeEntryKey: mode === "browsing" ? activeKey : null,
      draft: draft ? { commandId: currentDraft().commandId, invocation: currentDraft().invocation, schemaFingerprint: currentDraft().schemaFingerprint,
        available: currentDraft().available !== false, focusedOption: currentDraft().focused, submittable: submittable(),
        missingRequired: missingRequired(), droppedOptions: [...currentDraft().droppedOptions],
        options: optionsFor().map(optionStatus) } : null,
      autocomplete: { ...autocomplete },
    };
  }
  /** @param {Readonly<CommandDraft>} commandDraft @param {string} optionName @param {unknown} value */
  function autocompleteKey(commandDraft, optionName, value) {
    return JSON.stringify([commandDraft.commandId, commandDraft.path, commandDraft.schemaFingerprint, optionName, String(value)]);
  }
  function clearAutocompleteState() {
    for (const [token, timer] of timers) {
      if (token.startsWith("autocomplete:")) {
        clearTimeout(timer);
        timers.delete(token);
      }
    }
    autocompleteResults.clear();
    autocomplete = { option: null, state: "idle", choiceCount: 0 };
  }

  function renderBrowse() {
    if (!popup) return;
    clearPopup();
    const pending = [];
    const heading = node("div", "command-popup-heading command-app-heading", catalog?.application?.name || "COMMANDS");
    const avatarOptions = { assets, loadAsset, isCurrent: () => Boolean(heading.isConnected && popup?.contains(heading) && !popupElement().hidden) };
    popupElement().classList.add("is-browsing");
    const rail = node("div", "command-app-rail");
    const identity = catalog?.application ? { name: catalog.application.name, avatar: catalog.application.avatarAssetId, bot: true } : null;
    if (identity) {
      const rendered = renderIdentityAvatar(identity, avatarOptions, "command-avatar");
      pending.push(...rendered.pending);
      heading.prepend(rendered.element);
      const appButton = node("button", "command-app-button");
      appButton.type = "button";
      appButton.setAttribute("aria-label", `Show ${identity.name} commands`);
      const appAvatar = renderIdentityAvatar(identity, avatarOptions, "command-avatar");
      pending.push(...appAvatar.pending);
      appButton.append(appAvatar.element);
      appButton.addEventListener("click", () => {
        list.scrollTop = 0;
        input.focus();
      });
      rail.append(appButton);
    }
    const content = node("div", "command-browse-content");
    content.append(heading);
    const list = node("div", "command-popup-list");
    list.id = "command-picker-listbox";
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", "Application commands");
    matches.slice(0, 50).forEach((entry) => {
      const item = node("div", "command-entry");
      item.dataset.entryKey = entry.key;
      item.tabIndex = -1;
      const avatar = renderIdentityAvatar(identity || {}, avatarOptions, "command-avatar");
      pending.push(...avatar.pending);
      item.append(avatar.element);
      const text = node("span", "command-entry-text");
      const title = node("span", "command-entry-title");
      title.append(node("strong", "", `/${entry.invocation}`));
      const optional = entry.options.filter((option) => !option.required).length;
      if (optional) {
        title.append(node("span", "command-optional-count", `+${optional} optional`));
      }
      text.append(title, node("span", "command-entry-description", entry.description || ""));
      item.append(text, node("span", "command-entry-app", catalog?.application?.name || ""));
      item.addEventListener("click", () => select(entry));
      list.append(item);
    });
    if (!matches.length && catalogState === "loading") list.append(node("div", "command-empty", "Loading commands"));
    if (!matches.length && catalogState === "failed") list.append(node("div", "command-empty", "Commands are unavailable"));
    if (catalog?.truncated) {
      const truncated = node("div", "command-truncated", "Some commands are not shown");
      truncated.setAttribute("aria-disabled", "true");
      list.append(truncated);
    }
    content.append(list);
    popupElement().append(rail, content);
    listbox = createListbox({
      list, owner: input, idPrefix: "command-entry", activeClass: "is-active", wrap: false,
      onNavigate: (item) => {
        activeKey = item?.dataset.entryKey || null;
        const active = matches.find((entry) => entry.key === activeKey);
        if (active) announce?.(`/${active.invocation}`);
      },
      onCommit: (item) => {
        const entry = matches.find((candidate) => candidate.key === item?.dataset.entryKey);
        if (entry) select(entry);
      },
    });
    listbox.setItems([...list.querySelectorAll(".command-entry")].filter((item) => item instanceof HTMLElement));
    const selectedIndex = matches.findIndex((item) => item.key === activeKey);
    if (selectedIndex >= 0) listbox.setActive(selectedIndex, { scroll: false });
    listbox.setExpanded(true);
    popupElement().hidden = false;
    popupPending = pending;
    reportPendingMedia();
    positionPopup();
  }
  function ensurePopup() {
    if (popup?.isConnected) return popup;
    popup = node("div", "command-popup");
    popupElement().hidden = true;
    form.append(popup);
    return popup;
  }
  function positionPopup() {
    if (!popup || popupElement().hidden) return;
    const anchor = form;
    const rect = anchor?.getBoundingClientRect();
    if (!rect) return;
    const width = Math.min(Math.max(rect.width, 320), innerWidth - 16);
    popupElement().style.width = `${width}px`;
    popupElement().style.left = `${Math.max(8, Math.min(rect.left, innerWidth - width - 8))}px`;
    const available = Math.max(0, rect.top - 16);
    popupElement().style.maxHeight = `${Math.min(mode === "browsing" ? 416 : 280, available)}px`;
    popupElement().style.bottom = `${Math.max(8, innerHeight - rect.top + 8)}px`;
    popupElement().style.top = "auto";
  }
  function closeBrowse() {
    if (mode !== "browsing") return;
    mode = "closed";
    input.removeAttribute("aria-autocomplete");
    input.removeAttribute("aria-controls");
    input.removeAttribute("aria-activedescendant");
    clearPopup();
    matches = [];
    activeKey = null;
  }
  function updateBrowse() {
    const value = input.value;
    if (!canOpen() || value.includes("\n") || !value.startsWith("/")) {
      if (mode === "browsing") closeBrowse();
      return;
    }
    query = value.slice(1);
    if (catalogState === "loading") {
      matches = [];
      activeKey = null;
      mode = "browsing";
      input.setAttribute("aria-autocomplete", "list");
      ensurePopup();
      renderBrowse();
      return;
    }
    matches = matchCommands(catalog?.entries || [], query);
    if (!matches.length) { closeBrowse(); return; }
    mode = "browsing";
    if (!matches.some((item) => item.key === activeKey)) activeKey = matches[0].key;
    input.setAttribute("aria-autocomplete", "list");
    ensurePopup();
    renderBrowse();
    announce?.(`${matches.length} commands`);
  }
  /** @param {CommandEntry} entry */
  function select(entry) {
    if (!entry) return;
    closeBrowse();
    clearAutocompleteState();
    const required = entry.options.filter((option) => option.required);
    drafts.create(entry);
    detachedReply = composer.report().replyToId;
    ensurePopup();
    if (required.length) renderCompose({ focus: true });
    else { drafts.focus(null); renderCompose({ focus: true }); }
    onCommandMode?.(true);
    announce?.(required.length ? `/${entry.invocation}; ${required.length} required options missing` : `/${entry.invocation}`);
  }
  function exitCommand() {
    if (!draft) return;
    const invocation = `/${currentDraft().invocation}`;
    for (const timer of timers.values()) clearTimeout(timer);
    timers.clear();
    clearAutocompleteState();
    drafts.clear();
    mode = "closed";
    clearPopup();
    contextBar?.remove(); contextBar = null;
    composer.replaceInput(input);
    commandRow = null;
    composePending = [];
    reportPendingMedia();
    input.value = invocation;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    onCommandMode?.(false, detachedReply);
    detachedReply = null;
    input.focus();
    input.setSelectionRange(invocation.length, invocation.length);
  }
  function context() {
    const entry = entryForDraft();
    if (!contextBar) {
      contextBar = node("div", "command-context");
      const text = node("span", "command-context-text");
      text.id = "command-context-description";
      const close = node("button", "command-context-close", "×");
      close.type = "button";
      close.setAttribute("aria-label", "Exit command mode");
      close.addEventListener("click", exitCommand);
      contextElement().append(text, close);
      form.insertBefore(contextBar, row);
    }
    const text = queryElement(contextElement(), ".command-context-text");
    const focused = optionsFor(entry).find((option) => option.name === currentDraft().focused);
    const [name, description] = focused
      ? [focused.name, focused.description || ""]
      : [`/${currentDraft().invocation}`, currentDraft().description || ""];
    text?.replaceChildren(node("strong", "command-context-name", name), node("span", "command-context-detail", description));
    return entry;
  }
  /** @param {CommandOption} option */
  function optionChoices(option) {
    if (option.type === "boolean") return [{ name: "True", value: true }, { name: "False", value: false }];
    return Array.isArray(option.choices) && !option.autocomplete ? option.choices : [];
  }
  /** @param {HTMLElement} owner @param {string} heading @param {readonly Identity[]} items @param {(value: Identity) => void} onChoose @param {{optional?: boolean}} [options] */
  function renderPopup(owner, heading, items, onChoose, { optional = false } = {}) {
    ensurePopup();
    clearPopup();
    popupElement().classList.remove("is-browsing");
    const title = node("div", "command-popup-heading", heading);
    popupElement().append(title);
    /** @type {Promise<unknown>[]} */
    const pending = [];
    const avatarOptions = { assets, loadAsset, isCurrent: () => Boolean(title.isConnected && popup?.contains(title) && !popupElement().hidden && owner.isConnected) };
    const list = node("div", "command-popup-list");
    list.setAttribute("role", "listbox");
    list.id = "command-suggestion-listbox";
    /** @type {HTMLElement[]} */
    const listItems = [];
    items.forEach((value) => {
      const item = node("div", `command-suggestion${value.kind ? " command-entity-suggestion" : ""}`);
      item.dataset.value = String(value.value ?? value.id ?? value.name ?? "");
      if (value.kind === "user") {
        const avatar = node("span", "command-entity-avatar");
        const rendered = renderIdentityAvatar(value, avatarOptions, "command-avatar");
        pending.push(...rendered.pending);
        avatar.append(rendered.element);
        const presence = presenceDot(value);
        if (presence) avatar.append(presence);
        item.append(avatar);
      } else if (value.kind === "role") {
        const swatch = node("span", "command-role-swatch");
        const color = Number(value.color ?? value.icon_color ?? 0) >>> 0;
        swatch.style.backgroundColor = color ? `#${color.toString(16).padStart(6, "0")}` : "#949ca4";
        item.append(swatch);
      } else if (value.kind === "channel") item.append(node("span", "command-channel-mark", "#"));
      const label = String(value.name ?? value.label ?? value.value ?? "");
      item.append(node("span", "command-suggestion-label", label));
      if (value.username) item.append(node("span", "command-suggestion-meta", value.username));
      if (optional) item.append(node("span", "command-suggestion-ellipsis", "…"));
      item.addEventListener("click", () => onChoose(value));
      list.append(item);
      listItems.push(item);
    });
    if (!items.length) list.append(node("div", "command-empty", autocomplete.state === "failed" ? "Loading options failed" : "No options match"));
    popupElement().append(list);
    suggestionListbox = createListbox({ list, owner, idPrefix: "command-suggestion", activeClass: "is-active", onCommit: (item) => {
      const value = items.find((candidate) => String(candidate.value ?? candidate.id ?? candidate.name ?? "") === item?.dataset.value);
      if (value) onChoose(value);
    }});
    suggestionListbox.setItems(listItems);
    if (listItems.length) suggestionListbox.setActive(0, { scroll: false });
    currentPopupOwner = owner;
    owner.setAttribute("aria-controls", list.id);
    owner.setAttribute("aria-expanded", "true");
    popupElement().hidden = false;
    popupPending = pending;
    reportPendingMedia();
    positionPopup();
  }
  function clearPopup() {
    if (popup) {
      popupElement().hidden = true;
      popupElement().replaceChildren();
    }
    listbox?.setExpanded(false);
    listbox = null;
    popupPending = [];
    currentPopupOwner?.setAttribute("aria-expanded", "false");
    currentPopupOwner?.removeAttribute("aria-controls");
    currentPopupOwner?.removeAttribute("aria-activedescendant");
    currentPopupOwner = null;
    suggestionListbox = null;
    reportPendingMedia();
  }
  /** @param {CommandOption | undefined} option @param {Identity} value */
  function chooseValue(option, value) {
    if (!option) return;
    const raw = value.value;
    drafts.value(option.name, ENTITY_TYPES.has(option.type)
      ? { raw: String(raw), entity: value }
      : { raw: typeof raw === "boolean" ? raw : String(raw), label: value.name });
    clearPopup();
    const next = optionsFor().find((candidate) => candidate.required && !valueExists(candidate));
    drafts.focus(next?.name || null);
    renderCompose({ focus: Boolean(next) });
  }
  /** @param {CommandOption} option */
  function candidateEntries(option) {
    const key = controlKey(option);
    const descriptor = lastSnapshot?.candidates?.[key];
    return Array.isArray(descriptor?.entries) ? descriptor.entries.map((entry) => ({
      ...entry, value: String(entry.value ?? entry.id), kind: entry.kind || typeName(option.type),
      name: entry.label ?? entry.name ?? entry.username ?? entry.id,
    })) : [];
  }
  /** @param {CommandOption} option @param {string} value */
  function queryEntity(option, value) {
    const key = controlKey(option);
    const token = `${key}`;
    const queryValue = String(value || "");
    if (timers.has(token)) clearTimeout(timers.get(token));
    timers.set(token, setTimeout(() => {
      timers.delete(token);
      if (!draft || currentDraft().focused !== option.name) return;
      dispatch("browse_candidates", { control_key: key, query: queryValue, cursor: null });
    }, 200));
  }
  /** @param {CommandOption} option @param {string} value */
  function queryAutocomplete(option, value) {
    const commandDraft = currentDraft();
    const queryValue = String(value);
    const token = `autocomplete:${commandDraft.id}:${option.name}`;
    if (timers.has(token)) clearTimeout(timers.get(token));
    autocomplete = { option: option.name, state: "loading", choiceCount: 0 };
    timers.set(token, setTimeout(() => {
      timers.delete(token);
      const input = /** @type {HTMLInputElement | null} */ (queryElement(commandRow, `.command-option-input[data-option="${CSS.escape(option.name)}"]`));
      if (!draft || currentDraft().id !== commandDraft.id || currentDraft().focused !== option.name || input?.value !== queryValue) return;
      const { values } = checkedOptions();
      dispatch("autocomplete_command", { command_id: commandDraft.commandId, path: commandDraft.path,
        schema_fingerprint: commandDraft.schemaFingerprint, focused: option.name, value: queryValue, options: values },
      { draftId: commandDraft.id, draftVersion: commandDraft.version });
    }, 250));
  }
  /** @param {CommandOption} option */
  function addOptional(option) {
    drafts.add(option.name);
    clearPopup();
    renderCompose({ focus: true });
  }
  function optionalItems() {
    return optionsFor().filter((option) => !option.required && !currentDraft().added.includes(option.name));
  }
  /** @param {HTMLElement} owner */
  function openOptions(owner) {
    const items = optionalItems().map((option) => ({ name: option.name, value: option.name, description: option.description }));
    renderPopup(owner, "OPTIONS", items, (value) => {
      const option = optionsFor().find((candidate) => candidate.name === value.value);
      if (option) addOptional(option);
    }, { optional: true });
  }
  function renderCompose({ focus = false } = {}) {
    if (!draft) return;
    mode = pendingKind === "run_command" ? "pending" : "composing";
    const entry = context();
    if (!commandRow || !commandElement().isConnected) {
      commandRow = node("div", "command-row");
      commandElement().dataset.commandMode = "true";
      composer.replaceInput(commandRow);
    }
    const active = commandElement().contains(document.activeElement) && document.activeElement instanceof HTMLInputElement
      ? document.activeElement
      : null;
    const restore = active?.dataset.option
      ? { option: active.dataset.option, start: active.selectionStart, end: active.selectionEnd }
      : null;
    clearPopup();
    commandElement().replaceChildren();
    const app = catalog?.application;
    const pending = [];
    if (app) {
      const rendered = renderIdentityAvatar({ name: app.name, avatar: app.avatarAssetId, bot: true }, {
        assets, loadAsset, isCurrent: () => Boolean(rendered.element.isConnected && commandRow?.contains(rendered.element)),
      }, "command-avatar command-leading-avatar");
      pending.push(...rendered.pending);
      commandElement().append(rendered.element);
    }
    composePending = pending;
    reportPendingMedia();
    const chip = node("button", "command-chip", `/${currentDraft().invocation}`);
    chip.type = "button";
    chip.dataset.commandChip = "true";
    chip.dataset.controlKey = `command:${currentDraft().commandId}:${currentDraft().path.join(".")}:chip`;
    chip.setAttribute("aria-label", `Command /${currentDraft().invocation}`);
    chip.setAttribute("aria-describedby", "command-context-description");
    chip.addEventListener("click", () => {
      drafts.focus(null);
      commandElement().querySelectorAll(".command-pill").forEach((pill) => pill.classList.remove("is-focused"));
      context();
      openOptions(chip);
    });
    chip.addEventListener("keydown", (event) => {
      if (event.key === "Backspace") { event.preventDefault(); exitCommand(); }
    });
    commandElement().append(chip);
    const options = optionsFor(entry);
    const displayed = options.filter((option) => option.required || currentDraft().added.includes(option.name));
    for (const option of displayed) {
      const pill = node("label", `command-pill${currentDraft().focused === option.name ? " is-focused" : ""}${currentDraft().errors[option.name] ? " has-error" : ""}`);
      pill.dataset.option = option.name;
      pill.append(node("span", "command-pill-label", option.name));
      const value = currentDraft().values[option.name];
      if (ENTITY_TYPES.has(option.type) && value?.entity && currentDraft().focused !== option.name) {
        const mention = node("button", "command-mention", `${entityMark(option.type)}${value.entity.name || value.entity.label || value.entity.id}`);
        mention.type = "button";
        mention.dataset.controlKey = controlKey(option);
        mention.dataset.option = option.name;
        mention.setAttribute("aria-label", `${option.name}: ${mention.textContent}; edit`);
        mention.addEventListener("click", () => {
          drafts.value(option.name, { raw: "" });
          drafts.focus(option.name);
          renderCompose({ focus: true });
        });
        pill.append(mention);
      } else if (option.type === "attachment") {
        const fileInput = node("input", "command-file-input");
        fileInput.type = "file";
        fileInput.dataset.controlKey = controlKey(option);
        fileInput.dataset.option = option.name;
        fileInput.tabIndex = -1;
        fileInput.accept = (option.fileTypes || []).map((item) => FILE_TYPE_GROUPS[String(item).toLowerCase()]
          ? `${String(item).toLowerCase()}/*`
          : String(item).startsWith(".") ? item : `.${item}`).join(",");
        fileInput.setAttribute("aria-label", `${option.name} attachment${option.fileTypes?.length ? `, ${option.fileTypes.join(", ")}` : ""}`);
        fileInput.addEventListener("change", () => {
          const file = fileInput.files?.[0];
          if (!file) return;
          if (file.size > 10 * 1024 * 1024) {
            drafts.error(option.name, "File exceeds the 10 MiB per-file limit.");
            renderCompose({ focus: true }); return;
          }
          const allowed = allowedFileExtensions(option.fileTypes);
          const suffix = /\.[^.]+$/.exec(file.name)?.[0]?.toLowerCase() || "";
          if (allowed.length && !allowed.includes(suffix)) {
            drafts.error(option.name, "This file type is not allowed.");
            renderCompose({ focus: true }); return;
          }
          drafts.value(option.name, { file, raw: file.name });
          renderCompose({ focus: true });
        });
        const choose = node("button", "command-file-choose", value?.file ? value.file.name : "Choose file");
        choose.type = "button";
        choose.dataset.option = option.name;
        choose.addEventListener("click", () => fileInput.click());
        pill.append(choose);
        if (option.fileTypes?.length) pill.append(node("small", "command-file-hint", option.fileTypes.join(", ")));
        if (value?.file) {
          const remove = node("button", "command-file-remove", "×");
          remove.type = "button"; remove.dataset.option = option.name; remove.setAttribute("aria-label", `Remove ${value.file.name}`);
          remove.addEventListener("click", () => { drafts.value(option.name, undefined); renderCompose(); });
          pill.append(remove);
        }
        pill.append(fileInput);
        if (currentDraft().errors[option.name]) {
          fileInput.setAttribute("aria-invalid", "true");
          fileInput.setAttribute("aria-describedby", `command-context-description command-error-${option.name}`);
          const error = node("span", "command-error", currentDraft().errors[option.name]);
          error.id = `command-error-${option.name}`;
          pill.append(error);
        } else fileInput.setAttribute("aria-describedby", "command-context-description");
      } else {
        const field = node("input", "command-option-input");
        field.type = option.type === "integer" || option.type === "number" ? "text" : "text";
        field.autocomplete = "off";
        field.spellcheck = false;
        field.dataset.option = option.name;
        field.dataset.controlKey = controlKey(option);
        field.value = typeof value?.raw === "boolean" ? (value.raw ? "True" : "False") : String(value?.raw ?? "");
        const resizeField = () => { field.style.width = `${Math.min(24, Math.max(2, [...field.value].length + 1))}ch`; };
        resizeField();
        field.setAttribute("aria-label", `${option.name}${option.required ? ", required" : ""}`);
        field.setAttribute("aria-describedby", `command-context-description${currentDraft().errors[option.name] ? ` command-error-${option.name}` : ""}`);
        if (currentDraft().errors[option.name]) {
          field.setAttribute("aria-invalid", "true");
          const error = node("span", "command-error", currentDraft().errors[option.name]); error.id = `command-error-${option.name}`; pill.append(error);
        }
        const suggestions = option.type === "boolean" || option.choices?.length || option.autocomplete || ENTITY_TYPES.has(option.type);
        if (suggestions) { field.setAttribute("role", "combobox"); field.setAttribute("aria-autocomplete", "list"); }
        field.readOnly = pendingKind === "run_command";
        field.addEventListener("focus", () => {
          drafts.focus(option.name);
          commandElement().querySelectorAll(".command-pill").forEach((item) => {
            item.classList.toggle("is-focused", item instanceof HTMLElement && item.dataset.option === option.name);
          });
          context();
          if (option.type === "boolean" || option.choices?.length) {
            const choices = optionChoices(option).map((choice) => ({ name: String(choice.name), value: choice.value }));
            renderPopup(field, "OPTIONS", choices, (choice) => chooseValue(option, choice));
          } else if (ENTITY_TYPES.has(option.type)) {
            renderPopup(field, entityCaption(option.type), candidateEntries(option), (candidate) => chooseValue(option, candidate));
            queryEntity(option, field.value);
          } else if (option.autocomplete) {
            const current = String(field.value || "");
            const choices = autocompleteResults.get(autocompleteKey(currentDraft(), option.name, current)) || [];
            renderPopup(field, `OPTIONS MATCHING ${current.toUpperCase()}`, choices, (choice) => chooseValue(option, choice));
          } else clearPopup();
        });
        field.addEventListener("input", () => {
          const raw = option.type === "boolean" && /^(true|false)$/i.test(field.value)
            ? field.value.toLowerCase() === "true" : field.value;
          drafts.value(option.name, { raw });
          resizeField();
          clearOptionError(option, field);
          if (ENTITY_TYPES.has(option.type)) {
            queryEntity(option, field.value);
            renderPopup(field, entityCaption(option.type), candidateEntries(option), (candidate) => chooseValue(option, candidate));
          } else if (option.autocomplete) {
            queryAutocomplete(option, field.value);
            renderPopup(field, `OPTIONS MATCHING ${field.value.toUpperCase()}`, autocompleteResults.get(autocompleteKey(currentDraft(), option.name, field.value)) || [], (choice) => chooseValue(option, choice));
          } else if (option.type === "boolean" || option.choices?.length) {
            renderPopup(field, "OPTIONS", optionChoices(option).filter((choice) => String(choice.name).toLowerCase().includes(field.value.toLowerCase()) || String(choice.value).toLowerCase().includes(field.value.toLowerCase())).map((choice) => ({ name: String(choice.name), value: choice.value })), (choice) => chooseValue(option, choice));
          }
        });
        field.addEventListener("blur", () => setTimeout(() => {
          if (draft && field.isConnected && !popup?.contains(document.activeElement)) validateOnBlur(option, field);
        }, 0));
        pill.append(field);
      }
      if (!option.required) {
        const remove = node("button", "command-option-remove", "×");
        remove.type = "button";
        remove.setAttribute("aria-label", `Remove ${option.name} option`);
        remove.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          drafts.remove(option.name);
          clearPopup();
          renderCompose();
        });
        pill.append(remove);
      }
      if (pendingKind === "run_command") pill.classList.add("is-readonly");
      const result = optionValue(option);
      if (currentDraft().errors[option.name] || (result.present && !result.valid)) pill.classList.add("has-error");
      commandElement().append(pill);
    }
    const remaining = options.filter((option) => !option.required && !currentDraft().added.includes(option.name)).length;
    if (remaining) {
      const hint = node("button", "command-ghost", `+${remaining} ${displayed.length ? "more" : "options"}`);
      hint.type = "button"; hint.addEventListener("click", () => openOptions(hint)); commandElement().append(hint);
    } else if (!displayed.length && options.length) {
      const hint = node("button", "command-ghost", "+ options"); hint.type = "button"; hint.addEventListener("click", () => openOptions(hint)); commandElement().append(hint);
    }
    if (!contextBar?.isConnected) form.insertBefore(contextElement(), row);
    if (pendingKind === "run_command") {
      commandElement().querySelectorAll("button, input[type=file]").forEach((control) => { if (control instanceof HTMLButtonElement || control instanceof HTMLInputElement) control.disabled = true; });
    }
    if (currentDraft().available === false) {
      const alert = node("span", "command-unavailable", "This command is no longer available. Exit command mode to continue.");
      alert.setAttribute("role", "alert"); commandElement().append(alert);
    }
    commandElement().setAttribute("aria-busy", String(autocomplete.state === "loading"));
    /** @param {string} name @returns {HTMLElement | null} */
    const fieldFor = (name) => queryElement(commandElement(),
      `.command-option-input[data-option="${CSS.escape(name)}"], .command-file-choose[data-option="${CSS.escape(name)}"], .command-mention[data-option="${CSS.escape(name)}"]`,
    );
    const focusedOption = currentDraft().focused;
    const target = focus && focusedOption ? fieldFor(focusedOption) : restore ? fieldFor(restore.option) : null;
    if (target) {
      target.focus();
      if (target instanceof HTMLInputElement) {
        const end = target.value.length;
        if (focus) target.setSelectionRange(end, end);
        else target.setSelectionRange(Math.min(restore?.start ?? end, end), Math.min(restore?.end ?? end, end));
      }
    } else if (focus && !currentDraft().focused) chip.focus();
    if (currentDraft().openOptionsOnRender) {
      drafts.optionsOpened();
      openOptions(chip);
    }
  }
  function validateAndRun() {
    if (!draft || currentDraft().available === false) return;
    const { values, invalid } = checkedOptions();
    const uploadOptions = optionsFor().filter((option) => option.type === "attachment" && currentDraft().values[option.name]?.file);
    const totalUploadBytes = uploadOptions.reduce((total, option) => total + (currentDraft().values[option.name]?.file?.size || 0), 0);
    if (totalUploadBytes > 25 * 1024 * 1024 && uploadOptions.length) {
      const last = uploadOptions.at(-1);
      if (last) drafts.error(last.name, "Uploads exceed the 25 MiB aggregate limit.");
    }
    const missing = missingRequired();
    if (missing.length) announce?.(`${missing.length} required options missing`);
    for (const option of optionsFor()) {
      if (option.required && !valueExists(option)) {
        if (!currentDraft().errors[option.name]) drafts.error(option.name, "This field is required.");
      } else if (invalid.some((item) => item.option.name === option.name)) {
        const code = invalid.find((item) => item.option.name === option.name)?.result.error;
        drafts.error(option.name, errorText(code));
      }
    }
    const firstInvalid = optionsFor().find((option) => currentDraft().errors[option.name]);
    if (firstInvalid) { drafts.focus(firstInvalid.name); renderCompose({ focus: true }); return; }
    if (!submittable()) return;
    mode = "pending";
    clearPopup();
    renderCompose();
    /** @type {Record<string, File>} */
    const files = {};
    for (const option of optionsFor()) { const file = currentDraft().values[option.name]?.file; if (file) files[option.name] = file; }
    run({ command_id: currentDraft().commandId, path: currentDraft().path, schema_fingerprint: currentDraft().schemaFingerprint, options: values }, files, {
      draftId: currentDraft().id, draftVersion: currentDraft().version,
    });
  }
  /** @param {KeyboardEvent} event */
  function handleKey(event) {
    if (event.isComposing || event.keyCode === 229 || composer.report().editTargetId) return false;
    if (mode === "browsing") {
      if (["ArrowDown", "ArrowUp", "Enter", "Tab"].includes(event.key)) {
        if (event.key === "Tab" && !listbox?.activeItem()) listbox?.setActive(0);
        if (event.key === "Tab" && listbox?.activeItem()) { listbox.commit(); return true; }
        return Boolean(listbox?.handleKey(event));
      }
      if (event.key === "Escape") { closeBrowse(); return true; }
      return false;
    }
    if (!draft) return false;
    if (["ArrowDown", "ArrowUp"].includes(event.key) && popup && !popupElement().hidden && currentPopupOwner === event.target) {
      return Boolean(suggestionListbox?.handleKey(event));
    }
    if (event.key === "Escape") { clearPopup(); return true; }
    if (event.key === "Backspace" && event.target === commandElement().querySelector(".command-chip")) { exitCommand(); return true; }
    if (event.key === "Backspace" && event.target instanceof HTMLInputElement && !event.target.value && currentDraft().focused === optionsFor()[0]?.name) {
      drafts.focus(null); renderCompose(); queryElement(commandElement(), ".command-chip")?.focus(); return true;
    }
    if (event.key === "Enter") {
      if (popup && !popupElement().hidden && currentPopupOwner === event.target) {
        const list = popupElement().querySelector('[role="listbox"]');
        const active = list?.querySelector('[aria-selected="true"]');
        if (active instanceof HTMLElement) { active.click(); return true; }
      }
      if (event.target instanceof HTMLButtonElement && !event.target.matches(".command-chip")) return false;
      validateAndRun(); return true;
    }
    return false;
  }
  /** @param {KeyboardEvent} event */
  function intercept(event) { return handleKey(event); }
  composer.setKeyInterceptor(intercept);
  form.addEventListener("submit", (event) => {
    if (!draft || composer.report().editTargetId) return;
    event.preventDefault(); event.stopImmediatePropagation(); validateAndRun();
  }, true);
  input.addEventListener("input", () => { if (!draft) updateBrowse(); });
  input.addEventListener("click", () => { if (!draft) updateBrowse(); });
  window.addEventListener("resize", positionPopup);

  const catalogOwner = createCommandCatalog({ fetchCatalog, changed: (report, previous) => {
    catalog = report.catalog;
    catalogFingerprint = report.fingerprint;
    catalogState = report.state;
    if (catalogState !== "loading" && draft) {
      const entry = catalog?.state === "unavailable" ? null
        : catalog?.entries?.find((item) => item.commandId === currentDraft().commandId && item.path.join("\u0000") === currentDraft().path.join("\u0000"));
      const previousEntry = previous?.entries?.find((item) => item.commandId === currentDraft().commandId && item.path.join("\u0000") === currentDraft().path.join("\u0000"));
      const previousSchema = currentDraft().schemaFingerprint;
      drafts.reconcile(entry, previousEntry);
      if (currentDraft().schemaFingerprint !== previousSchema && currentDraft().droppedOptions.length) {
        announce?.(`Dropped values for ${currentDraft().droppedOptions.join(", ")} because the command changed.`);
      }
      renderCompose();
    }
    if (mode === "browsing") updateBrowse();
  } });
  /** @param {Snapshot} snapshot @param {{reset?: boolean}} [options] */
  function update(snapshot, { reset = false } = {}) {
    lastSnapshot = snapshot;
    assets = snapshot?.assets || {};
    const newContext = snapshot?.context?.id || null;
    const newGeneration = snapshot?.context?.generation ?? null;
    if (reset || (contextId !== null && (contextId !== newContext || contextGeneration !== newGeneration || viewerId !== snapshot.viewerId))) {
      clear();
      lastSnapshot = snapshot;
      assets = snapshot?.assets || {};
    }
    contextId = newContext; contextGeneration = newGeneration; viewerId = snapshot?.viewerId ?? null;
    if (!canOpen(snapshot)) {
      catalogOwner.observe(`${contextId}:${contextGeneration}:${viewerId}`, null);
      if (!draft) closeBrowse();
      return;
    }
    catalogOwner.observe(`${contextId}:${contextGeneration}:${viewerId}`, snapshot.commands?.fingerprint || null);
  }
  /** @param {Snapshot} snapshot */
  function receiveSnapshot(snapshot) {
    lastSnapshot = snapshot;
    if (!draft) { if (mode === "browsing") updateBrowse(); return; }
    if (popup && !popupElement().hidden && currentDraft().focused) {
      const option = optionsFor().find((item) => item.name === currentDraft().focused);
      if (option && ENTITY_TYPES.has(option.type)) {
        const input = queryElement(commandRow, `.command-option-input[data-option="${CSS.escape(option.name)}"]`);
        if (input) renderPopup(input, entityCaption(option.type), candidateEntries(option), (entry) => chooseValue(option, entry));
      }
    }
  }
  /** @param {string} kind @param {Partial<Receipt>} receipt @param {ActionBody | null} body @param {ActionMetadata | null} [metadata] */
  function receiveAction(kind, receipt, body, metadata = null) {
    if (kind === "autocomplete_command" && draft && body && metadata?.draftId === currentDraft().id
      && metadata?.draftVersion === draft?.version
      && body.command_id === draft?.commandId
      && body.path?.join("\u0000") === draft?.path.join("\u0000")
      && body.schema_fingerprint === draft?.schemaFingerprint
      && body.focused === draft?.focused) {
      const input = /** @type {HTMLInputElement | null} */ (queryElement(commandRow, `.command-option-input[data-option="${CSS.escape(body.focused || "")}"]`));
      if (!input || input.value !== String(body.value ?? "")) return;
      const key = autocompleteKey(currentDraft(), body.focused || "", body.value);
      const result = receipt?.result;
      if (result?.answered) {
        const choices = Array.isArray(result.choices) ? result.choices.map((choice) => ({ name: choice.name, value: choice.value })) : [];
        autocompleteResults.set(key, choices);
        autocomplete = { option: body.focused || null, state: "answered", choiceCount: choices.length };
      } else {
        autocompleteResults.set(key, []);
        autocomplete = { option: body.focused || null, state: "failed", choiceCount: 0 };
      }
      renderPopup(input, `OPTIONS MATCHING ${input.value.toUpperCase()}`, autocompleteResults.get(key) || [], (choice) => chooseValue(optionsFor().find((item) => item.name === body.focused), choice));
    }
    const submittedDraftIsCurrent = Boolean(draft && metadata?.draftId === currentDraft().id && metadata?.draftVersion === currentDraft().version);
    if (kind === "run_command" && submittedDraftIsCurrent && receipt?.settlement === "settled" && !receipt.rejected) {
      drafts.clear(); mode = "closed"; clearAutocompleteState(); clearPopup(); contextBar?.remove(); contextBar = null;
      composer.replaceInput(input);
      commandRow = null;
      composePending = [];
      reportPendingMedia();
      input.value = "";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      onCommandMode?.(false, detachedReply); detachedReply = null;
    } else if (kind === "run_command" && submittedDraftIsCurrent
      && receipt?.diagnostics?.some((item) => item.code === "command-option-invalid")) {
      const name = receipt.diagnostics.find((item) => item.code === "command-option-invalid")?.subject?.commandOption;
      if (name) { drafts.error(name, "Enter a valid value."); drafts.focus(name); renderCompose({ focus: true }); }
    }
  }
  function setPending() { if (draft) { mode = "pending"; renderCompose(); } }
  function resume() {
    if (draft && mode === "pending" && pendingKind !== "run_command") {
      mode = "composing";
      renderCompose();
    }
  }
  function clear() {
    catalogOwner.reset();
    if (draft) exitCommand();
    closeBrowse();
    for (const timer of timers.values()) clearTimeout(timer);
    timers.clear();
    clearAutocompleteState();
    clearPopup();
    popup?.remove();
    popup = null;
    commandRow?.remove();
    commandRow = null;
    contextBar?.remove();
    contextBar = null;
    composePending = [];
    reportPendingMedia();
    query = null;
    matches = [];
    activeKey = null;
    detachedReply = null;
    lastSnapshot = null;
    assets = {};
    contextId = null;
    contextGeneration = null;
    viewerId = null;
    catalog = null; catalogState = "idle"; catalogFingerprint = null;
  }
  function isCatalogLoadingOpen() { return mode === "browsing" && catalogState === "loading"; }
  return {
    /** @param {{authorized: boolean, pendingKind: string | null}} context */
    setContext: (context) => { authorized = context.authorized; pendingKind = context.pendingKind; },
    update, receiveSnapshot, receiveAction, status, render: () => { if (draft) renderCompose(); else if (mode === "browsing") updateBrowse(); }, setPending, resume, clear, exit: exitCommand, isCatalogLoadingOpen };
}
