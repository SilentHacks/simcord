/** Workbench drawers, inspector tabs, and modal isolation; app owns state. */
export function initWorkbench({ state, ui }) {
  const inspectorTabs = [...document.querySelectorAll("[data-inspector-tab]")];
  const inspectorPanels = {
    activity: document.getElementById("activity-panel"),
    diagnostics: document.getElementById("diagnostics-panel"),
    capture: document.getElementById("capture-panel"),
  };

  function isMobileWorkbench() {
    return window.matchMedia("(max-width: 760px)").matches;
  }

  function updateWorkbenchIsolation(modalOpen = Boolean(state.modalHandle)) {
    const mobile = isMobileWorkbench();
    if (mobile) ui.shell.classList.remove("messages-closed");
    else ui.shell.classList.remove("messages-open");
    const drawerOpen = ui.shell.classList.contains("messages-open");
    const sidebarClosed = mobile ? !drawerOpen : ui.shell.classList.contains("messages-closed");
    const inspectorOpen = !ui.panel.hidden;
    ui.toolbar.inert = modalOpen;
    ui.sidebar.inert = modalOpen || sidebarClosed || (mobile && inspectorOpen);
    ui.sidebar.setAttribute("aria-hidden", String(modalOpen || sidebarClosed || (mobile && inspectorOpen)));
    ui.panel.inert = modalOpen || !inspectorOpen;
    ui.stage.inert = !modalOpen && mobile && (drawerOpen || inspectorOpen);
    ui.drawerScrim.hidden = !mobile || !drawerOpen || modalOpen;
    ui.messagesToggle.setAttribute("aria-expanded", String(!sidebarClosed));
    ui.inspectorToggle.setAttribute("aria-expanded", String(inspectorOpen));
  }

  function setInspectorTab(name) {
    inspectorTabs.forEach((button) => {
      const selected = button.dataset.inspectorTab === name;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
      inspectorPanels[button.dataset.inspectorTab].hidden = !selected;
    });
  }

  function closeMessagesDrawer(restore = true) {
    if (!ui.shell.classList.contains("messages-open")) return;
    ui.shell.classList.remove("messages-open");
    updateWorkbenchIsolation();
    if (restore) (state.drawerOpener?.isConnected ? state.drawerOpener : ui.messagesToggle).focus({ preventScroll: true });
    state.drawerOpener = null;
  }

  function openInspector(name = "activity", opener = document.activeElement) {
    if (ui.panel.hidden) state.panelOpener = opener instanceof HTMLElement ? opener : ui.inspectorToggle;
    closeMessagesDrawer(false);
    ui.panel.hidden = false;
    ui.shell.classList.add("inspector-open");
    setInspectorTab(name);
    updateWorkbenchIsolation();
    requestAnimationFrame(() => inspectorTabs.find((button) => button.dataset.inspectorTab === name)?.focus());
  }

  function closeInspector(restore = true) {
    if (ui.panel.hidden) return;
    ui.panel.hidden = true;
    ui.shell.classList.remove("inspector-open");
    updateWorkbenchIsolation();
    if (restore) {
      const target = state.panelOpener instanceof HTMLElement && state.panelOpener.isConnected
        && !state.panelOpener.inert ? state.panelOpener : ui.inspectorToggle;
      target.focus({ preventScroll: true });
    }
    state.panelOpener = null;
  }

  function openMessagesDrawer() {
    closeInspector(false);
    if (isMobileWorkbench()) {
      state.drawerOpener = ui.messagesToggle;
      ui.shell.classList.add("messages-open");
      updateWorkbenchIsolation();
      ui.search.focus({ preventScroll: true });
    } else {
      ui.shell.classList.remove("messages-closed");
      updateWorkbenchIsolation();
    }
  }

  ui.messagesToggle.addEventListener("click", () => {
    const closed = isMobileWorkbench()
      ? !ui.shell.classList.contains("messages-open")
      : ui.shell.classList.contains("messages-closed");
    if (closed) openMessagesDrawer();
    else if (isMobileWorkbench()) closeMessagesDrawer();
    else {
      ui.shell.classList.add("messages-closed");
      updateWorkbenchIsolation();
      ui.messagesToggle.focus({ preventScroll: true });
    }
  });
  ui.drawerScrim.addEventListener("click", () => closeMessagesDrawer());
  ui.inspectorToggle.addEventListener("click", () => {
    if (ui.panel.hidden) openInspector();
    else closeInspector();
  });
  ui.captureOpen.addEventListener("click", () => openInspector("capture", ui.captureOpen));
  ui.inspectorClose.addEventListener("click", () => closeInspector());
  inspectorTabs.forEach((button, index) => {
    button.addEventListener("click", () => setInspectorTab(button.dataset.inspectorTab));
    button.addEventListener("keydown", (event) => {
      let next = index;
      if (event.key === "ArrowRight") next = (index + 1) % inspectorTabs.length;
      else if (event.key === "ArrowLeft") next = (index + inspectorTabs.length - 1) % inspectorTabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = inspectorTabs.length - 1;
      else return;
      event.preventDefault();
      setInspectorTab(inspectorTabs[next].dataset.inspectorTab);
      inspectorTabs[next].focus();
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || event.defaultPrevented || state.modalHandle) return;
    if (!ui.panel.hidden) {
      event.preventDefault();
      closeInspector();
    } else if (ui.shell.classList.contains("messages-open")) {
      event.preventDefault();
      closeMessagesDrawer();
    }
  });
  window.addEventListener("resize", () => updateWorkbenchIsolation());
  updateWorkbenchIsolation();
  return { updateWorkbenchIsolation };
}
