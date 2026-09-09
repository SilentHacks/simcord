const TYPE = Object.freeze({
  ROW: 1, BUTTON: 2, STRING_SELECT: 3, TEXT_INPUT: 4, USER_SELECT: 5, ROLE_SELECT: 6,
  MENTIONABLE_SELECT: 7, CHANNEL_SELECT: 8, SECTION: 9, TEXT_DISPLAY: 10, THUMBNAIL: 11,
  MEDIA_GALLERY: 12, FILE: 13, SEPARATOR: 14, CONTAINER: 17, LABEL: 18, FILE_UPLOAD: 19,
  RADIO_GROUP: 21, CHECKBOX_GROUP: 22, CHECKBOX: 23,
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
    return ["http:", "https:", "mailto:"].includes(url.protocol) ? url.href : null;
  } catch (_) { return null; }
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
  return component.type === TYPE.STRING_SELECT ? (Array.isArray(component.options) ? component.options : []) : (Array.isArray(candidates) ? candidates : []);
}
function displaySelection(values, entries, placeholder) {
  const labels = values.map((value) => {
    const found = entries.find((item) => String(item.value ?? item.id) === String(value));
    return found ? String(found.label ?? found.name ?? found.value ?? found.id) : String(value);
  });
  return labels.length ? labels.join(", ") : (placeholder || "Select an option");
}

function appendTextWithMentions(parent, value, options) {
  const text = String(value ?? "");
  const names = options.mentions || {};
  const pattern = /<@!?([0-9]+)>|<@&([0-9]+)>/g;
  let offset = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > offset) parent.append(document.createTextNode(text.slice(offset, match.index)));
    const id = match[1] || match[2];
    const name = names[id];
    parent.append(document.createTextNode(name ? `@${name}` : match[0]));
    offset = match.index + match[0].length;
  }
  if (offset < text.length) parent.append(document.createTextNode(text.slice(offset)));
}

function renderInlineTokens(parent, tokens, options) {
  const stack = [parent];
  (tokens || []).forEach((token) => {
    if (!token || typeof token !== "object") return;
    const type = token.type;
    if (type === "text") { appendTextWithMentions(stack[stack.length - 1], token.content, options); return; }
    if (type === "code") { stack[stack.length - 1].append(node("code", "inline-code", token.content)); return; }
    if (type === "break") { stack[stack.length - 1].append(document.createElement("br")); return; }
    if (type === "timestamp") {
      const date = new Date(Number(token.unix) * 1000);
      const time = node("time", "discord-timestamp", Number.isFinite(date.getTime()) ? date.toLocaleString(options.locale || "en-US", { timeZone: options.timezone || "UTC" }) : `<t:${token.unix}>`);
      if (Number.isFinite(date.getTime())) time.dateTime = date.toISOString();
      stack[stack.length - 1].append(time);
      return;
    }
    if (type === "link_open") {
      const href = safeLink(token.href);
      if (!href) return;
      const link = node("a", "markdown-link"); link.href = href; link.target = "_blank"; link.rel = "noopener noreferrer";
      stack[stack.length - 1].append(link); stack.push(link); return;
    }
    if (type === "link_close") { if (stack.length > 1) stack.pop(); return; }
    const marks = { strong: "strong", em: "em", s: "del", u: "u" };
    const open = type.endsWith("_open");
    const mark = marks[type.replace(/_(?:open|close)$/, "")];
    if (mark) {
      if (open) { const element = document.createElement(mark); stack[stack.length - 1].append(element); stack.push(element); }
      else if (stack.length > 1) stack.pop();
    }
  });
}
function renderMarkdown(parent, tokens, options = {}) {
  const renderBlock = (block, target) => {
    if (!block || typeof block !== "object") return;
    const type = block.type;
    if (type === "inline") { renderInlineTokens(target, block.children, options); return; }
    if (type === "code_block") { target.append(node("pre", "code-block", block.content || "")); return; }
    const tags = { paragraph: "p", blockquote: "blockquote", bullet_list: "ul", ordered_list: "ol", list_item: "li", heading: /^h[1-6]$/.test(block.tag || "") ? block.tag : "h3" };
    const element = node(tags[type] || "div", `markdown-${type || "block"}`);
    (block.children || []).forEach((child) => renderBlock(child, element));
    target.append(element);
  };
  (tokens || []).forEach((token) => renderBlock(token, parent));
}
function appendMarkdownOrText(parent, value, tokens, options) {
  if (Array.isArray(tokens) && tokens.length) renderMarkdown(parent, tokens, options);
  else appendTextWithMentions(parent, value, options);
}

function renderSelect(component, path, options) {
  const { drafts, candidates, dropdown, scope = "message", onInit, onOpen, onDraft, onCommit, onCancel } = options;
  const key = `${scope}:${keyFor(component, path)}`;
  const entries = optionEntries(component, candidates?.[component.custom_id]);
  const multi = Number(component.max_values ?? 1) > 1 || Number(component.min_values ?? 1) > 1;
  const minimum = Number(component.min_values ?? 1), maximum = Number(component.max_values ?? 1);
  if (!drafts.has(key)) onInit?.(key, optionDefaults(component, entries).slice(0, maximum));
  const selected = Array.isArray(drafts.get(key)) ? [...drafts.get(key)] : [];
  const isOpen = dropdown?.key === key;
  const wrap = node("div", `preview-select${isOpen ? " is-open" : ""}`); wrap.dataset.controlKey = key;
  const label = component.placeholder || (multi ? "Select one or more options" : "Select an option");
  const trigger = node("button", "select-trigger", displaySelection(selected, entries, label));
  trigger.type = "button"; trigger.disabled = component.disabled === true; trigger.dataset.controlKey = key;
  trigger.setAttribute("aria-haspopup", "listbox"); trigger.setAttribute("aria-expanded", String(isOpen));
  trigger.setAttribute("aria-label", label); trigger.setAttribute("aria-controls", `listbox-${path.replace(/[^a-zA-Z0-9_-]/g, "-")}`);
  trigger.addEventListener("click", () => onOpen?.(key, selected, multi, minimum, maximum));
  trigger.addEventListener("keydown", (event) => { if (["ArrowDown", "Enter", " "].includes(event.key)) { event.preventDefault(); onOpen?.(key, selected, multi, minimum, maximum); } });
  wrap.append(trigger);
  const list = node("div", "select-list"); list.id = `listbox-${path.replace(/[^a-zA-Z0-9_-]/g, "-")}`; list.hidden = !isOpen;
  list.setAttribute("role", "listbox"); list.setAttribute("aria-multiselectable", String(multi)); list.tabIndex = isOpen ? 0 : -1;
  if (isOpen) {
    const search = node("input", "select-search"); search.type = "search"; search.placeholder = "Search options"; search.setAttribute("aria-label", "Search options");
    list.append(search);
    list.addEventListener("keydown", (event) => {
      const optionNodes = [...list.querySelectorAll('[role="option"]')];
      let index = Math.max(0, optionNodes.findIndex((item) => item.dataset.value === String(dropdown.highlight)));
      if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); index = Math.max(0, Math.min(optionNodes.length - 1, index + (event.key === "ArrowDown" ? 1 : -1))); onOpen?.(key, selected, multi, minimum, maximum, optionNodes[index]?.dataset.value); }
      else if (event.key === "Home" || event.key === "End") { event.preventDefault(); index = event.key === "Home" ? 0 : optionNodes.length - 1; onOpen?.(key, selected, multi, minimum, maximum, optionNodes[index]?.dataset.value); }
      else if (event.key === "Enter" || event.key === " ") { event.preventDefault(); const value = optionNodes[index]?.dataset.value; if (value !== undefined) onDraft?.(key, value, multi, minimum, maximum, selected); if (!multi && value !== undefined) onCommit?.(key, [value]); }
      else if (event.key === "Escape") { event.preventDefault(); onCancel?.(key); }
    });
    search.addEventListener("input", () => { const query = search.value.toLocaleLowerCase(); [...list.querySelectorAll('[role="option"]')].forEach((item) => { item.hidden = !item.textContent.toLocaleLowerCase().includes(query); }); });
  }
  entries.forEach((entry) => {
    const value = String(entry.value ?? entry.id ?? ""); const option = node("div", "select-option"); option.dataset.value = value; option.setAttribute("role", "option"); option.setAttribute("aria-selected", String(selected.includes(value))); option.tabIndex = -1;
    if (selected.includes(value)) option.classList.add("is-selected"); if (isOpen && String(dropdown.highlight ?? "") === value) option.classList.add("is-highlighted");
    if (entry.emoji?.name) option.append(node("span", "option-emoji", entry.emoji.name)); option.append(node("span", "option-label", entry.label ?? entry.name ?? value));
    if (entry.description) option.append(node("small", "option-description", entry.description));
    option.addEventListener("click", () => { onDraft?.(key, value, multi, minimum, maximum, selected); if (!multi) onCommit?.(key, [value]); }); list.append(option);
  });
  if (!entries.length) list.append(node("div", "select-empty", "No available options"));
  if (isOpen && multi) list.append(node("div", "select-hint", `Enter to apply · Escape to cancel · ${selected.length}/${maximum} selected`));
  wrap.append(list);
  return { element: wrap, value: () => (Array.isArray(drafts.get(key)) ? [...drafts.get(key)] : []), key };
}

function renderButton(component, path, options) {
  const style = Number(component.style || 1); const button = node("button", `component-button button-style-${style}`); button.type = "button"; button.disabled = component.disabled === true; button.dataset.controlKey = keyFor(component, path);
  if (component.emoji && typeof component.emoji === "object") button.append(node("span", "button-emoji", component.emoji.name || ""));
  if (component.label) button.append(node("span", "button-label", component.label));
  if (style === 5) {
    const href = safeLink(component.url);
    if (href) { const link = node("a", `component-button button-style-${style} link-button`, button.textContent || "Open link"); link.href = href; link.target = "_blank"; link.rel = "noopener noreferrer"; return link; }
    button.disabled = true; button.append(node("span", "button-unavailable", "Unavailable link")); options.onDiagnostic?.({ code: "invalid-link", severity: "warning", message: "Link button has no safe URL", complete: false });
  } else if (style === 6) {
    button.disabled = true; button.append(node("span", "button-unavailable", "Premium purchase unavailable")); options.onDiagnostic?.({ code: "premium-unavailable", severity: "info", message: `Premium SKU ${component.sku_id ?? "unknown"} cannot be purchased offline`, complete: false });
  } else if (typeof component.custom_id === "string") button.addEventListener("click", () => options.onClick?.(component.custom_id));
  return button;
}
function mediaElement(media, className, options, label) {
  const assetId = media && typeof media.asset_id === "string" ? media.asset_id : null;
  const manifest = assetId ? options.assets?.[assetId] : null;
  if (!assetId || media.available === false || manifest?.available === false || !options.loadAsset) {
    options.onDiagnostic?.({ code: media?.diagnostic ? "media-rejected" : "media-unavailable", severity: "warning", message: media?.diagnostic || `${label || "Media"} is unavailable offline`, complete: false });
    return { element: node("div", "media-unavailable", `${label || "Media"} unavailable`), pending: [] };
  }
  const image = node("img", className); image.alt = String(media.description || label || "Preview media"); image.loading = "eager";
  const pending = [Promise.resolve(options.loadAsset(assetId)).then((url) => { if (!options.isCurrent?.()) return; image.src = url; return image.decode ? image.decode().catch(() => undefined) : undefined; }).catch((error) => { if (options.isCurrent?.()) { options.onDiagnostic?.({ code: "media-unavailable", severity: "warning", message: `${label || "Media"} is unavailable offline`, detail: String(error), complete: false }); image.replaceWith(node("div", "media-unavailable", `${label || "Media"} unavailable`)); } })];
  return { element: image, pending };
}
function renderSpoilerMedia(media, className, options, label) {
  if (!media?.spoiler) return mediaElement(media, className, options, label);
  const wrapper = node("button", "spoiler-media", "Reveal spoiler media"); wrapper.type = "button"; let revealed = false;
  const render = () => { wrapper.replaceChildren(); if (!revealed) wrapper.append(node("span", "spoiler-cover", "Spoiler — activate to reveal")); else { const result = mediaElement(media, className, options, label); wrapper.append(result.element); options.pendingMedia?.push(...result.pending); } };
  wrapper.addEventListener("click", () => { revealed = true; render(); options.onLocalRender?.(); }); render(); return { element: wrapper, pending: [] };
}
function renderNode(component, path, options) {
  const type = Number(component?.type);
  if (type === TYPE.ROW) { const row = node("div", "component-row"); (component.components || []).forEach((child, index) => row.append(renderNode(child, `${path}.components.${index}`, options))); return row; }
  if (type === TYPE.BUTTON) return renderButton(component, path, options);
  if (SELECT_TYPES.has(type)) return renderSelect(component, path, options).element;
  if (type === TYPE.SECTION) { const section = node("section", "component-section"); const text = node("div", "section-text"); (component.components || []).forEach((child, index) => text.append(renderNode(child, `${path}.components.${index}`, options))); section.append(text); if (component.accessory) { const accessory = node("div", "section-accessory"); accessory.append(renderNode(component.accessory, `${path}.accessory`, options)); section.append(accessory); } return section; }
  if (type === TYPE.CONTAINER) { const container = node("section", "component-container"); if (component.accent_color !== undefined) { const color = Number(component.accent_color); if (Number.isFinite(color)) container.style.setProperty("--accent", `#${color.toString(16).padStart(6, "0").slice(-6)}`); } (component.components || []).forEach((child, index) => container.append(renderNode(child, `${path}.components.${index}`, options))); return container; }
  if (type === TYPE.SEPARATOR) { const separator = node("hr", `component-separator spacing-${Number(component.spacing || 1)}`); separator.setAttribute("aria-hidden", "true"); return separator; }
  if (type === TYPE.THUMBNAIL) { const result = renderSpoilerMedia(component.media, "component-thumbnail", options, "Thumbnail"); const figure = node("figure", "component-media"); figure.append(result.element); if (component.description) figure.append(node("figcaption", "media-description", component.description)); options.pendingMedia?.push(...result.pending); return figure; }
  if (type === TYPE.MEDIA_GALLERY) { const gallery = node("div", "component-gallery"); (component.items || []).forEach((item, index) => { const result = renderSpoilerMedia(item.media, "gallery-image", options, `Gallery item ${index + 1}`); const figure = node("figure", "gallery-item"); figure.append(result.element); if (item.description) figure.append(node("figcaption", "media-description", item.description)); gallery.append(figure); options.pendingMedia?.push(...result.pending); }); return gallery; }
  if (type === TYPE.FILE) { const file = node("div", "component-file", component.name || component.file?.filename || "Attached file"); if (component.size !== undefined || component.file?.size !== undefined) file.append(node("small", "file-size", `${component.size ?? component.file.size} bytes`)); if (component.file?.asset_id) { const result = mediaElement(component.file, "component-file-preview", options, component.name || "Attached file"); file.append(result.element); options.pendingMedia?.push(...result.pending); } return file; }
  options.onDiagnostic?.({ code: "unsupported-component", severity: "warning", message: `Unsupported component type ${type} at ${path}`, complete: false }); return node("div", "component-unavailable", `Component type ${type} unavailable`);
}
function renderEmbed(embed, index, options) {
  const card = node("article", "embed-card"); const color = Number(embed.color ?? embed.color_value); if (Number.isFinite(color)) card.style.setProperty("--embed-color", `#${color.toString(16).padStart(6, "0").slice(-6)}`);
  if (embed.author?.name) card.append(node("div", "embed-author", embed.author.name));
  if (embed.title) { const href = safeLink(embed.url); const title = href ? node("a", "embed-title", "") : node("div", "embed-title"); if (href) { title.href = href; title.target = "_blank"; title.rel = "noopener noreferrer"; } appendMarkdownOrText(title, embed.title, embed.title_tokens, options); card.append(title); }
  if (embed.description) { const description = node("div", "embed-description"); appendMarkdownOrText(description, embed.description, embed.description_tokens, options); card.append(description); }
  if (Array.isArray(embed.fields) && embed.fields.length) { const fields = node("div", "embed-fields"); embed.fields.forEach((field) => { const item = node("div", field.inline ? "embed-field inline" : "embed-field"); const name = node("strong", "embed-field-name"); appendMarkdownOrText(name, field.name || "", field.name_tokens, options); const value = node("span", "embed-field-value"); appendMarkdownOrText(value, field.value || "", field.value_tokens, options); item.append(name, value); fields.append(item); }); card.append(fields); }
  const media = embed.thumbnail || embed.image; if (media) { const result = renderSpoilerMedia(media, "embed-image", options, `Embed ${index + 1} image`); card.append(result.element); options.pendingMedia?.push(...result.pending); }
  if (embed.footer?.text || embed.timestamp) { const footer = node("footer", "embed-footer"); appendMarkdownOrText(footer, embed.footer?.text || embed.timestamp, embed.footer_tokens, options); card.append(footer); }
  return card;
}
function referencedAssets(components, embeds) {
  const found = new Set(); const visit = (value) => { if (!value || typeof value !== "object") return; if (typeof value.asset_id === "string") found.add(value.asset_id); Object.values(value).forEach(visit); }; visit(components); visit(embeds); return found;
}
export function renderMessage(root, message, options = {}) {
  const pendingMedia = options.pendingMedia || []; options.pendingMedia = pendingMedia; root.replaceChildren(); if (!message) return { pendingMedia };
  const header = node("header", "message-header"); header.tabIndex = -1; header.dataset.controlKey = `message:${message.id}`; header.append(node("strong", "message-author", message.author?.name || "Unknown author")); if (message.timestamp) { const time = node("time", "message-time", message.timestamp); time.dateTime = message.timestamp; header.append(time); } if (message.edited_timestamp) header.append(node("span", "message-badge", "Edited")); if (message.ephemeral) header.append(node("span", "message-badge", "Ephemeral")); if (message.components_v2) header.append(node("span", "message-badge", "Components V2")); if ((Number(message.flags) & 4) !== 0) header.append(node("span", "message-badge", "Embeds suppressed")); root.append(header);
  const v2 = message.components_v2 === true || (Number(message.flags) & 32768) !== 0;
  if (!v2 && message.content) { const content = node("div", "message-content"); appendMarkdownOrText(content, message.content, message.content_tokens, options); root.append(content); }
  if (!v2 && (Number(message.flags) & 4) === 0) (message.embeds || []).forEach((embed, index) => root.append(renderEmbed(embed, index, options)));
  if (message.components?.length) { const components = node("div", "message-components"); message.components.forEach((component, index) => components.append(renderNode(component, `message.${index}`, options))); root.append(components); }
  const refs = referencedAssets(message.components, v2 ? [] : message.embeds); const attachments = (message.attachments || []).filter((attachment) => !v2 ? !refs.has(attachment.asset_id) : refs.has(attachment.asset_id));
  if (attachments.length) { const list = node("ul", "message-attachments"); attachments.forEach((attachment) => { const item = node("li", "attachment"); item.append(node("span", "attachment-name", attachment.filename || "attachment")); if (attachment.size !== undefined) item.append(node("small", "attachment-size", `${attachment.size} bytes`)); if (attachment.inline && attachment.asset_id) { const result = renderSpoilerMedia(attachment, "attachment-image", options, attachment.filename || "Attachment"); item.append(result.element); pendingMedia.push(...result.pending); } else if (attachment.asset_id) { const download = node("a", "attachment-download", "Download"); download.href = `/api/assets/${encodeURIComponent(attachment.asset_id)}`; download.target = "_blank"; download.rel = "noopener noreferrer"; item.append(download); } list.append(item); }); root.append(list); }
  return { pendingMedia };
}

function modalDefault(component, entries) {
  const type = Number(component.type); if (type === TYPE.TEXT_INPUT) return String(component.value ?? component.default ?? ""); if (SELECT_TYPES.has(type)) return optionDefaults(component, entries); if (type === TYPE.RADIO_GROUP) return (component.options || []).find((item) => item.default)?.value ?? null; if (type === TYPE.CHECKBOX_GROUP) return optionDefaults(component, component.options || []); if (type === TYPE.CHECKBOX) return component.default === true; return [];
}
function modalControl(component, path, labelText, options) {
  const customId = String(component.custom_id || path), key = `modal:${customId}`, field = node("div", "modal-field"); field.dataset.controlKey = key;
  const type = Number(component.type);
  const required = component.required === true || (component.required === undefined && (type === TYPE.TEXT_INPUT || SELECT_TYPES.has(type))); if (labelText) appendLabel(field, labelText, required);
  if (type === TYPE.TEXT_INPUT) { const input = node(component.style === 2 ? "textarea" : "input", "modal-input"); input.id = `modal-input-${path.replace(/[^a-zA-Z0-9_-]/g, "-")}`; input.name = customId; input.setAttribute("aria-label", labelText || customId); input.placeholder = String(component.placeholder || ""); input.required = required; if (component.min_length !== undefined) input.minLength = Number(component.min_length); if (component.max_length !== undefined) input.maxLength = Number(component.max_length); if (!options.drafts.has(key)) options.drafts.set(key, modalDefault(component, [])); input.value = String(options.drafts.get(key) ?? ""); input.addEventListener("input", () => options.onDraft?.(key, input.value)); field.append(input); return { field, get: () => String(options.drafts.get(key) ?? input.value) }; }
  if (SELECT_TYPES.has(type)) { const entries = optionEntries(component, options.candidates?.[customId]); if (!options.drafts.has(key)) options.drafts.set(key, modalDefault(component, entries)); const select = renderSelect(component, path, { ...options, scope: "modal" }); field.append(select.element); return { field, get: select.value }; }
  if (type === TYPE.RADIO_GROUP || type === TYPE.CHECKBOX_GROUP) { const entries = component.options || []; if (!options.drafts.has(key)) options.drafts.set(key, modalDefault(component, entries)); const group = node("div", "modal-choice-group"); group.setAttribute("role", type === TYPE.RADIO_GROUP ? "radiogroup" : "group"); entries.forEach((entry) => { const value = String(entry.value ?? ""), choice = node("label", "modal-choice"), input = node("input"); input.type = type === TYPE.RADIO_GROUP ? "radio" : "checkbox"; input.name = customId; input.value = value; const current = options.drafts.get(key); input.checked = type === TYPE.RADIO_GROUP ? current === value : Array.isArray(current) && current.includes(value); input.addEventListener("change", () => { if (type === TYPE.RADIO_GROUP) options.onDraft?.(key, value); else { const next = new Set(Array.isArray(options.drafts.get(key)) ? options.drafts.get(key) : []); input.checked ? next.add(value) : next.delete(value); options.onDraft?.(key, [...next]); } }); choice.append(input, node("span", "choice-label", entry.label || value)); if (entry.description) choice.append(node("small", "choice-description", entry.description)); group.append(choice); }); field.append(group); return { field, get: () => options.drafts.get(key) }; }
  if (type === TYPE.CHECKBOX) { if (!options.drafts.has(key)) options.drafts.set(key, modalDefault(component, [])); const choice = node("label", "modal-choice"), input = node("input"); input.type = "checkbox"; input.name = customId; input.checked = options.drafts.get(key) === true; input.addEventListener("change", () => options.onDraft?.(key, input.checked)); choice.append(input, node("span", "choice-label", labelText || customId)); field.append(choice); return { field, get: () => options.drafts.get(key) === true }; }
  if (type === TYPE.FILE_UPLOAD) { if (!options.drafts.has(key)) options.drafts.set(key, []); const input = node("input", "modal-input"); input.type = "file"; input.multiple = Number(component.max_values ?? 1) > 1; input.required = component.required === true; input.setAttribute("aria-label", labelText || customId); const list = node("ul", "upload-list"); const redraw = () => { list.replaceChildren(); (options.drafts.get(key) || []).forEach((file, index) => { const row = node("li", "upload-item", `${file.name} (${file.size} bytes)`); const remove = node("button", "upload-remove", "Remove"); remove.type = "button"; remove.addEventListener("click", () => { const next = [...(options.drafts.get(key) || [])]; next.splice(index, 1); options.onFiles?.(key, next); redraw(); }); row.append(remove); list.append(row); }); }; input.addEventListener("change", () => { const next = [...(options.drafts.get(key) || []), ...[...input.files || []]]; options.onFiles?.(key, next); input.value = ""; redraw(); }); field.append(input, list); redraw(); return { field, get: () => options.drafts.get(key) || [] }; }
  options.onDiagnostic?.({ code: "unsupported-modal-component", severity: "warning", message: `Unsupported modal component ${type}`, complete: false }); return { field: node("div", "component-unavailable", `Component type ${type} unavailable`), get: () => "" };
}
export function renderModal(root, modal, options = {}) {
  root.replaceChildren(); if (!modal) return { controls: {}, focus: null };
  const backdrop = node("div", "modal-backdrop"), dialog = node("form", "modal-dialog"); dialog.setAttribute("role", "dialog"); dialog.setAttribute("aria-modal", "true"); dialog.setAttribute("aria-labelledby", "modal-title");
  const title = node("h2", "modal-title", modal.title || "Dialog"); title.id = "modal-title"; dialog.append(title); const fields = node("div", "modal-fields"), controls = {};
  const render = (component, path, labelText = "") => { const type = Number(component?.type); if (type === TYPE.TEXT_DISPLAY) { const text = node("div", "text-display"); appendMarkdownOrText(text, component.content, component.markdown_tokens, options); fields.append(text); return; } if (type === TYPE.LABEL) { const rendered = render(component.component, `${path}.component`, component.label || ""); if (component.description) rendered.field.append(node("small", "field-description", component.description)); fields.append(rendered.field); controls[String(component.component?.custom_id || path)] = rendered.get; return; } if (type === TYPE.ROW) { (component.components || []).forEach((child, index) => render(child, `${path}.components.${index}`, labelText)); return; } const rendered = modalControl(component, path, labelText, options); fields.append(rendered.field); controls[String(component.custom_id || path)] = rendered.get; };
  (modal.components || []).forEach((component, index) => render(component, `modal.${index}`)); dialog.append(fields); const actions = node("footer", "modal-actions"), cancel = node("button", "button-secondary", "Cancel"), submit = node("button", "button-primary", "Submit"); cancel.type = "button"; cancel.addEventListener("click", () => options.onCancel?.()); submit.type = "submit"; actions.append(cancel, submit); dialog.append(actions);
  dialog.addEventListener("submit", (event) => { event.preventDefault(); const values = {}; Object.entries(controls).forEach(([id, get]) => { values[id] = get(); }); options.onSubmit?.(values); }); backdrop.append(dialog); root.append(backdrop); return { controls, focus: dialog.querySelector("input, textarea, button") };
}
export function getSelectTypes() { return SELECT_TYPES; }
