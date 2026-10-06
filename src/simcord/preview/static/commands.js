import { createListbox } from "./listbox.js";
import { node, renderIdentityAvatar, presenceDot } from "./dom.js";

const INTEGER_INPUT = /^-?(?:0|[1-9][0-9]*)$/;
const INTEGER_LIMIT = 9007199254740991n;
const NUMBER_LIMIT = 9007199254740992;
const ENTITY_TYPES = new Set(["user", "channel", "role", "mentionable"]);
const FILE_TYPE_GROUPS = {
  image: [".png", ".gif", ".jpg", ".jpeg", ".jfif", ".webp", ".avif"],
  video: [".mp4", ".mov", ".qt", ".webm"],
  audio: [".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac"],
};

function allowedFileExtensions(types) {
  return (types || []).flatMap((type) => FILE_TYPE_GROUPS[String(type).toLowerCase()] || [String(type).toLowerCase()]);
}

function typeName(type) {
  return ({ 3: "string", 4: "integer", 5: "boolean", 6: "user", 7: "channel", 8: "role", 9: "mentionable", 10: "number", 11: "attachment" })[type] || type;
}

/** Browser mirror of parse_option_input/validate_option_value. */
export function validateOptionInput(option, raw) {
  const type = typeName(option.type);
  let value = raw;
  if (type === "string") {
    if (typeof raw !== "string") return { code: "option-type" };
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
    if (typeof raw !== "string" || !raw.trim()) return { code: "option-type" };
    value = Number(raw);
    if (!Number.isFinite(value) || Math.abs(value) > NUMBER_LIMIT) return { code: "option-type" };
  } else if (type === "boolean") {
    if (typeof raw !== "boolean") return { code: "option-type" };
  } else if (ENTITY_TYPES.has(type)) {
    if (typeof raw !== "string" || !/^\d+$/.test(raw)) return { code: "option-type" };
  } else if (type === "attachment") {
    if (!(raw instanceof File)) return { code: "option-type" };
    const allowed = allowedFileExtensions(option.fileTypes || option.file_types);
    const suffix = /\.[^.]+$/.exec(raw.name.replaceAll("\\", "/").split("/").pop() || "")?.[0]?.toLowerCase() || "";
    if (allowed.length && !allowed.includes(suffix)) return { code: "option-file-type" };
    return { value: raw };
  } else return { code: "option-type" };

  if (["integer", "number"].includes(type)) {
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

const entityCaption = (type) => ({ user: "MEMBERS", mentionable: "MEMBERS", role: "ROLES", channel: "CHANNELS" })[type] || "OPTIONS";
const entityMark = (type) => ({ user: "@", mentionable: "@", role: "@", channel: "#" })[type] || "";

export function createCommandPicker({
  form, input, composer, dispatch, run, fetchCatalog, getSnapshot, getState, announce,
  onCommandMode, assets = {}, loadAsset, onCatalogState,
}) {
  let catalog = null;
  let catalogState = "idle";
  let catalogFingerprint = null;
  let generation = 0;
  let contextGeneration = null;
  let contextId = null;
  let viewerId = null;
  let mode = "closed";
  let query = null;
  let matches = [];
  let activeKey = null;
  let draft = null;
  let listbox = null;
  let suggestionListbox = null;
  let popup = null;
  let contextBar = null;
  let commandRow = null;
  let liveNote = "";
  let autocomplete = { option: null, state: "idle", choiceCount: 0 };
  let autocompleteResults = new Map();
  let timers = new Map();
  let candidateQueries = new Map();
  let detachedReply = null;
  let lastSnapshot = null;
  let currentPopupOwner = null;
  const row = composer.inputContainer;

  function canOpen(snapshot = lastSnapshot) {
    return Boolean(snapshot?.layout === "channel" && !getState().editTargetId
      && getState().authorized && snapshot.channel?.canUseApplicationCommands
      && snapshot.commands?.state === "available");
  }
  function entryForDraft() {
    return catalog?.entries?.find((item) => item.commandId === draft?.commandId
      && item.path.join("\u0000") === draft?.path.join("\u0000")) || null;
  }
  function optionsFor(entry = entryForDraft()) { return entry?.options || []; }
  function controlKey(option) {
    return `command:${draft.commandId}:${draft.path.join(".")}:option:${option.name}`;
  }
  function inputValue(option) { return draft?.values?.[option.name]?.raw; }
  function valueExists(option) {
    const value = draft?.values?.[option.name];
    return Boolean(value && (value.file || value.raw !== "" && value.raw != null));
  }
  function optionValue(option, { partial = false } = {}) {
    const value = draft.values[option.name];
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
  function checkedOptions({ partial = false } = {}) {
    const values = {};
    const invalid = [];
    for (const option of optionsFor()) {
      const result = optionValue(option, { partial });
      if (!result.present) continue;
      if (!result.valid) { invalid.push({ option, result }); continue; }
      if (option.type === "attachment") {
        values[option.name] = { upload: Object.keys(values).filter((key) => values[key]?.upload !== undefined).length };
      } else if (option.type === "integer" || option.type === "number") {
        values[option.name] = String(result.wire);
      } else values[option.name] = result.wire;
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
  function errorText(code) {
    return ({
      "option-length": "Value length is outside the allowed range.",
      "option-range": "Value is outside the allowed range.",
      "option-choice": "Choose an available option.",
      "option-type": "Enter a valid value.",
      "option-integer-range": "Integer is outside the safe range.",
      "option-file-type": "This file type is not allowed.",
    })[code] || "Enter a valid value.";
  }
  function clearOptionError(option, field) {
    delete draft.errors[option.name];
    const pill = field.closest(".command-pill");
    pill?.classList.remove("has-error");
    pill?.querySelector(".command-error")?.remove();
    field.removeAttribute("aria-invalid");
    field.setAttribute("aria-describedby", "command-context-description");
  }
  function showOptionError(option, field, message) {
    draft.errors[option.name] = message;
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
  function validateOnBlur(option, field) {
    const result = optionValue(option);
    if (!result.present && !option.required) return;
    if (!result.valid) {
      showOptionError(option, field, result.error === "This field is required."
        ? result.error
        : errorText(result.error));
    } else clearOptionError(option, field);
    refreshStatus();
  }
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
    const state = mode === "composing" && getState().pendingAction?.kind === "run_command" ? "pending" : mode;
    return {
      state: !canOpen(lastSnapshot) && !draft ? "unavailable" : state,
      catalog: { fingerprint: catalogFingerprint, state: catalogState, truncated: Boolean(catalog?.truncated) },
      query: mode === "browsing" ? query : null,
      entryKeys: mode === "browsing" ? matches.slice(0, 50).map((item) => item.key) : [],
      activeEntryKey: mode === "browsing" ? activeKey : null,
      draft: draft ? { commandId: draft.commandId, invocation: draft.invocation, schemaFingerprint: draft.schemaFingerprint,
        available: draft.available !== false, focusedOption: draft.focused, submittable: submittable(),
        missingRequired: missingRequired(), droppedOptions: [...draft.droppedOptions],
        options: optionsFor().map(optionStatus) } : null,
      autocomplete: { ...autocomplete },
    };
  }
  function refreshStatus() { onCatalogState?.(catalogState); }

  function renderBrowse() {
    if (!popup) return;
    popup.replaceChildren();
    const heading = node("div", "command-popup-heading", catalog?.application?.name || "COMMANDS");
    const identity = catalog?.application ? { name: catalog.application.name, avatar: catalog.application.avatarAssetId, bot: true } : null;
    if (identity) {
      const rendered = renderIdentityAvatar(identity, { assets, loadAsset }, "command-avatar");
      heading.prepend(rendered.element);
    }
    popup.append(heading);
    const list = node("div", "command-popup-list");
    list.id = "command-picker-listbox";
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", "Application commands");
    matches.slice(0, 50).forEach((entry) => {
      const item = node("div", "command-entry");
      item.dataset.entryKey = entry.key;
      item.tabIndex = -1;
      const avatar = renderIdentityAvatar(identity || {}, { assets, loadAsset }, "command-avatar");
      item.append(avatar.element);
      const text = node("span", "command-entry-text");
      const title = node("span", "command-entry-title");
      title.append(node("strong", "", `/${entry.invocation}`));
      const optional = entry.options.filter((option) => !option.required).length;
      if (optional) {
        title.append(node("span", "command-optional-count", `+${optional} optional`));
      }
      text.append(title, node("span", "command-entry-description", entry.description || ""));
      item.append(text, node("span", "command-entry-app", catalog.application?.name || ""));
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
    popup.append(list);
    listbox = createListbox({
      list, owner: input, idPrefix: "command-entry", activeClass: "is-active", wrap: false,
      onNavigate: (item) => {
        activeKey = item?.dataset.entryKey || null;
        const active = matches.find((entry) => entry.key === activeKey);
        if (active) announce?.(`/${active.invocation}`);
        refreshStatus();
      },
      onCommit: (item) => {
        const entry = matches.find((candidate) => candidate.key === item?.dataset.entryKey);
        if (entry) select(entry);
      },
    });
    listbox.setItems([...list.querySelectorAll(".command-entry")]);
    const selectedIndex = matches.findIndex((item) => item.key === activeKey);
    if (selectedIndex >= 0) listbox.setActive(selectedIndex, { scroll: false });
    listbox.setExpanded(true);
    popup.hidden = false;
    positionPopup();
  }
  function ensurePopup() {
    if (popup?.isConnected) return popup;
    popup = node("div", "command-popup");
    popup.hidden = true;
    form.append(popup);
    return popup;
  }
  function positionPopup() {
    if (!popup || popup.hidden) return;
    const anchor = mode === "browsing" ? input : contextBar?.isConnected ? contextBar : commandRow;
    const rect = anchor?.getBoundingClientRect();
    if (!rect) return;
    const width = Math.min(Math.max(rect.width, 320), innerWidth - 16);
    popup.style.width = `${width}px`;
    popup.style.left = `${Math.max(8, Math.min(rect.left, innerWidth - width - 8))}px`;
    const available = Math.max(120, rect.top - 16);
    popup.style.maxHeight = `${Math.min(360, available)}px`;
    popup.style.bottom = `${Math.max(8, innerHeight - rect.top + 8)}px`;
    popup.style.top = "auto";
  }
  function closeBrowse() {
    if (mode !== "browsing") return;
    mode = "closed";
    input.removeAttribute("aria-autocomplete");
    input.removeAttribute("aria-controls");
    input.removeAttribute("aria-activedescendant");
    if (popup) popup.hidden = true;
    matches = [];
    activeKey = null;
    refreshStatus();
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
      refreshStatus();
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
    refreshStatus();
  }
  function focusOption(name) {
    draft.focused = name;
    renderCompose({ focus: true });
  }
  function select(entry) {
    if (!entry) return;
    closeBrowse();
    const required = entry.options.filter((option) => option.required);
    draft = { commandId: entry.commandId, path: [...entry.path], invocation: entry.invocation,
      schemaFingerprint: entry.schemaFingerprint, description: entry.description, available: true,
      focused: required[0]?.name || null, values: {}, added: new Set(), errors: {}, droppedOptions: [], version: 0,
      openOptionsOnRender: entry.options.length > 0 && required.length === 0 };
    detachedReply = getState().replyToId;
    onCommandMode?.(true);
    ensurePopup();
    if (required.length) renderCompose({ focus: true });
    else { draft.focused = null; renderCompose(); }
    announce?.(required.length ? `/${entry.invocation}; ${required.length} required options missing` : `/${entry.invocation}`);
    refreshStatus();
  }
  function exitCommand() {
    if (!draft) return;
    const invocation = `/${draft.invocation}`;
    for (const timer of timers.values()) clearTimeout(timer);
    timers.clear();
    draft = null;
    mode = "closed";
    if (popup) popup.hidden = true;
    contextBar?.remove(); contextBar = null;
    composer.replaceInput(input);
    input.value = invocation;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    onCommandMode?.(false, detachedReply);
    detachedReply = null;
    input.focus();
    input.setSelectionRange(invocation.length, invocation.length);
    refreshStatus();
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
      contextBar.append(text, close);
      form.insertBefore(contextBar, row);
    }
    const text = contextBar.querySelector(".command-context-text");
    const focused = optionsFor(entry).find((option) => option.name === draft.focused);
    const [name, description] = focused
      ? [focused.name, focused.description || ""]
      : [`/${draft.invocation}`, draft.description || ""];
    text.replaceChildren(node("strong", "command-context-name", name), node("span", "command-context-detail", description));
    return entry;
  }
  function optionChoices(option) {
    if (option.type === "boolean") return [{ name: "True", value: true }, { name: "False", value: false }];
    return Array.isArray(option.choices) && !option.autocomplete ? option.choices : [];
  }
  function renderPopup(owner, heading, items, onChoose, { entityType = null, optional = false } = {}) {
    ensurePopup();
    popup.replaceChildren(node("div", "command-popup-heading", heading));
    const list = node("div", "command-popup-list");
    list.setAttribute("role", "listbox");
    list.id = "command-suggestion-listbox";
    const listItems = [];
    items.forEach((value) => {
      const item = node("div", `command-suggestion${value.kind ? " command-entity-suggestion" : ""}`);
      item.dataset.value = String(value.value ?? value.id ?? value.name ?? "");
      if (value.kind === "user") {
        const rendered = renderIdentityAvatar(value, { assets, loadAsset }, "command-avatar");
        item.append(rendered.element);
        if (presenceDot(value)) item.append(presenceDot(value));
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
    popup.append(list);
    suggestionListbox = createListbox({ list, owner, idPrefix: "command-suggestion", activeClass: "is-active", onCommit: (item) => {
      const value = items.find((candidate) => String(candidate.value ?? candidate.id ?? candidate.name ?? "") === item?.dataset.value);
      if (value) onChoose(value);
    }});
    suggestionListbox.setItems(listItems);
    if (listItems.length) suggestionListbox.setActive(0, { scroll: false });
    currentPopupOwner = owner;
    owner.setAttribute("aria-controls", list.id);
    owner.setAttribute("aria-expanded", "true");
    popup.hidden = false;
    positionPopup();
  }
  function clearPopup() {
    if (popup) popup.hidden = true;
    currentPopupOwner?.setAttribute("aria-expanded", "false");
    currentPopupOwner?.removeAttribute("aria-controls");
    currentPopupOwner?.removeAttribute("aria-activedescendant");
    currentPopupOwner = null;
    suggestionListbox = null;
  }
  function chooseValue(option, value) {
    const raw = value.value;
    draft.values[option.name] = ENTITY_TYPES.has(option.type)
      ? { raw: String(raw), entity: value }
      : { raw: typeof raw === "boolean" ? raw : String(raw), label: value.name };
    delete draft.errors[option.name];
    draft.version += 1;
    clearPopup();
    const next = optionsFor().find((candidate) => candidate.required && !valueExists(candidate));
    draft.focused = next?.name || null;
    renderCompose({ focus: Boolean(next) });
    refreshStatus();
  }
  function candidateEntries(option) {
    const key = controlKey(option);
    const descriptor = lastSnapshot?.candidates?.[key];
    return Array.isArray(descriptor?.entries) ? descriptor.entries.map((entry) => ({
      ...entry, value: String(entry.value ?? entry.id), kind: entry.kind || option.type,
      name: entry.label ?? entry.name ?? entry.username ?? entry.id,
    })) : [];
  }
  function queryEntity(option, value) {
    const key = controlKey(option);
    const token = `${key}`;
    const queryValue = String(value || "");
    candidateQueries.set(token, queryValue);
    if (timers.has(token)) clearTimeout(timers.get(token));
    timers.set(token, setTimeout(() => {
      timers.delete(token);
      if (!draft || draft.focused !== option.name) return;
      dispatch("browse_candidates", { control_key: key, query: queryValue, cursor: null });
    }, 200));
  }
  function queryAutocomplete(option, value) {
    const token = `${draft.commandId}:${draft.path.join(".")}:${option.name}`;
    if (timers.has(token)) clearTimeout(timers.get(token));
    autocomplete = { option: option.name, state: "loading", choiceCount: 0 };
    autocompleteResults.set(option.name, []);
    timers.set(token, setTimeout(() => {
      timers.delete(token);
      if (!draft || draft.focused !== option.name) return;
      const { values } = checkedOptions({ partial: true });
      dispatch("autocomplete_command", { command_id: draft.commandId, path: draft.path,
        schema_fingerprint: draft.schemaFingerprint, focused: option.name, value: String(value), options: values });
      refreshStatus();
    }, 250));
  }
  function addOptional(option) {
    draft.added.add(option.name);
    draft.focused = option.name;
    clearPopup();
    renderCompose({ focus: true });
  }
  function optionalItems() {
    return optionsFor().filter((option) => !option.required && !draft.added.has(option.name));
  }
  function openOptions(owner) {
    const items = optionalItems().map((option) => ({ name: option.name, value: option.name, description: option.description }));
    renderPopup(owner, "OPTIONS", items, (value) => {
      const option = optionsFor().find((candidate) => candidate.name === value.value);
      if (option) addOptional(option);
    }, { optional: true });
  }
  function renderCompose({ focus = false } = {}) {
    if (!draft) return;
    mode = "composing";
    const entry = context();
    if (!commandRow || !commandRow.isConnected) {
      commandRow = node("div", "command-row");
      commandRow.dataset.commandMode = "true";
      composer.replaceInput(commandRow);
    }
    const active = commandRow.contains(document.activeElement) && document.activeElement instanceof HTMLInputElement
      ? document.activeElement
      : null;
    const restore = active?.dataset.option
      ? { option: active.dataset.option, start: active.selectionStart, end: active.selectionEnd }
      : null;
    commandRow.replaceChildren();
    const app = catalog?.application;
    if (app) commandRow.append(renderIdentityAvatar({ name: app.name, avatar: app.avatarAssetId, bot: true }, { assets, loadAsset }, "command-avatar command-leading-avatar").element);
    const chip = node("button", "command-chip", `/${draft.invocation}`);
    chip.type = "button";
    chip.dataset.commandChip = "true";
    chip.dataset.controlKey = `command:${draft.commandId}:${draft.path.join(".")}:chip`;
    chip.setAttribute("aria-label", `Command /${draft.invocation}`);
    chip.setAttribute("aria-describedby", "command-context-description");
    chip.addEventListener("click", () => {
      draft.focused = null;
      commandRow.querySelectorAll(".command-pill").forEach((pill) => pill.classList.remove("is-focused"));
      context();
      openOptions(chip);
    });
    chip.addEventListener("keydown", (event) => {
      if (event.key === "Backspace") { event.preventDefault(); exitCommand(); }
    });
    commandRow.append(chip);
    const options = optionsFor(entry);
    const displayed = options.filter((option) => option.required || draft.added.has(option.name));
    const files = [];
    for (const option of displayed) {
      const pill = node("label", `command-pill${draft.focused === option.name ? " is-focused" : ""}${draft.errors[option.name] ? " has-error" : ""}`);
      pill.dataset.option = option.name;
      pill.append(node("span", "command-pill-label", option.name));
      const value = draft.values[option.name];
      if (ENTITY_TYPES.has(option.type) && value?.entity && draft.focused !== option.name) {
        const mention = node("button", "command-mention", `${entityMark(option.type)}${value.entity.name || value.entity.label || value.entity.id}`);
        mention.type = "button";
        mention.dataset.controlKey = controlKey(option);
        mention.setAttribute("aria-label", `${option.name}: ${mention.textContent}; edit`);
        mention.addEventListener("click", () => {
          draft.values[option.name] = { raw: "" };
          draft.focused = option.name;
          renderCompose({ focus: true });
        });
        pill.append(mention);
      } else if (option.type === "attachment") {
        const fileInput = node("input", "command-file-input");
        fileInput.type = "file";
        fileInput.dataset.controlKey = controlKey(option);
        fileInput.accept = (option.fileTypes || []).map((item) => FILE_TYPE_GROUPS[String(item).toLowerCase()]
          ? `${String(item).toLowerCase()}/*`
          : String(item).startsWith(".") ? item : `.${item}`).join(",");
        fileInput.setAttribute("aria-label", `${option.name} attachment${option.fileTypes?.length ? `, ${option.fileTypes.join(", ")}` : ""}`);
        fileInput.addEventListener("change", () => {
          const file = fileInput.files?.[0];
          if (!file) return;
          if (file.size > 10 * 1024 * 1024) {
            draft.errors[option.name] = "File exceeds the 10 MiB per-file limit.";
            renderCompose({ focus: true }); return;
          }
          const allowed = allowedFileExtensions(option.fileTypes);
          const suffix = /\.[^.]+$/.exec(file.name)?.[0]?.toLowerCase() || "";
          if (allowed.length && !allowed.includes(suffix)) {
            draft.errors[option.name] = "This file type is not allowed.";
            renderCompose({ focus: true }); return;
          }
          draft.values[option.name] = { file, raw: file.name };
          draft.errors[option.name] = null;
          draft.version += 1;
          renderCompose({ focus: true });
        });
        const choose = node("button", "command-file-choose", value?.file ? value.file.name : "Choose file");
        choose.type = "button";
        choose.addEventListener("click", () => fileInput.click());
        pill.append(choose);
        if (option.fileTypes?.length) pill.append(node("small", "command-file-hint", option.fileTypes.join(", ")));
        if (value?.file) {
          const remove = node("button", "command-file-remove", "×");
          remove.type = "button"; remove.setAttribute("aria-label", `Remove ${value.file.name}`);
          remove.addEventListener("click", () => { delete draft.values[option.name]; renderCompose(); });
          pill.append(remove);
        }
        pill.append(fileInput);
        if (draft.errors[option.name]) {
          fileInput.setAttribute("aria-invalid", "true");
          fileInput.setAttribute("aria-describedby", `command-context-description command-error-${option.name}`);
          const error = node("span", "command-error", draft.errors[option.name]);
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
        field.value = value?.raw == null ? "" : String(value.raw);
        field.setAttribute("aria-label", `${option.name}${option.required ? ", required" : ""}`);
        field.setAttribute("aria-describedby", `command-context-description${draft.errors[option.name] ? ` command-error-${option.name}` : ""}`);
        if (draft.errors[option.name]) {
          field.setAttribute("aria-invalid", "true");
          const error = node("span", "command-error", draft.errors[option.name]); error.id = `command-error-${option.name}`; pill.append(error);
        }
        const suggestions = option.type === "boolean" || option.choices?.length || option.autocomplete || ENTITY_TYPES.has(option.type);
        if (suggestions) { field.setAttribute("role", "combobox"); field.setAttribute("aria-autocomplete", "list"); }
        field.readOnly = Boolean(getState().pendingAction?.kind === "run_command");
        field.addEventListener("focus", () => {
          draft.focused = option.name;
          commandRow.querySelectorAll(".command-pill").forEach((item) => {
            item.classList.toggle("is-focused", item.dataset.option === option.name);
          });
          context();
          if (option.type === "boolean" || option.choices?.length) {
            const choices = optionChoices(option).map((choice) => ({ name: String(choice.name), value: choice.value }));
            renderPopup(field, "OPTIONS", choices, (choice) => chooseValue(option, choice));
          } else if (ENTITY_TYPES.has(option.type)) {
            renderPopup(field, entityCaption(option.type), candidateEntries(option), (candidate) => chooseValue(option, candidate), { entityType: option.type });
          } else if (option.autocomplete) {
            const choices = autocompleteResults.get(option.name) || [];
            const current = String(field.value || "");
            renderPopup(field, `OPTIONS MATCHING ${current.toUpperCase()}`, choices, (choice) => chooseValue(option, choice));
          } else clearPopup();
        });
        field.addEventListener("input", () => {
          draft.values[option.name] = { raw: field.value };
          clearOptionError(option, field);
          draft.version += 1;
          if (ENTITY_TYPES.has(option.type)) {
            queryEntity(option, field.value);
            renderPopup(field, entityCaption(option.type), candidateEntries(option), (candidate) => chooseValue(option, candidate), { entityType: option.type });
          } else if (option.autocomplete) {
            queryAutocomplete(option, field.value);
            renderPopup(field, `OPTIONS MATCHING ${field.value.toUpperCase()}`, autocompleteResults.get(option.name) || [], (choice) => chooseValue(option, choice));
          } else if (option.choices?.length) {
            renderPopup(field, "OPTIONS", optionChoices(option).filter((choice) => String(choice.name).toLowerCase().includes(field.value.toLowerCase()) || String(choice.value).toLowerCase().includes(field.value.toLowerCase())).map((choice) => ({ name: String(choice.name), value: choice.value })), (choice) => chooseValue(option, choice));
          }
          refreshStatus();
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
          draft.added.delete(option.name);
          delete draft.values[option.name];
          delete draft.errors[option.name];
          draft.focused = null;
          clearPopup();
          renderCompose();
        });
        pill.append(remove);
      }
      if (getState().pendingAction?.kind === "run_command") pill.classList.add("is-readonly");
      const result = optionValue(option);
      if (draft.errors[option.name] || (result.present && !result.valid)) pill.classList.add("has-error");
      commandRow.append(pill);
      if (option.type === "attachment" && value?.file) files.push(value.file);
    }
    const remaining = options.filter((option) => !option.required && !draft.added.has(option.name)).length;
    if (remaining) {
      const hint = node("button", "command-ghost", `+${remaining} ${displayed.length ? "more" : "options"}`);
      hint.type = "button"; hint.addEventListener("click", () => openOptions(hint)); commandRow.append(hint);
    } else if (!displayed.length && options.length) {
      const hint = node("button", "command-ghost", "+ options"); hint.type = "button"; hint.addEventListener("click", () => openOptions(hint)); commandRow.append(hint);
    }
    if (!contextBar?.isConnected) form.insertBefore(contextBar, row);
    if (getState().pendingAction?.kind === "run_command") {
      commandRow.querySelectorAll("button, input[type=file]").forEach((control) => { control.disabled = true; });
    }
    if (draft.available === false) {
      const alert = node("span", "command-unavailable", "This command is no longer available. Exit command mode to continue.");
      alert.setAttribute("role", "alert"); commandRow.append(alert);
    }
    commandRow.setAttribute("aria-busy", String(autocomplete.state === "loading"));
    const fieldFor = (name) => commandRow.querySelector(`.command-option-input[data-option="${CSS.escape(name)}"]`);
    const target = focus && draft.focused ? fieldFor(draft.focused) : restore ? fieldFor(restore.option) : null;
    if (target) {
      target.focus();
      const end = target.value.length;
      if (focus) target.setSelectionRange(end, end);
      else target.setSelectionRange(Math.min(restore.start ?? end, end), Math.min(restore.end ?? end, end));
    }
    if (draft.openOptionsOnRender) {
      draft.openOptionsOnRender = false;
      openOptions(chip);
    }
    refreshStatus();
  }
  function validateAndRun() {
    if (!draft || draft.available === false) return;
    const { values, invalid } = checkedOptions();
    const uploadOptions = optionsFor().filter((option) => option.type === "attachment" && draft.values[option.name]?.file);
    const totalUploadBytes = uploadOptions.reduce((total, option) => total + draft.values[option.name].file.size, 0);
    if (totalUploadBytes > 25 * 1024 * 1024 && uploadOptions.length) {
      draft.errors[uploadOptions.at(-1).name] = "Uploads exceed the 25 MiB aggregate limit.";
    }
    const missing = missingRequired();
    if (missing.length) announce?.(`${missing.length} required options missing`);
    for (const option of optionsFor()) {
      if (option.required && !valueExists(option)) {
        if (!draft.errors[option.name]) draft.errors[option.name] = "This field is required.";
      } else if (invalid.some((item) => item.option.name === option.name)) {
        const code = invalid.find((item) => item.option.name === option.name)?.result.error;
        draft.errors[option.name] = errorText(code);
      }
    }
    const firstInvalid = optionsFor().find((option) => draft.errors[option.name]);
    if (firstInvalid) { draft.focused = firstInvalid.name; renderCompose({ focus: true }); return; }
    if (!submittable()) return;
    mode = "pending";
    clearPopup();
    renderCompose();
    const files = {};
    for (const option of optionsFor()) if (draft.values[option.name]?.file) files[option.name] = draft.values[option.name].file;
    run({ command_id: draft.commandId, path: draft.path, schema_fingerprint: draft.schemaFingerprint, options: values }, files, draft.version);
  }
  function handleKey(event) {
    if (event.isComposing || event.keyCode === 229) return false;
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
    if (["ArrowDown", "ArrowUp"].includes(event.key) && popup && !popup.hidden && currentPopupOwner === event.target) {
      return Boolean(suggestionListbox?.handleKey(event));
    }
    if (event.key === "Escape") { clearPopup(); return true; }
    if (event.key === "Tab") {
      const active = event.target instanceof HTMLElement ? event.target.closest("[data-option]")?.dataset.option : null;
      const options = optionsFor().filter((option) => option.required || draft.added.has(option.name));
      const index = options.findIndex((option) => option.name === active);
      if (event.shiftKey && index <= 0) { draft.focused = null; renderCompose(); commandRow.querySelector(".command-chip")?.focus(); return true; }
      const next = options[(index + (event.shiftKey ? -1 : 1) + options.length) % options.length];
      if (next) { draft.focused = next.name; renderCompose({ focus: true }); return true; }
      draft.focused = null; renderCompose(); commandRow.querySelector(".command-chip")?.focus(); return true;
    }
    if (event.key === "Backspace" && event.target === commandRow.querySelector(".command-chip")) { exitCommand(); return true; }
    if (event.key === "Backspace" && event.target instanceof HTMLInputElement && !event.target.value && draft.focused === optionsFor()[0]?.name) {
      draft.focused = null; renderCompose(); commandRow.querySelector(".command-chip")?.focus(); return true;
    }
    if (event.key === "Enter") {
      if (popup && !popup.hidden && currentPopupOwner) {
        const list = popup.querySelector('[role="listbox"]');
        const active = list?.querySelector('[aria-selected="true"]');
        if (active) { active.click(); return true; }
      }
      validateAndRun(); return true;
    }
    return false;
  }
  function intercept(event) { return handleKey(event); }
  composer.setKeyInterceptor(intercept);
  form.addEventListener("submit", (event) => {
    if (!draft) return;
    event.preventDefault(); event.stopImmediatePropagation(); validateAndRun();
  }, true);
  input.addEventListener("input", () => { if (!draft) updateBrowse(); });
  input.addEventListener("click", () => { if (!draft) updateBrowse(); });
  window.addEventListener("resize", positionPopup);

  async function update(snapshot, { reset = false } = {}) {
    lastSnapshot = snapshot;
    assets = snapshot?.assets || {};
    const state = getState();
    const newContext = snapshot?.context?.id || null;
    const newGeneration = snapshot?.context?.generation ?? null;
    if (reset || (contextId !== null && (contextId !== newContext || contextGeneration !== newGeneration || viewerId !== snapshot.viewerId))) {
      if (draft) exitCommand();
      closeBrowse();
      catalog = null; catalogFingerprint = null; catalogState = "idle";
    }
    contextId = newContext; contextGeneration = newGeneration; viewerId = snapshot?.viewerId ?? null;
    if (!canOpen(snapshot)) {
      if (!draft) closeBrowse();
      if (snapshot?.commands?.state !== "available") { catalog = null; catalogState = "idle"; }
      refreshStatus(); return;
    }
    const fingerprint = snapshot.commands?.fingerprint;
    if (!fingerprint || fingerprint === catalogFingerprint || catalogState === "loading") return;
    catalogFingerprint = fingerprint;
    const requestGeneration = ++generation;
    const requestContext = contextId, requestRevisionFingerprint = fingerprint;
    catalogState = "loading";
    refreshStatus();
    try {
      const next = await fetchCatalog();
      if (requestGeneration !== generation || contextId !== requestContext || catalogFingerprint !== requestRevisionFingerprint) return;
      const previous = catalog;
      catalog = next;
      catalogState = next.state === "unavailable" ? "failed" : "ready";
      if (draft) {
        const entry = next.entries.find((item) => item.commandId === draft.commandId && item.path.join("\u0000") === draft.path.join("\u0000"));
        if (!entry) { draft.available = false; renderCompose(); }
        else if (entry.schemaFingerprint !== draft.schemaFingerprint) {
          const old = draft;
          const kept = {};
          const oldOptions = new Map((previous?.entries?.find((item) => item.commandId === old.commandId && item.path.join("\u0000") === old.path.join("\u0000"))?.options || []).map((item) => [item.name, item.type]));
          const nextNames = new Set(entry.options.map((item) => item.name));
          for (const option of entry.options) if (oldOptions.get(option.name) === option.type && old.values[option.name]) kept[option.name] = old.values[option.name];
          old.droppedOptions = Object.keys(old.values).filter((name) => !nextNames.has(name) || oldOptions.get(name) !== entry.options.find((item) => item.name === name)?.type);
          draft = { ...old, schemaFingerprint: entry.schemaFingerprint, description: entry.description, values: kept,
            added: new Set([...old.added].filter((name) => entry.options.some((item) => item.name === name && !item.required))), errors: {}, available: true };
          if (draft.droppedOptions.length) { liveNote = `Dropped values for ${draft.droppedOptions.join(", ")} because the command changed.`; announce?.(liveNote); }
          renderCompose();
        }
      }
      if (mode === "browsing") updateBrowse();
    } catch (_) {
      if (requestGeneration === generation) catalogState = "failed";
    }
    refreshStatus();
  }
  function receiveSnapshot(snapshot) {
    lastSnapshot = snapshot;
    if (!draft) { if (mode === "browsing") updateBrowse(); return; }
    if (popup && !popup.hidden && draft.focused) {
      const option = optionsFor().find((item) => item.name === draft.focused);
      if (option && ENTITY_TYPES.has(option.type)) {
        const input = commandRow?.querySelector(`.command-option-input[data-option="${CSS.escape(option.name)}"]`);
        if (input) renderPopup(input, entityCaption(option.type), candidateEntries(option), (entry) => chooseValue(option, entry));
      }
    }
  }
  function receiveAction(kind, receipt, body) {
    if (kind === "autocomplete_command" && body.command_id === draft?.commandId && body.focused === draft?.focused) {
      const input = commandRow?.querySelector(`.command-option-input[data-option="${CSS.escape(body.focused)}"]`);
      if (input && input.value !== String(body.value ?? "")) return;
      const result = receipt?.result;
      if (result?.answered) {
        const choices = Array.isArray(result.choices) ? result.choices.map((choice) => ({ name: choice.name, value: choice.value })) : [];
        autocompleteResults.set(body.focused, choices);
        autocomplete = { option: body.focused, state: "answered", choiceCount: choices.length };
      } else {
        autocompleteResults.set(body.focused, []);
        autocomplete = { option: body.focused, state: "failed", choiceCount: 0 };
      }
      if (input) renderPopup(input, `OPTIONS MATCHING ${input.value.toUpperCase()}`, autocompleteResults.get(body.focused) || [], (choice) => chooseValue(optionsFor().find((item) => item.name === body.focused), choice));
    }
    if (kind === "run_command" && draft && receipt?.settlement === "settled" && !receipt.rejected) {
      draft = null; mode = "closed"; clearPopup(); contextBar?.remove(); contextBar = null;
      composer.replaceInput(input);
      input.value = "";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      onCommandMode?.(false, detachedReply); detachedReply = null;
    } else if (kind === "run_command" && receipt?.diagnostics?.some((item) => item.code === "command-option-invalid")) {
      const name = receipt.diagnostics.find((item) => item.code === "command-option-invalid")?.subject?.commandOption;
      if (name && draft) { draft.errors[name] = "Enter a valid value."; draft.focused = name; renderCompose({ focus: true }); }
    }
    refreshStatus();
  }
  function setPending() { if (draft) { mode = "pending"; renderCompose(); } }
  function resume() {
    if (draft && mode === "pending" && getState().pendingAction?.kind !== "run_command") {
      mode = "composing";
      renderCompose();
    }
  }
  function clear() { if (draft) exitCommand(); closeBrowse(); catalog = null; catalogState = "idle"; catalogFingerprint = null; }
  function isCatalogLoadingOpen() { return mode === "browsing" && catalogState === "loading"; }
  function shouldShowComposer(snapshot) { return Boolean(snapshot?.layout === "channel" && (snapshot.channel?.canSendMessages || snapshot.channel?.canUseApplicationCommands)); }
  function setAvailability(snapshot) { return shouldShowComposer(snapshot); }

  return { update, receiveSnapshot, receiveAction, status, render: () => { if (draft) renderCompose(); else if (mode === "browsing") updateBrowse(); }, setPending, resume, clear, isCatalogLoadingOpen, setAvailability, handleKey };
}
