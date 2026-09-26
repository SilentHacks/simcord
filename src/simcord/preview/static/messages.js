import { applyRoleColor, presenceDot, renderIdentityAvatar } from "./dom.js";
import {
  appendMarkdownOrText,
  node,
  renderEmbed,
  renderNode,
  renderSpoilerMedia,
} from "./components.js";

function referencedAssets(components, embeds) {
  const found = new Set();
  const visit = (value) => {
    if (!value || typeof value !== "object") return;
    if (typeof value.asset_id === "string") found.add(value.asset_id);
    Object.values(value).forEach(visit);
  };
  visit(components);
  visit(embeds);
  return found;
}

function absoluteTime(value, options) {
  return new Intl.DateTimeFormat(options.locale || "en-US", {
    timeZone: options.timezone || "UTC",
    dateStyle: "long",
    timeStyle: "long",
  }).format(new Date(value));
}

function appendReply(root, reply, options) {
  if (reply?.state !== "resolved") return;
  const preview = node("div", "message-reply");
  const identity = reply.author || {};
  const avatar = renderIdentityAvatar(identity, options, "reply-avatar");
  preview.append(avatar.element);
  options.pendingMedia?.push(...avatar.pending);
  const text = node("span", "reply-text");
  text.append(node("strong", "reply-author", identity.name || "Unknown author"));
  if (reply.channel_name) text.append(node("span", "reply-channel", ` in #${reply.channel_name}`));
  const excerpt = node("span", "reply-excerpt");
  appendMarkdownOrText(excerpt, "", reply.excerpt_tokens, options);
  text.append(excerpt);
  preview.append(text);
  root.append(preview);
}

function appendThread(root, thread) {
  if (!thread) return;
  const summary = node(
    "div",
    "thread-summary",
    `${thread.name || "Thread"} · ${thread.message_count} ${thread.message_count === 1 ? "message" : "messages"}`,
  );
  if (thread.archived) summary.append(node("span", "thread-archived", "Archived"));
  root.append(summary);
}

export function renderMessage(root, message, options = {}) {
  const pendingMedia = options.pendingMedia || [];
  options.pendingMedia = pendingMedia;
  root.replaceChildren();
  if (!message) return { pendingMedia };
  const shortTime = (value) => new Intl.DateTimeFormat(options.locale || "en-US", {
    timeZone: options.timezone || "UTC",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(value));

  appendReply(root, message.reply, options);
  if (message.interaction_header?.kind === "application_command") {
    const invoker = message.interaction_header.user?.name;
    root.append(node("div", "message-interaction-header", invoker ? `${invoker} used an application command` : "Application command"));
  }
  if (!message.compact) {
    const header = node("header", "message-header");
    header.tabIndex = -1;
    header.dataset.controlKey = `message:${message.id}`;
    const avatar = node("span", "message-avatar");
    const renderedAvatar = renderIdentityAvatar(message.author, options, "message-avatar-image");
    avatar.append(renderedAvatar.element);
    pendingMedia.push(...renderedAvatar.pending);
    const avatarPresence = presenceDot(message.author);
    if (avatarPresence) avatar.append(avatarPresence);
    header.append(avatar);
    const author = node("strong", "message-author", message.author?.name || "Unknown author");
    applyRoleColor(author, message.author);
    header.append(author);
    if (message.author?.kind === "application") header.append(node("span", "message-app-badge", "APP"));
    else if (message.author?.webhook) header.append(node("span", "message-app-badge", "WEBHOOK"));
    else if (message.author?.bot) header.append(node("span", "message-app-badge", "BOT"));
    if (message.timestamp) {
      const time = node("time", "message-time", shortTime(message.timestamp));
      time.dateTime = message.timestamp;
      time.title = absoluteTime(message.timestamp, options);
      if (message.edited_timestamp) {
        time.append(node("span", "message-edited", " (edited)"));
        time.title = `Edited ${absoluteTime(message.edited_timestamp, options)}`;
      }
      header.append(time);
    }
    root.append(header);
  } else if (message.timestamp) {
    const gutter = node("time", "message-gutter-time", shortTime(message.timestamp));
    gutter.dateTime = message.timestamp;
    gutter.title = absoluteTime(message.timestamp, options);
    root.append(gutter);
    if (message.edited_timestamp) {
      const edited = node("span", "message-edited-compact", "(edited)");
      edited.title = `Edited ${absoluteTime(message.edited_timestamp, options)}`;
      root.append(edited);
    }
  }
  if (message.ephemeral) {
    root.append(node("div", "message-ephemeral-note", "Only you can see this"));
  }
  const v2 = message.components_v2 === true || (Number(message.flags) & 32768) !== 0;
  if (!v2 && message.content) {
    const content = node("div", "message-content");
    appendMarkdownOrText(content, message.content, message.content_tokens, options);
    root.append(content);
  }
  if (!v2 && (Number(message.flags) & 4) === 0) {
    (message.embeds || []).forEach((embed, index) => root.append(renderEmbed(embed, index, options)));
  }
  if (message.components?.length) {
    const components = node("div", v2 ? "message-components components-v2" : "message-components");
    message.components.forEach((component, index) => components.append(renderNode(component, `message.${index}`, options)));
    root.append(components);
  }
  const refs = referencedAssets(message.components, v2 ? [] : message.embeds);
  const attachments = (message.attachments || []).filter((attachment) => !v2 && !refs.has(attachment.asset_id));
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
        const available = Boolean(attachment.asset_id) && attachment.available !== false
          && options.assets?.[attachment.asset_id]?.available !== false && options.loadAsset;
        if (!available) {
          item.append(node("div", "media-unavailable", `${attachment.filename || "Attachment"} unavailable`));
          options.onDiagnostic?.({ code: "file-unavailable", severity: "warning", message: `${attachment.filename || "Attachment"} is unavailable offline`, complete: false });
        }
        const footer = node("div", "attachment-footer");
        const info = node("span", "attachment-info");
        info.append(node("span", "attachment-name", attachment.filename || "attachment"));
        if (attachment.size !== undefined) info.append(node("small", "attachment-size", `${Math.max(1, Math.ceil(attachment.size / 1024))} KB`));
        footer.append(info);
        if (available) {
          const openAsset = async (download = false) => {
            try {
              const url = await options.loadAsset?.(attachment.asset_id, { download: true });
              if (!url) return;
              const link = node("a");
              link.href = url;
              if (download) link.download = attachment.filename || "attachment";
              else link.target = "_blank";
              link.rel = "noopener noreferrer";
              link.click();
            } catch (error) {
              options.onDiagnostic?.({ code: "file-unavailable", severity: "warning", message: `${attachment.filename || "Attachment"} is unavailable offline`, detail: String(error), complete: false });
            }
          };
          const icons = {
            expand: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" d="M9.6 5.6 4.5 12l5.1 6.4M14.4 5.6 19.5 12l-5.1 6.4"/></svg>',
            open: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" d="M7 17 17 7M8.5 6.5h9v9"/></svg>',
            more: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="currentColor" d="M5 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4zm7 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4zm7 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4z"/></svg>',
          };
          const actions = node("span", "attachment-actions");
          const expand = node("button", "attachment-action");
          expand.type = "button"; expand.setAttribute("aria-label", "Expand preview"); expand.innerHTML = icons.expand;
          expand.addEventListener("click", () => item.classList.toggle("is-expanded"));
          const open = node("button", "attachment-action");
          open.type = "button"; open.setAttribute("aria-label", "Open attachment"); open.innerHTML = icons.open;
          open.addEventListener("click", () => openAsset(false));
          const more = node("button", "attachment-action");
          more.type = "button"; more.setAttribute("aria-label", "Download attachment"); more.innerHTML = icons.more;
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
  appendThread(root, message.thread);
  if (options.channelLayout && typeof options.onReply === "function") {
    const actions = node("div", "message-context-actions");
    const reply = node("button", "reply-button", "Reply");
    reply.type = "button";
    reply.dataset.controlKey = `reply:${message.id}`;
    reply.setAttribute("aria-label", `Reply to ${message.author?.name || "message"}`);
    reply.addEventListener("click", () => options.onReply(message));
    actions.append(reply);
    root.append(actions);
  }
  return { pendingMedia };
}
