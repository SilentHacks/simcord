import { renderModal } from "./components.js";
import { disposeSurface } from "./dom.js";
import { updateActionControls } from "./surfaces.js";

/** @import {Snapshot, Profile, Dropdown, RenderOptions, ActionBody, Diagnostic, AssetOptions, Asset, ModalComponent, DraftValue} from "./preview-types" */
/** @typedef {ReturnType<import("./select-drafts").initSelectDrafts>} Selections */

/** Modal lifetime, validation, focus trapping, and pending render work.
 * @param {{root: HTMLElement, selections: Selections, loadAsset: (id: string, options?: AssetOptions) => Promise<string>,
 * assetMetadata: (id: string) => Asset, dispatch: (kind: string, body: ActionBody) => void,
 * render: (force: boolean) => void, diagnostic: (item: Diagnostic) => void, clearDiagnostic: () => void,
 * clearRenderDiagnostics: (owner: string | null) => void, isolate: (open: boolean) => void,
 * restoreFocus: (key?: string | null) => boolean, fallbackFocus: () => void,
 * changed: (report: {modalHandle: string | null, validationPaths: string[]}) => void}} options
 */
export function createModal({ root, selections, loadAsset, assetMetadata, dispatch, render, diagnostic,
  clearDiagnostic, clearRenderDiagnostics, isolate, restoreFocus, fallbackFocus, changed }) {
  /** @type {Snapshot | null} */
  let snapshot = null;
  /** @type {string | null} */
  let handle = null;
  /** @type {string | null} */
  let dismissed = null;
  /** @type {{controlId: string, message: string} | null} */
  let error = null;
  /** @type {string | null} */
  let errorHandle = null;
  /** @type {string | null} */
  let openerKey = null;
  let fingerprint = "";
  /** @type {Promise<unknown>[]} */
  let pending = [];
  function report() { return Object.freeze({ modalHandle: handle, validationPaths: error ? [error.controlId] : [] }); }
  function publish() { changed(report()); }
  function clearValidation() {
    if (error) {
      const controlId = error.controlId;
      const field = [...root.querySelectorAll(".modal-field")].filter((item) => item instanceof HTMLElement).find((item) => item.dataset.customId === controlId);
      const message = field?.querySelector(".field-error");
      if (field && message) {
        field.querySelectorAll("[aria-invalid='true']").forEach((target) => {
          target.removeAttribute("aria-invalid");
          const ids = (target.getAttribute("aria-describedby") || "").split(/\s+/).filter((id) => id && id !== message.id);
          if (ids.length) target.setAttribute("aria-describedby", ids.join(" ")); else target.removeAttribute("aria-describedby");
        });
        message.remove();
      }
    }
    error = null; clearDiagnostic(); publish();
  }
  function cancel() {
    selections.closeDropdown(); clearValidation(); dismissed = handle; selections.clearModalDrafts(); errorHandle = null; render(true);
  }
  /** @param {Record<string, DraftValue>} values */
  function submit(values) {
    const modal = snapshot?.modal;
    if (!modal || modal.handle !== handle) return;
    const invalid = validateModalValues(modal.payload || {}, values);
    if (invalid) {
      error = invalid; errorHandle = handle; clearDiagnostic();
      diagnostic({ code: "modal-validation", severity: "error", complete: false });
      fingerprint = ""; publish(); render(true); return;
    }
    clearValidation(); errorHandle = null; dispatch("modal_submit", { modal_handle: modal.handle, values });
  }
  /** @param {Snapshot} next @param {{profile: Readonly<Profile>, dropdown: Readonly<Dropdown> | null, focusKey: string | null,
   * spoilerOptions: Pick<RenderOptions, "isSpoilerRevealed" | "onRevealSpoiler">}} context @param {boolean} [force] */
  function update(next, context, force = false) {
    snapshot = next;
    const dropdown = context.dropdown;
    const modal = next.modal && next.modal.handle !== dismissed ? next.modal : null;
    const loading = Boolean(dropdown?.key?.startsWith(`modal:${next.modal?.handle}:`) && selections.candidateLoading(dropdown.key));
    const key = next.modal ? `${JSON.stringify(next.modal)}:${selections.fingerprint(`modal:${next.modal.handle}`)}:${loading}` : "";
    if (modal && errorHandle !== modal.handle) { clearValidation(); errorHandle = modal.handle; }
    else if (!modal) { clearValidation(); errorHandle = null; }
    const nextHandle = modal?.handle || null;
    const previous = handle;
    if (nextHandle && !previous) openerKey = context.focusKey;
    if (nextHandle !== handle) selections.clearModalDrafts();
    isolate(Boolean(nextHandle)); root.hidden = !nextHandle;
    if (force || key !== fingerprint) {
      const scrollTop = root.querySelector(".modal-body")?.scrollTop || 0;
      clearRenderDiagnostics(handle ? `modal:${handle}` : null);
      if (modal) clearRenderDiagnostics(`modal:${modal.handle}`);
      pending = [];
      const rendered = renderModal(root, modal?.payload, {
        ...selections.renderOptions(true), ...context.profile, dropdown, candidates: next.candidates || {},
        modalHandle: modal?.handle || "", isTouched: selections.isTouched, loadAsset, assetMetadata,
        assets: next.assets || {}, contextId: next.context.id, ...context.spoilerOptions,
        validationError: error, scrollTop, pendingMedia: pending,
        onSelectOpen: selections.openDropdown,
        onSelectDraft: (...args) => { clearValidation(); selections.touch(args[0]); selections.updateDraft(...args); },
        onSelectCommit: selections.commitDropdown, onSelectCancel: selections.cancelDropdown,
        onNavigate: selections.navigateDropdown, onClear: selections.clearSelection,
        onDraft: (key, value) => { clearValidation(); selections.modalDraft(key, value); render(false); },
        onFiles: (key, files) => { clearValidation(); selections.modalDraft(key, files); render(true); },
        onCancel: cancel, onSubmit: submit,
        onDiagnostic: (item) => diagnostic({ ...item, id: `local:render:modal:${modal?.handle}:${item.code}`, renderOwner: `modal:${modal?.handle}` }),
      });
      if (nextHandle && error && rendered.errorTarget) {
        rendered.errorTarget.scrollIntoView({ block: "nearest" }); rendered.errorTarget.focus({ preventScroll: true });
      } else if (nextHandle && !previous) requestAnimationFrame(() => { if (handle === nextHandle) rendered.focus?.focus(); });
      else if (nextHandle) { selections.fitOpenDropdowns(); restoreFocus(); }
      else if (previous && !restoreFocus(openerKey)) fallbackFocus();
    }
    handle = nextHandle; fingerprint = key; publish();
    return pending;
  }
  function reset() {
    disposeSurface(root); root.replaceChildren(); root.hidden = true;
    snapshot = null; handle = null; dismissed = null; error = null; errorHandle = null;
    openerKey = null; fingerprint = ""; pending = []; isolate(false); publish();
  }
  root.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (event.isComposing || event.keyCode === 229) return;
      if (selections.cancelOpenDropdown()) { event.preventDefault(); return; }
      if (handle) { event.preventDefault(); cancel(); }
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [...root.querySelectorAll("a[href], button, input, textarea, select, [tabindex]:not([tabindex='-1'])")]
      .filter((item) => item instanceof HTMLElement)
      .filter((item) => !("disabled" in item && item.disabled === true) && item.tabIndex >= 0 && item.checkVisibility())
      .filter((item, index, items) => !(item instanceof HTMLInputElement) || item.type !== "radio"
        || !item.name || item.checked || !items.some((other) => other instanceof HTMLInputElement
          && other.type === "radio" && other.name === item.name && (other.checked || items.indexOf(other) < index)));
    if (!focusable.length) { event.preventDefault(); const dialog = root.querySelector(".modal-dialog"); if (dialog instanceof HTMLElement) dialog.focus(); return; }
    const active = document.activeElement;
    if (event.shiftKey && (active === focusable[0] || !root.contains(active))) { event.preventDefault(); focusable.at(-1)?.focus(); }
    else if (!event.shiftKey && (active === focusable.at(-1) || !root.contains(active))) { event.preventDefault(); focusable[0].focus(); }
  });
  return {
    /** @param {boolean} blocked */
    admission: (blocked) => updateActionControls(root, blocked),
    update, reset, report, clearValidation,
  };
}
/** @param {ModalComponent} modal @param {Readonly<Record<string, DraftValue>>} values */
function validateModalValues(modal, values) {
  /** @type {Record<string, ModalComponent>} */
  const controls = {};
  const uploadLimit = 10 * 1024 * 1024;
  let totalUploadBytes = 0;
  /** @param {unknown} node */
  const walk = (node) => {
    if (!node || typeof node !== "object") return;
    if ("custom_id" in node && typeof node.custom_id === "string") controls[node.custom_id] = /** @type {ModalComponent} */ (node);
    Object.values(node).forEach((value) => Array.isArray(value) ? value.forEach(walk) : walk(value));
  };
  (modal.components || []).forEach(walk);
  for (const [id, component] of Object.entries(controls)) {
    const type = Number(component.type);
    const supplied = Object.prototype.hasOwnProperty.call(values, id);
    const value = values[id];
    const required = component.required === true
      || (component.required === undefined && (type === 4 || [3, 5, 6, 7, 8].includes(type)));
    /** @param {string} message */
    const invalid = (message) => ({ controlId: id, message });
    if (type === 4) {
      const text = String(value ?? component.value ?? component.default ?? "");
      const length = Array.from(text).length;
      if (required && !text) return invalid("This field is required.");
      if (text && component.min_length !== undefined && length < Number(component.min_length)) {
        return invalid(`Enter at least ${component.min_length} characters.`);
      }
      if (component.max_length !== undefined && length > Number(component.max_length)) {
        return invalid(`Enter no more than ${component.max_length} characters.`);
      }
    } else if ([3, 5, 6, 7, 8, 19, 22].includes(type)) {
      const selected = Array.isArray(value) ? value : [];
      const minimum = Number(component.min_values ?? 1);
      const maximum = Number(component.max_values ?? (type === 22 ? component.options?.length || 1 : 1));
      if (required && selected.length === 0) return invalid("Choose at least one value.");
      if ((selected.length || required) && selected.length < minimum) {
        return invalid(`Choose at least ${minimum} value(s).`);
      }
      if (selected.length > maximum) return invalid(`Choose no more than ${maximum} value(s).`);
      if (type === 19) {
        for (const file of selected) {
          if (!(file instanceof File)) return invalid("Choose a valid file.");
          if (file.size > uploadLimit) return invalid(`${file.name} exceeds the 10 MiB per-file limit.`);
          totalUploadBytes += file.size;
          if (totalUploadBytes > 25 * 1024 * 1024) {
            return invalid("Uploads exceed the 25 MiB aggregate limit.");
          }
        }
      }
    } else if (type === 21) {
      if (required && !value) return invalid("Choose one option.");
      if (value != null && !(component.options || []).some((item) => String(item.value) === String(value))) {
        return invalid("Choose an available option.");
      }
    } else if (type === 23 && supplied && typeof value !== "boolean") {
      return invalid("Choose a valid checkbox value.");
    }
  }
  return null;
}
