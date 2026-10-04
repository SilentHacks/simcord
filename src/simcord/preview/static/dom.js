export function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

const ICON_PATHS = {
  messages: "M4 5h16v11H8l-4 4V5Z",
  refresh: "M20 7v5h-5M4 17v-5h5M19 12a7 7 0 0 0-12-5L4 10m1 2a7 7 0 0 0 12 5l3-3",
  capture: "M4 7h4l2-3h4l2 3h4v13H4V7Zm12 6a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
  inspector: "M4 4h16v16H4V4Zm10 0v16M17 8h0m0 4h0",
  send: "m3 3 18 9-18 9 4-9-4-9Zm4 9h14",
  check: "m5 12 4 4L19 6",
  close: "m6 6 12 12M6 18 18 6",
  download: "M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5",
  external: "M14 3h7v7m0-7L10 14M10 3H3v18h18v-7",
  zoom: "M15 15a7 7 0 1 0-10-10 7 7 0 0 0 10 10Zm0 0 6 6M10 7v6m-3-3h6",
  fit: "M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5",
  left: "m15 5-7 7 7 7",
  right: "m9 5 7 7-7 7",
  code: "m8 7-5 5 5 5m8-10 5 5-5 5",
  reply: "m9 5-6 6 6 6M3 11h10a7 7 0 0 1 7 7",
  edit: "m16 3 5 5-12 12H4v-5L16 3Z",
  delete: "M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7",
  pin: "m9 3 12 12-3 1-3 4-4-4-7-1 4-3 1-3ZM3 21l5-5",
};

export function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.classList.add("ui-icon");
  const path = document.createElementNS(svg.namespaceURI, "path");
  path.setAttribute("d", ICON_PATHS[name]);
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "1.8");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  svg.append(path);
  return svg;
}

export function iconButton(name, label, className) {
  const button = node("button", className);
  button.type = "button";
  button.setAttribute("aria-label", label);
  button.title = label;
  button.append(icon(name));
  return button;
}

const PERSON_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm0 2c-4.4 0-8 2.2-8 5v1h16v-1c0-2.8-3.6-5-8-5z"/></svg>';

export function renderIdentityAvatar(identity, options = {}, className = "entity-avatar") {
  const classes = [className];
  if (identity?.bot) classes.push("avatar-bot");
  else if (identity?.avatar_kind === "default") classes.push("avatar-default");
  else classes.push("avatar-user");
  const avatar = node("span", classes.join(" "));
  const pending = [];
  const assetId = typeof identity?.avatar === "string" ? identity.avatar : null;
  const manifest = assetId ? options.assets?.[assetId] : null;
  const available = identity?.avatar_available !== false && manifest?.available !== false;
  if (assetId && available && options.loadAsset) {
    const image = node("img", "entity-avatar-img");
    image.alt = "";
    const task = Promise.resolve(options.loadAsset(assetId)).then((url) => {
      if (options.isCurrent && !options.isCurrent()) return;
      image.src = url;
      return image.decode ? image.decode() : undefined;
    }).catch((error) => {
      if (!options.isCurrent || options.isCurrent()) {
        if (identity?.avatar_kind !== "default") {
          options.onDiagnostic?.({ code: "avatar-unavailable", severity: "warning", message: "Avatar failed to load or decode", detail: String(error), complete: false });
        }
      }
    });
    pending.push(task);
    avatar.append(image);
  } else if (identity?.avatar_kind !== "default" && assetId) {
    options.onDiagnostic?.({ code: "avatar-unavailable", severity: "warning", message: "Avatar is unavailable offline", complete: false });
  }
  if (!avatar.childNodes.length) avatar.insertAdjacentHTML("beforeend", PERSON_SVG);
  return { element: avatar, pending };
}

export function presenceDot(identity, className = "entity-presence") {
  const presence = identity?.presence;
  if (!presence || !["online", "idle", "dnd", "offline"].includes(presence)) return null;
  const dot = node("span", `${className} presence-${presence}`);
  dot.title = presence;
  return dot;
}

export function applyRoleColor(element, identity) {
  const color = Number(identity?.role_color ?? 0) >>> 0;
  if (color) element.style.color = `#${color.toString(16).padStart(6, "0")}`;
  return element;
}
