const TYPE = Object.freeze({
  ROW: 1,
  BUTTON: 2,
  STRING_SELECT: 3,
  TEXT_INPUT: 4,
  USER_SELECT: 5,
  ROLE_SELECT: 6,
  MENTIONABLE_SELECT: 7,
  CHANNEL_SELECT: 8,
  SECTION: 9,
  TEXT_DISPLAY: 10,
  THUMBNAIL: 11,
  MEDIA_GALLERY: 12,
  FILE: 13,
  SEPARATOR: 14,
  CONTAINER: 17,
  LABEL: 18,
  FILE_UPLOAD: 19,
  RADIO_GROUP: 21,
  CHECKBOX_GROUP: 22,
  CHECKBOX: 23,
});

const SELECT_TYPES = new Set([TYPE.STRING_SELECT, TYPE.USER_SELECT, TYPE.ROLE_SELECT, TYPE.MENTIONABLE_SELECT, TYPE.CHANNEL_SELECT]);

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function keyFor(component, path) {
  if (typeof component.custom_id === "string") return component.custom_id;
  if (typeof component.id === "number" && component.id > 0) return `id-${component.id}`;
  return path;
}

function safeLink(value) {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value, window.location.origin);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch (_) {
    return null;
  }
}

function appendLabel(parent, text, required = false) {
  const label = node("span", "control-label", text || "");
  if (required) label.append(node("span", "required-marker", " *"));
  parent.append(label);
  return label;
}

function optionDefaults(component, options) {
  if (Array.isArray(component.default_values)) return component.default_values.map(String);
  return options.filter((item) => item && item.default === true).map((item) => String(item.value ?? item.id));
}

function optionEntries(component, candidates) {
  if (component.type === TYPE.STRING_SELECT) return Array.isArray(component.options) ? component.options : [];
  return Array.isArray(candidates) ? candidates : [];
}

function displaySelection(values, entries, placeholder) {
  const labels = values.map((value) => {
    const found = entries.find((item) => String(item.value ?? item.id) === String(value));
    return found ? String(found.label ?? found.name ?? found.value ?? found.id) : String(value);
  });
  return labels.length ? labels.join(", ") : (placeholder || "Select an option");
}

function renderSelect(component, path, options) {
  const {
    drafts,
    candidates,
    dropdown,
    scope = "message",
    onInit,
    onOpen,
    onDraft,
    onCommit,
    onCancel,
  } = options;
  const key = `${scope}:${keyFor(component, path)}`;
  const entries = optionEntries(component, candidates?.[component.custom_id]);
  const multi = Number(component.max_values ?? 1) > 1 || Number(component.min_values ?? 1) > 1;
  const minimum = Number(component.min_values ?? 1);
  const maximum = Number(component.max_values ?? 1);
  if (!drafts.has(key)) onInit?.(key, optionDefaults(component, entries).slice(0, maximum));
  const selected = Array.isArray(drafts.get(key)) ? [...drafts.get(key)] : [];
  const isOpen = dropdown?.key === key;
  const wrap = node("div", `preview-select${isOpen ? " is-open" : ""}`);
  wrap.dataset.controlKey = key;
  const trigger = node("button", "select-trigger", displaySelection(selected, entries, component.placeholder));
  trigger.type = "button";
  trigger.disabled = component.disabled === true;
  trigger.dataset.controlKey = key;
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", String(isOpen));
  trigger.setAttribute("aria-controls", `listbox-${path.replace(/[^a-zA-Z0-9_-]/g, "-")}`);
  trigger.addEventListener("click", () => onOpen?.(key, selected, multi, minimum, maximum));
  trigger.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onOpen?.(key, selected, multi, minimum, maximum);
    }
  });
  wrap.append(trigger);

  const list = node("div", "select-list");
  list.id = `listbox-${path.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  list.hidden = !isOpen;
  list.setAttribute("role", "listbox");
  list.setAttribute("aria-multiselectable", String(multi));
  list.tabIndex = isOpen ? 0 : -1;
  if (isOpen) {
    list.addEventListener("keydown", (event) => {
      const optionNodes = [...list.querySelectorAll('[role="option"]')];
      let index = Math.max(0, optionNodes.findIndex((item) => item.dataset.value === String(dropdown.highlight)));
      if (event.key === "ArrowDown") {
        event.preventDefault();
        index = Math.min(optionNodes.length - 1, index + 1);
        onOpen?.(key, selected, multi, minimum, maximum, optionNodes[index]?.dataset.value);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        index = Math.max(0, index - 1);
        onOpen?.(key, selected, multi, minimum, maximum, optionNodes[index]?.dataset.value);
      } else if (event.key === "Home" || event.key === "End") {
        event.preventDefault();
        index = event.key === "Home" ? 0 : optionNodes.length - 1;
        onOpen?.(key, selected, multi, minimum, maximum, optionNodes[index]?.dataset.value);
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        const value = optionNodes[index]?.dataset.value;
        if (value !== undefined) onDraft?.(key, value, multi, minimum, maximum, selected);
        if (!multi && value !== undefined) onCommit?.(key, [value]);
      } else if (event.key === "Escape") {
        event.preventDefault();
        onCancel?.(key);
      }
    });
  }
  entries.forEach((entry, index) => {
    const value = String(entry.value ?? entry.id ?? "");
    const option = node("div", "select-option");
    option.dataset.value = value;
    option.setAttribute("role", "option");
    option.setAttribute("aria-selected", String(selected.includes(value)));
    option.tabIndex = -1;
    if (selected.includes(value)) option.classList.add("is-selected");
    if (dropdown?.key === key && String(dropdown.highlight ?? "") === value) option.classList.add("is-highlighted");
    const text = node("span", "option-label", entry.label ?? entry.name ?? value);
    option.append(text);
    if (entry.description) option.append(node("small", "option-description", entry.description));
    option.addEventListener("click", () => {
      onDraft?.(key, value, multi, minimum, maximum, selected);
      if (!multi) onCommit?.(key, [value]);
    });
    list.append(option);
    if (isOpen && index === 0 && dropdown.highlight === undefined) dropdown.highlight = value;
  });
  if (!entries.length) list.append(node("div", "select-empty", "No available options"));
  if (isOpen && multi) {
    const hint = node("div", "select-hint", `Enter to apply · Escape to cancel · ${selected.length}/${maximum} selected`);
    list.append(hint);
  }
  wrap.append(list);
  return { element: wrap, value: () => (Array.isArray(drafts.get(key)) ? [...drafts.get(key)] : []), key };
}

function renderButton(component, path, options) {
  const button = node("button", "component-button");
  const style = Number(component.style || 1);
  button.classList.add(`button-style-${style}`);
  button.type = "button";
  button.disabled = component.disabled === true;
  button.dataset.controlKey = keyFor(component, path);
  if (component.emoji && typeof component.emoji === "object") {
    button.append(node("span", "button-emoji", component.emoji.name || ""));
  }
  if (component.label) button.append(node("span", "button-label", component.label));
  if (style === 5) {
    const href = safeLink(component.url);
    if (href) {
      const link = node("a", "component-button link-button");
      link.href = href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = button.textContent || "Open link";
      return link;
    }
    button.disabled = true;
    button.append(node("span", "button-unavailable", "Unavailable link"));
  } else if (style === 6) {
    button.disabled = true;
    button.append(node("span", "button-unavailable", "Purchase unavailable"));
  } else if (typeof component.custom_id === "string") {
    button.addEventListener("click", () => options.onClick?.(component.custom_id));
  }
  return button;
}

function mediaElement(media, className, options, label) {
  const assetId = media && typeof media.asset_id === "string" ? media.asset_id : null;
  if (!assetId || !options.loadAsset) {
    options.onDiagnostic?.({ code: "media-unavailable", severity: "warning", message: `${label || "Media"} is unavailable offline`, complete: false });
    return { element: node("div", "media-unavailable", `${label || "Media"} unavailable`), pending: [] };
  }
  const image = node("img", className);
  image.alt = String(media.description || label || "Preview media");
  const pending = [Promise.resolve(options.loadAsset(assetId)).then((url) => {
    if (!options.isCurrent?.()) return;
    image.src = url;
    if (typeof image.decode === "function") return image.decode().catch(() => undefined);
    return new Promise((resolve) => {
      image.complete ? resolve() : image.addEventListener("load", resolve, { once: true });
    });
  }).catch((error) => {
    if (options.isCurrent?.()) {
      options.onDiagnostic?.({ code: "media-unavailable", severity: "warning", message: `${label || "Media"} is unavailable offline`, detail: String(error), complete: false });
      image.replaceWith(node("div", "media-unavailable", `${label || "Media"} unavailable`));
    }
  })];
  return { element: image, pending };
}

function renderNode(component, path, options) {
  const type = Number(component?.type);
  if (type === TYPE.ROW) {
    const row = node("div", "component-row");
    (component.components || []).forEach((child, index) => row.append(renderNode(child, `${path}.components.${index}`, options)));
    return row;
  }
  if (type === TYPE.BUTTON) return renderButton(component, path, options);
  if (SELECT_TYPES.has(type)) return renderSelect(component, path, options).element;
  if (type === TYPE.TEXT_DISPLAY) return node("div", "text-display", component.content || "");
  if (type === TYPE.SECTION) {
    const section = node("section", "component-section");
    const text = node("div", "section-text");
    (component.components || []).forEach((child, index) => text.append(renderNode(child, `${path}.components.${index}`, options)));
    section.append(text);
    if (component.accessory) {
      const accessory = node("div", "section-accessory");
      accessory.append(renderNode(component.accessory, `${path}.accessory`, options));
      section.append(accessory);
    }
    return section;
  }
  if (type === TYPE.CONTAINER) {
    const container = node("section", "component-container");
    if (component.accent_color !== undefined) container.style.setProperty("--accent", `#${Number(component.accent_color).toString(16).padStart(6, "0")}`);
    (component.components || []).forEach((child, index) => container.append(renderNode(child, `${path}.components.${index}`, options)));
    return container;
  }
  if (type === TYPE.SEPARATOR) {
    const separator = node("hr", `component-separator spacing-${Number(component.spacing || 1)}`);
    separator.setAttribute("aria-hidden", "true");
    return separator;
  }
  if (type === TYPE.THUMBNAIL) {
    const media = mediaElement(component.media, "component-thumbnail", options, "Thumbnail");
    const figure = node("figure", "component-media");
    figure.append(media.element);
    if (component.description) figure.append(node("figcaption", "media-description", component.description));
    options.pendingMedia?.push(...media.pending);
    return figure;
  }
  if (type === TYPE.MEDIA_GALLERY) {
    const gallery = node("div", "component-gallery");
    (component.items || []).forEach((item, index) => {
      const media = mediaElement(item.media, "gallery-image", options, `Gallery item ${index + 1}`);
      const figure = node("figure", "gallery-item");
      figure.append(media.element);
      if (item.description) figure.append(node("figcaption", "media-description", item.description));
      gallery.append(figure);
      options.pendingMedia?.push(...media.pending);
    });
    return gallery;
  }
  if (type === TYPE.FILE) {
    const file = node("div", "component-file", component.name || component.file?.filename || "Attached file");
    if (component.size !== undefined) file.append(node("small", "file-size", `${component.size} bytes`));
    return file;
  }
  options.onDiagnostic?.({ code: "unsupported-component", severity: "warning", message: `Unsupported component type ${type} at ${path}`, complete: false });
  return node("div", "component-unavailable", `Component type ${type} unavailable`);
}

function renderEmbed(embed, index, options) {
  const card = node("article", "embed-card");
  if (embed.color !== undefined || embed.color_value !== undefined) {
    const color = Number(embed.color ?? embed.color_value);
    if (Number.isFinite(color)) card.style.setProperty("--embed-color", `#${color.toString(16).padStart(6, "0")}`);
  }
  if (embed.author?.name) card.append(node("div", "embed-author", embed.author.name));
  if (embed.title) {
    const title = safeLink(embed.url) ? node("a", "embed-title", embed.title) : node("div", "embed-title", embed.title);
    const href = safeLink(embed.url);
    if (href) { title.href = href; title.target = "_blank"; title.rel = "noopener noreferrer"; }
    card.append(title);
  }
  if (embed.description) card.append(node("div", "embed-description", embed.description));
  if (Array.isArray(embed.fields) && embed.fields.length) {
    const fields = node("div", "embed-fields");
    embed.fields.forEach((field) => {
      const item = node("div", field.inline ? "embed-field inline" : "embed-field");
      item.append(node("strong", "embed-field-name", field.name || ""));
      item.append(node("span", "embed-field-value", field.value || ""));
      fields.append(item);
    });
    card.append(fields);
  }
  const media = embed.thumbnail || embed.image;
  if (media) {
    const rendered = mediaElement(media, "embed-image", options, `Embed ${index + 1} image`);
    card.append(rendered.element);
    options.pendingMedia?.push(...rendered.pending);
  }
  if (embed.footer?.text || embed.timestamp) card.append(node("footer", "embed-footer", embed.footer?.text || embed.timestamp));
  return card;
}

export function renderMessage(root, message, options = {}) {
  const pendingMedia = options.pendingMedia || [];
  options.pendingMedia = pendingMedia;
  root.replaceChildren();
  if (!message) return { pendingMedia };
  const header = node("header", "message-header");
  header.append(node("strong", "message-author", message.author?.name || "Unknown author"));
  if (message.timestamp) header.append(node("time", "message-time", message.timestamp));
  if (message.ephemeral) header.append(node("span", "message-badge", "Ephemeral"));
  root.append(header);
  if (message.content) root.append(node("div", "message-content", message.content));
  if ((Number(message.flags) & 4) === 0) {
    (message.embeds || []).forEach((embed, index) => root.append(renderEmbed(embed, index, options)));
  }
  if (message.components?.length) {
    const components = node("div", "message-components");
    message.components.forEach((component, index) => components.append(renderNode(component, `message.${index}`, options)));
    root.append(components);
  }
  if (message.attachments?.length) {
    const attachments = node("ul", "message-attachments");
    message.attachments.forEach((attachment) => {
      const item = node("li", "attachment");
      item.append(node("span", "attachment-name", attachment.filename || "attachment"));
      if (attachment.size !== undefined) item.append(node("small", "attachment-size", `${attachment.size} bytes`));
      if (attachment.asset_id) {
        const media = mediaElement(attachment, "attachment-image", options, attachment.filename || "Attachment");
        item.append(media.element);
        pendingMedia.push(...media.pending);
      }
      attachments.append(item);
    });
    root.append(attachments);
  }
  return { pendingMedia };
}

function modalDefault(component, entries) {
  const type = Number(component.type);
  if (type === TYPE.TEXT_INPUT) return String(component.value ?? component.default ?? "");
  if (SELECT_TYPES.has(type)) return optionDefaults(component, entries);
  if (type === TYPE.RADIO_GROUP) return (component.options || []).find((item) => item.default)?.value ?? null;
  if (type === TYPE.CHECKBOX_GROUP) return optionDefaults(component, component.options || []);
  if (type === TYPE.CHECKBOX) return component.default === true;
  return "";
}

function modalControl(component, path, labelText, options) {
  const customId = String(component.custom_id || path);
  const key = `modal:${customId}`;
  const field = node("div", "modal-field");
  field.dataset.controlKey = key;
  const required = component.required === true || (component.required === undefined && Number(component.type) === TYPE.TEXT_INPUT);
  if (labelText) appendLabel(field, labelText, required);
  const type = Number(component.type);
  if (type === TYPE.TEXT_INPUT) {
    const input = node(component.style === 2 ? "textarea" : "input", "modal-input");
    input.id = `modal-input-${path.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
    input.name = customId;
    input.placeholder = String(component.placeholder || "");
    input.required = required;
    if (component.min_length !== undefined) input.minLength = Number(component.min_length);
    if (component.max_length !== undefined) input.maxLength = Number(component.max_length);
    if (!options.drafts.has(key)) options.drafts.set(key, modalDefault(component, []));
    input.value = String(options.drafts.get(key) ?? "");
    input.addEventListener("input", () => options.onDraft?.(key, input.value));
    field.append(input);
    return { field, get: () => String(options.drafts.get(key) ?? input.value) };
  }
  if (SELECT_TYPES.has(type)) {
    const entries = optionEntries(component, options.candidates?.[customId]);
    if (!options.drafts.has(key)) options.drafts.set(key, modalDefault(component, entries));
    const select = renderSelect(component, path, { ...options, scope: "modal" });
    field.append(select.element);
    return { field, get: select.value };
  }
  if (type === TYPE.RADIO_GROUP || type === TYPE.CHECKBOX_GROUP) {
    const entries = component.options || [];
    if (!options.drafts.has(key)) options.drafts.set(key, modalDefault(component, entries));
    const group = node("div", "modal-choice-group");
    group.setAttribute("role", type === TYPE.RADIO_GROUP ? "radiogroup" : "group");
    entries.forEach((entry) => {
      const value = String(entry.value ?? "");
      const choice = node("label", "modal-choice");
      const input = node("input");
      input.type = type === TYPE.RADIO_GROUP ? "radio" : "checkbox";
      input.name = customId;
      input.value = value;
      const current = options.drafts.get(key);
      input.checked = type === TYPE.RADIO_GROUP ? current === value : Array.isArray(current) && current.includes(value);
      input.addEventListener("change", () => {
        if (type === TYPE.RADIO_GROUP) options.onDraft?.(key, value);
        else {
          const next = new Set(Array.isArray(options.drafts.get(key)) ? options.drafts.get(key) : []);
          input.checked ? next.add(value) : next.delete(value);
          options.onDraft?.(key, [...next]);
        }
      });
      choice.append(input, node("span", "choice-label", entry.label || value));
      group.append(choice);
    });
    field.append(group);
    return { field, get: () => options.drafts.get(key) };
  }
  if (type === TYPE.CHECKBOX) {
    if (!options.drafts.has(key)) options.drafts.set(key, modalDefault(component, []));
    const choice = node("label", "modal-choice");
    const input = node("input");
    input.type = "checkbox";
    input.name = customId;
    input.checked = options.drafts.get(key) === true;
    input.addEventListener("change", () => options.onDraft?.(key, input.checked));
    choice.append(input, node("span", "choice-label", labelText || customId));
    field.append(choice);
    return { field, get: () => options.drafts.get(key) === true };
  }
  if (type === TYPE.FILE_UPLOAD) {
    const input = node("input", "modal-input");
    input.type = "file";
    input.disabled = true;
    field.append(input, node("small", "field-note", "File upload is unavailable in this browser slice"));
    return { field, get: () => [] };
  }
  options.onDiagnostic?.({ code: "unsupported-modal-component", severity: "warning", message: `Unsupported modal component ${type}`, complete: false });
  return { field: node("div", "component-unavailable", `Component type ${type} unavailable`), get: () => "" };
}

export function renderModal(root, modal, options = {}) {
  root.replaceChildren();
  if (!modal) return { controls: {}, focus: null };
  const backdrop = node("div", "modal-backdrop");
  const dialog = node("form", "modal-dialog");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "modal-title");
  const title = node("h2", "modal-title", modal.title || "Dialog");
  title.id = "modal-title";
  dialog.append(title);
  const fields = node("div", "modal-fields");
  const controls = {};
  const render = (component, path, labelText = "") => {
    const type = Number(component?.type);
    if (type === TYPE.TEXT_DISPLAY) { fields.append(node("div", "text-display", component.content || "")); return; }
    if (type === TYPE.LABEL) {
      const rendered = render(component.component, `${path}.component`, component.label || "");
      if (component.description) rendered.field.append(node("small", "field-description", component.description));
      fields.append(rendered.field);
      controls[String(component.component?.custom_id || path)] = rendered.get;
      return;
    }
    if (type === TYPE.ROW) {
      (component.components || []).forEach((child, index) => render(child, `${path}.components.${index}`, labelText));
      return;
    }
    const rendered = modalControl(component, path, labelText, options);
    fields.append(rendered.field);
    controls[String(component.custom_id || path)] = rendered.get;
  };
  (modal.components || []).forEach((component, index) => render(component, `modal.${index}`));
  dialog.append(fields);
  const actions = node("footer", "modal-actions");
  const cancel = node("button", "button-secondary", "Cancel");
  cancel.type = "button";
  cancel.addEventListener("click", () => options.onCancel?.());
  const submit = node("button", "button-primary", "Submit");
  submit.type = "submit";
  actions.append(cancel, submit);
  dialog.append(actions);
  dialog.addEventListener("submit", (event) => {
    event.preventDefault();
    const values = {};
    Object.entries(controls).forEach(([id, get]) => { values[id] = get(); });
    options.onSubmit?.(values);
  });
  backdrop.append(dialog);
  root.append(backdrop);
  return { controls, focus: dialog.querySelector("input, textarea, button") };
}

export function getSelectTypes() { return SELECT_TYPES; }
