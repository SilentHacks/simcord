import { node } from "./dom.js";

const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);
let lightbox;

function current(options) {
  return !options.isCurrent || options.isCurrent();
}

function diagnostic(options, code, label, error) {
  options.onDiagnostic?.({
    code,
    severity: "warning",
    message: `${label || "Media"} is unavailable offline`,
    ...(error ? { detail: String(error) } : {}),
    complete: false,
  });
}

function getLightbox() {
  if (lightbox) return lightbox;
  const dialog = node("dialog", "media-lightbox");
  dialog.setAttribute("aria-label", "Image preview");
  const frame = node("div", "media-lightbox-frame");
  const image = node("img", "media-lightbox-image");
  const position = node("p", "media-lightbox-position");
  position.setAttribute("aria-live", "polite");
  const controls = node("div", "media-lightbox-controls");
  const previous = node("button", "media-lightbox-control", "Previous");
  previous.type = "button";
  previous.setAttribute("aria-label", "Previous image");
  const close = node("button", "media-lightbox-control", "Close");
  close.type = "button";
  close.setAttribute("aria-label", "Close image preview");
  const next = node("button", "media-lightbox-control", "Next");
  next.type = "button";
  next.setAttribute("aria-label", "Next image");
  controls.append(previous, close, next);
  frame.append(image);
  dialog.append(frame, position, controls);
  document.body.append(dialog);

  let items = [];
  let index = 0;
  let opener = null;
  const draw = () => {
    const item = items[index];
    if (!item) return;
    image.src = item.image.src;
    image.alt = item.label;
    const multiple = items.length > 1;
    previous.hidden = !multiple;
    next.hidden = !multiple;
    position.textContent = multiple ? `${index + 1} of ${items.length}` : "";
  };
  const move = (step) => {
    if (items.length < 2) return;
    index = (index + step + items.length) % items.length;
    draw();
  };
  const restore = () => {
    image.removeAttribute("src");
    image.alt = "";
    items = [];
    index = 0;
    const target = opener;
    opener = null;
    if (target?.isConnected) target.focus({ preventScroll: true });
  };
  previous.addEventListener("click", () => move(-1));
  next.addEventListener("click", () => move(1));
  close.addEventListener("click", () => dialog.close());
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    dialog.close();
  });
  dialog.addEventListener("close", restore);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft" && items.length > 1) {
      event.preventDefault();
      move(-1);
    } else if (event.key === "ArrowRight" && items.length > 1) {
      event.preventDefault();
      move(1);
    }
  });
  lightbox = {
    open(group, selected, trigger) {
      items = group.items.filter(({ image: candidate }) => (
        candidate.isConnected
        && candidate.src.startsWith("blob:")
        && !candidate.closest(".spoiler-content:not(.is-revealed)")
      ));
      index = items.indexOf(selected);
      if (index < 0) return;
      opener = trigger;
      draw();
      if (!dialog.open) dialog.showModal();
      close.focus();
    },
    close() {
      if (dialog.open) dialog.close();
    },
  };
  return lightbox;
}

export function closeLightbox() {
  lightbox?.close();
}

export function renderMedia(media, className, options, label, group = options.lightboxGroup) {
  const assetId = typeof media?.asset_id === "string" ? media.asset_id : null;
  const manifest = assetId ? options.assets?.[assetId] : null;
  const mediaType = String(media?.content_type || manifest?.contentType || "").toLowerCase();
  if (mediaType && !IMAGE_TYPES.has(mediaType)) {
    options.onDiagnostic?.({
      code: "unsupported-media-type",
      severity: "warning",
      message: `${label || "Media"} type ${mediaType} cannot be rendered inline`,
      complete: false,
    });
    return { element: node("div", "media-unavailable", `${label || "Media"} unavailable`), pending: [] };
  }
  if (!assetId || media.available === false || manifest?.available === false || !options.loadAsset) {
    const message = manifest?.diagnostic || `${label || "Media"} is unavailable offline`;
    options.onDiagnostic?.({
      code: manifest?.diagnostic ? "media-rejected" : "media-unavailable",
      severity: "warning",
      message,
      complete: false,
    });
    return { element: node("div", "media-unavailable", `${label || "Media"} unavailable`), pending: [] };
  }
  const trigger = node("button", "media-lightbox-trigger");
  trigger.type = "button";
  trigger.setAttribute("aria-label", `Open ${label || "image"} in image preview`);
  trigger.disabled = true;
  const image = node("img", className);
  image.alt = String(media.description || label || "Preview media");
  image.loading = "eager";
  const width = Number(manifest?.displayWidth);
  const height = Number(manifest?.displayHeight);
  if (width > 0 && height > 0) {
    image.width = width;
    image.height = height;
  }
  trigger.append(image);
  const entry = { image, label: image.alt };
  const lightboxGroup = group || { items: [] };
  lightboxGroup.items.push(entry);
  trigger.addEventListener("click", () => {
    if (image.src.startsWith("blob:") && current(options)) {
      getLightbox().open(lightboxGroup, entry, trigger);
    }
  });
  const pending = [Promise.resolve(options.loadAsset(assetId)).then(async (url) => {
    if (!current(options)) return;
    if (typeof url !== "string" || !url.startsWith("blob:")) throw new Error("asset is not a local blob URL");
    image.src = url;
    if (image.decode) await image.decode();
    else if (!image.complete) {
      await new Promise((resolve, reject) => {
        image.addEventListener("load", resolve, { once: true });
        image.addEventListener("error", reject, { once: true });
      });
    }
    if (!current(options)) return;
    const displayWidth = image.naturalWidth;
    const displayHeight = image.naturalHeight;
    if (displayWidth < 1 || displayHeight < 1) throw new Error("decoded image has no intrinsic dimensions");
    if ((width && width !== displayWidth) || (height && height !== displayHeight)) {
      throw new Error("validated display dimensions do not match the decoded image");
    }
    image.width = displayWidth;
    if (manifest) {
      manifest.displayReady = true;
      manifest.displayWidth = displayWidth;
      manifest.displayHeight = displayHeight;
    }
    image.height = displayHeight;
    trigger.disabled = false;
  }).catch((error) => {
    if (!current(options)) return;
    diagnostic(options, "media-unavailable", label, error);
    const unavailable = node("div", "media-unavailable", `${label || "Media"} unavailable`);
    unavailable.inert = trigger.inert;
    if (trigger.hasAttribute("aria-hidden")) unavailable.setAttribute("aria-hidden", "true");
    trigger.replaceWith(unavailable);
  })];
  return { element: trigger, pending };
}

function spoilerKey(media, options, label, stateKey) {
  return [options.contextId || "", options.messageId || options.scope || "", stateKey || label, media.asset_id || ""].join(":");
}

function reveal(element, spoiler, options, label, key) {
  if (!spoiler) return element;
  const wrapper = node("div", "spoiler-content");
  wrapper.append(element);
  const state = options.spoilerState;
  const revealed = Boolean(state?.has(key));
  if (!revealed) {
    element.inert = true;
    element.setAttribute("aria-hidden", "true");
    const cover = node("button", "spoiler-cover");
    cover.type = "button";
    cover.setAttribute("aria-label", `Reveal ${label} spoiler`);
    cover.addEventListener("click", () => {
      state?.add(key);
      element.inert = false;
      element.removeAttribute("aria-hidden");
      wrapper.classList.add("is-revealed");
      cover.remove();
    });
    wrapper.append(cover);
  } else {
    wrapper.classList.add("is-revealed");
  }
  return wrapper;
}

export function renderSpoiler(element, spoiler, options, label, key) {
  return reveal(element, spoiler, options, label, [options.contextId || "", options.messageId || options.scope || "", key || label].join(":"));
}

export function renderSpoilerMedia(media, className, options, label, stateKey, group = options.lightboxGroup) {
  const result = renderMedia(media, className, options, label, group);
  const key = spoilerKey(media, options, label, stateKey);
  result.element = reveal(result.element, media?.spoiler, options, label, key);
  if (media?.spoiler) result.element.classList.add("spoiler-media");
  return result;
}

export function formatFileSize(value) {
  if (value === null || value === undefined) return "";
  const size = Number(value);
  if (!Number.isFinite(size) || size < 0) return "";
  if (size >= 1024 * 1024) return `${Math.ceil(size / (1024 * 1024))} MB`;
  return `${Math.max(1, Math.ceil(size / 1024))} KB`;
}

export function fileTypeLabel(file) {
  const extension = String(file?.filename || "").split(".").at(-1);
  if (extension && extension !== file?.filename) return extension.slice(0, 5).toUpperCase();
  const type = String(file?.content_type || "").split("/").at(-1);
  return type ? type.slice(0, 5).toUpperCase() : "FILE";
}

export function downloadButton(file, options, label = file?.filename || "attachment") {
  const button = node("button", "file-download", "Download");
  button.type = "button";
  button.setAttribute("aria-label", `Download ${label}`);
  const assetId = typeof file?.asset_id === "string" ? file.asset_id : null;
  if (!assetId || file.available === false || options.assets?.[assetId]?.available === false || !options.loadAsset) {
    button.disabled = true;
    diagnostic(options, "file-unavailable", label);
    return button;
  }
  button.addEventListener("click", async () => {
    try {
      const url = await options.loadAsset(assetId, { download: true });
      if (!url || !current(options)) return;
      const link = node("a");
      link.href = url;
      link.download = String(file.filename || label).replace(/[\\/\r\n]/g, "_");
      link.rel = "noopener noreferrer";
      link.click();
    } catch (error) {
      if (current(options)) diagnostic(options, "file-unavailable", label, error);
    }
  });
  return button;
}
