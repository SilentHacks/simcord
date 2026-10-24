/** @import {CommandDraft, MutableCommandDraft, CommandEntry, CommandValue} from "./preview-types" */
/** File-bearing command drafts: versions and reconciliation never JSON-clone uploads.
 * @param {(draft: Readonly<CommandDraft> | null) => void} changed
 */
export function createCommandDrafts(changed) {
  /** @type {MutableCommandDraft | null} */
  let current = null;
  let sequence = 0;
  function publish() {
    changed(current ? Object.freeze({ ...current,
      path: Object.freeze([...current.path]), added: Object.freeze([...current.added]),
      errors: Object.freeze({ ...current.errors }), droppedOptions: Object.freeze([...current.droppedOptions]),
      values: Object.freeze(Object.fromEntries(Object.entries(current.values).map(([name, value]) => [name,
        Object.freeze({ ...value, ...(value.entity ? { entity: Object.freeze({ ...value.entity }) } : {}) }),
      ]))),
    }) : null);
  }
  /** @param {CommandEntry} entry */
  function create(entry) {
    const required = entry.options.filter((option) => option.required);
    current = { id: ++sequence, version: 0, commandId: entry.commandId, path: [...entry.path],
      invocation: entry.invocation, schemaFingerprint: entry.schemaFingerprint, description: entry.description,
      available: true, focused: required[0]?.name || null, values: {}, added: new Set(), errors: {}, droppedOptions: [],
      openOptionsOnRender: entry.options.length > 0 && required.length === 0 };
    publish();
  }
  /** @param {string} name @param {CommandValue | undefined} next */
  function value(name, next) {
    if (!current) return;
    if (next === undefined) delete current.values[name]; else current.values[name] = next;
    delete current.errors[name]; current.version += 1; publish();
  }
  /** @param {CommandEntry | null | undefined} entry @param {CommandEntry | null | undefined} previousEntry */
  function reconcile(entry, previousEntry) {
    if (!current) return;
    if (!entry) { current.available = false; publish(); return; }
    if (entry.schemaFingerprint !== current.schemaFingerprint) {
      const oldTypes = new Map((previousEntry?.options || []).map((option) => [option.name, option.type]));
      const nextTypes = new Map(entry.options.map((option) => [option.name, option.type]));
      /** @param {string} name */
      const keep = (name) => nextTypes.has(name) && nextTypes.get(name) === oldTypes.get(name);
      current.droppedOptions = Object.keys(current.values).filter((name) => !keep(name));
      current.values = Object.fromEntries(Object.entries(current.values).filter(([name]) => keep(name)));
      current.added = new Set([...current.added].filter((name) => entry.options.some((option) => option.name === name && !option.required)));
      current.schemaFingerprint = entry.schemaFingerprint; current.description = entry.description;
      current.errors = {}; current.version += 1;
    }
    current.available = true; publish();
  }
  return {
    create, value, reconcile, clear: () => { current = null; publish(); },
    /** @param {string | null} name */
    focus: (name) => { if (current) { current.focused = name; publish(); } },
    /** @param {string} name @param {string | null} message */
    error: (name, message) => { if (current) { if (message) current.errors[name] = message; else delete current.errors[name]; publish(); } },
    /** @param {string} name */
    add: (name) => { if (current) { current.added.add(name); current.focused = name; publish(); } },
    /** @param {string} name */
    remove: (name) => { if (current) { current.added.delete(name); delete current.values[name]; delete current.errors[name]; current.focused = null; current.version += 1; publish(); } },
    optionsOpened: () => { if (current) { current.openOptionsOnRender = false; publish(); } },
  };
}
