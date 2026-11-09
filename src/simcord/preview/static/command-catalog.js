/** @import {CommandCatalog} from "./preview-types" */
/** Catalog observations have their own lifetime, independent of action sequencing.
 * @param {{fetchCatalog: () => Promise<CommandCatalog>, changed: (report: {catalog: CommandCatalog | null, fingerprint: string | null, state: string}, previous: CommandCatalog | null) => void}} options
 */
export function createCommandCatalog({ fetchCatalog, changed }) {
  /** @type {string | null} */
  let identity = null;
  /** @type {string | null} */
  let desired = null;
  let epoch = 0;
  /** @type {CommandCatalog | null} */
  let catalog = null;
  /** @type {string | null} */
  let fingerprint = null;
  let state = "idle";
  /** @param {CommandCatalog | null} [previous] */
  function publish(previous = catalog) { changed({ catalog, fingerprint, state }, previous); }
  function reset() {
    epoch += 1;
    identity = null;
    desired = null;
    catalog = null;
    fingerprint = null;
    state = "idle";
  }
  /** @param {string} nextIdentity @param {string | null} nextFingerprint */
  function observe(nextIdentity, nextFingerprint) {
    if (identity !== nextIdentity) { reset(); identity = nextIdentity; }
    if (desired === nextFingerprint) return;
    desired = nextFingerprint;
    const admittedEpoch = ++epoch;
    if (!nextFingerprint) {
      const previous = catalog;
      catalog = null; fingerprint = null; state = "idle";
      publish(previous);
      return;
    }
    if (fingerprint === nextFingerprint) {
      state = catalog?.state === "unavailable" ? "failed" : "ready";
      publish();
      return;
    }
    state = "loading";
    publish();
    void fetchCatalog().then((next) => {
      if (admittedEpoch !== epoch) return;
      const previous = catalog;
      catalog = next;
      state = next.state === "unavailable" ? "failed" : "ready";
      fingerprint = state === "failed" ? null : nextFingerprint;
      publish(previous);
    }).catch(() => {
      if (admittedEpoch !== epoch) return;
      const previous = catalog;
      catalog = null; fingerprint = null; state = "failed";
      publish(previous);
    });
  }
  return { observe, reset };
}
