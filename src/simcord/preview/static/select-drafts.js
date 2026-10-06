export function initSelectDrafts({ state, rememberFocus, restoreFocus, localRender, queueCandidateQuery, dispatch, clearModalValidation }) {
  function currentDrafts(key) {
    return state.modalDrafts.has(key) || (state.dropdown?.key === key && state.dropdown.modal)
      ? state.modalDrafts
      : state.drafts;
  }

  function initDraft(key, value, drafts = currentDrafts(key)) {
    if (!drafts.has(key)) drafts.set(key, value);
  }

  function initSelectDraft(key, value, drafts = currentDrafts(key)) {
    initDraft(key, value, drafts);
    const current = drafts.get(key);
    if (Array.isArray(current)) {
      state.selectDrafts.set(key, current.map(String));
      if (state.snapshot?.candidates?.[key] && !state.selectValidationRevisions.has(key)) {
        state.selectValidationRevisions.set(key, state.publishedRevision);
        state.selectVerifiedValues.set(key, current.map(String));
      }
    }
  }

  function identityEntries(key) {
    return [...(state.candidateIdentities.get(key)?.values() || [])];
  }
  function reconcileCandidateSelection(key, selected, requested, revision) {
    const authorized = new Map(selected.map((entry) => [String(entry.value ?? entry.id), entry]));
    const values = requested.filter((value) => authorized.has(String(value))).map(String);
    currentDrafts(key).set(key, values);
    state.selectDrafts.set(key, values);
    const identities = new Map(values.map((id) => [id, authorized.get(id)]));
    if (identities.size) state.candidateIdentities.set(key, identities);
    else state.candidateIdentities.delete(key);
    state.selectValidationRevisions.set(key, Number(revision));
    state.selectVerifiedValues.set(key, values);
    state.pendingSelectValidations.delete(key);
    if (values.length !== requested.length) {
      state.selectStatuses.set(key, {
        fingerprint: JSON.stringify(["authorization", values]),
        message: "One or more selected options are no longer available.",
        authorization: true,
      });
    } else if (state.selectStatuses.get(key)?.authorization) {
      state.selectStatuses.delete(key);
    }
  }
  function clearModalDrafts() {
    for (const [timerKey, timer] of state.selectQueryTimers) {
      if (timerKey.includes("modal:")) {
        clearTimeout(timer);
        state.selectQueryTimers.delete(timerKey);
      }
    }
    for (const key of state.modalDrafts.keys()) {
      state.selectDrafts.delete(key);
      state.selectStatuses.delete(key);
      state.candidateIdentities.delete(key);
      state.candidateQueries.delete(key);
      state.selectValidationRevisions.delete(key);
      state.selectVerifiedValues.delete(key);
      state.pendingSelectValidations.delete(key);
    }
    state.modalDrafts.clear();
    state.modalTouched.clear();
  }

  function openDropdown(key, selected, multi, minimum, maximum, highlight, entries = [], modal = false) {
    rememberFocus();
    if (state.dropdown?.key === key) {
      if (multi) commitDropdown(key);
      else cancelDropdown(key);
      return;
    }
    if (state.dropdown) {
      const previousKey = state.dropdown.key;
      if (state.dropdown.multi) {
        if (!commitDropdown(previousKey)) return;
      } else cancelDropdown(previousKey);
    }
    const firstAvailable = entries.find((entry) => (
      !multi || selected.includes(String(entry.value ?? entry.id ?? "")) || selected.length < maximum
    ));
    state.dropdown = {
      key,
      selected: [...selected],
      multi,
      minimum,
      maximum,
      modal,
      optional: modal && [...document.querySelectorAll(".preview-select")]
        .find((item) => item.dataset.controlKey === key)?.dataset.optional === "true",
      highlight: highlight ?? selected[0] ?? firstAvailable?.value ?? firstAvailable?.id ?? null,
    };
    const descriptor = state.snapshot?.candidates?.[key];
    if (descriptor) queueCandidateQuery(key, state.candidateQueries.get(key) ?? descriptor.query ?? "", null, modal ? state.modalHandle : null);
    localRender(true);
    fitOpenDropdowns(true);
    state.focusKey = key;
    const trigger = [...document.querySelectorAll(".select-trigger")].find(
      (item) => item.dataset.controlKey === key,
    );
    trigger?.focus();
  }

  function announceSelectStatus(key, fingerprint, message) {
    if (state.selectStatuses.get(key)?.fingerprint === fingerprint) return;
    state.selectStatuses.set(key, { fingerprint, message });
    const wrap = [...document.querySelectorAll(".preview-select")].find((item) => item.dataset.controlKey === key);
    const status = wrap?.querySelector(".select-guidance");
    if (status) { status.textContent = message; status.hidden = false; }
  }

  function announceInvalidSelection(key, values, minimum, maximum) {
    const fingerprint = JSON.stringify([values, minimum, maximum]);
    const message = values.length < minimum
      ? `Choose at least ${minimum} option${minimum === 1 ? "" : "s"}.`
      : `Choose no more than ${maximum} options.`;
    announceSelectStatus(key, fingerprint, message);
    const status = state.selectStatuses.get(key);
    if (status) status.invalid = true;
    const wrap = [...document.querySelectorAll(".preview-select")].find((item) => item.dataset.controlKey === key);
    wrap?.querySelector(".select-trigger")?.setAttribute("aria-invalid", "true");
  }

  function updateDraft(key, value, multi, minimum, maximum, selected, entry) {
    const drafts = currentDrafts(key);
    const current = Array.isArray(drafts.get(key)) ? [...drafts.get(key)] : [...selected];
    const id = String(value);
    const next = multi ? (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]) : [id];
    if (next.length > maximum) {
      announceInvalidSelection(key, next, minimum, maximum);
      return;
    }
    drafts.set(key, next);
    state.selectDrafts.set(key, [...next]);
    if (state.snapshot?.candidates?.[key]) {
      const identities = state.candidateIdentities.get(key) || new Map();
      if (entry && next.includes(id)) identities.set(id, entry);
      for (const candidateId of identities.keys()) {
        if (!next.includes(candidateId)) identities.delete(candidateId);
      }
      if (identities.size) state.candidateIdentities.set(key, identities);
      else state.candidateIdentities.delete(key);
    }
    state.selectStatuses.delete(key);
    if (state.dropdown) state.dropdown.highlight = id;
    localRender(true);
    fitOpenDropdowns(true);
  }

  function navigateDropdown(key, highlight) {
    if (state.dropdown?.key !== key) return;
    state.dropdown.highlight = highlight;
    localRender(true);
    fitOpenDropdowns(true);
  }

  function clearSelection(key, minimum = 1) {
    const modal = state.modalDrafts.has(key) || (state.dropdown?.key === key && state.dropdown.modal);
    if (modal) {
      clearModalValidation();
      state.modalTouched.add(key);
    }
    currentDrafts(key).set(key, []);
    state.selectDrafts.set(key, []);
    if (state.snapshot?.candidates?.[key]) {
      state.selectValidationRevisions.set(key, state.publishedRevision);
      state.selectVerifiedValues.set(key, []);
    }
    state.pendingSelectValidations.delete(key);
    state.selectStatuses.delete(key);
    state.candidateIdentities.delete(key);
    if (state.dropdown?.key === key) state.dropdown = null;
    localRender(true);
    if (!modal && minimum <= 0) dispatch("select", { control_key: key, values: [] });
  }

  function commitDropdown(key) {
    const dropdown = state.dropdown;
    if (!dropdown || dropdown.key !== key) return false;
    const values = [...(currentDrafts(key).get(key) || [])].map(String);
    if (state.pendingAction) {
      announceSelectStatus(
        key,
        JSON.stringify(["pending", state.pendingAction.requestId, values]),
        "Wait for the current action to finish before applying.",
      );
      return false;
    }
    if ((values.length < dropdown.minimum && !(dropdown.optional && !values.length)) || values.length > dropdown.maximum) {
      announceInvalidSelection(key, values, dropdown.minimum, dropdown.maximum);
      return false;
    }
    const previous = dropdown.selected || [];
    const changed = values.length !== previous.length || values.some((value, index) => value !== previous[index]);
    state.selectStatuses.delete(key);
    state.dropdown = null;
    const wrap = [...document.querySelectorAll(".preview-select")].find((item) => item.dataset.controlKey === key);
    const list = wrap?.querySelector(".select-list");
    const restoreTrigger = dropdown.modal || list?.contains(document.activeElement);
    const trigger = wrap?.querySelector(".select-trigger");
    if (list) {
      if (typeof list.hidePopover === "function" && list.matches(":popover-open")) list.hidePopover();
      list.hidden = true;
    }
    if (trigger) {
      trigger.setAttribute("aria-expanded", "false");
      trigger.removeAttribute("aria-activedescendant");
    } else localRender(true);
    wrap?.classList.remove("is-open", "opens-up");
    if (restoreTrigger) {
      state.focusKey = key;
      restoreFocus(key);
    }
    if (!dropdown.modal && !state.modalDrafts.has(key) && changed) dispatch("select", { control_key: key, values });
    return true;
  }

  function cancelDropdown(key) {
    const dropdown = state.dropdown;
    if (!dropdown || dropdown.key !== key) return;
    const restoreTrigger = [...document.querySelectorAll(".preview-select")].some(
      (element) => element.dataset.controlKey === key && element.contains(document.activeElement),
    );
    const values = [...dropdown.selected];
    currentDrafts(key).set(key, values);
    state.selectDrafts.set(key, values);
    state.selectStatuses.delete(key);
    const identities = state.candidateIdentities.get(key);
    if (identities) for (const candidateId of identities.keys()) {
      if (!values.includes(candidateId)) identities.delete(candidateId);
    }
    state.dropdown = null;
    localRender(true);
    if (restoreTrigger) {
      state.focusKey = key;
      restoreFocus(key);
    }
  }

  function fitOpenDropdowns(scrollHighlighted = false) {
    const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
    const viewportHeight = document.documentElement.clientHeight || window.innerHeight;
    for (const wrap of document.querySelectorAll(".preview-select.is-open")) {
      const trigger = wrap.querySelector(".select-trigger");
      const list = wrap.querySelector(".select-list");
      if (!trigger || !list) continue;
      if (typeof list.showPopover === "function" && !list.matches(":popover-open")) list.showPopover();
      const rect = trigger.getBoundingClientRect();
      const modal = wrap.closest(".modal-dialog");
      const boundary = modal?.getBoundingClientRect();
      const footer = modal?.querySelector(".modal-actions")?.getBoundingClientRect();
      const top = Math.max(0, boundary?.top || 0);
      const bottom = Math.min(viewportHeight, footer?.top ?? viewportHeight);
      if (rect.bottom <= 0 || rect.top >= viewportHeight) {
        cancelDropdown(wrap.dataset.controlKey);
        continue;
      }
      const gutter = 8, gap = 4;
      const width = Math.max(0, Math.min(rect.width, viewportWidth - gutter * 2));
      const left = Math.max(gutter, Math.min(rect.left, viewportWidth - width - gutter));
      const below = Math.max(0, bottom - rect.bottom - gap - gutter);
      const above = Math.max(0, rect.top - top - gap - gutter);
      const desired = Math.min(list.scrollHeight, 220);
      const opensUp = below < desired && above > below;
      const available = opensUp ? above : below;
      const height = Math.min(220, available);
      list.style.left = `${left}px`;
      list.style.top = `${opensUp ? Math.max(top + gutter, rect.top - gap - height) : Math.max(top + gutter, Math.min(bottom - gutter - height, rect.bottom + gap))}px`;
      list.style.width = `${width}px`;
      list.style.maxHeight = `${height}px`;
      wrap.classList.toggle("opens-up", opensUp);
      const active = list.querySelector(".select-option.is-highlighted");
      if (active && scrollHighlighted === true) {
        if (active.offsetTop < list.scrollTop) list.scrollTop = active.offsetTop;
        else if (active.offsetTop + active.offsetHeight > list.scrollTop + list.clientHeight) {
          list.scrollTop = active.offsetTop + active.offsetHeight - list.clientHeight;
        }
      }
    }
  }
  return { initSelectDraft, identityEntries, reconcileCandidateSelection, clearModalDrafts, openDropdown, updateDraft, navigateDropdown, clearSelection, commitDropdown, cancelDropdown, fitOpenDropdowns };
}
