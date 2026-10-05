import { current, icon, iconButton, node, presenceDot, renderIdentityAvatar } from "./dom.js";
import { appendEmojiValue, appendMarkdownOrText } from "./text.js";
import { SELECT_TYPES, optionDefaults, optionEntries, renderSelect, selectedIds } from "./selects.js";
import {
  downloadButton,
  fileTypeLabel,
  formatFileSize,
  renderMedia,
  renderSpoiler,
  renderSpoilerMedia,
} from "./media.js";
const TYPE = Object.freeze({
  ROW: 1, BUTTON: 2, STRING_SELECT: 3, TEXT_INPUT: 4, USER_SELECT: 5, ROLE_SELECT: 6,
  MENTIONABLE_SELECT: 7, CHANNEL_SELECT: 8, SECTION: 9, TEXT_DISPLAY: 10, THUMBNAIL: 11,
  MEDIA_GALLERY: 12, FILE: 13, SEPARATOR: 14, CONTAINER: 17, LABEL: 18, FILE_UPLOAD: 19,
  RADIO_GROUP: 21, CHECKBOX_GROUP: 22, CHECKBOX: 23,
});
const MODAL_CONTROL_TYPES = new Set([TYPE.TEXT_INPUT, ...SELECT_TYPES, TYPE.RADIO_GROUP, TYPE.CHECKBOX_GROUP, TYPE.CHECKBOX, TYPE.FILE_UPLOAD]);
const FILE_ICON_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30 40" aria-hidden="true"><path fill="#d3d6fd" d="M3 0h17l10 10v27a3 3 0 0 1-3 3H3a3 3 0 0 1-3-3V3a3 3 0 0 1 3-3z"/><path fill="#939bf9" d="M20 0l10 10h-7a3 3 0 0 1-3-3V0z"/><path fill="#5865f2" d="M7 17h5v2H7zm2 2h2v4H9zm8-2h5v2h-5zm0 5h5v2h-5zM7 27h15v2H7zm0 5h15v2H7z"/></svg>';

function appendEmojiText(parent, text) {
  parent.append(document.createTextNode(String(text ?? "")));
}

function keyFor(component, path, scope = "message") {
  if (typeof component.control_key === "string") return component.control_key;
  if (typeof component.id === "number" && component.id > 0) return `${scope}:component:${component.id}`;
  return `${scope}:component:${path}`;
}
function safeId(path) {
  return encodeURIComponent(path);
}
function safeLink(value) {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value, window.location.origin);
    return ["http:", "https:", "mailto:"].includes(url.protocol) ? url.href : null;
  } catch (_) { return null; }
}
function appendLabel(parent, text, required = false) {
  const label = node("span", "control-label", text || "");
  if (required) label.append(node("span", "required-marker", " *"));
  parent.append(label);
  return label;
}



function appendPremiumIcon(button, presentation, options) {
  const assetId = presentation.icon_asset_id;
  if (!assetId || presentation.icon_available !== true || !options.loadAsset) {
    options.onDiagnostic?.({
      code: "premium-sku-icon-unavailable",
      severity: "warning",
      message: `Offline icon for premium SKU ${presentation.sku_id} is unavailable`,
      complete: false,
    });
    return;
  }
  const icon = node("img", "premium-button-icon");
  icon.alt = "";
  button.prepend(icon);
  const pending = Promise.resolve(options.loadAsset(assetId)).then(async (url) => {
    if (!current(options, icon)) return;
    if (typeof url !== "string" || !url.startsWith("blob:")) throw new Error("icon is not a local asset");
    icon.src = url;
    if (icon.decode) await icon.decode();
  }).catch(() => {
    if (!current(options, icon)) return;
    icon.remove();
    options.onDiagnostic?.({
      code: "premium-sku-icon-unavailable",
      severity: "warning",
      message: `Offline icon for premium SKU ${presentation.sku_id} failed to load`,
      complete: false,
    });
  });
  options.pendingMedia?.push(pending);
}

function renderButton(component, path, options) {
  const style = Number(component.style || 1);
  const button = node("button", `component-button button-style-${style}`);
  button.type = "button";
  button.disabled = component.disabled === true;
  const controlKey = keyFor(component, path, options.scope || "message");
  button.dataset.controlKey = controlKey;
  if (component.emoji && typeof component.emoji === "object") {
    const emoji = node("span", "button-emoji");
    appendEmojiValue(emoji, component.emoji, options);
    button.append(emoji);
  }
  if (component.label) {
    const buttonLabel = node("span", "button-label");
    appendEmojiText(buttonLabel, component.label);
    button.append(buttonLabel);
  }
  if (style === 5) {
    const href = safeLink(component.url);
    if (href) {
      const link = node("a", `component-button button-style-${style} link-button`);
      const external = node("span", "external-link-icon");
      external.append(icon("external"));
      link.append(...button.childNodes, external);
      if (component.disabled === true) {
        link.classList.add("is-disabled");
        link.setAttribute("aria-disabled", "true");
        link.tabIndex = -1;
      } else {
        link.href = href; link.target = "_blank"; link.rel = "noopener noreferrer";
      }
      return link;
    }
    button.disabled = true; button.append(node("span", "button-unavailable", "Unavailable link")); options.onDiagnostic?.({ code: "invalid-link", severity: "warning", message: "Link button has no safe URL", complete: false });
  } else if (style === 6) {
    button.classList.add("premium-button");
    const presentation = component.sku_presentation;
    if (presentation) {
      const copy = node("span", "premium-button-copy");
      const name = node("span", "premium-button-name", presentation.name);
      const price = node("span", "premium-button-price", presentation.price_text);
      name.lang = presentation.locale;
      price.lang = presentation.locale;
      copy.append(name, price);
      button.append(copy);
      if (presentation.icon_asset_id) appendPremiumIcon(button, presentation, options);
    } else {
      button.append(node("span", "button-unavailable", "Premium item details unavailable"));
    }
    button.addEventListener("click", () => {
      if (!button.disabled) options.onPremiumActivate?.(String(component.sku_id));
    });
  } else if (typeof component.custom_id === "string") {
    button.addEventListener("click", () => {
      if (!button.disabled) options.onClick?.(controlKey);
    });
  }
  if (button.querySelector(".button-emoji") && !button.querySelector(".button-label")) {
    button.classList.add("is-icon-only");
  }
  return button;
}
function renderNode(component, path, options) {
  const type = Number(component?.type);
  if (type === TYPE.ROW) { const row = node("div", "component-row"); (component.components || []).forEach((child, index) => row.append(renderNode(child, `${path}.components.${index}`, options))); return row; }
  if (type === TYPE.BUTTON) return renderButton(component, path, options);
  if (SELECT_TYPES.has(type)) return renderSelect(component, path, options).element;
  if (type === TYPE.TEXT_DISPLAY) { const text = node("div", "text-display"); appendMarkdownOrText(text, component.content, component.markdown_tokens, options); return text; }
  if (type === TYPE.SECTION) { const section = node("section", "component-section"); const text = node("div", "section-text"); (component.components || []).forEach((child, index) => text.append(renderNode(child, `${path}.components.${index}`, options))); section.append(text); if (component.accessory) { const accessory = node("div", "section-accessory"); accessory.append(renderNode(component.accessory, `${path}.accessory`, options)); section.append(accessory); } return section; }
  if (type === TYPE.CONTAINER) {
    const container = node("section", "component-container");
    if (component.accent_color !== null && component.accent_color !== undefined) {
      const color = Number(component.accent_color);
      if (Number.isInteger(color) && color >= 0 && color <= 0xffffff) {
        container.style.setProperty("--accent", `#${color.toString(16).padStart(6, "0")}`);
      }
    }
    (component.components || []).forEach((child, index) => {
      container.append(renderNode(child, `${path}.components.${index}`, options));
    });
    return renderSpoiler(container, component.spoiler, options, "container", `container:${path}`);
  }
  if (type === TYPE.THUMBNAIL) {
    const result = renderSpoilerMedia(
      { ...component.media, spoiler: component.spoiler, description: component.description },
      "component-thumbnail",
      options,
      "Thumbnail",
      `thumbnail:${path}`,
    );
    const figure = node("figure", "component-media");
    figure.append(result.element);
    options.pendingMedia?.push(...result.pending);
    return figure;
  }
  if (type === TYPE.MEDIA_GALLERY) {
    const gallery = node("div", "component-gallery");
    (component.items || []).forEach((item, index) => {
      const result = renderSpoilerMedia(
        { ...item.media, spoiler: item.spoiler, description: item.description },
        "gallery-image",
        options,
        `Gallery item ${index + 1}`,
        `gallery:${path}:${index}`,
      );
      const figure = node("figure", "gallery-item");
      figure.append(result.element);
      gallery.append(figure);
      options.pendingMedia?.push(...result.pending);
    });
    return gallery;
  }
  if (type === TYPE.SEPARATOR) { const separator = node(component.divider === false ? "div" : "hr", `component-separator spacing-${Number(component.spacing || 1)}${component.divider === false ? " no-divider" : ""}`); separator.setAttribute("aria-hidden", "true"); return separator; }
  if (type === TYPE.FILE) {
    const data = component.file || {};
    const label = component.name || data.filename || "Attached file";
    const file = node("div", "component-file");
    const info = node("span", "file-info");
    info.append(node("span", "file-name", label));
    const size = data.size ?? component.size;
    const sizeText = formatFileSize(size);
    if (sizeText) info.append(node("small", "file-size", sizeText));
    if (data.description) info.append(node("small", "file-description", data.description));
    const download = downloadButton(data, options, label);
    download.classList.add("file-download");
    const icon = node("span", "file-icon file-type", fileTypeLabel(data));
    icon.setAttribute("aria-hidden", "true");
    file.append(icon, info, download);
    return renderSpoiler(file, component.spoiler, options, "file", `file:${path}:${data.asset_id || data.filename || ""}`);
  }
  options.onDiagnostic?.({ code: "unsupported-component", severity: "warning", message: `Unsupported component type ${type} at ${path}`, complete: false }); return node("div", "component-unavailable", `Component type ${type} unavailable`);
}
function renderEmbed(embed, index, options) {
  const card = node("article", "embed-card");
  card.dataset.embedType = typeof embed.type === "string" ? embed.type : "rich";
  const color = Number(embed.color ?? embed.color_value);
  if (Number.isFinite(color)) {
    card.style.setProperty("--embed-color", `#${color.toString(16).padStart(6, "0").slice(-6)}`);
  }

  const main = node("div", "embed-main");
  const text = node("div", "embed-main-text");
  const providerLink = safeLink(embed.provider?.url);
  if (embed.provider?.name || providerLink) {
    const provider = node(
      providerLink ? "a" : "span",
      "embed-provider",
      typeof embed.provider?.name === "string" ? embed.provider.name : "Open provider",
    );
    if (providerLink) {
      provider.href = providerLink;
      provider.target = "_blank";
      provider.rel = "noopener noreferrer";
    }
    text.append(provider);
  }
  if (embed.author?.name) {
    const author = node("div", "embed-author");
    if (embed.author.icon_asset_id) {
      const icon = renderMedia(
        { asset_id: embed.author.icon_asset_id, available: embed.author.icon_available !== false },
        "embed-author-icon",
        options,
        "Embed author icon",
      );
      author.append(icon.element);
      options.pendingMedia?.push(...icon.pending);
    }
    const authorLink = safeLink(embed.author.url);
    const name = authorLink ? node("a", "embed-author-link", embed.author.name) : node("span", "", embed.author.name);
    if (authorLink) {
      name.href = authorLink;
      name.target = "_blank";
      name.rel = "noopener noreferrer";
    }
    author.append(name);
    text.append(author);
  }
  if (embed.title) {
    const href = safeLink(embed.url);
    const title = href ? node("a", "embed-title") : node("div", "embed-title");
    if (href) {
      title.href = href;
      title.target = "_blank";
      title.rel = "noopener noreferrer";
    }
    appendMarkdownOrText(title, embed.title, embed.title_tokens, options);
    text.append(title);
  }
  if (embed.description) {
    const description = node("div", "embed-description");
    appendMarkdownOrText(description, embed.description, embed.description_tokens, options);
    text.append(description);
  }
  main.append(text);
  if (embed.thumbnail) {
    const result = renderSpoilerMedia(
      embed.thumbnail,
      "embed-thumbnail",
      options,
      `Embed ${index + 1} thumbnail`,
      `embed:${index}:thumbnail:${embed.thumbnail.asset_id || ""}`,
    );
    const figure = node("figure", "embed-media embed-thumbnail");
    figure.append(result.element);
    main.classList.add("embed-main-with-thumbnail");
    main.append(figure);
    options.pendingMedia?.push(...result.pending);
  }
  card.append(main);

  if (Array.isArray(embed.fields) && embed.fields.length) {
    const fields = node("div", "embed-fields");
    let inlineRun = [];
    const appendField = (field) => {
      const item = node("div", "embed-field");
      const name = node("strong", "embed-field-name");
      appendMarkdownOrText(name, field.name || "", field.name_tokens, options);
      const value = node("span", "embed-field-value");
      appendMarkdownOrText(value, field.value || "", field.value_tokens, options);
      item.append(name, value);
      return item;
    };
    const flushInline = () => {
      for (let start = 0; start < inlineRun.length; start += 3) {
        const rowFields = inlineRun.slice(start, start + 3);
        const row = node("div", "embed-field-row");
        row.style.setProperty("--embed-field-columns", String(rowFields.length));
        rowFields.forEach((field) => row.append(appendField(field)));
        fields.append(row);
      }
      inlineRun = [];
    };
    embed.fields.forEach((field) => {
      if (field.inline) inlineRun.push(field);
      else {
        flushInline();
        fields.append(appendField(field));
      }
    });
    flushInline();
    text.append(fields);
  }

  for (const [kind, media] of [["image", embed.image], ["video", embed.video]]) {
    if (!media) continue;
    const label = `Embed ${index + 1} ${kind}`;
    const result = renderSpoilerMedia(
      media,
      `embed-${kind}`,
      options,
      label,
      `embed:${index}:${kind}:${media.asset_id || ""}`,
    );
    const figure = node("figure", `embed-media embed-${kind}`);
    figure.append(result.element);
    card.append(figure);
    options.pendingMedia?.push(...result.pending);
  }

  const rawTimestamp = typeof embed.timestamp === "string" ? embed.timestamp : "";
  const date = rawTimestamp ? new Date(rawTimestamp) : null;
  const timestamp = date && Number.isFinite(date.getTime())
    ? new Intl.DateTimeFormat(options.locale || "en-US", {
      timeZone: options.timezone || "UTC",
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).format(date)
    : "";
  if (embed.footer?.text || timestamp || embed.footer?.icon_asset_id) {
    const footer = node("footer", "embed-footer");
    if (embed.footer?.icon_asset_id) {
      const icon = renderMedia(
        { asset_id: embed.footer.icon_asset_id, available: embed.footer.icon_available !== false },
        "embed-footer-icon",
        options,
        "Embed footer icon",
      );
      footer.append(icon.element);
      options.pendingMedia?.push(...icon.pending);
    }
    if (embed.footer?.text) {
      footer.append(document.createTextNode(String(embed.footer.text)));
    }
    if (timestamp) {
      if (embed.footer?.text) footer.append(document.createTextNode(" • "));
      const time = node("time", "embed-timestamp", timestamp);
      time.dateTime = rawTimestamp;
      footer.append(time);
    }
    card.append(footer);
  }
  return card;
}

function addDescribedBy(control, id) {
  if (!control) return;
  const ids = new Set((control.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean));
  ids.add(id);
  control.setAttribute("aria-describedby", [...ids].join(" "));
}

function attachModalError(field, control, error) {
  if (!error) return;
  const message = node("span", "field-error", error.message);
  message.id = `modal-error-${safeId(error.controlId)}`;
  message.setAttribute("role", "alert");
  message.setAttribute("aria-live", "assertive");
  field.append(message);
  [control, control?.querySelector?.("input")].filter(Boolean).forEach((target) => {
    target.setAttribute("aria-invalid", "true");
    addDescribedBy(target, message.id);
  });
}

function addModalDescription(field, control, text, path) {
  const description = node("small", "field-description", text);
  description.id = `modal-description-${safeId(path)}`;
  field.insertBefore(description, field.children[1] || null);
  [control, control?.querySelector?.("input")].filter(Boolean).forEach((target) => addDescribedBy(target, description.id));
}

function modalError(options, customId) {
  return options.validationError?.controlId === customId ? options.validationError : null;
}

function modalDefault(component, entries) {
  const type = Number(component.type);
  if (type === TYPE.TEXT_INPUT) return String(component.value ?? component.default ?? "");
  if (SELECT_TYPES.has(type)) return optionDefaults(entries);
  if (type === TYPE.RADIO_GROUP) return (component.options || []).find((item) => item.default)?.value ?? null;
  if (type === TYPE.CHECKBOX_GROUP) return optionDefaults(component.options || []);
  if (type === TYPE.CHECKBOX) return component.default === true;
  return [];
}
function checkboxGlyph(isCheckbox) {
  if (!isCheckbox) return null;
  const glyph = node("span", "modal-choice-glyph");
  glyph.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" d="M5.2 10.6 8.6 14l6.2-8.2"/></svg>';
  return glyph;
}

function modalControl(component, path, labelText, options) {
  const customId = String(component.custom_id || path);
  const key = typeof component.control_key === "string"
    ? component.control_key
    : `modal:${options.modalHandle ?? ""}:component:${path}`;
  const field = node("div", "modal-field");
  field.dataset.controlKey = key;
  field.dataset.customId = customId;
  const type = Number(component.type);
  const required = component.required === true
    || (component.required === undefined && (type === TYPE.TEXT_INPUT || SELECT_TYPES.has(type)));
  const include = (hasDefault) => () => required || options.isTouched?.(key) === true || hasDefault;
  let labelId;
  if (labelText && type !== TYPE.CHECKBOX) {
    const label = appendLabel(field, labelText, required);
    label.id = `control-label-${safeId(key)}`;
    labelId = label.id;
  }
  const finish = (control, focus, get, hasDefault = false) => {
    const error = modalError(options, customId);
    attachModalError(field, control, error);
    return { field, control, focus, get, include: include(hasDefault) };
  };
  if (type === TYPE.TEXT_INPUT) {
    const input = node(component.style === 2 ? "textarea" : "input", "modal-input");
    input.id = `modal-input-${safeId(path)}`;
    input.name = customId;
    if (labelId) input.setAttribute("aria-labelledby", labelId);
    else input.setAttribute("aria-label", customId);
    input.placeholder = String(component.placeholder || "");
    input.required = required;
    if (component.max_length !== undefined) input.maxLength = Number(component.max_length) * 2;
    if (!options.drafts.has(key)) options.drafts.set(key, modalDefault(component, []));
    input.value = String(options.drafts.get(key) ?? "");
    input.addEventListener("input", () => options.onDraft?.(key, input.value));
    field.append(input);
    const hasDefault = component.value != null || component.default != null;
    return finish(input, input, () => String(options.drafts.get(key) ?? ""), hasDefault);
  }
  if (SELECT_TYPES.has(type)) {
    const descriptor = options.candidates?.[component.control_key || key];
    const entries = optionEntries(component, descriptor);
    const defaults = Array.isArray(descriptor?.selected) ? selectedIds(descriptor) : modalDefault(component, entries);
    if (!options.drafts.has(key)) options.drafts.set(key, defaults);
    options.onInit?.(key, options.drafts.get(key));
    const select = renderSelect(component, path, {
      ...options,
      labelledBy: labelId,
      scope: `modal:${options.modalHandle ?? ""}`,
      onOpen: options.onSelectOpen,
      onDraft: options.onSelectDraft,
      onCommit: options.onSelectCommit,
      onCancel: options.onSelectCancel,
    });
    field.append(select.element);
    return finish(
      select.element.querySelector(".select-trigger"),
      select.element.querySelector(".select-trigger"),
      select.value,
      defaults.length > 0,
    );
  }
  if (type === TYPE.RADIO_GROUP || type === TYPE.CHECKBOX_GROUP) {
    const entries = component.options || [];
    const defaults = modalDefault(component, entries);
    if (!options.drafts.has(key)) options.drafts.set(key, defaults);
    const group = node("div", "modal-choice-group");
    group.setAttribute("role", type === TYPE.RADIO_GROUP ? "radiogroup" : "group");
    if (labelId) group.setAttribute("aria-labelledby", labelId);
    if (required) group.setAttribute("aria-required", "true");
    entries.forEach((entry) => {
      const value = String(entry.value ?? "");
      const choice = node("label", "modal-choice");
      const input = node("input");
      input.type = type === TYPE.RADIO_GROUP ? "radio" : "checkbox";
      input.name = customId;
      input.value = value;
      const current = options.drafts.get(key);
      input.checked = type === TYPE.RADIO_GROUP
        ? current === value
        : Array.isArray(current) && current.includes(value);
      input.addEventListener("change", () => {
        if (type === TYPE.RADIO_GROUP) options.onDraft?.(key, value);
        else {
          const next = new Set(Array.isArray(options.drafts.get(key)) ? options.drafts.get(key) : []);
          input.checked ? next.add(value) : next.delete(value);
          options.onDraft?.(key, [...next]);
        }
      });
      choice.append(...[input, checkboxGlyph(type === TYPE.CHECKBOX_GROUP), node("span", "choice-label", entry.label || value)].filter(Boolean));
      if (entry.description) choice.append(node("small", "choice-description", entry.description));
      group.append(choice);
    });
    field.append(group);
    const hasDefault = type === TYPE.RADIO_GROUP ? defaults != null : defaults.length > 0;
    return finish(group, group.querySelector("input"), () => options.drafts.get(key), hasDefault);
  }
  if (type === TYPE.CHECKBOX) {
    if (!options.drafts.has(key)) options.drafts.set(key, modalDefault(component, []));
    const choice = node("label", "modal-choice");
    const input = node("input");
    input.type = "checkbox";
    input.name = customId;
    input.checked = options.drafts.get(key) === true;
    input.addEventListener("change", () => options.onDraft?.(key, input.checked));
    choice.append(input, checkboxGlyph(true), node("span", "choice-label", labelText || customId));
    field.append(choice);
    return finish(
      input,
      input,
      () => options.drafts.get(key) === true,
      typeof component.default === "boolean",
    );
  }
  if (type === TYPE.FILE_UPLOAD) {
    if (!options.drafts.has(key)) options.drafts.set(key, []);
    const input = node("input", "upload-input");
    input.type = "file";
    input.multiple = Number(component.max_values ?? 1) > 1;
    input.required = required;
    input.setAttribute("aria-label", labelText || customId);
    const list = node("ul", "upload-list");
    const redraw = () => {
      list.replaceChildren();
      (options.drafts.get(key) || []).forEach((file, index) => {
        const row = node("li", "upload-item");
        const icon = node("span", "upload-file-icon");
        icon.innerHTML = FILE_ICON_SVG;
        row.append(icon, node("span", "upload-file-name", file.name));
        const remove = node("button", "upload-remove", "×");
        remove.type = "button";
        remove.setAttribute("aria-label", `Remove ${file.name}`);
        remove.addEventListener("click", () => {
          const next = [...(options.drafts.get(key) || [])];
          next.splice(index, 1);
          options.onFiles?.(key, next);
        });
        row.append(remove);
        list.append(row);
      });
    };
    const addFiles = (files) => {
      if (files.length) options.onFiles?.(key, [...(options.drafts.get(key) || []), ...files]);
    };
    const dropzone = node("label", "upload-dropzone");
    const prompt = node("span", "upload-prompt", "Drop files here or ");
    prompt.append(node("span", "upload-browse", "browse"));
    const icon = node("span", "upload-icon");
    icon.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true"><path fill="#fff" d="M3 2h9l5 5v13a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V3a1 1 0 0 1 0-1z"/><path fill="#35353c" d="M12 2l5 5h-5z"/><path fill="#fff" stroke="#35353c" stroke-width="1.6" d="M17.5 10.8l4.2 4.2h-2.2v6h-4v-6h-2.2z"/></svg>';
    dropzone.append(
      icon,
      prompt,
      node("small", "upload-limit", `Upload up to ${component.max_values ?? 1} files, 10 MiB per file.`),
      input,
    );
    input.addEventListener("change", () => {
      const files = [...(input.files || [])];
      input.value = "";
      addFiles(files);
    });
    dropzone.addEventListener("dragover", (event) => event.preventDefault());
    dropzone.addEventListener("drop", (event) => {
      event.preventDefault();
      addFiles([...(event.dataTransfer?.files || [])]);
    });
    field.append(dropzone, list);
    redraw();
    return finish(input, input, () => options.drafts.get(key) || []);
  }
  options.onDiagnostic?.({ code: "unsupported-modal-component", severity: "warning", message: `Unsupported modal component ${type}`, complete: false });
  return { field: node("div", "component-unavailable", `Component type ${type} unavailable`), get: () => "", include: include(false) };
}

export function renderModal(root, modal, options = {}) {
  root.replaceChildren();
  if (!modal) return { controls: {}, focus: null };
  const backdrop = node("div", "modal-backdrop");
  const dialog = node("form", "modal-dialog");
  dialog.noValidate = true;
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "modal-title");
  dialog.setAttribute("tabindex", "-1");
  const heading = node("header", "modal-header");
  const identity = renderIdentityAvatar(modal.application_identity, options, "modal-identity");
  identity.element.setAttribute("aria-hidden", "true");
  options.pendingMedia?.push(...identity.pending);
  const title = node("h2", "modal-title", modal.title || "Dialog");
  title.id = "modal-title";
  const close = iconButton("close", "Close modal", "modal-close");
  close.addEventListener("click", () => options.onCancel?.());
  heading.append(identity.element, title, close);
  dialog.append(heading);
  const body = node("div", "modal-body");
  const disclaimer = node(
    "p",
    "modal-disclaimer",
    `This form will be submitted to ${modal.application_name || "this application"}. Do not share passwords or other sensitive information.`,
  );
  const warnIcon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  warnIcon.setAttribute("class", "modal-disclaimer-icon");
  warnIcon.setAttribute("viewBox", "0 0 20 18");
  warnIcon.innerHTML =
    '<path d="M10 1.5 19 17H1Z" fill="#fcb529"/>' +
    '<path fill="#48423c" d="M9.1 6.6h1.8v4.4H9.1zM9.1 12.6h1.8v1.8H9.1z"/>';
  disclaimer.prepend(warnIcon);
  body.append(disclaimer);
  const fields = node("div", "modal-fields"), controls = {};
  const render = (component, path, labelText = "") => {
    const type = Number(component?.type);
    if (type === TYPE.TEXT_DISPLAY) {
      const text = node("div", "text-display");
      appendMarkdownOrText(text, component.content, component.markdown_tokens, options);
      fields.append(text);
      return;
    }
    if (type === TYPE.LABEL) {
      const rendered = modalControl(component.component, `${path}.component`, component.label || "", options);
      if (component.description) {
        addModalDescription(rendered.field, rendered.control, component.description, path);
      }
      fields.append(rendered.field);
      controls[String(component.component?.custom_id || path)] = rendered;
      return;
    }
    if (type === TYPE.ROW) {
      (component.components || []).forEach((child, index) => {
        const childPath = `${path}.components.${index}`;
        if (!MODAL_CONTROL_TYPES.has(Number(child?.type))) return render(child, childPath, labelText);
        const rendered = modalControl(child, childPath, child.label || labelText || "", options);
        fields.append(rendered.field);
        controls[String(child.custom_id || childPath)] = rendered;
      });
      return;
    }
    options.onDiagnostic?.({
      code: "unsupported-modal-component",
      severity: "warning",
      message: `Unsupported modal component ${type}`,
      complete: false,
    });
  };
  (modal.components || []).forEach((component, index) => render(component, `modal.${index}`));
  body.append(fields);
  dialog.append(body);
  const actions = node("footer", "modal-actions");
  const cancel = node("button", "button-secondary", "Cancel");
  const submit = node("button", "button-primary", "Submit");
  cancel.type = "button";
  cancel.addEventListener("click", () => options.onCancel?.());
  submit.type = "submit";
  actions.append(cancel, submit);
  dialog.append(actions);
  dialog.addEventListener("submit", (event) => {
    event.preventDefault();
    const values = {};
    Object.entries(controls).forEach(([id, control]) => {
      if (control.include && !control.include()) return;
      values[id] = control.get();
    });
    options.onSubmit?.(values);
  });
  backdrop.append(dialog);
  root.append(backdrop);
  body.scrollTop = Number(options.scrollTop || 0);
  const invalid = options.validationError
    ? Object.values(controls).find((control) => control.field.dataset.customId === options.validationError.controlId)
    : null;
  return {
    controls,
    focus: dialog.querySelector("input, textarea, button") || dialog,
    errorTarget: invalid?.focus || null,
  };
}
export { renderEmbed, renderNode, renderSpoilerMedia };
