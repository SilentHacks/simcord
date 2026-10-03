import { node, presenceDot, renderIdentityAvatar } from "./dom.js";
import { appendEmojiValue } from "./text.js";

const TYPE = Object.freeze({
  STRING_SELECT: 3,
  USER_SELECT: 5,
  ROLE_SELECT: 6,
  MENTIONABLE_SELECT: 7,
  CHANNEL_SELECT: 8,
});
export const SELECT_TYPES = new Set(Object.values(TYPE));
const PERSON_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="currentColor" d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm0 2c-4.4 0-8 2.2-8 5v1h16v-1c0-2.8-3.6-5-8-5z"/></svg>';

function safeId(path) {
  return encodeURIComponent(path);
}
function optionId(key, value) {
  return `option-${safeId(JSON.stringify([key, value]))}`;
}
function keyFor(component, path, scope = "message") {
  if (typeof component.control_key === "string") return component.control_key;
  if (typeof component.id === "number" && component.id > 0) return `${scope}:component:${component.id}`;
  return `${scope}:component:${path}`;
}
function appendEmojiText(parent, text) {
  parent.append(document.createTextNode(String(text ?? "")));
}
export function optionDefaults(options) {
  return options.filter((item) => item && item.default === true).map((item) => String(item.value));
}
export function optionEntries(component, candidates) {
  if (component.type === TYPE.STRING_SELECT) return Array.isArray(component.options) ? component.options : [];
  return Array.isArray(candidates?.entries) ? candidates.entries : [];
}
function entryValue(entry) {
  return String(entry.value ?? entry.id ?? "");
}
export function selectedIds(descriptor) {
  return Array.isArray(descriptor?.selected)
    ? descriptor.selected.map((entry) => String(entry.id))
    : [];
}
function displaySelection(values, entries, selectedEntries, placeholder) {
  const labels = values.map((value) => {
    const found = entries.find((item) => entryValue(item) === String(value))
      || selectedEntries.find((item) => entryValue(item) === String(value));
    return found ? String(found.label ?? found.name ?? found.value ?? found.id) : String(value);
  });
  return labels.length ? labels.join(", ") : (placeholder || "Select an option");
}

export function renderSelect(component, path, options) {
  const { drafts, candidates, dropdown, scope = "message", onInit, onOpen, onDraft, onCommit, onCancel, onNavigate, onClear } = options;
  const key = keyFor(component, path, scope);
  const candidateKey = component.control_key || key;
  const descriptor = candidates?.[candidateKey];
  const entries = optionEntries(component, descriptor);
  const ROLE_MARK_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="currentColor" fill-rule="evenodd" d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM8.6 8.6a3.4 3.4 0 1 0 6.8 0 3.4 3.4 0 0 0-6.8 0zM12 13.2c-3.8 0-7 2-8.4 4.9a9.95 9.95 0 0 0 8.4 4.9 9.95 9.95 0 0 0 8.4-4.9c-1.4-2.9-4.6-4.9-8.4-4.9z"/></svg>';
  const HASH_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="currentColor" d="M10.4 3h2l-.8 5.4h4.4l.8-5.4h2l-.8 5.4h3.6v1.9h-3.9l-1 6.4h3.9v1.9h-4.2l-.8 5.4h-2l.8-5.4H4.4v-1.9h3.9l1-6.4H5.4V8.4h4.2l.8-5.4zM10.3 15.7h4.4l1-6.4h-4.4l-1 6.4z"/></svg>';
  const entityIcon = (entry, kind) => {
    const icon = node("span", "entity-icon");
    if (kind === "user") {
      const rendered = renderIdentityAvatar(entry, options);
      icon.append(rendered.element);
      options.pendingMedia?.push(...rendered.pending);
      const dot = presenceDot(entry);
      if (dot) icon.append(dot);
      return icon;
    }
    if (kind === "role") {
      const mark = node("span", "entity-role-mark");
      const iconColor = Number(entry.icon_color ?? entry.color ?? 0) >>> 0;
      mark.style.color = iconColor ? `#${iconColor.toString(16).padStart(6, "0")}` : "#b5bac1";
      mark.insertAdjacentHTML("beforeend", ROLE_MARK_SVG);
      icon.append(mark);
      return icon;
    }
    if (kind === "channel") {
      const hash = node("span", "entity-channel");
      hash.innerHTML = HASH_SVG;
      icon.append(hash);
      return icon;
    }
    return icon;
  };
  const multi = Number(component.max_values ?? 1) > 1 || Number(component.min_values ?? 1) > 1;
  const minimum = Number(component.min_values ?? 1), maximum = Number(component.max_values ?? 1);
  const identityPending = component.type !== TYPE.STRING_SELECT && options.identityPending?.(candidateKey) === true;
  const selectedEntries = identityPending ? [] : [
    ...(Array.isArray(descriptor?.selected) ? descriptor.selected.filter((entry) => entry && typeof entry === "object") : []),
    ...(options.identityEntries?.(candidateKey) || []),
  ];
  if (!drafts.has(key)) {
    const initial = component.type !== TYPE.STRING_SELECT && Array.isArray(descriptor?.selected)
      ? selectedIds(descriptor)
      : optionDefaults(entries).slice(0, maximum);
    onInit?.(key, initial);
  }
  const rawSelected = Array.isArray(drafts.get(key)) ? [...drafts.get(key)] : [];
  const entryValues = new Set(entries.map(entryValue));
  let selected = identityPending ? [] : rawSelected.map(String);
  if (component.type === TYPE.STRING_SELECT) {
    const valid = selected.filter((value) => entryValues.has(value));
    if (valid.length !== selected.length) drafts.set(key, valid);
    selected = valid;
    onInit?.(key, valid);
  }
  const isOpen = dropdown?.key === key;
  const wrap = node("div", `preview-select select-type-${Number(component.type)}${isOpen ? " is-open" : ""}`); wrap.dataset.controlKey = key;
  wrap.dataset.minimum = String(minimum);
  wrap.dataset.maximum = String(maximum);
  wrap.dataset.optional = String(options.modalHandle != null && component.required === false);
  const popupOpen = () => wrap.classList.contains("is-open");
  const label = component.placeholder || (multi ? "Select one or more options" : "Select an option");
  const trigger = node("button", "select-trigger");
  const valueDisplay = node("span", "select-value");
  const findEntry = (value) => entries.find((entry) => entryValue(entry) === String(value))
    || selectedEntries.find((entry) => entryValue(entry) === String(value));
  const single = selected.length === 1 ? findEntry(selected[0]) : null;
  trigger.setAttribute("aria-invalid", String(options.selectionInvalid?.(key) === true));
  if (multi && selected.length > 0) {
    const chips = node("span", "select-chips");
    for (const value of selected) {
      const entry = findEntry(value);
      const chip = node("span", entry?.kind && entry.kind !== "string" ? "select-chip select-entity" : "select-chip");
      if (entry?.kind && entry.kind !== "string") {
        chip.append(entityIcon(entry, entry.kind));
        if (entry.kind === "role") {
          const swatch = node("span", "entity-role-swatch");
          const color = Number(entry.color ?? 0) >>> 0;
          swatch.style.background = color ? `#${color.toString(16).padStart(6, "0")}` : "#f2f3f5";
          chip.append(swatch);
        }
      } else if (entry?.emoji) appendEmojiValue(chip, entry.emoji, options);
      const chipLabel = node("span", "select-chip-label");
      appendEmojiText(chipLabel, entry ? (entry.label ?? entry.name ?? value) : value);
      chip.append(chipLabel);
      chips.append(chip);
    }
    valueDisplay.append(chips);
  } else if (single && single.kind && single.kind !== "string") {
    const chip = node("span", "select-entity");
    chip.append(entityIcon(single, single.kind));
    if (single.kind === "role") {
      const swatch = node("span", "entity-role-swatch");
      const color = Number(single.color ?? 0) >>> 0;
      swatch.style.background = color ? `#${color.toString(16).padStart(6, "0")}` : "#f2f3f5";
      chip.append(swatch);
    }
    chip.append(node("span", "select-entity-name", single.label ?? single.name ?? selected[0]));
    valueDisplay.append(chip);
  } else {
    if (single?.emoji) { const emoji = node("span", "selected-emoji"); appendEmojiValue(emoji, single.emoji, options); valueDisplay.append(emoji); }
    const valueLabel = node("span", "select-value-label");
    if (selected.length === 1 && single) appendEmojiText(valueLabel, single.label ?? single.name ?? selected[0]);
    else valueLabel.textContent = displaySelection(selected, entries, selectedEntries, label);
    valueDisplay.append(valueLabel);
  }
  trigger.append(valueDisplay);
  let clear = null;
  if (!multi && single && single.kind && single.kind !== "string") {
    clear = node("button", "select-clear");
    clear.type = "button";
    clear.dataset.controlKey = key;
    clear.setAttribute("aria-label", "Clear selection");
    clear.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M18.4 4 12 10.4 5.6 4 4 5.6 10.4 12 4 18.4 5.6 20 12 13.6 18.4 20 20 18.4 13.6 12 20 5.6 18.4 4Z"/></svg>';
    clear.addEventListener("click", () => onClear?.(key, minimum));
    wrap.classList.add("has-clear");
  }
  trigger.type = "button"; trigger.disabled = component.disabled === true || identityPending; trigger.dataset.controlKey = key;
  trigger.id = `select-${safeId(key)}`;
  trigger.setAttribute("role", "combobox");
  trigger.setAttribute("aria-haspopup", "listbox"); trigger.setAttribute("aria-expanded", String(isOpen));
  if (options.labelledBy) trigger.setAttribute("aria-labelledby", options.labelledBy);
  else trigger.setAttribute("aria-label", label);
  trigger.setAttribute("aria-valuetext", displaySelection(selected, entries, selectedEntries, label));
  if (scope.startsWith("modal:") && component.required !== false) trigger.setAttribute("aria-required", "true");
  if (typeof component.custom_id === "string" && component.custom_id && options.validationError?.controlId === component.custom_id) {
    trigger.setAttribute("aria-invalid", "true");
  }
  trigger.setAttribute("aria-controls", `listbox-${safeId(key)}`);
  const activeValue = isOpen ? String(dropdown?.highlight ?? "") : "";
  if (activeValue && entries.some((entry) => String(entry.value ?? entry.id ?? "") === activeValue)) {
    trigger.setAttribute("aria-activedescendant", optionId(key, activeValue));
  }
  // Uncalibrated: singles commit on choice; multis on Enter/close/outside; Escape/blur cancel; modal stays local.
  const choose = (value) => {
    onDraft?.(key, value, multi, minimum, maximum, selected, findEntry(value));
    if (!multi) onCommit?.(key, [value]);
  };
  trigger.addEventListener("click", () => {
    if (popupOpen()) {
      if (multi) onCommit?.(key);
      else onCancel?.(key);
    } else onOpen?.(key, selected, multi, minimum, maximum, undefined, entries, scope.startsWith("modal:"));
  });
  trigger.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && popupOpen()) {
      event.preventDefault();
      event.stopPropagation();
      onCancel?.(key);
      return;
    }
    if (!popupOpen() && ["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) {
      event.preventDefault();
      onOpen?.(key, selected, multi, minimum, maximum, undefined, entries, scope.startsWith("modal:"));
      return;
    }
    if (!popupOpen()) return;
    const optionNodes = [...list.querySelectorAll('[role="option"]:not(.is-disabled)')];
    let index = optionNodes.findIndex((item) => item.dataset.value === activeValue);
    if (event.key === "ArrowDown" || event.key === "ArrowUp" || event.key === "Home" || event.key === "End") {
      event.preventDefault();
      if (event.key === "Home") index = 0;
      else if (event.key === "End") index = optionNodes.length - 1;
      else {
        const start = index < 0 ? (event.key === "ArrowUp" ? optionNodes.length : -1) : index;
        const delta = event.key === "ArrowDown" ? 1 : -1;
        index = Math.max(0, Math.min(optionNodes.length - 1, start + delta));
      }
      if (optionNodes[index]) onNavigate?.(key, optionNodes[index].dataset.value);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (multi && event.key === "Enter") {
        onCommit?.(key);
        return;
      }
      const value = optionNodes[index]?.dataset.value;
      if (value !== undefined) choose(value);
    }
  });
  wrap.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && popupOpen()) {
      event.preventDefault();
      event.stopPropagation();
      onCancel?.(key);
    }
  });
  wrap.addEventListener("focusout", () => {
    if (!popupOpen() || !wrap.isConnected) return;
    setTimeout(() => {
      if (!wrap.isConnected || wrap.contains(document.activeElement)) return;
      if (multi) onCommit?.(key);
      else onCancel?.(key);
    }, 0);
  });
  const candidateQuery = () => options.candidateQuery?.(candidateKey) ?? descriptor?.query ?? "";
  const candidatePage = (cursor) => options.onCandidateQuery?.(
    candidateKey, candidateQuery(), cursor, options.modalHandle || null,
  );
  wrap.append(trigger);
  if (clear) wrap.append(clear);
  {
    const guidance = node("div", "select-guidance");
    guidance.setAttribute("role", "status");
    guidance.setAttribute("aria-live", "polite");
    guidance.textContent = identityPending ? "Checking access to saved selections."
      : options.selectionStatus?.(key) || `${selected.length} selected. Choose ${minimum}–${maximum} options.`;
    guidance.id = `select-guidance-${safeId(key)}`;
    trigger.setAttribute("aria-describedby", guidance.id);
    wrap.append(guidance);
  }
  const popup = node("div", "select-list");
  popup.hidden = !isOpen;
  popup.setAttribute("popover", "manual");
  popup.dataset.controlKey = `${key}:list`;
  const list = node("div", "select-options");
  list.id = `listbox-${safeId(key)}`;
  list.setAttribute("role", "listbox");
  list.setAttribute("aria-multiselectable", String(multi));
  list.setAttribute("aria-labelledby", trigger.id);
  list.dataset.controlKey = `${key}:options`;
  if (component.type !== TYPE.STRING_SELECT) {
    const tools = node("div", "select-candidate-tools");
    const searchLabel = node("label", "", "Search options");
    const search = node("input", "select-candidate-search");
    search.type = "search";
    search.maxLength = 128;
    search.autocomplete = "off";
    search.dataset.controlKey = `${key}:candidate-query`;
    search.value = options.candidateQuery?.(candidateKey) ?? descriptor?.query ?? "";
    searchLabel.append(search);
    search.addEventListener("input", () => {
      options.onCandidateQuery?.(candidateKey, search.value, null, options.modalHandle || null);
    });
    const pages = node("div", "select-candidate-pages");
    const previous = node("button", "", "Previous");
    previous.type = "button";
    previous.disabled = !descriptor?.hasPrevious;
    previous.addEventListener("click", () => candidatePage(descriptor?.previousCursor ?? null));
    const next = node("button", "", "Next");
    next.type = "button";
    next.disabled = !descriptor?.hasNext;
    next.addEventListener("click", () => candidatePage(descriptor?.nextCursor ?? null));
    pages.append(previous, next);
    const stateLabel = descriptor?.state === "unavailable"
      ? "Options unavailable"
      : options.candidateLoading?.(candidateKey) || descriptor?.state === "loading"
        ? "Loading options"
        : descriptor?.state === "empty"
          ? "No matching options"
          : `${entries.length} options on this page`;
    tools.append(searchLabel, pages, node("span", "select-candidate-status", stateLabel));
    popup.append(tools);
  }
  const decorateEntity = (option, entry, kind) => {
    option.classList.add("option-entity");
    option.append(entityIcon(entry, kind));
    const label = node("span", "entity-label");
    if (kind === "user") {
      label.append(node("span", "entity-name", entry.label ?? entry.name ?? ""));
      if (entry.username) label.append(node("span", "entity-username", entry.username));
      if (entry.bot) label.append(node("span", "entity-app-badge", "APP"));
      option.append(label);
      return;
    }
    if (kind === "role") {
      const color = Number(entry.color ?? 0) >>> 0;
      const swatch = node("span", "entity-role-swatch");
      swatch.style.background = color ? `#${color.toString(16).padStart(6, "0")}` : "#f2f3f5";
      label.append(swatch, node("span", "entity-name entity-dim", entry.label ?? entry.name ?? ""));
      const count = node("span", "entity-count");
      count.insertAdjacentHTML("beforeend", PERSON_SVG);
      count.append(document.createTextNode(String(entry.members ?? 0)));
      label.append(count);
      option.append(label);
      return;
    }
    label.append(node("span", `entity-name${kind === "channel" ? " entity-dim" : ""}`, entry.label ?? entry.name ?? ""));
    option.append(label);
  };
  const atMax = multi && selected.length >= maximum;
  entries.forEach((entry) => {
    const value = entryValue(entry);
    const option = node("div", "select-option");
    option.id = optionId(key, value);
    option.dataset.value = value;
    option.setAttribute("role", "option");
    option.setAttribute("aria-selected", String(selected.includes(value)));
    option.tabIndex = -1;
    const disabled = atMax && !selected.includes(value);
    if (disabled) { option.classList.add("is-disabled"); option.setAttribute("aria-disabled", "true"); }
    if (selected.includes(value)) { option.classList.add("is-selected"); const tick = node("span", "option-check"); tick.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true"><path fill="#fff" d="M9.55 16.93 4.41 11.79a1.1 1.1 0 1 0-1.41 1.41l5.84 5.84a1.1 1.1 0 0 0 1.42 0L21 8.3a1.1 1.1 0 1 0-1.41-1.41L9.55 16.93Z"/></svg>'; option.append(tick); } if (isOpen && String(dropdown.highlight ?? "") === value) option.classList.add("is-highlighted");
    if (entry.kind && entry.kind !== "string") {
      decorateEntity(option, entry, entry.kind);
    } else {
      if (entry.emoji) { const emoji = node("span", "option-emoji"); appendEmojiValue(emoji, entry.emoji, options); option.append(emoji); }
      const optionLabel = node("span", "option-label"); appendEmojiText(optionLabel, entry.label ?? entry.name ?? value); option.append(optionLabel);
      if (entry.description) option.append(node("small", "option-description", entry.description));
    }
    option.addEventListener("click", () => { if (disabled) return; choose(value); }); list.append(option);
  });
  if (!entries.length) list.append(node("div", "select-empty", "No available options"));
  popup.append(list);
  if (multi && isOpen) {
    const actions = node("div", "select-draft-actions preview-helper");
    actions.setAttribute("role", "group");
    actions.setAttribute("aria-label", "SimCord selection draft actions");
    actions.append(node("span", "", "SimCord selection draft"));
    const apply = node("button", "select-apply", "Apply");
    apply.type = "button";
    apply.addEventListener("click", () => onCommit?.(key));
    const cancel = node("button", "select-cancel", "Cancel");
    cancel.type = "button";
    cancel.addEventListener("click", () => onCancel?.(key));
    actions.append(apply, cancel);
    popup.append(actions);
  }
  wrap.append(popup);
  return { element: wrap, value: () => (Array.isArray(drafts.get(key)) ? [...drafts.get(key)] : []), key };
}
