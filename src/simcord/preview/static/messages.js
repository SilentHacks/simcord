import { applyRoleColor, presenceDot, renderIdentityAvatar } from "./dom.js";
import {
  appendEmojiValue,
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

function appendDiscordLink(parent, value, label) {
  if (typeof value?.url !== "string") return;
  try {
    const url = new URL(value.url);
    if (url.protocol !== "https:" || url.hostname !== "discord.com") return;
    const link = node("a", "message-system-link", label);
    link.href = url.href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    parent.append(link);
  } catch {
    // Invalid projected links are omitted rather than rendered as active links.
  }
}

function appendSystemMessage(root, system) {
  const content = node("div", "message-system");
  content.append(node("span", "message-system-icon", system.icon || "system"));
  content.append(node("span", "message-system-text", system.text || ""));
  const details = node("span", "message-system-details");
  if (system.recipient?.name) details.append(node("span", "", ` ${system.recipient.name}`));
  appendDiscordLink(details, system.channel, system.channel?.name || "channel");
  appendDiscordLink(
    details,
    system.reference,
    system.reference?.author?.name ? ` ${system.reference.author.name}'s message` : " message",
  );
  if (details.childNodes.length) content.append(details);
  root.append(content);
}

function appendInteractionHeader(root, interaction) {
  const header = node("div", "message-interaction-header");
  const invoker = interaction.user?.name;
  header.append(document.createTextNode(invoker ? `${invoker} used ` : "Used "));
  const name = interaction.name || "an application command";
  const command = interaction.command_type === "chat_input" ? `/${name}` : name;
  header.append(node("strong", "", command));
  if (interaction.target_user?.name) {
    header.append(document.createTextNode(` · ${interaction.target_user.name}`));
  }
  appendDiscordLink(header, interaction.target_message, " · View target message");
  root.append(header);
}

function appendStickers(root, stickers, options, pendingMedia) {
  if (!stickers?.length) return;
  const list = node("div", "message-stickers");
  for (const sticker of stickers) {
    if (sticker.format_type !== 1) {
      list.append(node("div", "message-sticker-unavailable", `${sticker.name} · animated sticker preview unavailable`));
      options.onDiagnostic?.({
        code: "sticker-animation-unavailable",
        severity: "warning",
        message: `Sticker ${sticker.name} uses an animated format not rendered in this preview`,
        complete: false,
      });
      continue;
    }
    if (sticker.available === false) {
      list.append(node("div", "message-sticker-unavailable", `${sticker.name} unavailable`));
      continue;
    }
    const result = renderSpoilerMedia(
      { ...sticker, content_type: "image/png", description: sticker.name },
      "message-sticker",
      options,
      sticker.name || "Sticker",
    );
    list.append(result.element);
    pendingMedia.push(...result.pending);
  }
  if (list.childNodes.length) root.append(list);
}

function appendMessageContent(root, message, options, v2) {
  if (message.system) {
    appendSystemMessage(root, message.system);
  } else if (!v2 && message.content) {
    const content = node("div", "message-content");
    if (message.type_info?.kind === "unknown") content.textContent = message.content;
    else appendMarkdownOrText(content, message.content, message.content_tokens, options);
    root.append(content);
  }
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
  if (["application_command", "context_menu_command"].includes(message.interaction_header?.kind)) {
    appendInteractionHeader(root, message.interaction_header);
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
  appendMessageContent(root, message, options, v2);
  appendStickers(root, message.stickers, options, pendingMedia);
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
  const allowedActions = new Set(message.allowed_actions || []);
  if (message.reactions?.length) {
    const reactions = node("div", "message-reactions");
    message.reactions.forEach((reaction, index) => {
      const canReact = Boolean(reaction.can_toggle) && typeof options.onReaction === "function";
      const chip = node(canReact ? "button" : "span", "reaction-chip", "");
      if (canReact) {
        chip.type = "button";
        chip.dataset.controlKey = `message:${message.id}:reaction:${index}`;
        chip.setAttribute("aria-pressed", String(Boolean(reaction.viewer_reacted)));
        chip.addEventListener("click", () => options.onReaction(message, reaction));
      }
      if (reaction.viewer_reacted) chip.classList.add("is-selected");
      const emoji = node("span", "reaction-emoji");
      appendEmojiValue(emoji, reaction.emoji, options, "Reaction emoji");
      chip.append(emoji, node("span", "reaction-count", reaction.count));
      reactions.append(chip);
    });
    root.append(reactions);
  }
  if (message.poll) {
    const poll = message.poll;
    const card = node("section", "message-poll");
    card.append(node("h3", "poll-question", poll.question));
    const ended = Boolean(poll.finalized || poll.expired);
    const expiry = new Date(poll.expiry);
    const status = ended
      ? "Poll ended"
      : `Ends ${Number.isFinite(expiry.getTime()) ? absoluteTime(poll.expiry, options) : "later"}`;
    card.append(node("div", "poll-status", `${status} · ${poll.total_votes} ${poll.total_votes === 1 ? "vote" : "votes"}`));
    const canVote = allowedActions.has("set_poll_votes")
      && !ended
      && typeof options.onPollDraft === "function";
    const drafted = options.pollAnswers?.(message);
    const selected = new Set(
      drafted || poll.answers.filter((answer) => answer.viewer_selected).map((answer) => String(answer.id)),
    );
    (poll.answers || []).forEach((answer) => {
      const answerId = String(answer.id);
      const row = node("div", "poll-answer-row");
      const choice = node(canVote ? "button" : "span", "poll-answer");
      if (answer.emoji) {
        appendEmojiValue(choice, answer.emoji, options, "Poll emoji");
        choice.append(document.createTextNode(" "));
      }
      if (canVote) {
        choice.type = "button";
        choice.dataset.controlKey = `message:${message.id}:poll:${answerId}`;
        choice.setAttribute("aria-pressed", String(selected.has(answerId)));
        choice.addEventListener("click", () => {
          const next = new Set(selected);
          if (next.has(answerId)) next.delete(answerId);
          else {
            if (!poll.multiselect) next.clear();
            next.add(answerId);
          }
          options.onPollDraft(message, [...next]);
        });
      }
      if (selected.has(answerId)) choice.classList.add("is-selected");
      const result = node("div", "poll-result-track");
      const fill = node("span", "poll-result-fill");
      fill.style.width = `${Math.max(0, Math.min(100, Number(answer.percentage) || 0))}%`;
      result.append(fill);
      row.append(choice, node("span", "poll-answer-count", answer.count), result);
      row.append(node("span", "poll-answer-percentage", `${answer.percentage}%`));
      card.append(row);
    });
    if (canVote && typeof options.onPollSubmit === "function") {
      const submit = node("button", "poll-submit", "Vote");
      submit.type = "button";
      submit.dataset.controlKey = `message:${message.id}:poll:submit`;
      submit.addEventListener("click", () => options.onPollSubmit(message, [...selected]));
      card.append(submit);
    }
    root.append(card);
  }
  if (options.channelLayout) {
    const toolbar = node("div", "message-context-actions");
    toolbar.setAttribute("role", "toolbar");
    toolbar.setAttribute("aria-label", `Actions for ${message.author?.name || "message"}`);
    const addAction = (key, label, callback) => {
      if (typeof callback !== "function") return;
      const button = node("button", "message-action-button", label);
      button.type = "button";
      button.dataset.controlKey = `message:${message.id}:action:${key}`;
      button.addEventListener("click", () => callback(message));
      toolbar.append(button);
    };
    if (allowedActions.has("reply")) addAction("reply", "Reply", options.onReply);
    if (allowedActions.has("edit_message")) addAction("edit", "Edit", options.onEdit);
    if (allowedActions.has("delete_message")) addAction("delete", "Delete", options.onDelete);
    if (allowedActions.has("set_pinned")) {
      addAction("pin", message.pinned ? "Unpin" : "Pin", options.onPin);
    }
    if (toolbar.childElementCount) root.append(toolbar);
  }
  return { pendingMedia };
}
