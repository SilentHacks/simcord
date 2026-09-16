const TYPE = Object.freeze({
  ROW: 1, BUTTON: 2, STRING_SELECT: 3, TEXT_INPUT: 4, USER_SELECT: 5, ROLE_SELECT: 6,
  MENTIONABLE_SELECT: 7, CHANNEL_SELECT: 8, SECTION: 9, TEXT_DISPLAY: 10, THUMBNAIL: 11,
  MEDIA_GALLERY: 12, FILE: 13, SEPARATOR: 14, CONTAINER: 17, LABEL: 18, FILE_UPLOAD: 19,
  RADIO_GROUP: 21, CHECKBOX_GROUP: 22, CHECKBOX: 23,
});
const SELECT_TYPES = new Set([TYPE.STRING_SELECT, TYPE.USER_SELECT, TYPE.ROLE_SELECT, TYPE.MENTIONABLE_SELECT, TYPE.CHANNEL_SELECT]);
const MODAL_CONTROL_TYPES = new Set([TYPE.TEXT_INPUT, ...SELECT_TYPES, TYPE.RADIO_GROUP, TYPE.CHECKBOX_GROUP, TYPE.CHECKBOX, TYPE.FILE_UPLOAD]);

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}
const EMOJI_ART = {
  "✨": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36"><path fill="#FFAC33" d="M34.347 16.893l-8.899-3.294-3.323-10.891c-.128-.42-.517-.708-.956-.708-.439 0-.828.288-.956.708l-3.322 10.891-8.9 3.294c-.393.146-.653.519-.653.938 0 .418.26.793.653.938l8.895 3.293 3.324 11.223c.126.424.516.715.959.715.442 0 .833-.291.959-.716l3.324-11.223 8.896-3.293c.391-.144.652-.518.652-.937 0-.418-.261-.792-.653-.938z"/><path fill="#FFCC4D" d="M14.347 27.894l-2.314-.856-.9-3.3c-.118-.436-.513-.738-.964-.738-.451 0-.846.302-.965.737l-.9 3.3-2.313.856c-.393.145-.653.52-.653.938 0 .418.26.793.653.938l2.301.853.907 3.622c.112.444.511.756.97.756.459 0 .858-.312.97-.757l.907-3.622 2.301-.853c.393-.144.653-.519.653-.937 0-.418-.26-.793-.653-.937zM10.009 6.231l-2.364-.875-.876-2.365c-.145-.393-.519-.653-.938-.653-.418 0-.792.26-.938.653l-.875 2.365-2.365.875c-.393.146-.653.52-.653.938 0 .418.26.793.653.938l2.365.875.875 2.365c.146.393.52.653.938.653.418 0 .792-.26.938-.653l.875-2.365 2.365-.875c.393-.146.653-.52.653-.938 0-.418-.26-.792-.653-.938z"/></svg>',
  "🌙": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36"><path fill="#FFD983" d="M30.312.776C32 19 20 32 .776 30.312c8.199 7.717 21.091 7.588 29.107-.429C37.9 21.867 38.03 8.975 30.312.776z"/><path d="M30.705 15.915c-.453.454-.453 1.189 0 1.644.454.453 1.189.453 1.643 0 .454-.455.455-1.19 0-1.644-.453-.454-1.189-.454-1.643 0zm-16.022 14.38c-.682.681-.682 1.783 0 2.465.68.682 1.784.682 2.464 0 .681-.682.681-1.784 0-2.465-.68-.682-1.784-.682-2.464 0zm13.968-2.147c-1.135 1.135-2.974 1.135-4.108 0-1.135-1.135-1.135-2.975 0-4.107 1.135-1.136 2.974-1.136 4.108 0 1.135 1.133 1.135 2.973 0 4.107z" fill="#FFCC4D"/></svg>',
  "🌲": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36"><path fill="#662113" d="M22 33c0 2.209-1.791 3-4 3s-4-.791-4-3l1-9c0-2.209.791-2 3-2s3-.209 3 2l1 9z"/><path fill="#5C913B" d="M31.406 27.297C24.443 21.332 21.623 12.791 18 12.791c-3.623 0-6.443 8.541-13.405 14.506-2.926 2.507-1.532 3.957 2.479 3.667 3.576-.258 6.919-1.069 10.926-1.069s7.352.812 10.926 1.069c4.012.29 5.405-1.16 2.48-3.667z"/><path fill="#3E721D" d="M29.145 24.934C23.794 20.027 20.787 13 18 13c-2.785 0-5.793 7.027-11.144 11.934-4.252 3.898 5.572 4.773 11.144 0 5.569 4.773 15.396 3.898 11.145 0z"/><path fill="#5C913B" d="M29.145 20.959C23.794 16.375 20.787 9.811 18 9.811c-2.785 0-5.793 6.564-11.144 11.148-4.252 3.642 5.572 4.459 11.144 0 5.569 4.459 15.396 3.642 11.145 0z"/><path fill="#3E721D" d="M26.7 17.703C22.523 14.125 20.176 9 18 9c-2.174 0-4.523 5.125-8.7 8.703-3.319 2.844 4.35 3.482 8.7 0 4.349 3.482 12.02 2.844 8.7 0z"/><path fill="#5C913B" d="M26.7 14.726c-4.177-3.579-6.524-8.703-8.7-8.703-2.174 0-4.523 5.125-8.7 8.703-3.319 2.844 4.35 3.481 8.7 0 4.349 3.481 12.02 2.843 8.7 0z"/><path fill="#3E721D" d="M25.021 12.081C21.65 9.193 19.756 5.057 18 5.057c-1.755 0-3.65 4.136-7.021 7.024-2.679 2.295 3.511 2.809 7.021 0 3.51 2.81 9.701 2.295 7.021 0z"/><path fill="#5C913B" d="M25.021 9.839C21.65 6.951 19.756 2.815 18 2.815c-1.755 0-3.65 4.136-7.021 7.024-2.679 2.295 3.511 2.809 7.021 0 3.51 2.81 9.701 2.295 7.021 0z"/><path fill="#3E721D" d="M23.343 6.54C20.778 4.342 19.336 1.195 18 1.195c-1.335 0-2.778 3.148-5.343 5.345-2.038 1.747 2.671 2.138 5.343 0 2.671 2.138 7.382 1.746 5.343 0z"/><path fill="#5C913B" d="M23.343 5.345C20.778 3.148 19.336 0 18 0c-1.335 0-2.778 3.148-5.343 5.345-2.038 1.747 2.671 2.138 5.343 0 2.671 2.138 7.382 1.746 5.343 0z"/></svg>',
  "🌊": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36"><path fill="#269" d="M33.398 23.678c-7.562 4.875-20.062-.438-18.375-8.062 1.479-6.684 9.419-4.763 11.225-3.861 1.806.902.713-3.889-3.475-5.327C17.1 4.48 10.156 4.893 7.961 14.678c-1.5 6.687 1.438 16.062 12.719 16.187 11.281.125 12.718-7.187 12.718-7.187z"/><path fill="#55ACEE" d="M35.988 25.193c0-2.146-2.754-2.334-4-1.119-2.994 2.919-7.402 4.012-13.298 2.861-10.25-2-10.341-14.014-3.333-17.441 3.791-1.854 8.289.341 9.999 1.655 1.488 1.143 4.334 2.66 4.185.752C29.223 7.839 21.262-.86 10.595 4.64-.071 10.14 0 22.553 0 24.803v7.25C0 34.262 1.814 36 4.023 36h28C34.232 36 36 34.262 36 32.053c0 0-.004-6.854-.012-6.86z"/></svg>',
};
const EMOJI_PATTERN = new RegExp(`(${Object.keys(EMOJI_ART).join("|")})`, "g");
const DISCORD_LOGO_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="#fff" d="M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.21.3753-.4447.8648-.6083 1.2495-1.8447-.2762-3.68-.2762-5.4868 0-.1636-.3933-.4058-.8742-.6177-1.2495a.077.077 0 00-.0785-.037 19.7363 19.7363 0 00-4.8852 1.515.0699.0699 0 00-.0321.0277C.5334 9.0458-.319 13.5799.0992 18.0578a.0824.0824 0 00.0312.0561 19.9 19.9 0 005.9935 3.03.0777.0777 0 00.0842-.0276c.4616-.6304.8731-1.2952 1.226-1.9942a.076.076 0 00-.0416-.1057c-.6528-.2476-1.2743-.5495-1.8722-.8923a.077.077 0 01-.0076-.1277c.1258-.0943.2517-.1923.3718-.2914a.0743.0743 0 01.0776-.0105c3.9278 1.7933 8.18 1.7933 12.0614 0a.0739.0739 0 01.0785.0095c.1202.099.246.1981.3728.2924a.077.077 0 01-.0066.1276 12.2986 12.2986 0 01-1.873.8914.0766.0766 0 00-.0407.1067c.3604.698.7719 1.3628 1.225 1.9932a.076.076 0 00.0842.0286 19.8385 19.8385 0 006.0024-3.03.077.077 0 00.0313-.0552c.5004-5.177-.8382-9.6739-3.5485-13.6604a.061.061 0 00-.0312-.0286zM8.02 15.3312c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9555-2.4189 2.157-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.9555 2.4189-2.1569 2.4189zm7.9748 0c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9554-2.4189 2.1569-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.946 2.4189-2.1568 2.4189z"/></svg>';
function emojiImage(glyph, className = "emoji") {
  const img = node("span", className);
  img.innerHTML = EMOJI_ART[glyph] || "";
  img.setAttribute("role", "img");
  img.setAttribute("aria-label", glyph);
  return img;
}
function appendEmojiText(parent, text) {
  const value = String(text ?? "");
  let offset = 0;
  for (const match of value.matchAll(EMOJI_PATTERN)) {
    if (match.index > offset) parent.append(document.createTextNode(value.slice(offset, match.index)));
    parent.append(emojiImage(match[0]));
    offset = match.index + match[0].length;
  }
  if (offset < value.length) parent.append(document.createTextNode(value.slice(offset)));
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
    if (match.index > offset) appendEmojiText(parent, text.slice(offset, match.index));
    const id = match[1] || match[2];
    const name = names[id];
    if (name) parent.append(node("span", "mention", `@${name}`));
    else appendEmojiText(parent, match[0]);
    offset = match.index + match[0].length;
  }
  if (offset < text.length) appendEmojiText(parent, text.slice(offset));
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
    const marks = { strong: "strong", em: "em", s: "del", u: "u", spoiler: "span" };
    const open = type.endsWith("_open");
    const name = type.replace(/_(?:open|close)$/, "");
    const mark = marks[name];
    if (mark) {
      if (open) {
        const element = document.createElement(mark);
        if (name === "spoiler") {
          element.className = "markdown-spoiler";
          element.tabIndex = 0;
          element.setAttribute("role", "button");
          element.setAttribute("aria-label", "Reveal spoiler");
          const reveal = () => { element.classList.add("is-revealed"); element.removeAttribute("role"); element.removeAttribute("tabindex"); element.removeAttribute("aria-label"); };
          element.addEventListener("click", reveal);
          element.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); reveal(); } });
        }
        stack[stack.length - 1].append(element); stack.push(element);
      } else if (stack.length > 1) stack.pop();
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
  const PERSON_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="currentColor" d="M12 12a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9zm0 2c-4.15 0-8 2.02-8 4.5V21h16v-2.5c0-2.48-3.85-4.5-8-4.5z"/></svg>';
  const ROLE_MARK_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="currentColor" fill-rule="evenodd" d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM8.6 8.6a3.4 3.4 0 1 0 6.8 0 3.4 3.4 0 0 0-6.8 0zM12 13.2c-3.8 0-7 2-8.4 4.9a9.95 9.95 0 0 0 8.4 4.9 9.95 9.95 0 0 0 8.4-4.9c-1.4-2.9-4.6-4.9-8.4-4.9z"/></svg>';
  const HASH_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="currentColor" d="M10.4 3h2l-.8 5.4h4.4l.8-5.4h2l-.8 5.4h3.6v1.9h-3.9l-1 6.4h3.9v1.9h-4.2l-.8 5.4h-2l.8-5.4H4.4v-1.9h3.9l1-6.4H5.4V8.4h4.2l.8-5.4zM10.3 15.7h4.4l1-6.4h-4.4l-1 6.4z"/></svg>';
  const entityIcon = (entry, kind) => {
    const icon = node("span", "entity-icon");
    if (kind === "user") {
      const avatar = node("span", `entity-avatar${entry.bot ? " avatar-bot" : " avatar-user"}`);
      if (entry.bot) avatar.innerHTML = DISCORD_LOGO_SVG;
      else if (entry.avatar) {
        const img = node("img", "entity-avatar-img");
        img.alt = "";
        options.loadAsset?.(entry.avatar).then((url) => { img.src = url; }).catch(() => {});
        avatar.append(img);
      }
      icon.append(avatar, node("span", "entity-presence"));
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
  if (!drafts.has(key)) onInit?.(key, optionDefaults(component, entries).slice(0, maximum));
  const selected = Array.isArray(drafts.get(key)) ? [...drafts.get(key)] : [];
  const isOpen = dropdown?.key === key;
  const wrap = node("div", `preview-select select-type-${Number(component.type)}${isOpen ? " is-open" : ""}`); wrap.dataset.controlKey = key;
  const label = component.placeholder || (multi ? "Select one or more options" : "Select an option");
  const trigger = node("button", "select-trigger");
  const valueDisplay = node("span", "select-value");
  const single = selected.length === 1 ? entries.find((entry) => String(entry.value ?? entry.id ?? "") === selected[0]) : null;
  if (multi && selected.length > 0) {
    const chips = node("span", "select-chips");
    for (const value of selected) {
      const entry = entries.find((item) => String(item.value ?? item.id ?? "") === value);
      const chip = node("span", "select-chip");
      if (entry?.emoji?.name) appendEmojiText(chip, entry.emoji.name);
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
    if (single?.emoji?.name) { const emoji = node("span", "selected-emoji"); appendEmojiText(emoji, single.emoji.name); valueDisplay.append(emoji); }
    const valueLabel = node("span", "select-value-label");
    if (selected.length === 1 && single) appendEmojiText(valueLabel, single.label ?? single.name ?? selected[0]);
    else valueLabel.textContent = displaySelection(selected, entries, label);
    valueDisplay.append(valueLabel);
  }
  trigger.append(valueDisplay);
  if (!multi && single && single.kind && single.kind !== "string") {
    const clear = node("span", "select-clear");
    clear.setAttribute("role", "button");
    clear.setAttribute("aria-label", "Clear selection");
    clear.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M18.4 4 12 10.4 5.6 4 4 5.6 10.4 12 4 18.4 5.6 20 12 13.6 18.4 20 20 18.4 13.6 12 20 5.6 18.4 4Z"/></svg>';
    clear.addEventListener("click", (event) => { event.stopPropagation(); onDraft?.(key, [], multi, minimum, maximum, selected); onCommit?.(key, []); });
    trigger.append(clear);
  }
  trigger.type = "button"; trigger.disabled = component.disabled === true; trigger.dataset.controlKey = key;
  trigger.setAttribute("aria-haspopup", "listbox"); trigger.setAttribute("aria-expanded", String(isOpen));
  trigger.setAttribute("aria-label", label); trigger.setAttribute("aria-controls", `listbox-${path.replace(/[^a-zA-Z0-9_-]/g, "-")}`);
  trigger.addEventListener("click", () => onOpen?.(key, selected, multi, minimum, maximum));
  trigger.addEventListener("keydown", (event) => { if (["ArrowDown", "Enter", " "].includes(event.key)) { event.preventDefault(); onOpen?.(key, selected, multi, minimum, maximum); } });
  wrap.append(trigger);
  const list = node("div", "select-list"); list.id = `listbox-${path.replace(/[^a-zA-Z0-9_-]/g, "-")}`; list.hidden = !isOpen;
  list.setAttribute("role", "listbox"); list.setAttribute("aria-multiselectable", String(multi)); list.tabIndex = isOpen ? 0 : -1;
  if (isOpen) {
    list.addEventListener("keydown", (event) => {
      const optionNodes = [...list.querySelectorAll('[role="option"]')];
      let index = Math.max(0, optionNodes.findIndex((item) => item.dataset.value === String(dropdown.highlight)));
      if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); index = Math.max(0, Math.min(optionNodes.length - 1, index + (event.key === "ArrowDown" ? 1 : -1))); onOpen?.(key, selected, multi, minimum, maximum, optionNodes[index]?.dataset.value); }
      else if (event.key === "Home" || event.key === "End") { event.preventDefault(); index = event.key === "Home" ? 0 : optionNodes.length - 1; onOpen?.(key, selected, multi, minimum, maximum, optionNodes[index]?.dataset.value); }
      else if (event.key === "Enter" || event.key === " ") { event.preventDefault(); const node = optionNodes[index]; const value = node?.dataset.value; if (value !== undefined && !node?.classList.contains("is-disabled")) onDraft?.(key, value, multi, minimum, maximum, selected); if (!multi && value !== undefined) onCommit?.(key, [value]); }
      else if (event.key === "Escape") { event.preventDefault(); onCancel?.(key); }
    });
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
    const value = String(entry.value ?? entry.id ?? ""); const option = node("div", "select-option"); option.dataset.value = value; option.setAttribute("role", "option"); option.setAttribute("aria-selected", String(selected.includes(value))); option.tabIndex = -1;
    const disabled = atMax && !selected.includes(value);
    if (disabled) { option.classList.add("is-disabled"); option.setAttribute("aria-disabled", "true"); }
    if (selected.includes(value)) { option.classList.add("is-selected"); const tick = node("span", "option-check"); tick.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true"><path fill="#fff" d="M9.55 16.93 4.41 11.79a1.1 1.1 0 1 0-1.41 1.41l5.84 5.84a1.1 1.1 0 0 0 1.42 0L21 8.3a1.1 1.1 0 1 0-1.41-1.41L9.55 16.93Z"/></svg>'; option.append(tick); } if (isOpen && String(dropdown.highlight ?? "") === value) option.classList.add("is-highlighted");
    if (entry.kind && entry.kind !== "string") {
      decorateEntity(option, entry, entry.kind);
    } else {
      if (entry.emoji?.name) { const emoji = node("span", "option-emoji"); appendEmojiText(emoji, entry.emoji.name); option.append(emoji); }
      const optionLabel = node("span", "option-label"); appendEmojiText(optionLabel, entry.label ?? entry.name ?? value); option.append(optionLabel);
      if (entry.description) option.append(node("small", "option-description", entry.description));
    }
    option.addEventListener("click", () => { if (disabled) return; onDraft?.(key, value, multi, minimum, maximum, selected); if (!multi) onCommit?.(key, [value]); }); list.append(option);
  });
  if (!entries.length) list.append(node("div", "select-empty", "No available options"));
  wrap.append(list);
  return { element: wrap, value: () => (Array.isArray(drafts.get(key)) ? [...drafts.get(key)] : []), key };
}

function renderButton(component, path, options) {
  const style = Number(component.style || 1); const button = node("button", `component-button button-style-${style}`); button.type = "button"; button.disabled = component.disabled === true; button.dataset.controlKey = keyFor(component, path);
  if (component.emoji && typeof component.emoji === "object") { const emoji = node("span", "button-emoji"); appendEmojiText(emoji, component.emoji.name || ""); button.append(emoji); }
  if (component.label) { const buttonLabel = node("span", "button-label"); appendEmojiText(buttonLabel, component.label); button.append(buttonLabel); }
  if (style === 5) {
    const href = safeLink(component.url);
    if (href) {
      const link = node("a", `component-button button-style-${style} link-button`);
      const external = node("span", "external-link-icon");
      external.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M10 5V3H5.5A2.5 2.5 0 0 0 3 5.5v13A2.5 2.5 0 0 0 5.5 21h13a2.5 2.5 0 0 0 2.5-2.5V14h-2v4.5a.5.5 0 0 1-.5.5h-13a.5.5 0 0 1-.5-.5v-13a.5.5 0 0 1 .5-.5H10zm4-2v2h3.59l-6.3 6.29 1.42 1.42 6.29-6.3V10h2V3h-5z"/></svg>';
      link.append(...button.childNodes, external);
      link.href = href; link.target = "_blank"; link.rel = "noopener noreferrer";
      return link;
    }
    button.disabled = true; button.append(node("span", "button-unavailable", "Unavailable link")); options.onDiagnostic?.({ code: "invalid-link", severity: "warning", message: "Link button has no safe URL", complete: false });
  } else if (style === 6) {
    button.disabled = true; button.append(node("span", "button-unavailable", "Premium purchase unavailable")); options.onDiagnostic?.({ code: "premium-unavailable", severity: "info", message: `Premium SKU ${component.sku_id ?? "unknown"} cannot be purchased offline`, complete: false });
  } else if (typeof component.custom_id === "string") button.addEventListener("click", () => options.onClick?.(component.custom_id));
  return button;
}
function mediaElement(media, className, options, label) {
  const assetId = media && typeof media.asset_id === "string" ? media.asset_id : null;
  const manifest = assetId ? options.assets?.[assetId] : null;
  if (typeof media?.content_type === "string" && !media.content_type.startsWith("image/")) {
    options.onDiagnostic?.({ code: "unsupported-media-type", severity: "warning", message: `${label || "Media"} type ${media.content_type} cannot be rendered inline`, complete: false });
    return { element: node("div", "media-unavailable", `${label || "Media"} unavailable`), pending: [] };
  }
  if (!assetId || media.available === false || manifest?.available === false || !options.loadAsset) {
    options.onDiagnostic?.({ code: media?.diagnostic ? "media-rejected" : "media-unavailable", severity: "warning", message: media?.diagnostic || `${label || "Media"} is unavailable offline`, complete: false });
    return { element: node("div", "media-unavailable", `${label || "Media"} unavailable`), pending: [] };
  }
  const image = node("img", className); image.alt = String(media.description || label || "Preview media"); image.loading = "eager";
  if (Number(media.width) > 0) image.width = Number(media.width);
  if (Number(media.height) > 0) image.height = Number(media.height);
  const pending = [Promise.resolve(options.loadAsset(assetId)).then((url) => { if (!options.isCurrent?.()) return; image.src = url; return image.decode ? image.decode().catch(() => undefined) : undefined; }).catch((error) => { if (options.isCurrent?.()) { options.onDiagnostic?.({ code: "media-unavailable", severity: "warning", message: `${label || "Media"} is unavailable offline`, detail: String(error), complete: false }); image.replaceWith(node("div", "media-unavailable", `${label || "Media"} unavailable`)); } })];
  return { element: image, pending };
}
function revealSpoiler(element, spoiler, options, label) {
  if (!spoiler) return element;
  const wrapper = node("div", "spoiler-content");
  const reveal = node("button", "spoiler-cover");
  reveal.type = "button";
  reveal.setAttribute("aria-label", `Reveal ${label} spoiler`);
  reveal.addEventListener("click", () => { wrapper.replaceChildren(element); });
  wrapper.append(element, reveal);
  return wrapper;
}
function downloadButton(file, options, label) {
  const download = node("button", "attachment-download", "Download"); download.type = "button";
  if (!file?.asset_id || file.available === false || !options.loadAsset) {
    download.disabled = true;
    options.onDiagnostic?.({ code: "file-unavailable", severity: "warning", message: `${label} is unavailable offline`, complete: false });
    return download;
  }
  download.addEventListener("click", async () => {
    try {
      const url = await options.loadAsset(file.asset_id);
      if (!url) return;
      const link = node("a"); link.href = url; link.download = file.filename || label; link.click();
    } catch (error) {
      options.onDiagnostic?.({ code: "file-unavailable", severity: "warning", message: `${label} is unavailable offline`, detail: String(error), complete: false });
    }
  });
  return download;
}
function renderSpoilerMedia(media, className, options, label) {
  if (!media?.spoiler) return mediaElement(media, className, options, label);
  const wrapper = node("button", "spoiler-media");
  wrapper.type = "button";
  wrapper.setAttribute("aria-label", `Reveal ${label} spoiler`);
  const result = mediaElement(media, className, options, label);
  const cover = node("span", "spoiler-cover");
  wrapper.append(result.element, cover);
  wrapper.addEventListener("click", () => {
    wrapper.classList.add("is-revealed");
    cover.remove();
  });
  return { element: wrapper, pending: result.pending };
}
function renderNode(component, path, options) {
  const type = Number(component?.type);
  if (type === TYPE.ROW) { const row = node("div", "component-row"); (component.components || []).forEach((child, index) => row.append(renderNode(child, `${path}.components.${index}`, options))); return row; }
  if (type === TYPE.BUTTON) return renderButton(component, path, options);
  if (SELECT_TYPES.has(type)) return renderSelect(component, path, options).element;
  if (type === TYPE.TEXT_DISPLAY) { const text = node("div", "text-display"); appendMarkdownOrText(text, component.content, component.markdown_tokens, options); return text; }
  if (type === TYPE.SECTION) { const section = node("section", "component-section"); const text = node("div", "section-text"); (component.components || []).forEach((child, index) => text.append(renderNode(child, `${path}.components.${index}`, options))); section.append(text); if (component.accessory) { const accessory = node("div", "section-accessory"); accessory.append(renderNode(component.accessory, `${path}.accessory`, options)); section.append(accessory); } return section; }
  if (type === TYPE.CONTAINER) { const container = node("section", "component-container"); if (component.accent_color !== undefined) { const color = Number(component.accent_color); if (Number.isFinite(color)) container.style.setProperty("--accent", `#${color.toString(16).padStart(6, "0").slice(-6)}`); } (component.components || []).forEach((child, index) => container.append(renderNode(child, `${path}.components.${index}`, options))); return revealSpoiler(container, component.spoiler, options, "container"); }
  if (type === TYPE.SEPARATOR) { const separator = node(component.divider === false ? "div" : "hr", `component-separator spacing-${Number(component.spacing || 1)}${component.divider === false ? " no-divider" : ""}`); separator.setAttribute("aria-hidden", "true"); return separator; }
  if (type === TYPE.THUMBNAIL) { const result = renderSpoilerMedia({ ...component.media, spoiler: component.spoiler, description: component.description }, "component-thumbnail", options, "Thumbnail"); const figure = node("figure", "component-media"); figure.append(result.element); if (component.description) figure.append(node("figcaption", "media-description", component.description)); options.pendingMedia?.push(...result.pending); return figure; }
  if (type === TYPE.MEDIA_GALLERY) { const gallery = node("div", "component-gallery"); (component.items || []).forEach((item, index) => { const result = renderSpoilerMedia({ ...item.media, spoiler: item.spoiler, description: item.description }, "gallery-image", options, `Gallery item ${index + 1}`); const figure = node("figure", "gallery-item"); figure.append(result.element); if (item.description) figure.append(node("figcaption", "media-description", item.description)); gallery.append(figure); options.pendingMedia?.push(...result.pending); }); return gallery; }
  if (type === TYPE.FILE) {
    const data = component.file || {};
    const label = component.name || data.filename || "Attached file";
    const file = node("div", "component-file");
    const info = node("span", "file-info");
    info.append(node("span", "file-name", label));
    const size = data.size ?? component.size;
    if (size !== undefined) info.append(node("small", "file-size", `${size} bytes`));
    if (data.description) info.append(node("small", "file-description", data.description));
    const download = downloadButton(data, options, label);
    download.classList.add("file-download");
    const icon = node("span", "file-icon");
    icon.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30 40" aria-hidden="true"><path fill="#d3d6fd" d="M3 0h17l10 10v27a3 3 0 0 1-3 3H3a3 3 0 0 1-3-3V3a3 3 0 0 1 3-3z"/><path fill="#939bf9" d="M20 0l10 10h-7a3 3 0 0 1-3-3V0z"/><path fill="#5865f2" d="M7 17h5v2H7zm2 2h2v4H9zm8-2h5v2h-5zm0 5h5v2h-5zM7 27h15v2H7zm0 5h15v2H7z"/></svg>';
    file.append(icon, info, download);
    return revealSpoiler(file, component.spoiler, options, "file");
  }
  options.onDiagnostic?.({ code: "unsupported-component", severity: "warning", message: `Unsupported component type ${type} at ${path}`, complete: false }); return node("div", "component-unavailable", `Component type ${type} unavailable`);
}
function renderEmbed(embed, index, options) {
  const card = node("article", "embed-card"); const color = Number(embed.color ?? embed.color_value); if (Number.isFinite(color)) card.style.setProperty("--embed-color", `#${color.toString(16).padStart(6, "0").slice(-6)}`);
  if (embed.author?.name) card.append(node("div", "embed-author", embed.author.name));
  if (embed.title) { const href = safeLink(embed.url); const title = href ? node("a", "embed-title", "") : node("div", "embed-title"); if (href) { title.href = href; title.target = "_blank"; title.rel = "noopener noreferrer"; } appendMarkdownOrText(title, embed.title, embed.title_tokens, options); card.append(title); }
  if (embed.description) { const description = node("div", "embed-description"); appendMarkdownOrText(description, embed.description, embed.description_tokens, options); card.append(description); }
  if (Array.isArray(embed.fields) && embed.fields.length) { const fields = node("div", "embed-fields"); embed.fields.forEach((field) => { const item = node("div", field.inline ? "embed-field inline" : "embed-field"); const name = node("strong", "embed-field-name"); appendMarkdownOrText(name, field.name || "", field.name_tokens, options); const value = node("span", "embed-field-value"); appendMarkdownOrText(value, field.value || "", field.value_tokens, options); item.append(name, value); fields.append(item); }); card.append(fields); }
  for (const [kind, media] of [["thumbnail", embed.thumbnail], ["image", embed.image]]) { if (!media) continue; const result = renderSpoilerMedia(media, `embed-${kind}`, options, `Embed ${index + 1} ${kind}`); const figure = node("figure", `embed-media embed-${kind}`); figure.append(result.element); card.append(figure); options.pendingMedia?.push(...result.pending); }
  if (embed.video) { card.append(node("div", "component-unavailable", "Embed video unavailable")); options.onDiagnostic?.({ code: "unsupported-embed-video", severity: "warning", message: `Embed ${index + 1} video playback is unavailable`, complete: false }); }
  if (embed.footer?.text || embed.timestamp) {
    const timestamp = embed.timestamp ? new Intl.DateTimeFormat(options.locale || "en-US", { timeZone: options.timezone || "UTC", day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(embed.timestamp)) : "";
    const footer = node("footer", "embed-footer", [embed.footer?.text, timestamp].filter(Boolean).join(" • "));
    card.append(footer);
  }
  return card;
}
function referencedAssets(components, embeds) {
  const found = new Set(); const visit = (value) => { if (!value || typeof value !== "object") return; if (typeof value.asset_id === "string") found.add(value.asset_id); Object.values(value).forEach(visit); }; visit(components); visit(embeds); return found;
}
export function renderMessage(root, message, options = {}) {
  const pendingMedia = options.pendingMedia || []; options.pendingMedia = pendingMedia; root.replaceChildren(); if (!message) return { pendingMedia };
  const shortTime = (value) => new Intl.DateTimeFormat(options.locale || "en-US", { timeZone: options.timezone || "UTC", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(value));
  if (!message.compact) {
    const header = node("header", "message-header");
    header.tabIndex = -1;
    header.dataset.controlKey = `message:${message.id}`;
    const avatar = node("span", "message-avatar");
    avatar.innerHTML = DISCORD_LOGO_SVG;
    header.append(avatar);
    header.append(node("strong", "message-author", message.author?.name || "Unknown author"));
    if (message.author?.bot) header.append(node("span", "message-app-badge", "APP"));
    if (message.timestamp) {
      const time = node("time", "message-time", shortTime(message.timestamp));
      time.dateTime = message.timestamp;
      header.append(time);
    }
    if (message.edited_timestamp) header.append(node("span", "message-badge", "Edited"));
    if (message.ephemeral) header.append(node("span", "message-badge", "Ephemeral"));
    root.append(header);
  } else if (message.timestamp) {
    const gutter = node("time", "message-gutter-time", shortTime(message.timestamp));
    gutter.dateTime = message.timestamp;
    root.append(gutter);
  }
  const v2 = message.components_v2 === true || (Number(message.flags) & 32768) !== 0;
  if (!v2 && message.content) { const content = node("div", "message-content"); appendMarkdownOrText(content, message.content, message.content_tokens, options); root.append(content); }
  if (!v2 && (Number(message.flags) & 4) === 0) (message.embeds || []).forEach((embed, index) => root.append(renderEmbed(embed, index, options)));
  if (message.components?.length) { const components = node("div", v2 ? "message-components components-v2" : "message-components"); message.components.forEach((component, index) => components.append(renderNode(component, `message.${index}`, options))); root.append(components); }
  const refs = referencedAssets(message.components, v2 ? [] : message.embeds); const attachments = (message.attachments || []).filter((attachment) => !v2 && !refs.has(attachment.asset_id));
  if (attachments.length) {
    const list = node("ul", "message-attachments");
    attachments.forEach((attachment) => {
      const inline = attachment.inline && attachment.asset_id;
      const item = node("li", inline ? "attachment attachment-inline" : "attachment");
      if (inline) {
        const result = renderSpoilerMedia(attachment, "attachment-image", options, attachment.filename || "Attachment");
        item.append(result.element);
        pendingMedia.push(...result.pending);
      } else {
        if (attachment.preview) item.append(node("pre", "attachment-preview", attachment.preview));
        const footer = node("div", "attachment-footer");
        const info = node("span", "attachment-info");
        info.append(node("span", "attachment-name", attachment.filename || "attachment"));
        if (attachment.size !== undefined) info.append(node("small", "attachment-size", `${Math.max(1, Math.ceil(attachment.size / 1024))} KB`));
        footer.append(info);
        if (attachment.asset_id) {
          const openAsset = async (download = false) => {
            const url = await options.loadAsset?.(attachment.asset_id);
            if (!url) return;
            const link = node("a");
            link.href = url;
            if (download) link.download = attachment.filename || "attachment";
            else link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.click();
          };
          const ICONS = {
            expand: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" d="M9.6 5.6 4.5 12l5.1 6.4M14.4 5.6 19.5 12l-5.1 6.4"/></svg>',
            open: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" d="M7 17 17 7M8.5 6.5h9v9"/></svg>',
            more: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="currentColor" d="M5 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4zm7 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4zm7 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4z"/></svg>',
          };
          const actions = node("span", "attachment-actions");
          const expand = node("button", "attachment-action");
          expand.type = "button"; expand.setAttribute("aria-label", "Expand preview"); expand.innerHTML = ICONS.expand;
          expand.addEventListener("click", () => item.classList.toggle("is-expanded"));
          const open = node("button", "attachment-action");
          open.type = "button"; open.setAttribute("aria-label", "Open attachment"); open.innerHTML = ICONS.open;
          open.addEventListener("click", () => openAsset(false));
          const more = node("button", "attachment-action");
          more.type = "button"; more.setAttribute("aria-label", "Download attachment"); more.innerHTML = ICONS.more;
          more.addEventListener("click", () => openAsset(true));
          actions.append(expand, open, more);
          footer.append(actions);
        }
        item.append(footer);
      }
      list.append(item);
    });
    root.append(list);
  }
  return { pendingMedia };
}

function modalDefault(component, entries) {
  const type = Number(component.type); if (type === TYPE.TEXT_INPUT) return String(component.value ?? component.default ?? ""); if (SELECT_TYPES.has(type)) return optionDefaults(component, entries); if (type === TYPE.RADIO_GROUP) return (component.options || []).find((item) => item.default)?.value ?? null; if (type === TYPE.CHECKBOX_GROUP) return optionDefaults(component, component.options || []); if (type === TYPE.CHECKBOX) return component.default === true; return [];
}
function checkboxGlyph(isCheckbox) {
  if (!isCheckbox) return null;
  const glyph = node("span", "modal-choice-glyph");
  glyph.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" d="M5.2 10.6 8.6 14l6.2-8.2"/></svg>';
  return glyph;
}

function modalControl(component, path, labelText, options) {
  const customId = String(component.custom_id || path), key = `modal:${customId}`, field = node("div", "modal-field"); field.dataset.controlKey = key;
  const type = Number(component.type);
  const required = component.required === true || (component.required === undefined && (type === TYPE.TEXT_INPUT || SELECT_TYPES.has(type)));
  if (labelText && type !== TYPE.CHECKBOX) {
    const label = appendLabel(field, labelText, required);
    if (options.validationError?.includes(customId)) label.append(node("em", "field-error", " - This field is required."));
  }
  if (type === TYPE.TEXT_INPUT) { const input = node(component.style === 2 ? "textarea" : "input", "modal-input"); input.id = `modal-input-${path.replace(/[^a-zA-Z0-9_-]/g, "-")}`; input.name = customId; input.setAttribute("aria-label", labelText || customId); input.placeholder = String(component.placeholder || ""); input.required = required; if (component.min_length !== undefined) input.minLength = Number(component.min_length); if (component.max_length !== undefined) input.maxLength = Number(component.max_length); if (!options.drafts.has(key)) options.drafts.set(key, modalDefault(component, [])); input.value = String(options.drafts.get(key) ?? ""); input.addEventListener("input", () => options.onDraft?.(key, input.value)); field.append(input); return { field, get: () => String(options.drafts.get(key) ?? input.value) }; }
  if (SELECT_TYPES.has(type)) { const entries = optionEntries(component, options.candidates?.[customId]); if (!options.drafts.has(key)) options.drafts.set(key, modalDefault(component, entries)); const select = renderSelect(component, path, { ...options, scope: "modal", onOpen: options.onSelectOpen, onDraft: options.onSelectDraft, onCommit: options.onSelectCommit, onCancel: options.onSelectCancel }); field.append(select.element); return { field, get: select.value }; }
  if (type === TYPE.RADIO_GROUP || type === TYPE.CHECKBOX_GROUP) { const entries = component.options || []; if (!options.drafts.has(key)) options.drafts.set(key, modalDefault(component, entries)); const group = node("div", "modal-choice-group"); group.setAttribute("role", type === TYPE.RADIO_GROUP ? "radiogroup" : "group"); entries.forEach((entry) => { const value = String(entry.value ?? ""), choice = node("label", "modal-choice"), input = node("input"); input.type = type === TYPE.RADIO_GROUP ? "radio" : "checkbox"; input.name = customId; input.value = value; const current = options.drafts.get(key); input.checked = type === TYPE.RADIO_GROUP ? current === value : Array.isArray(current) && current.includes(value); input.addEventListener("change", () => { if (type === TYPE.RADIO_GROUP) options.onDraft?.(key, value); else { const next = new Set(Array.isArray(options.drafts.get(key)) ? options.drafts.get(key) : []); input.checked ? next.add(value) : next.delete(value); options.onDraft?.(key, [...next]); } }); choice.append(...[input, checkboxGlyph(type === TYPE.CHECKBOX_GROUP), node("span", "choice-label", entry.label || value)].filter(Boolean)); if (entry.description) choice.append(node("small", "choice-description", entry.description)); group.append(choice); }); field.append(group); return { field, get: () => options.drafts.get(key) }; }
  if (type === TYPE.CHECKBOX) { if (!options.drafts.has(key)) options.drafts.set(key, modalDefault(component, [])); const choice = node("label", "modal-choice"), input = node("input"); input.type = "checkbox"; input.name = customId; input.checked = options.drafts.get(key) === true; input.addEventListener("change", () => options.onDraft?.(key, input.checked)); choice.append(input, checkboxGlyph(true), node("span", "choice-label", labelText || customId)); field.append(choice); return { field, get: () => options.drafts.get(key) === true }; }
  if (type === TYPE.FILE_UPLOAD) {
    if (!options.drafts.has(key)) options.drafts.set(key, []);
    const input = node("input", "upload-input");
    input.type = "file";
    input.multiple = Number(component.max_values ?? 1) > 1;
    input.required = component.required === true;
    input.setAttribute("aria-label", labelText || customId);
    const list = node("ul", "upload-list");
    const redraw = () => {
      list.replaceChildren();
      (options.drafts.get(key) || []).forEach((file, index) => {
        const row = node("li", "upload-item");
        const ficon = node("span", "upload-file-icon");
        ficon.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30 40" aria-hidden="true"><path fill="#d3d6fd" d="M3 0h17l10 10v27a3 3 0 0 1-3 3H3a3 3 0 0 1-3-3V3a3 3 0 0 1 3-3z"/><path fill="#939bf9" d="M20 0l10 10h-7a3 3 0 0 1-3-3V0z"/><path fill="#5865f2" d="M7 17h5v2H7zm2 2h2v4H9zm8-2h5v2h-5zm0 5h5v2h-5zM7 27h15v2H7zm0 5h15v2H7z"/></svg>';
        row.append(ficon, node("span", "upload-file-name", file.name));
        const remove = node("button", "upload-remove", "×");
        remove.type = "button";
        remove.setAttribute("aria-label", `Remove ${file.name}`);
        remove.addEventListener("click", () => {
          const next = [...(options.drafts.get(key) || [])];
          next.splice(index, 1);
          options.onFiles?.(key, next);
          redraw();
        });
        row.append(remove);
        list.append(row);
      });
    };
    const dropzone = node("label", "upload-dropzone");
    const prompt = node("span", "upload-prompt", "Drop files here or ");
    prompt.append(node("span", "upload-browse", "browse"));
    dropzone.append(
      (() => { const icon = node("span", "upload-icon"); icon.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true"><path fill="#fff" d="M3 2h9l5 5v13a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V3a1 1 0 0 1 0-1z"/><path fill="#35353c" d="M12 2l5 5h-5z"/><path fill="#fff" stroke="#35353c" stroke-width="1.6" d="M17.5 10.8l4.2 4.2h-2.2v6h-4v-6h-2.2z"/></svg>'; return icon; })(),
      prompt,
      node("small", "upload-limit", `Upload up to ${component.max_values ?? 1} files under 500 MB.`),
      input,
      list,
    );
    input.addEventListener("change", () => {
      const next = [...(options.drafts.get(key) || []), ...[...(input.files || [])]];
      options.onFiles?.(key, next);
      input.value = "";
      redraw();
    });
    field.append(dropzone);
    redraw();
    return { field, get: () => options.drafts.get(key) || [] };
  }
  options.onDiagnostic?.({ code: "unsupported-modal-component", severity: "warning", message: `Unsupported modal component ${type}`, complete: false }); return { field: node("div", "component-unavailable", `Component type ${type} unavailable`), get: () => "" };
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
  const heading = node("header", "modal-header");
  const identity = node("span", "modal-identity", "●");
  identity.setAttribute("aria-hidden", "true");
  const title = node("h2", "modal-title", modal.title || "Dialog");
  title.id = "modal-title";
  const close = node("button", "modal-close");
  close.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M18.4 4 12 10.4 5.6 4 4 5.6 10.4 12 4 18.4 5.6 20 12 13.6 18.4 20 20 18.4 13.6 12 20 5.6z"/></svg>';
  close.type = "button";
  close.setAttribute("aria-label", "Close modal");
  close.addEventListener("click", () => options.onCancel?.());
  heading.append(identity, title, close);
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
        const description = node("small", "field-description", component.description);
        rendered.field.insertBefore(description, rendered.field.children[1] || null);
      }
      fields.append(rendered.field);
      controls[String(component.component?.custom_id || path)] = rendered.get;
      return;
    }
    if (type === TYPE.ROW) {
      (component.components || []).forEach((child, index) => {
        const childPath = `${path}.components.${index}`;
        if (!MODAL_CONTROL_TYPES.has(Number(child?.type))) return render(child, childPath, labelText);
        const rendered = modalControl(child, childPath, child.label || labelText || "", options);
        fields.append(rendered.field);
        controls[String(child.custom_id || childPath)] = rendered.get;
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
  const scrollbar = node("div", "modal-scrollbar");
  scrollbar.setAttribute("aria-hidden", "true");
  const scrollThumb = node("div", "modal-scrollbar-thumb");
  scrollbar.append(scrollThumb);
  dialog.append(scrollbar);
  const updateScrollbar = () => {
    const overflow = body.scrollHeight - body.clientHeight;
    scrollbar.style.display = overflow > 1 ? "" : "none";
    dialog.classList.toggle("is-overflowing", overflow > 1);
    if (overflow <= 1) return;
    scrollbar.style.top = `${body.offsetTop + 3}px`;
    scrollbar.style.height = `${body.clientHeight - 6}px`;
    const track = body.clientHeight - 6;
    const thumbH = Math.max(24, (body.clientHeight * body.clientHeight) / body.scrollHeight);
    scrollThumb.style.height = `${thumbH}px`;
    scrollThumb.style.transform = `translateY(${(body.scrollTop / overflow) * (track - thumbH)}px)`;
  };
  body.addEventListener("scroll", updateScrollbar);
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(updateScrollbar).observe(body);
    new ResizeObserver(updateScrollbar).observe(fields);
  }
  requestAnimationFrame(updateScrollbar);
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
