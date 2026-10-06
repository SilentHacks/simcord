import { applyRoleColor, icon, iconButton, node, presenceDot, renderIdentityAvatar } from "./dom.js";
import { isEmojiOnly, appendEmojiValue, appendMarkdownOrText } from "./text.js";
import { renderEmbed, renderNode } from "./components.js";
import {
  downloadButton,
  fileTypeLabel,
  formatFileSize,
  renderMedia,
  renderSpoiler,
  renderSpoilerMedia,
} from "./media.js";

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

function appendSystemMessage(root, system, options) {
  const content = node("div", "message-system");
  content.append(node("span", "message-system-icon", system.icon || "system"));
  const text = node("span", "message-system-text");
  appendMarkdownOrText(text, system.text || "", system.text_tokens, options);
  content.append(text);
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
    if (sticker.available === false) {
      list.append(node("div", "message-sticker-unavailable", `${sticker.name} unavailable`));
      options.onDiagnostic?.({
        code: "sticker-asset-unavailable",
        severity: "warning",
        message: `Sticker ${sticker.name} has no available media asset`,
        complete: false,
      });
      continue;
    }
    const result = renderSpoilerMedia(
      { ...sticker, description: sticker.name },
      "message-sticker",
      options,
      sticker.name || "Sticker",
      `sticker:${sticker.id || sticker.name}`,
    );
    list.append(result.element);
    pendingMedia.push(...result.pending);
  }
  if (list.childNodes.length) root.append(list);
}

function appendMessageContent(root, message, options, v2) {
  if (message.system) {
    appendSystemMessage(root, message.system, options);
  } else if (!v2 && message.content) {
    const content = node("div", "message-content");
    if (message.type_info?.kind === "unknown") content.textContent = message.content;
    else {
      appendMarkdownOrText(content, message.content, message.content_tokens, options);
      if (isEmojiOnly(message.content_tokens)) content.classList.add("emoji-only");
    }
    root.append(content);
  }
}
function appendFileAttachment(item, attachment, options) {
  const filename = attachment.filename || "attachment";
  const content = attachment.spoiler ? node("div") : item;
  const footer = node("div", "attachment-footer");
  const badge = node("span", "file-icon file-type", fileTypeLabel(attachment));
  badge.setAttribute("aria-hidden", "true");
  const info = node("span", "attachment-info");
  info.append(node("span", "attachment-name", filename));
  const size = formatFileSize(attachment.size);
  if (size) info.append(node("small", "attachment-size", size));
  if (attachment.description) info.append(node("small", "file-description", attachment.description));
  footer.append(info);

  const actions = node("span", "attachment-actions");
  if (typeof attachment.preview === "string" && attachment.preview.length) {
    const preview = node("pre", "attachment-preview", attachment.preview);
    preview.tabIndex = 0;
    preview.setAttribute("aria-label", `Contents of ${filename}`);
    const toggle = iconButton("code", `Toggle preview of ${filename}`, "attachment-action attachment-preview-toggle");
    toggle.setAttribute("aria-expanded", "true");
    toggle.addEventListener("click", () => {
      preview.hidden = !preview.hidden;
      toggle.setAttribute("aria-expanded", String(!preview.hidden));
    });
    content.append(preview);
    actions.append(toggle);
  } else {
    footer.prepend(badge);
  }
  const available = Boolean(attachment.asset_id) && attachment.available !== false
    && options.assets?.[attachment.asset_id]?.available !== false && options.loadAsset;
  if (!available) content.append(node("div", "media-unavailable", `${filename} unavailable`));
  const download = downloadButton(attachment, options, filename);
  download.classList.add("attachment-action");
  actions.append(download);
  footer.append(actions);
  content.append(footer);
  if (attachment.spoiler) {
    item.append(renderSpoiler(
      content,
      attachment.spoiler,
      options,
      filename,
      `attachment:${attachment.id || attachment.asset_id || filename}`,
    ));
  }
}

function disposeUnusedPlayers(players) {
  players.forEach((player) => {
    if (!player.isConnected) player._previewMediaPlayer?.dispose?.();
  });
}

export function renderMessage(root, message, options = {}) {
  const pendingMedia = options.pendingMedia || [];
  options.pendingMedia = pendingMedia;
  const reuseScope = message
    ? JSON.stringify([String(message.id), String(options.contextId ?? ""), String(options.viewerId ?? "")])
    : null;
  const reusePlayers = new Map();
  const oldPlayers = [...root.querySelectorAll(".media-player[data-media-reuse-key]")];
  const canReuse = reuseScope !== null && root.dataset.mediaReuseScope === reuseScope;
  oldPlayers.forEach((player) => {
    const reusable = canReuse && player._previewMediaPlayer?.active === true;
    if (player._previewMediaPlayer) player._previewMediaPlayer.active = false;
    if (!reusable) return;
    const key = player.dataset.mediaReuseKey;
    const queue = reusePlayers.get(key) || [];
    queue.push(player);
    reusePlayers.set(key, queue);
  });
  root.replaceChildren();
  if (!message) {
    delete root.dataset.mediaReuseScope;
    disposeUnusedPlayers(oldPlayers);
    return { pendingMedia };
  }
  const hadPlayerReuse = Object.hasOwn(options, "mediaPlayerReuse");
  const previousPlayerReuse = options.mediaPlayerReuse;
  options.mediaPlayerReuse = reusePlayers;
  options.messageId = String(message.id);
  options.messageAuthor = message.author;
  options.messageTimestamp = message.timestamp;
  options.lightboxGroup ||= { items: [] };
  const shortTime = (value) => new Intl.DateTimeFormat(options.locale || "en-US", {
    timeZone: options.timezone || "UTC",
    hour: "numeric",
    minute: "2-digit",
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
      const time = node("time", "message-time", new Intl.DateTimeFormat(options.locale || "en-US", {
        timeZone: options.timezone || "UTC",
        dateStyle: "short",
        timeStyle: "short",
      }).format(new Date(message.timestamp)));
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
  const suppressEmbeds = (Number(message.flags) & 4) !== 0;
  appendMessageContent(root, message, options, v2);
  appendStickers(root, message.stickers, options, pendingMedia);
  if (!v2 && !suppressEmbeds) {
    (message.embeds || []).forEach((embed, index) => root.append(renderEmbed(embed, index, options)));
  }
  if (message.components?.length) {
    const components = node("div", v2 ? "message-components components-v2" : "message-components");
    message.components.forEach((component, index) => components.append(renderNode(component, `message.${index}`, options)));
    root.append(components);
  }
  const visibleEmbeds = v2 || suppressEmbeds ? [] : message.embeds || [];
  const refs = referencedAssets(message.components, visibleEmbeds);
  const attachments = (message.attachments || []).filter((attachment) => !v2 && !refs.has(attachment.asset_id));
  if (attachments.length) {
    const images = attachments.filter((attachment) => attachment.inline && attachment.asset_id);
    const list = node("ul", "message-attachments");
    list.dataset.imageCount = String(Math.min(10, images.length));
    list.dataset.attachmentCount = String(Math.min(10, attachments.length));
    const imageGroup = { items: [] };
    attachments.forEach((attachment, index) => {
      const inline = attachment.inline && attachment.asset_id;
      const item = node("li", inline ? "attachment attachment-inline" : "attachment");
      if (inline) {
        const filename = attachment.filename || "Attachment";
        const result = renderMedia(attachment, "attachment-image", options, filename, imageGroup);
        const content = node("div", "attachment-media");
        content.append(result.element);
        const actions = node("div", "attachment-media-actions");
        const download = downloadButton(attachment, options, filename);
        download.classList.add("attachment-action");
        actions.append(download);
        content.append(actions);
        if (attachment.spoiler) {
          const rendered = renderSpoiler(
            content,
            attachment.spoiler,
            options,
            filename,
            `attachment:${attachment.id || index}:${attachment.asset_id}`,
          );
          rendered.classList.add("spoiler-media");
          item.append(rendered);
        } else {
          item.append(content);
        }
        pendingMedia.push(...result.pending);
      } else {
        appendFileAttachment(item, attachment, options);
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
    card.append(node("div", "poll-instruction", poll.multiselect ? "Select one or more answers" : "Select one answer"));
    const canVote = allowedActions.has("set_poll_votes")
      && !ended
      && typeof options.onPollDraft === "function";
    const drafted = options.pollAnswers?.(message);
    const selected = new Set(
      drafted || poll.answers.filter((answer) => answer.viewer_selected).map((answer) => String(answer.id)),
    );
    const voted = poll.answers.some((answer) => answer.viewer_selected);
    const showResults = ended || voted || !canVote || options.pollShowingResults?.(message);
    (poll.answers || []).forEach((answer) => {
      const answerId = String(answer.id);
      const choice = node(canVote && !showResults ? "button" : "div", "poll-answer");
      const label = node("span", "poll-answer-label");
      if (answer.emoji) {
        const emoji = node("span", "poll-emoji");
        appendEmojiValue(emoji, answer.emoji, options, "Poll emoji");
        label.append(emoji);
      }
      label.append(node("span", "", String(answer.text ?? "")));
      choice.append(label);
      if (canVote && !showResults) {
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
      const isSelected = showResults ? answer.viewer_selected : selected.has(answerId);
      if (isSelected) choice.classList.add("is-selected");
      if (showResults) {
        const percentage = Math.max(0, Math.min(100, Number(answer.percentage) || 0));
        const fill = node("span", "poll-result-fill");
        fill.style.width = `${percentage}%`;
        choice.prepend(fill);
        choice.append(
          node("span", "poll-answer-count", `${answer.count} ${answer.count === 1 ? "vote" : "votes"}`),
          node("span", "poll-answer-percentage", `${percentage}%`),
        );
      }
      if (!showResults || answer.viewer_selected) {
        const indicator = node("span", `poll-indicator ${showResults ? "poll-voted" : poll.multiselect ? "poll-checkbox" : "poll-radio"}`);
        indicator.setAttribute("aria-hidden", "true");
        if (isSelected && (showResults || poll.multiselect)) indicator.append(icon("check"));
        choice.append(indicator);
      }
      card.append(choice);
    });
    const footer = node("div", "poll-footer");
    const status = node("div", "poll-status");
    status.append(node("span", "poll-total", `${poll.total_votes} ${poll.total_votes === 1 ? "vote" : "votes"}`));
    const remaining = new Date(poll.expiry).getTime() - new Date(options.presentationTime || Date.now()).getTime();
    let timeLeft = "Poll ended";
    if (!ended) {
      const hours = Math.max(0, Math.floor(remaining / 3600000));
      timeLeft = Number.isFinite(hours) ? (hours >= 24 ? `${Math.floor(hours / 24)}d left` : hours ? `${hours}h left` : "Less than 1h left") : "Ends later";
    }
    status.append(node("span", "poll-time", timeLeft));
    footer.append(status);
    const actions = node("div", "poll-actions");
    const addButton = (label, className, key, callback) => {
      const button = node("button", className, label);
      button.type = "button";
      button.dataset.controlKey = `message:${message.id}:poll:${key}`;
      button.addEventListener("click", callback);
      actions.append(button);
      return button;
    };
    if (canVote && typeof options.onPollSubmit === "function") {
      if (voted) {
        addButton("Remove Vote", "poll-secondary", "remove", () => options.onPollSubmit(message, []));
      } else if (showResults) {
        addButton("Go back to vote", "poll-secondary", "back", () => options.onPollResults?.(message, false));
      } else {
        addButton("Show results", "poll-show-results", "results", () => options.onPollResults?.(message, true));
        const submit = addButton("Vote", "poll-submit", "submit", () => options.onPollSubmit(message, [...selected]));
        submit.disabled = selected.size === 0;
      }
    }
    footer.append(actions);
    card.append(footer);
    root.append(card);
  }
  if (options.channelLayout) {
    const toolbar = node("div", "message-context-actions");
    toolbar.setAttribute("role", "toolbar");
    toolbar.setAttribute("aria-label", `Actions for ${message.author?.name || "message"}`);
    const addAction = (key, label, callback) => {
      if (typeof callback !== "function") return;
      const button = iconButton(key, label, "message-action-button");
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
  root.dataset.mediaReuseScope = reuseScope;
  disposeUnusedPlayers(oldPlayers);
  if (hadPlayerReuse) options.mediaPlayerReuse = previousPlayerReuse;
  else delete options.mediaPlayerReuse;
  return { pendingMedia };
}
