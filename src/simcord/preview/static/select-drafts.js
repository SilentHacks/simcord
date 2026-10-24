/** @import {Snapshot, AdmittedAction, ActionBody, DraftValue, Dropdown, Identity, CandidateDescriptor, RenderOptions} from "./preview-types" */
/** @typedef {{pendingAction: AdmittedAction | null, modalHandle: string | null, pinnedCapture: boolean}} SelectionContext */
/** Own select identities/validation, modal values, query debounce, and listbox edits.
 * @param {{rememberFocus: () => void, restoreFocus: (key?: string | null) => boolean, onRender: (force: boolean) => void,
 * queueQuery: (key: string, kind: string, body: ActionBody) => void, queryPending: (control: string, key: string) => boolean,
 * dispatch: (kind: string, body: ActionBody) => void, clearModalValidation: () => void,
 * setFocusKey: (key: string | null) => void, changed: (report: {dropdown: Readonly<Dropdown> | null}) => void}} options
 */
export function initSelectDrafts({ rememberFocus, restoreFocus, onRender, queueQuery, queryPending, dispatch, clearModalValidation, setFocusKey, changed }) {
  /** @type {SelectionContext & {blocked: boolean, snapshot: Snapshot | null, publishedRevision: number, dropdown: Dropdown | null,
   * drafts: Map<string, DraftValue>, modalDrafts: Map<string, DraftValue>, modalTouched: Set<string>,
   * selectDrafts: Map<string, string[]>, selectStatuses: Map<string, {fingerprint: string, message: string, authorization?: boolean, invalid?: boolean}>,
   * candidateIdentities: Map<string, Map<string, Identity>>, candidateQueries: Map<string, string>,
   * selectValidationRevisions: Map<string, number>,
   * pendingSelectValidations: Map<string, number>, selectQueryTimers: Map<string, number>, candidateFingerprints: Map<string, string>}} */
  const state = {
    snapshot: null, publishedRevision: 0, pendingAction: null, modalHandle: null, pinnedCapture: false,
    blocked: false,
    drafts: new Map(), modalDrafts: new Map(), modalTouched: new Set(), dropdown: null,
    selectDrafts: new Map(), selectStatuses: new Map(), candidateIdentities: new Map(), candidateQueries: new Map(),
    selectValidationRevisions: new Map(), pendingSelectValidations: new Map(),
    selectQueryTimers: new Map(), candidateFingerprints: new Map(),
  };
  /** @param {string} selector */
  function elements(selector) { return [...document.querySelectorAll(selector)].filter((item) => item instanceof HTMLElement); }
  /** @param {ParentNode} root @param {string} selector */
  function query(root, selector) { const found = root.querySelector(selector); return found instanceof HTMLElement ? found : null; }
  document.addEventListener("pointerdown", (event) => {
    const wrap = event.target instanceof Element ? event.target.closest(".preview-select") : null;
    if (!state.dropdown || (wrap instanceof HTMLElement && wrap.dataset.controlKey === state.dropdown.key)) return;
    const key = state.dropdown.key;
    if (state.dropdown.multi) commitDropdown(key);
    else setTimeout(() => cancelDropdown(key), 0);
  });
  /** @param {unknown} value */
  const fingerprint = (value) => JSON.stringify(value);
  function publish() { changed(Object.freeze({ dropdown: state.dropdown ? Object.freeze({ ...state.dropdown }) : null })); }
  /** @param {boolean} force */
  function localRender(force) { publish(); onRender(force); }
  /** @param {string} key */
  function currentDrafts(key) {
    return state.modalDrafts.has(key) || (state.dropdown?.key === key && state.dropdown.modal)
      ? state.modalDrafts
      : state.drafts;
  }

  /** @param {string} key @param {DraftValue} value @param {Map<string, DraftValue>} [drafts] */
  function initDraft(key, value, drafts = currentDrafts(key)) {
    if (!drafts.has(key)) drafts.set(key, value);
  }

  /** @param {string} key @param {DraftValue} value @param {Map<string, DraftValue>} [drafts] */
  function initSelectDraft(key, value, drafts = currentDrafts(key)) {
    initDraft(key, value, drafts);
    const current = drafts.get(key);
    if (Array.isArray(current)) {
      state.selectDrafts.set(key, current.map(String));
      if (state.snapshot?.candidates?.[key] && !state.selectValidationRevisions.has(key)) {
        state.selectValidationRevisions.set(key, state.publishedRevision);
      }
    }
  }

  /** @param {string} key */
  function identityEntries(key) {
    return [...(state.candidateIdentities.get(key)?.values() || [])];
  }
  /** @param {string} key @param {Identity[]} selected @param {string[]} requested @param {number} revision */
  function reconcileCandidateSelection(key, selected, requested, revision) {
    const authorized = new Map(selected.map((entry) => [String(entry.value ?? entry.id), entry]));
    const values = requested.filter((value) => authorized.has(String(value))).map(String);
    currentDrafts(key).set(key, values);
    state.selectDrafts.set(key, values);
    const identities = new Map(selected.filter((entry) => values.includes(String(entry.value ?? entry.id)))
      .map((entry) => [String(entry.value ?? entry.id), entry]));
    if (identities.size) state.candidateIdentities.set(key, identities);
    else state.candidateIdentities.delete(key);
    state.selectValidationRevisions.set(key, Number(revision));
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
      state.pendingSelectValidations.delete(key);
    }
    state.modalDrafts.clear();
    state.modalTouched.clear();
  }

  /** @param {string} key @param {string[]} selected @param {boolean} multi @param {number} minimum @param {number} maximum @param {string | undefined} [highlight] @param {Identity[]} [entries] @param {boolean} [modal] */
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
      optional: modal && elements(".preview-select").find((item) => item.dataset.controlKey === key)?.dataset.optional === "true",
      highlight: highlight ?? selected[0] ?? (firstAvailable ? String(firstAvailable.value ?? firstAvailable.id ?? "") : null),
    };
    const descriptor = state.snapshot?.candidates?.[key];
    if (descriptor) queueCandidateQuery(key, state.candidateQueries.get(key) ?? descriptor.query ?? "", null, modal ? state.modalHandle : null);
    localRender(true);
    fitOpenDropdowns(true);
    setFocusKey(key);
    const trigger = elements(".select-trigger").find((item) => item.dataset.controlKey === key);
    trigger?.focus();
  }

  /** @param {string} key @param {string} fingerprint @param {string} message */
  function announceSelectStatus(key, fingerprint, message) {
    if (state.selectStatuses.get(key)?.fingerprint === fingerprint) return;
    state.selectStatuses.set(key, { fingerprint, message });
    const wrap = elements(".preview-select").find((item) => item.dataset.controlKey === key);
    const status = wrap ? query(wrap, ".select-guidance") : null;
    if (status) { status.textContent = message; status.hidden = false; }
  }

  /** @param {string} key @param {string[]} values @param {number} minimum @param {number} maximum */
  function announceInvalidSelection(key, values, minimum, maximum) {
    const fingerprint = JSON.stringify([values, minimum, maximum]);
    const message = values.length < minimum
      ? `Choose at least ${minimum} option${minimum === 1 ? "" : "s"}.`
      : `Choose no more than ${maximum} options.`;
    announceSelectStatus(key, fingerprint, message);
    const status = state.selectStatuses.get(key);
    if (status) status.invalid = true;
    const wrap = elements(".preview-select").find((item) => item.dataset.controlKey === key);
    wrap?.querySelector(".select-trigger")?.setAttribute("aria-invalid", "true");
  }

  /** @param {string} key @param {DraftValue} value @param {boolean} [multi] @param {number} [minimum] @param {number} [maximum] @param {string[]} [selected] @param {Identity} [entry] */
  function updateDraft(key, value, multi = false, minimum = 1, maximum = 1, selected = [], entry) {
    const drafts = currentDrafts(key);
    const stored = drafts.get(key);
    const current = Array.isArray(stored) ? stored.map(String) : [...selected];
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

  /** @param {string} key @param {string} highlight */
  function navigateDropdown(key, highlight) {
    if (state.dropdown?.key !== key) return;
    state.dropdown.highlight = highlight;
    localRender(true);
    fitOpenDropdowns(true);
  }

  /** @param {string} key @param {number} [minimum] */
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
    }
    state.pendingSelectValidations.delete(key);
    state.selectStatuses.delete(key);
    state.candidateIdentities.delete(key);
    if (state.dropdown?.key === key) state.dropdown = null;
    localRender(true);
    if (!modal && minimum <= 0) dispatch("select", { control_key: key, values: [] });
  }

  /** @param {string} key */
  function commitDropdown(key) {
    const dropdown = state.dropdown;
    if (!dropdown || dropdown.key !== key) return false;
    const draft = currentDrafts(key).get(key);
    const values = Array.isArray(draft) ? draft.map(String) : [];
    if (state.pendingAction || state.blocked) {
      announceSelectStatus(
        key,
        JSON.stringify(["pending", state.pendingAction?.requestId || "blocked", values]),
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
    publish();
    const wrap = elements(".preview-select").find((item) => item.dataset.controlKey === key);
    const list = wrap ? query(wrap, ".select-list") : null;
    const restoreTrigger = dropdown.modal || list?.contains(document.activeElement);
    const trigger = wrap ? query(wrap, ".select-trigger") : null;
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
      setFocusKey(key);
      restoreFocus(key);
    }
    if (!dropdown.modal && !state.modalDrafts.has(key) && changed) dispatch("select", { control_key: key, values });
    return true;
  }

  /** @param {string} [key] */
  function cancelDropdown(key) {
    const dropdown = state.dropdown;
    if (!key || !dropdown || dropdown.key !== key) return;
    const restoreTrigger = elements(".preview-select").some(
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
      setFocusKey(key);
      restoreFocus(key);
    }
  }

  function fitOpenDropdowns(scrollHighlighted = false) {
    const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
    const viewportHeight = document.documentElement.clientHeight || window.innerHeight;
    for (const wrap of elements(".preview-select.is-open")) {
      const trigger = query(wrap, ".select-trigger");
      const list = query(wrap, ".select-list");
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
      const active = query(list, ".select-option.is-highlighted");
      if (active && scrollHighlighted === true) {
        if (active.offsetTop < list.scrollTop) list.scrollTop = active.offsetTop;
        else if (active.offsetTop + active.offsetHeight > list.scrollTop + list.clientHeight) {
          list.scrollTop = active.offsetTop + active.offsetHeight - list.clientHeight;
        }
      }
    }
  }
  /** @param {string} controlKey */
  function candidateLoading(controlKey) {
    const key = `candidate:${controlKey}:${state.modalHandle || ""}`;
    return queryPending(controlKey, key) || state.selectQueryTimers.has(key);
  }
  /** @param {string} controlKey @param {string} text @param {string | null} [cursor] @param {string | null} [modalHandle] */
  function queueCandidateQuery(controlKey, text, cursor = null, modalHandle = null) {
    if (state.pinnedCapture || typeof controlKey !== "string") return;
    const normalized = Array.from(String(text || "").normalize("NFC")).slice(0, 128).join("");
    state.candidateQueries.set(controlKey, normalized);
    const key = `candidate:${controlKey}:${modalHandle || ""}`;
    clearTimeout(state.selectQueryTimers.get(key));
    const body = { control_key: controlKey, query: normalized, cursor: typeof cursor === "string" ? cursor : null,
      ...(modalHandle ? { modal_handle: modalHandle } : {}), selected_values: [...(state.selectDrafts.get(controlKey) || [])] };
    if (cursor == null) {
      state.selectQueryTimers.set(key, window.setTimeout(() => {
        state.selectQueryTimers.delete(key); queueQuery(key, "browse_candidates", body);
      }, 180));
    } else { state.selectQueryTimers.delete(key); queueQuery(key, "browse_candidates", body); }
  }
  function queuePendingCandidateValidations() {
    for (const [key, revision] of state.pendingSelectValidations) {
      if (revision !== state.publishedRevision || !state.snapshot?.candidates?.[key] || candidateLoading(key)) continue;
      const handle = state.modalHandle && key.startsWith(`modal:${state.modalHandle}:`) ? state.modalHandle : null;
      queueCandidateQuery(key, state.candidateQueries.get(key) ?? state.snapshot.candidates[key].query ?? "", null, handle);
    }
  }
  /** @param {Snapshot} snapshot @param {string | null | undefined} validatedControl */
  function observe(snapshot, validatedControl) {
    state.snapshot = snapshot;
    state.publishedRevision = snapshot.publishedRevision;
    /** @type {Map<string, [string, CandidateDescriptor][]>} */
    const groups = new Map();
    for (const [key, descriptor] of Object.entries(snapshot.candidates || {})) {
      const scope = key.split(":").slice(0, 2).join(":");
      if (!groups.has(scope)) groups.set(scope, []);
      groups.get(scope)?.push([key, descriptor]);
    }
    for (const [key, values] of state.selectDrafts) {
      if (!state.selectValidationRevisions.has(key)) continue;
      const descriptor = snapshot.candidates?.[key];
      const selected = descriptor?.selected;
      const available = [...(selected || []), ...(descriptor?.entries || [])];
      const ids = new Set(available.map((entry) => String(entry.value ?? entry.id)));
      if (!values.length) {
        state.selectValidationRevisions.set(key, snapshot.publishedRevision);
        state.pendingSelectValidations.delete(key);
      } else if (Array.isArray(selected) && (key === validatedControl || values.every((value) => ids.has(String(value))))) {
        reconcileCandidateSelection(key, key === validatedControl ? selected : available, values, snapshot.publishedRevision);
      } else { state.pendingSelectValidations.set(key, snapshot.publishedRevision); if (state.dropdown?.key === key) state.dropdown = null; }
    }
    state.candidateFingerprints = new Map([...groups].map(([scope, entries]) => [scope,
      fingerprint(entries.map(([key, descriptor]) => [key, descriptor, state.pendingSelectValidations.has(key)]))]));
    publish();
  }
  function reset() {
    state.snapshot = null;
    for (const timer of state.selectQueryTimers.values()) clearTimeout(timer);
    state.selectQueryTimers.clear();
    for (const map of [state.drafts, state.selectDrafts, state.selectStatuses, state.candidateIdentities,
      state.candidateQueries, state.selectValidationRevisions, state.pendingSelectValidations,
      state.candidateFingerprints]) map.clear();
    clearModalDrafts(); state.dropdown = null; publish();
  }
  /** @param {boolean} [modal] @returns {RenderOptions} */
  function renderOptions(modal = false) {
    const drafts = modal ? state.modalDrafts : state.drafts;
    return {
      draftValue: (key) => drafts.get(key),
      initDraft: (key, value) => { initDraft(key, value, drafts); return drafts.get(key) ?? value; },
      onInit: (key, value) => initSelectDraft(key, value, drafts),
      onReconcileDraft: (key, value) => { drafts.set(key, value); state.selectDrafts.set(key, value); },
      identityEntries, identityPending: (key) => state.pendingSelectValidations.has(key),
      candidateQuery: (key) => state.candidateQueries.get(key), onCandidateQuery: queueCandidateQuery,
      selectionStatus: (key) => state.selectStatuses.get(key)?.message || "",
      selectionInvalid: (key) => state.selectStatuses.get(key)?.invalid === true,
      candidateLoading,
    };
  }
  function report() {
    return Object.fromEntries([...state.selectDrafts].map(([key, values]) => [
      key, state.pendingSelectValidations.has(key) ? [] : [...values],
    ]));
  }
  return { observe, reset, renderOptions, report,
    /** @param {{blocked: boolean, pendingAction: AdmittedAction | null, pinnedCapture: boolean}} admission */
    admission: ({blocked, pendingAction, pinnedCapture}) => {
      state.blocked = blocked; state.pendingAction = pendingAction; state.pinnedCapture = pinnedCapture;
      for (const button of elements(".select-draft-actions button")) if (button instanceof HTMLButtonElement) {
        button.disabled = blocked || !state.dropdown || pinnedCapture;
      }
    },
    /** @param {SelectionContext} context */
    updateContext: (context) => { state.pendingAction = context.pendingAction; state.modalHandle = context.modalHandle; state.pinnedCapture = context.pinnedCapture; },
    /** @param {string} key */
    values: (key) => [...(state.selectDrafts.get(key) || [])],
    /** @param {string} key */
    isPending: (key) => state.pendingSelectValidations.has(key),
    /** @param {string} key */
    isTouched: (key) => state.modalTouched.has(key),
    /** @param {string} key */
    touch: (key) => state.modalTouched.add(key),
    /** @param {string} key @param {DraftValue} value */
    modalDraft: (key, value) => { state.modalDrafts.set(key, value); state.modalTouched.add(key); },
    closeDropdown: () => { state.dropdown = null; publish(); },
    cancelOpenDropdown: () => {
      if (!state.dropdown) return false;
      cancelDropdown(state.dropdown.key);
      return true;
    },
    /** @param {string} scope */
    fingerprint: (scope) => state.candidateFingerprints.get(scope) || "",
    queueCandidateQuery, candidateLoading, queuePendingCandidateValidations,
    initSelectDraft, identityEntries, reconcileCandidateSelection, clearModalDrafts, openDropdown, updateDraft, navigateDropdown, clearSelection, commitDropdown, cancelDropdown, fitOpenDropdowns };
}
