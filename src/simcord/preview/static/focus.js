/** Native focus retention, including descendants of rebuilt modal controls. */
export function createFocus() {
  /** @type {HTMLElement | null} */
  let element = null;
  /** @type {string | null} */
  let key = null;
  /** @type {string | null} */
  let identity = null;
  let descendant = -1;
  let visible = false;
  /** @type {{start: number, end: number} | null} */
  let selection = null;
  const selector = "a[href], button, input, textarea, select, [tabindex]";
  /** @param {HTMLElement} item */
  function identify(item) {
    return [item.tagName, item.id, item.getAttribute("name"), item.getAttribute("type"), item.getAttribute("value"),
      item.getAttribute("aria-label"), item instanceof HTMLButtonElement ? item.textContent : null].join("\u0000");
  }
  function remember() {
    const active = document.activeElement;
    element = active instanceof HTMLElement ? active : null;
    const owner = element?.closest("[data-control-key]");
    descendant = owner && element ? [...owner.querySelectorAll(selector)].indexOf(element) : -1;
    identity = element ? identify(element) : null;
    key = owner instanceof HTMLElement ? owner.dataset.controlKey || null : element?.dataset.controlKey || null;
    visible = Boolean(element && (element.matches(":focus-visible") || element.classList.contains("focus-visible")));
    selection = (active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement) && active.selectionStart !== null
      ? { start: active.selectionStart, end: active.selectionEnd ?? active.selectionStart } : null;
  }
  /** @param {string | null} targetKey */
  function target(targetKey) {
    if (!targetKey) return null;
    const owners = [...document.querySelectorAll("[data-control-key]")].filter((item) => item instanceof HTMLElement && item.dataset.controlKey === targetKey);
    const owner = owners.find((item) => item instanceof HTMLElement && item.tabIndex >= 0) || owners[0];
    if (!(owner instanceof HTMLElement)) return null;
    return owner.matches("input, textarea, select, button, [role=listbox]") ? owner
      : owner.querySelector("input, textarea, select, button, [role=listbox]") || owner;
  }
  /** @param {Element | null | undefined} item @returns {item is HTMLElement} */
  function canFocus(item) { return item instanceof HTMLElement && !item.closest("[inert]") && !item.matches(":disabled") && item.checkVisibility(); }
  /** @param {string | null} [targetKey] */
  function restore(targetKey = key) {
    let next = target(targetKey);
    if (targetKey === key && element?.isConnected) next = element;
    else if (targetKey === key && descendant >= 0) {
      const owner = [...document.querySelectorAll("[data-control-key]")].find((item) => item instanceof HTMLElement && item.dataset.controlKey === targetKey);
      const descendants = [...(owner?.querySelectorAll(selector) || [])];
      next = descendants.find((item) => item instanceof HTMLElement && identify(item) === identity) || descendants[descendant] || next;
    }
    if (next?.getAttribute("role") === "listbox") next = next.querySelector(".select-option.is-highlighted") || next.closest(".preview-select")?.querySelector(".select-trigger") || null;
    if (!canFocus(next)) next = target(targetKey);
    if (!canFocus(next)) return false;
    next.focus({ preventScroll: true });
    if (selection && (next instanceof HTMLInputElement || next instanceof HTMLTextAreaElement)) {
      try { next.setSelectionRange(selection.start, selection.end); } catch {}
    }
    if (visible && !next.matches(":focus-visible")) {
      const focused = next;
      focused.classList.add("focus-visible");
      focused.addEventListener("blur", () => focused.classList.remove("focus-visible"), { once: true });
    }
    return true;
  }
  return { remember, restore, report: () => Object.freeze({ key }),
    /** @param {string | null} value */
    setKey: (value) => { key = value; },
    reset: () => { element = null; key = null; identity = null; descendant = -1; selection = null; visible = false; },
  };
}
