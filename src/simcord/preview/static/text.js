import { node } from "./dom.js";
import hljs from "./vendor/highlight/es/core.min.js";
import bash from "./vendor/highlight/es/languages/bash.min.js";
import cpp from "./vendor/highlight/es/languages/cpp.min.js";
import csharp from "./vendor/highlight/es/languages/csharp.min.js";
import css from "./vendor/highlight/es/languages/css.min.js";
import diff from "./vendor/highlight/es/languages/diff.min.js";
import dockerfile from "./vendor/highlight/es/languages/dockerfile.min.js";
import go from "./vendor/highlight/es/languages/go.min.js";
import ini from "./vendor/highlight/es/languages/ini.min.js";
import java from "./vendor/highlight/es/languages/java.min.js";
import javascript from "./vendor/highlight/es/languages/javascript.min.js";
import json from "./vendor/highlight/es/languages/json.min.js";
import markdown from "./vendor/highlight/es/languages/markdown.min.js";
import python from "./vendor/highlight/es/languages/python.min.js";
import rust from "./vendor/highlight/es/languages/rust.min.js";
import sql from "./vendor/highlight/es/languages/sql.min.js";
import typescript from "./vendor/highlight/es/languages/typescript.min.js";
import xml from "./vendor/highlight/es/languages/xml.min.js";
import yaml from "./vendor/highlight/es/languages/yaml.min.js";

const LANGUAGES = {
  bash, cpp, csharp, css, diff, dockerfile, go, ini, java, javascript,
  json, markdown, python, rust, sql, typescript, xml, yaml,
};
for (const [name, grammar] of Object.entries(LANGUAGES)) hljs.registerLanguage(name, grammar);

const ALIASES = {
  c: "cpp", "c++": "cpp", cs: "csharp", csharp: "csharp", "c#": "csharp",
  htm: "xml", html: "xml", js: "javascript", node: "javascript", py: "python",
  md: "markdown", sh: "bash", shell: "bash", ts: "typescript", yml: "yaml",
};

function appendText(parent, text) {
  parent.append(document.createTextNode(String(text ?? "")));
}

export function appendEmojiValue(parent, emoji, options = {}, label = "Custom emoji") {
  if (!emoji || typeof emoji !== "object") {
    appendText(parent, emoji);
    return;
  }
  if (emoji.custom !== true && !emoji.id) {
    parent.append(node("span", "unicode-emoji", emoji.name || ""));
    return;
  }
  const assetId = typeof emoji.asset_id === "string" ? emoji.asset_id : null;
  const manifest = assetId ? options.assets?.[assetId] : null;
  if (!assetId || emoji.available === false || manifest?.available === false || !options.loadAsset) {
    parent.append(node("span", "emoji-unavailable", `${label} unavailable`));
    options.onDiagnostic?.({
      code: "custom-emoji-unavailable",
      severity: "warning",
      message: `${label} is unavailable from supplied offline assets`,
      complete: false,
    });
    return;
  }
  const image = node("img", "custom-emoji");
  image.alt = String(emoji.name || label || "Custom emoji");
  const capture = emoji.animated === true && options.mediaTime !== null && options.mediaTime !== undefined;
  const pending = Promise.resolve(options.loadAsset(
    assetId,
    capture ? { capture: true, mediaTime: options.mediaTime } : {},
  ))
    .then((url) => {
      if (!image.isConnected && options.isCurrent && !options.isCurrent()) return;
      image.src = url;
      return image.decode ? image.decode() : undefined;
    })
    .catch((error) => {
      if (image.isConnected || !options.isCurrent || options.isCurrent()) {
        image.replaceWith(node("span", "emoji-unavailable", `${label} unavailable`));
        options.onDiagnostic?.({
          code: "custom-emoji-unavailable",
          severity: "warning",
          message: `${label} failed to decode`,
          detail: String(error),
          complete: false,
        });
      }
    });
  options.pendingMedia?.push(pending);
  parent.append(image);
}

function safeLink(value) {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value, window.location.origin);
    return ["http:", "https:", "mailto:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function renderTimestamp(parent, token, options) {
  const unix = Number(token.unix);
  const date = new Date(unix * 1000);
  const valid = Number.isFinite(unix) && Number.isFinite(date.getTime());
  const style = token.style || "f";
  let rendered = `<t:${token.unix}${style ? `:${style}` : ""}>`;
  let title;
  try {
    if (valid) {
      const locale = options.locale || "en-US";
      const timezone = options.timezone || "UTC";
      const formats = {
        t: { hour: "numeric", minute: "2-digit" },
        T: { hour: "numeric", minute: "2-digit", second: "2-digit" },
        d: { year: "numeric", month: "2-digit", day: "2-digit" },
        D: { year: "numeric", month: "long", day: "numeric" },
        f: { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" },
        F: { weekday: "long", year: "numeric", month: "long", day: "numeric", hour: "numeric", minute: "2-digit" },
      };
      title = new Intl.DateTimeFormat(locale, {
        timeZone: timezone,
        dateStyle: "full",
        timeStyle: "long",
      }).format(date);
      if (style === "R") {
        const basis = new Date(options.presentationTime || 0);
        if (!Number.isFinite(basis.getTime())) throw new RangeError("invalid presentation time");
        const seconds = (date.getTime() - basis.getTime()) / 1000;
        const absolute = Math.abs(seconds);
        const [unit, amount] = absolute < 60 ? ["second", seconds]
          : absolute < 3600 ? ["minute", seconds / 60]
            : absolute < 86400 ? ["hour", seconds / 3600]
              : absolute < 604800 ? ["day", seconds / 86400]
                : ["week", seconds / 604800];
        rendered = new Intl.RelativeTimeFormat(locale, { numeric: "auto" }).format(Math.round(amount), unit);
      } else {
        rendered = new Intl.DateTimeFormat(locale, {
          timeZone: timezone,
          ...(formats[style] || formats.f),
        }).format(date);
      }
    }
  } catch (error) {
    options.onDiagnostic?.({
      code: "timestamp-invalid",
      severity: "warning",
      message: "Timestamp could not be formatted with the supplied time profile",
      detail: String(error),
      complete: false,
    });
    rendered = `<t:${token.unix}${style ? `:${style}` : ""}>`;
    title = undefined;
  }
  if (!valid) {
    options.onDiagnostic?.({
      code: "timestamp-invalid",
      severity: "warning",
      message: "Timestamp is outside the supported date range",
      complete: false,
    });
  }
  const time = node("time", "discord-timestamp", rendered);
  if (valid) time.dateTime = date.toISOString();
  if (title) time.title = title;
  parent.append(time);
}

function renderInlineTokens(parent, tokens, options) {
  const stack = [parent];
  for (const token of tokens || []) {
    if (!token || typeof token !== "object") continue;
    const target = stack[stack.length - 1];
    if (token.type === "text") { appendText(target, token.content); continue; }
    if (token.type === "code") { target.append(node("code", "inline-code", token.content || "")); continue; }
    if (token.type === "break") { target.append(document.createElement("br")); continue; }
    if (token.type === "timestamp") { renderTimestamp(target, token, options); continue; }
    if (token.type === "emoji") { appendEmojiValue(target, token.emoji, options); continue; }
    if (token.type === "mention" || token.type === "command") {
      target.append(node("span", token.type === "mention" ? "mention" : "command-mention", token.label || ""));
      continue;
    }
    if (token.type === "link_open") {
      const href = safeLink(token.href);
      if (!href) continue;
      const link = node("a", "markdown-link");
      link.href = href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      target.append(link);
      stack.push(link);
      continue;
    }
    if (token.type === "link_close") { if (stack.length > 1) stack.pop(); continue; }
    const marks = { strong: "strong", em: "em", s: "del", u: "u", spoiler: "span" };
    const isOpen = token.type.endsWith("_open");
    const name = token.type.replace(/_(?:open|close)$/, "");
    const tag = marks[name];
    if (!tag) continue;
    if (!isOpen) { if (stack.length > 1) stack.pop(); continue; }
    const element = document.createElement(tag);
    const content = name === "spoiler" ? node("span", "markdown-spoiler-content") : element;
    if (name === "spoiler") {
      const group = token.group == null ? null : String(token.group);
      element.className = "markdown-spoiler";
      if (group !== null) element.dataset.spoilerGroup = group;
      element.tabIndex = 0;
      element.setAttribute("role", "button");
      element.setAttribute("aria-label", "Reveal spoiler");
      content.inert = true;
      content.setAttribute("aria-hidden", "true");
      const reveal = () => {
        const paragraph = element.closest(".markdown-paragraph");
        const fragments = group === null || !paragraph
          ? [element]
          : [...paragraph.querySelectorAll(".markdown-spoiler")]
            .filter((fragment) => fragment.dataset.spoilerGroup === group);
        for (const fragment of fragments) {
          fragment.classList.add("is-revealed");
          const fragmentContent = fragment.querySelector(".markdown-spoiler-content");
          if (!fragmentContent) continue;
          fragmentContent.inert = false;
          fragmentContent.removeAttribute("aria-hidden");
          fragment.removeAttribute("role");
          fragment.removeAttribute("tabindex");
          fragment.removeAttribute("aria-label");
        }
      };
      element.addEventListener("click", reveal);
      element.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); reveal(); }
      });
      element.append(content);
    }
    target.append(element);
    stack.push(content);
  }
}

function languageName(value) {
  const input = String(value || "").trim().toLowerCase();
  const language = ALIASES[input] || input;
  return Object.hasOwn(LANGUAGES, language) ? language : null;
}

function appendHighlightedOutput(parent, value) {
  const stack = [parent];
  const entities = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#x27;": "'" };
  let index = 0;
  while (index < value.length) {
    if (value.startsWith("</span>", index)) {
      if (stack.length > 1) stack.pop();
      index += 7;
      continue;
    }
    const opening = /^<span class="(hljs-[A-Za-z0-9_.-]+(?: [A-Za-z0-9_.-]+)*)">/.exec(value.slice(index));
    if (opening) {
      const span = document.createElement("span");
      for (const className of opening[1].split(" ")) span.classList.add(className);
      stack[stack.length - 1].append(span);
      stack.push(span);
      index += opening[0].length;
      continue;
    }
    const nextTag = value.indexOf("<", index);
    const end = nextTag < 0 ? value.length : nextTag;
    const text = value.slice(index, end);
    const entityPattern = /&(amp|lt|gt|quot|#x27);/g;
    let cursor = 0;
    for (const match of text.matchAll(entityPattern)) {
      appendText(stack[stack.length - 1], text.slice(cursor, match.index));
      appendText(stack[stack.length - 1], entities[match[0]]);
      cursor = match.index + match[0].length;
    }
    appendText(stack[stack.length - 1], text.slice(cursor));
    index = end === index ? index + 1 : end;
  }
}

function renderCodeBlock(block, options) {
  const wrapper = node("div", "code-block-wrap");
  const sourceLanguage = typeof block.language === "string" ? block.language : "";
  const language = languageName(sourceLanguage);
  if (sourceLanguage) wrapper.append(node("span", "code-language", sourceLanguage));
  const pre = node("pre", "code-block");
  const code = node("code", language ? `language-${language}` : "");
  const content = String(block.content || "");
  if (language) {
    try {
      appendHighlightedOutput(code, hljs.highlight(content, { language, ignoreIllegals: true }).value);
    } catch (error) {
      code.textContent = content;
      options.onDiagnostic?.({
        code: "code-highlight-failed",
        severity: "warning",
        message: `Code highlighting failed for ${language}`,
        detail: String(error),
        complete: false,
      });
    }
  } else code.textContent = content;
  pre.append(code);
  wrapper.append(pre);
  return wrapper;
}

function renderBlock(block, target, options) {
  if (!block || typeof block !== "object") return;
  if (block.type === "inline" || block.type === "subtext") {
    const children = block.children || [];
    if (block.type === "subtext") {
      const subtext = node("small", "markdown-subtext");
      renderInlineTokens(subtext, children, options);
      target.append(subtext);
    } else renderInlineTokens(target, children, options);
    return;
  }
  if (block.type === "code_block") { target.append(renderCodeBlock(block, options)); return; }
  const tags = {
    paragraph: "p", blockquote: "blockquote", bullet_list: "ul", ordered_list: "ol",
    list_item: "li", heading: /^h[1-6]$/.test(block.tag || "") ? block.tag : "h3",
  };
  const element = node(tags[block.type] || "div", `markdown-${block.type || "block"}`);
  for (const child of block.children || []) renderBlock(child, element, options);
  target.append(element);
}

export function appendMarkdownOrText(parent, value, tokens, options = {}) {
  if (Array.isArray(tokens) && tokens.length) {
    for (const token of tokens) renderBlock(token, parent, options);
  } else appendText(parent, value);
}

const EMOJI_CLUSTER = /(?:\p{Emoji_Presentation}|\p{Extended_Pictographic}|\p{Regional_Indicator}{2}|[#*0-9]\uFE0F?\u20E3)/u;
export function isEmojiOnly(tokens) {
  let found = false;
  const visit = (items) => {
    for (const token of items || []) {
      if (!token || typeof token !== "object") continue;
      if (token.type === "emoji") { found = true; continue; }
      if (token.type === "text") {
        const text = String(token.content || "");
        const clusters = typeof Intl.Segmenter === "function"
          ? [...new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(text)].map((part) => part.segment)
          : Array.from(text);
        if (!clusters.every((cluster) => /^\s*$/u.test(cluster) || EMOJI_CLUSTER.test(cluster))) return false;
        if (clusters.some((cluster) => !/^\s*$/u.test(cluster))) found = true;
        continue;
      }
      if (["strong_open", "strong_close", "em_open", "em_close", "s_open", "s_close", "u_open", "u_close", "spoiler_open", "spoiler_close"].includes(token.type)) continue;
      if (["inline", "paragraph", "heading", "blockquote", "list_item", "bullet_list", "ordered_list"].includes(token.type)) {
        if (!visit(token.children)) return false;
        continue;
      }
      if (token.type === "break") continue;
      return false;
    }
    return true;
  };
  const valid = visit(tokens);
  return found && valid;
}
