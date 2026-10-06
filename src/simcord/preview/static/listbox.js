/**
 * Build keyboard navigation for a role=listbox element. setItems accepts an
 * array of already-rendered option HTMLElements, keeping rendering caller-owned.
 */
export function createListbox({
  list,
  owner,
  idPrefix,
  onActivate,
  onCommit,
  onNavigate,
  onEscape,
  isSelected,
  activeClass,
  expanded,
  wrap = false,
  commitOnTab = false,
  closeOnEscape = false,
  activationKeys = [],
}) {
  let items = [];
  let index = -1;
  const activeClassName = activeClass || "";

  if (!list.id) list.id = `${idPrefix}-listbox`;
  owner.setAttribute("aria-controls", list.id);
  if (expanded !== undefined) owner.setAttribute("aria-expanded", String(expanded));
  else if (!owner.hasAttribute("aria-expanded")) owner.setAttribute("aria-expanded", "false");

  const isDisabled = (item) => item.matches(":disabled, [aria-disabled='true'], .is-disabled");
  const isItemSelected = (item, itemIndex) => typeof isSelected === "function"
    ? isSelected(item, itemIndex)
    : itemIndex === index;

  function sync() {
    items.forEach((item, itemIndex) => {
      if (!item.id) item.id = `${idPrefix}-${itemIndex}`;
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", String(Boolean(isItemSelected(item, itemIndex))));
      if (activeClassName) item.classList.toggle(activeClassName, itemIndex === index);
    });
    const active = items[index];
    if (active) owner.setAttribute("aria-activedescendant", active.id);
    else owner.removeAttribute("aria-activedescendant");
  }

  function setActive(nextIndex, { scroll = true } = {}) {
    const next = Number.isInteger(nextIndex) && nextIndex >= 0 && nextIndex < items.length
      && !isDisabled(items[nextIndex])
      ? nextIndex
      : -1;
    index = next;
    sync();
    if (scroll && items[index]) items[index].scrollIntoView?.({ block: "nearest" });
    return index;
  }

  function notifyNavigation() {
    if (items[index]) onNavigate?.(items[index], index);
  }

  function move(delta) {
    if (!items.length || !Number.isFinite(delta) || delta === 0) return index;
    const direction = delta > 0 ? 1 : -1;
    const steps = Math.abs(Math.trunc(delta));
    for (let step = 0; step < steps; step += 1) {
      let candidate = index < 0 ? (direction > 0 ? 0 : items.length - 1) : index + direction;
      while (candidate >= 0 && candidate < items.length && isDisabled(items[candidate])) candidate += direction;
      if (wrap && (candidate < 0 || candidate >= items.length)) {
        candidate = direction > 0 ? 0 : items.length - 1;
        while (candidate >= 0 && candidate < items.length && isDisabled(items[candidate])) candidate += direction;
      }
      if (candidate < 0 || candidate >= items.length) break;
      setActive(candidate);
    }
    notifyNavigation();
    return index;
  }

  function activeItem() {
    return items[index] || null;
  }

  function commit() {
    if (typeof onCommit !== "function") return false;
    onCommit(activeItem(), index);
    return true;
  }

  function handleKey(event) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      move(event.key === "ArrowDown" ? 1 : -1);
      return true;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      const direction = event.key === "Home" ? 1 : -1;
      let next = event.key === "Home" ? 0 : items.length - 1;
      while (next >= 0 && next < items.length && isDisabled(items[next])) next += direction;
      const previous = index;
      setActive(next);
      if (previous !== index) notifyNavigation();
      return true;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      commit();
      return true;
    }
    if (event.key === "Tab" && commitOnTab) {
      event.preventDefault();
      commit();
      return true;
    }
    if (event.key === "Escape" && closeOnEscape) {
      event.preventDefault();
      onEscape?.();
      return true;
    }
    if (activationKeys.includes(event.key)) {
      event.preventDefault();
      if (activeItem()) onActivate?.(activeItem(), index, event);
      return true;
    }
    return false;
  }

  function setItems(nextItems) {
    items = Array.from(nextItems || []).filter((item) => item instanceof HTMLElement);
    index = -1;
    sync();
  }

  function clear() {
    index = -1;
    sync();
  }

  function setExpanded(value) {
    owner.setAttribute("aria-expanded", String(Boolean(value)));
  }

  return {
    setItems,
    move,
    setActive,
    activeIndex: () => index,
    activeItem,
    commit,
    handleKey,
    setExpanded,
    clear,
  };
}
