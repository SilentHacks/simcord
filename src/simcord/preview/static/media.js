import { icon, iconButton, node, renderIdentityAvatar } from "./dom.js";

const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);
let lightbox;

function current(options, element) {
  return Boolean(element?.isConnected) || !options.isCurrent || options.isCurrent();
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
function diagnoseMemoryLimit(options, assetId) {
  if (options.assets?.[assetId]?.workerMemoryLimited !== false) return;
  options.onDiagnostic?.({
    code: "media-process-memory-limit-unavailable",
    severity: "warning",
    message: "This platform cannot enforce the 512 MiB media-process memory ceiling; adversarial-media safety is not certified.",
    complete: false,
  });
}

function getLightbox() {
  if (lightbox) return lightbox;
  const dialog = node("dialog", "media-lightbox");
  dialog.setAttribute("aria-label", "Image preview");
  const heading = node("header", "media-lightbox-header");
  const identity = node("div", "media-lightbox-identity");
  const controls = node("div", "media-lightbox-controls");
  const zoom = iconButton("zoom", "Zoom image", "media-lightbox-control");
  const actions = node("div", "media-lightbox-actions");
  const close = iconButton("close", "Close image preview", "media-lightbox-control");
  controls.append(zoom, actions, close);
  heading.append(identity, controls);
  const frame = node("div", "media-lightbox-frame");
  frame.tabIndex = 0;
  frame.setAttribute("role", "region");
  frame.setAttribute("aria-label", "Image; use arrow keys to pan when zoomed");
  const canvas = node("div", "media-lightbox-canvas");
  const image = node("img", "media-lightbox-image");
  canvas.append(image);
  frame.append(canvas);
  const position = node("p", "media-lightbox-position");
  position.setAttribute("aria-live", "polite");
  const previous = iconButton("left", "Previous image", "media-lightbox-control media-lightbox-previous");
  const next = iconButton("right", "Next image", "media-lightbox-control media-lightbox-next");
  dialog.append(heading, frame, previous, next, position);
  document.body.append(dialog);

  let items = [];
  let index = 0;
  let opener = null;
  let zoomed = false;
  let observer = null;
  let stage = null;
  let drawGeneration = 0;
  const sizeImage = () => {
    const source = items[index]?.image;
    if (!source) return;
    const ratio = source.naturalWidth / source.naturalHeight;
    const fit = Math.min(source.naturalWidth, frame.clientWidth, frame.clientHeight * ratio);
    const width = zoomed ? Math.max(source.naturalWidth, fit * 2) : fit;
    image.style.width = `${width}px`;
    image.style.height = `${width / ratio}px`;
    canvas.style.width = zoomed ? `${width}px` : "100%";
    canvas.style.height = zoomed ? `${width / ratio}px` : "100%";
    zoom.setAttribute("aria-pressed", String(zoomed));
    zoom.setAttribute("aria-label", zoomed ? "Fit image" : "Zoom image");
    zoom.title = zoomed ? "Fit image" : "Zoom image";
    zoom.replaceChildren(icon(zoomed ? "fit" : "zoom"));
    frame.classList.toggle("is-zoomed", zoomed);
  };
  const place = () => {
    const app = opener?.closest(".preview-app");
    if (!app?.isConnected) { dialog.close(); return; }
    const rect = app.getBoundingClientRect();
    const host = stage.getBoundingClientRect();
    const left = Math.max(0, rect.left, host.left);
    const top = Math.max(0, rect.top, host.top);
    const right = Math.min(innerWidth, rect.right, host.right);
    const bottom = Math.min(innerHeight, rect.bottom, host.bottom);
    if (right <= left || bottom <= top) { dialog.close(); return; }
    Object.assign(dialog.style, { left: `${left}px`, top: `${top}px`, width: `${right - left}px`, height: `${bottom - top}px` });
    sizeImage();
  };
  const draw = () => {
    const item = items[index];
    if (!item) return;
    const generation = ++drawGeneration;
    const isCurrent = () => dialog.open && generation === drawGeneration;
    image.src = item.image.src;
    image.alt = item.label;
    identity.replaceChildren();
    if (item.author) {
      const avatar = renderIdentityAvatar(item.author, { ...item.options, isCurrent }, "media-lightbox-avatar");
      const copy = node("div");
      copy.append(node("strong", "", item.author.name || "Unknown author"));
      const badge = item.author.kind === "application" ? "APP" : item.author.webhook ? "WEBHOOK" : item.author.bot ? "BOT" : null;
      if (badge) copy.append(node("span", "message-app-badge", badge));
      if (item.timestamp) {
        const time = node("time", "", new Intl.DateTimeFormat(item.options.locale || "en-US", {
          timeZone: item.options.timezone || "UTC", hour: "2-digit", minute: "2-digit",
        }).format(new Date(item.timestamp)));
        time.dateTime = item.timestamp;
        copy.append(time);
      }
      identity.append(avatar.element, copy);
    }
    actions.replaceChildren(downloadButton(item.media, item.options, item.label));
    const original = node("a", "media-lightbox-control");
    original.setAttribute("aria-label", "Open original image");
    original.title = "Open original image";
    original.target = "_blank";
    original.rel = "noopener noreferrer";
    original.append(icon("external"));
    // Only validated raster media enters this viewer; never open arbitrary file previews.
    Promise.resolve(item.options.loadAsset(item.media.asset_id, { download: true })).then((url) => {
      if (isCurrent() && typeof url === "string" && url.startsWith("blob:")) {
        original.href = url;
        actions.append(original);
      }
    }).catch((error) => {
      if (isCurrent()) diagnostic(item.options, "file-unavailable", item.label, error);
    });
    const multiple = items.length > 1;
    previous.hidden = !multiple;
    next.hidden = !multiple;
    position.textContent = multiple ? `${index + 1} of ${items.length}` : "";
    zoomed = false;
    sizeImage();
    frame.scrollTo(0, 0);
  };
  const move = (step) => {
    if (items.length < 2) return;
    index = (index + step + items.length) % items.length;
    draw();
  };
  const restore = () => {
    drawGeneration += 1;
    observer?.disconnect();
    observer = null;
    stage?.removeEventListener("scroll", place);
    window.removeEventListener("resize", place);
    image.removeAttribute("src");
    image.alt = "";
    identity.replaceChildren();
    actions.replaceChildren();
    items = [];
    index = 0;
    drag = null;
    const target = opener;
    opener = null;
    stage = null;
    if (target?.isConnected) target.focus({ preventScroll: true });
  };
  previous.addEventListener("click", () => move(-1));
  next.addEventListener("click", () => move(1));
  zoom.addEventListener("click", () => {
    zoomed = !zoomed;
    sizeImage();
    frame.scrollTo(Math.max(0, (canvas.offsetWidth - frame.clientWidth) / 2), Math.max(0, (canvas.offsetHeight - frame.clientHeight) / 2));
    frame.focus({ preventScroll: true });
  });
  close.addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", restore);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog || (!zoomed && (event.target === frame || event.target === canvas))) dialog.close();
  });
  let drag = null;
  frame.addEventListener("pointerdown", (event) => {
    if (!zoomed || event.button !== 0) return;
    drag = { x: event.clientX, y: event.clientY, left: frame.scrollLeft, top: frame.scrollTop };
    frame.setPointerCapture(event.pointerId);
    event.preventDefault();
  });
  frame.addEventListener("pointermove", (event) => {
    if (drag) frame.scrollTo(drag.left + drag.x - event.clientX, drag.top + drag.y - event.clientY);
  });
  frame.addEventListener("pointerup", () => { drag = null; });
  frame.addEventListener("pointercancel", () => { drag = null; });
  image.draggable = false;
  dialog.addEventListener("keydown", (event) => {
    const direction = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
    if (!direction) return;
    if (zoomed && !event.altKey) {
      event.preventDefault();
      frame.scrollBy(direction[0] * 80, direction[1] * 80);
    } else if (items.length > 1 && direction[0]) {
      event.preventDefault();
      move(direction[0]);
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
      stage = trigger.closest(".preview-stage");
      if (!stage) return;
      if (!dialog.open) dialog.showModal();
      place();
      if (!dialog.open) return;
      draw();
      observer = new ResizeObserver(place);
      observer.observe(trigger.closest(".preview-app"));
      observer.observe(stage);
      stage.addEventListener("scroll", place, { passive: true });
      window.addEventListener("resize", place);
      close.focus({ preventScroll: true });
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

function mediaTimeLabel(value) {
  const seconds = Math.max(0, Math.floor(Number(value) || 0));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function renderWaveform(host, values) {
  const samples = Array.isArray(values) ? values : [];
  host.replaceChildren();
  if (!samples.length) {
    host.hidden = true;
    host.removeAttribute("role");
    return;
  }
  host.hidden = false;
  host.setAttribute("role", "img");
  host.setAttribute("aria-label", "Voice message waveform");
  samples.forEach((value) => {
    const bar = node("span", "voice-wave-bar");
    bar.setAttribute("aria-hidden", "true");
    bar.style.setProperty("--wave-height", `${Math.max(1, Math.min(100, Number(value) || 1))}%`);
    host.append(bar);
  });
}

function playerCurrent(player) {
  return player.active && player.wrapper.isConnected;
}

function updatePlayer(player) {
  const { wrapper, element, play, seek, mute, volume, options, kind } = player;
  const label = player.label;
  wrapper.className = `media-player ${kind}-player ${player.className}`;
  wrapper.setAttribute("aria-label", `${label} player`);
  element.setAttribute("aria-label", label);
  play.setAttribute("aria-label", `${element.ended ? "Replay" : !element.paused ? "Pause" : "Play"} ${label}`);
  play.textContent = element.ended ? "Replay" : !element.paused ? "Pause" : "Play";
  seek.setAttribute("aria-label", `Seek ${label}`);
  mute.textContent = element.muted ? "Unmute" : "Mute";
  mute.setAttribute("aria-label", `${element.muted ? "Unmute" : "Mute"} ${label}`);
  volume.setAttribute("aria-label", `Volume ${label}`);
  volume.value = String(element.volume);
  if (kind === "video") element.playsInline = true;
  else renderWaveform(player.waveform, options.assets?.[player.assetId]?.waveform);
  player.updateTime();
}

function renderPlayer(media, className, options, label, assetId, kind) {
  const playerLabel = label || kind;
  const playerClass = className || "";
  const mediaTime = options.mediaTime ?? null;
  const reuseKey = JSON.stringify([assetId, kind, playerClass, mediaTime]);
  const reused = options.mediaPlayerReuse?.get(reuseKey)?.shift();
  if (reused?._previewMediaPlayer) {
    const player = reused._previewMediaPlayer;
    player.options = options;
    player.label = playerLabel;
    player.className = playerClass;
    player.active = true;
    updatePlayer(player);
    return { element: reused, pending: player.pending };
  }

  const wrapper = node("section", `media-player ${kind}-player ${playerClass}`);
  wrapper.dataset.mediaReuseKey = reuseKey;
  wrapper.setAttribute("role", "group");
  wrapper.setAttribute("aria-label", `${playerLabel} player`);
  const element = node(kind, "media-player-native");
  element.preload = "metadata";
  element.controls = false;
  if (kind === "video") {
    element.playsInline = true;
  }
  element.setAttribute("aria-label", playerLabel);
  const waveform = node("div", "media-waveform");
  const controls = node("div", "media-player-controls");
  const play = node("button", "media-player-button", "Play");
  play.type = "button";
  const seek = node("input", "media-player-seek");
  seek.type = "range";
  seek.min = "0";
  seek.max = "0";
  seek.step = "0.01";
  seek.value = "0";
  const time = node("span", "media-player-time", "0:00 / 0:00");
  time.setAttribute("aria-live", "off");
  const mute = node("button", "media-player-button", "Mute");
  mute.type = "button";
  const volume = node("input", "media-player-volume");
  volume.type = "range";
  volume.min = "0";
  volume.max = "1";
  volume.step = "0.05";
  volume.value = "1";
  controls.append(play, seek, time, mute, volume);
  let fullscreen = null;
  if (kind === "video" && document.fullscreenEnabled) {
    fullscreen = node("button", "media-player-button", "Fullscreen");
    fullscreen.type = "button";
    controls.append(fullscreen);
  }
  const status = node("span", "media-player-status", "Loading media…");
  status.setAttribute("role", "status");
  wrapper.append(element);
  if (kind === "audio") wrapper.append(waveform);
  wrapper.append(controls, status);

  const player = {
    wrapper, element, waveform, controls, play, seek, time, mute, volume, status,
    options, label: playerLabel, className: playerClass, assetId, kind, mediaTime,
    active: true, pending: [],
  };
  wrapper._previewMediaPlayer = player;
  player.dispose = () => {
    player.active = false;
    element.pause();
    element.removeAttribute("src");
    element.removeAttribute("poster");
    element.load();
  };

  player.updateTime = () => {
    const duration = Number.isFinite(element.duration) ? element.duration : 0;
    const position = Number.isFinite(element.currentTime) ? element.currentTime : 0;
    seek.max = String(duration);
    seek.value = String(Math.min(position, duration));
    time.textContent = `${mediaTimeLabel(position)} / ${mediaTimeLabel(duration)}`;
    wrapper.style.setProperty("--media-progress", duration ? `${position / duration * 100}%` : "0%");
  };
  const updatePlay = () => {
    if (!playerCurrent(player)) return;
    play.textContent = element.ended ? "Replay" : !element.paused ? "Pause" : "Play";
    play.setAttribute("aria-label", `${play.textContent} ${player.label}`);
  };
  const updateVolume = () => {
    if (!playerCurrent(player)) return;
    mute.textContent = element.muted ? "Unmute" : "Mute";
    mute.setAttribute("aria-label", `${mute.textContent} ${player.label}`);
    volume.value = String(element.volume);
  };
  play.addEventListener("click", () => {
    if (element.ended) element.currentTime = 0;
    if (element.paused) {
      element.play().catch((error) => {
        if (!playerCurrent(player)) return;
        status.textContent = "Playback is unavailable";
        diagnostic(player.options, "media-playback", player.label, error);
      });
    } else element.pause();
  });
  seek.addEventListener("input", () => {
    if (Number.isFinite(element.duration)) element.currentTime = Number(seek.value);
  });
  mute.addEventListener("click", () => {
    element.muted = !element.muted;
    updateVolume();
  });
  volume.addEventListener("input", () => {
    element.volume = Number(volume.value);
    if (element.volume > 0) element.muted = false;
    updateVolume();
  });
  if (fullscreen) {
    fullscreen.addEventListener("click", () => {
      wrapper.requestFullscreen?.().catch((error) => diagnostic(player.options, "media-fullscreen", player.label, error));
    });
  }
  ["durationchange", "timeupdate", "seeked"].forEach((name) => element.addEventListener(name, player.updateTime));
  ["play", "pause", "ended"].forEach((name) => element.addEventListener(name, updatePlay));
  element.addEventListener("volumechange", updateVolume);
  updatePlayer(player);

  player.pending.push(Promise.resolve(options.loadAsset(assetId)).then(async (url) => {
    if (!playerCurrent(player)) return;
    if (typeof url !== "string" || !url.startsWith("blob:")) throw new Error("asset is not a local blob URL");
    const metadata = new Promise((resolve, reject) => {
      element.addEventListener("loadedmetadata", resolve, { once: true });
      element.addEventListener("error", () => reject(new Error("browser cannot decode this validated media")), { once: true });
    });
    element.src = url;
    element.load();
    await metadata;
    if (!playerCurrent(player)) return;
    if (player.mediaTime !== null) {
      const duration = Number.isFinite(element.duration) ? element.duration : 0;
      const selected = Math.min(Math.max(0, Number(player.mediaTime) || 0), duration);
      if (Math.abs(element.currentTime - selected) > 0.01) {
        const seeked = new Promise((resolve, reject) => {
          element.addEventListener("seeked", resolve, { once: true });
          element.addEventListener("error", () => reject(new Error("media seek failed")), { once: true });
        });
        element.currentTime = selected;
        await seeked;
      }
      element.pause();
      player.options.onMediaCaptureTime?.(assetId, Number(element.currentTime) || 0);
    }
    const currentOptions = player.options;
    const manifest = currentOptions.assets?.[assetId];
    diagnoseMemoryLimit(currentOptions, assetId);
    if (kind === "audio") renderWaveform(waveform, manifest?.waveform);
    status.textContent = "";
    player.updateTime();
    updatePlay();
  }).catch((error) => {
    if (!playerCurrent(player)) return;
    diagnostic(player.options, "media-unavailable", player.label, error);
    player.active = false;
    wrapper.replaceWith(node("div", "media-unavailable", `${player.label} unavailable`));
  }));
  if (kind === "video") {
    player.pending.push(
      Promise.resolve(options.loadAsset(assetId, { poster: true })).then((url) => {
        if (playerCurrent(player) && typeof url === "string" && url.startsWith("blob:")) element.poster = url;
      }).catch(() => {}),
    );
  }
  return { element: wrapper, pending: player.pending };
}

let lottieRuntime;

function ensureLottieRuntime() {
  if (window.bodymovin?.loadAnimation) return Promise.resolve();
  if (!lottieRuntime) {
    lottieRuntime = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "/vendor/lottie/5.12.2/lottie_light_canvas.min.js";
      script.onload = () => window.bodymovin?.loadAnimation
        ? resolve()
        : reject(new Error("bundled Lottie canvas runtime did not initialize"));
      script.onerror = () => reject(new Error("bundled Lottie canvas runtime could not be loaded"));
      document.head.append(script);
    });
  }
  return lottieRuntime;
}

function renderLottie(media, options, label, assetId) {
  const stage = node("div", "media-lottie");
  stage.setAttribute("role", "img");
  stage.setAttribute("aria-label", label || "Animated sticker");
  const canvasHost = node("div", "media-lottie-canvas");
  stage.append(canvasHost);
  let animation = null;
  let observer = null;
  const dispose = () => {
    observer?.disconnect();
    observer = null;
    animation?.destroy();
    animation = null;
  };
  const pending = [Promise.resolve(options.loadAsset(assetId)).then(async (url) => {
    if (!current(options, stage)) return;
    await ensureLottieRuntime();
    const response = await fetch(url);
    if (!response.ok) throw new Error("validated Lottie data is unavailable");
    const data = await response.json();
    animation = window.bodymovin.loadAnimation({
      container: canvasHost,
      renderer: "canvas",
      loop: !options.reducedMotion,
      autoplay: false,
      animationData: data,
    });
    animation.setSubframe(false);
    await new Promise((resolve, reject) => {
      const timeout = window.setTimeout(() => reject(new Error("Lottie canvas render timed out")), 10_000);
      animation.addEventListener("DOMLoaded", () => {
        window.clearTimeout(timeout);
        resolve();
      });
      animation.addEventListener("data_failed", () => {
        window.clearTimeout(timeout);
        reject(new Error("Lottie canvas could not render this composition"));
      });
    });
    if (!current(options, stage)) {
      dispose();
      return;
    }
    const fps = Number(data.fr) || 1;
    const first = Number(data.ip) || 0;
    const end = Number(data.op) || first + 1;
    const duration = Math.max(0, (end - first) / fps);
    const capturing = Number.isFinite(options.mediaTime);
    const requested = capturing ? Math.max(0, Number(options.mediaTime) || 0) : 0;
    const frame = Math.max(first, Math.min(Math.ceil(end) - 1, first + Math.floor(Math.min(requested, duration) * fps)));
    animation.goToAndStop(frame, true);
    options.onMediaCaptureTime?.(assetId, Math.max(0, (frame - first) / fps));
    diagnoseMemoryLimit(options, assetId);
    if (!options.reducedMotion && !capturing) animation.play();
    observer = new MutationObserver(() => {
      if (!stage.isConnected) dispose();
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
  }).catch((error) => {
    dispose();
    if (!current(options, stage)) return;
    diagnostic(options, "lottie-unavailable", label, error);
    stage.replaceWith(node("div", "media-unavailable", `${label || "Sticker"} unavailable`));
  })];
  return { element: stage, pending };
}


export function renderMedia(media, className, options, label, group = options.lightboxGroup) {
  const assetId = typeof media?.asset_id === "string" ? media.asset_id : null;
  const manifest = assetId ? options.assets?.[assetId] : null;
  const mediaType = String(media?.content_type || manifest?.contentType || "").toLowerCase();
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
  if (mediaType.startsWith("audio/") || mediaType.startsWith("video/")) {
    return renderPlayer(media, className, options, label, assetId, mediaType.startsWith("audio/") ? "audio" : "video");
  }
  if (mediaType === "application/json" || manifest?.mediaKind === "lottie") {
    return renderLottie(media, options, label, assetId);
  }
  if (mediaType && !IMAGE_TYPES.has(mediaType)) {
    options.onDiagnostic?.({
      code: "unsupported-media-type",
      severity: "warning",
      message: `${label || "Media"} type ${mediaType} cannot be rendered inline`,
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
  const entry = { image, label: image.alt, media: { ...media, asset_id: assetId }, author: options.messageAuthor, timestamp: options.messageTimestamp, options };
  const lightboxGroup = group || { items: [] };
  lightboxGroup.items.push(entry);
  trigger.addEventListener("click", () => {
    if (image.src.startsWith("blob:") && current(options, trigger)) {
      getLightbox().open(lightboxGroup, entry, trigger);
    }
  });
  const capture = options.mediaTime !== null && options.mediaTime !== undefined;
  const pending = [Promise.resolve(options.loadAsset(
    assetId,
    capture ? { capture: true, mediaTime: options.mediaTime } : {},
  )).then(async (url) => {
    if (!current(options, trigger)) return;
    if (typeof url !== "string" || !url.startsWith("blob:")) throw new Error("asset is not a local blob URL");
    image.src = url;
    if (image.decode) await image.decode();
    else if (!image.complete) {
      await new Promise((resolve, reject) => {
        image.addEventListener("load", resolve, { once: true });
        image.addEventListener("error", reject, { once: true });
      });
    }
    if (!current(options, trigger)) return;
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
      diagnoseMemoryLimit(options, assetId);
    }
    image.height = displayHeight;
    trigger.disabled = false;
  }).catch((error) => {
    if (!current(options, trigger)) return;
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
      const content = wrapper.firstElementChild;
      if (content) {
        content.inert = false;
        content.removeAttribute("aria-hidden");
      }
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
  if (media?.spoiler) {
    result.element.classList.add("spoiler-media");
    result.element.classList.toggle("is-compact", className.includes("thumbnail"));
  }
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
  const button = iconButton("download", `Download ${label}`, "file-download");
  const assetId = typeof file?.asset_id === "string" ? file.asset_id : null;
  if (!assetId || file.available === false || options.assets?.[assetId]?.available === false || !options.loadAsset) {
    button.disabled = true;
    diagnostic(options, "file-unavailable", label);
    return button;
  }
  button.addEventListener("click", async () => {
    try {
      const url = await options.loadAsset(assetId, { download: true });
      if (!url || !current(options, button)) return;
      const link = node("a");
      link.href = url;
      link.download = String(file.filename || label).replace(/[\\/\r\n]/g, "_");
      link.rel = "noopener noreferrer";
      link.click();
    } catch (error) {
      if (current(options, button)) diagnostic(options, "file-unavailable", label, error);
    }
  });
  return button;
}
