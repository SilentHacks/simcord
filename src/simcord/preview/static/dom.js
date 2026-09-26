export function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
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
