/** @import {Snapshot} from "./preview-types" */
/** @param {unknown} value @returns {value is Snapshot} */
function compatible(value) {
  if (!value || typeof value !== "object") return false;
  const snapshot = /** @type {Partial<Snapshot>} */ (value);
  return Boolean(Number(snapshot.protocolVersion) === 3
    && snapshot.context && typeof snapshot.context.id === "string"
    && Number.isInteger(snapshot.context.generation) && snapshot.context.generation >= 0
    && Array.isArray(snapshot.messageIndex) && snapshot.messageIndex.length <= 50
    && snapshot.messages && typeof snapshot.messages === "object" && !Array.isArray(snapshot.messages)
    && Array.isArray(snapshot.timeline)
    && snapshot.history && typeof snapshot.history === "object"
    && snapshot.navigation && typeof snapshot.navigation === "object"
    && snapshot.presentation && typeof snapshot.presentation === "object"
    && snapshot.publication && typeof snapshot.publication === "object"
    && snapshot.candidates && typeof snapshot.candidates === "object" && !Array.isArray(snapshot.candidates)
    && Array.isArray(snapshot.activity) && Number.isInteger(snapshot.publishedRevision)
    && typeof snapshot.botGeneration === "number" && Number.isInteger(snapshot.botGeneration) && snapshot.botGeneration >= 0);
}
/** @typedef {{context: {id: string, generation: number}, botGeneration: number, publishedRevision: number}} Checkpoint */
/**
 * One page's state reads and polling lifetime. Action POSTs deliberately stay outside it.
 * @template T
 * @param {{read: (signal: AbortSignal) => Promise<T>, install: (value: T) => Promise<boolean>, tick: () => void, failure: (error: unknown) => void, interval?: number}} options
 */
export function createSession({ read, install, tick, failure, interval = 500 }) {
  let epoch = 0;
  /** @type {Checkpoint | null} */
  let checkpoint = null;
  /** @type {AbortController | null} */
  let controller = null;
  /** @type {Promise<boolean> | null} */
  let pending = null;
  /** @type {ReturnType<typeof setTimeout> | null} */
  let timer = null;
  let running = false;
  let stopped = false;
  let lifecycle = 0;
  /** @type {number | null} */
  let contextFloor = null;
  let contextAction = false;
  /** @param {Checkpoint} value */
  function accept(value) {
    if (stopped || (contextFloor !== null && value.context.generation < contextFloor)) return false;
    if (checkpoint && (value.context.id !== checkpoint.context.id
      || value.context.generation < checkpoint.context.generation
      || value.botGeneration < checkpoint.botGeneration
      || value.publishedRevision < checkpoint.publishedRevision)) return false;
    checkpoint = { context: { ...value.context }, botGeneration: value.botGeneration, publishedRevision: value.publishedRevision };
    contextFloor = null;
    return true;
  }
  function invalidate() {
    epoch += 1;
    controller?.abort();
    controller = null;
    pending = null;
  }
  /** @param {boolean} contextChange */
  function beginAction(contextChange = false) {
    invalidate();
    contextAction = contextChange;
    if (contextChange && checkpoint) contextFloor = checkpoint.context.generation + 1;
  }
  /** @param {boolean} rejected */
  function endAction(rejected = false) {
    if (rejected && contextAction) contextFloor = null;
    contextAction = false;
    invalidate();
  }
  function readState() {
    if (stopped) return Promise.resolve(false);
    if (pending) return pending;
    const identity = epoch;
    const owner = new AbortController();
    controller = owner;
    const work = (async () => {
      try {
        const value = await read(owner.signal);
        if (identity !== epoch || stopped) return false;
        return await install(value);
      } catch (error) {
        if (identity === epoch && !stopped) failure(error);
        return false;
      } finally {
        if (controller === owner) { controller = null; pending = null; }
      }
    })();
    pending = work;
    return work;
  }
  async function poll() {
    timer = null;
    if (!running || stopped) return;
    const lifetime = lifecycle;
    const identity = epoch;
    const accepted = await readState();
    if (!running || stopped || lifetime !== lifecycle) return;
    if (accepted && identity === epoch) tick();
    if (timer === null) timer = setTimeout(poll, interval);
  }
  function pause() {
    running = false;
    lifecycle += 1;
    clearTimeout(timer ?? undefined);
    timer = null;
    invalidate();
  }
  function resume() {
    if (running || stopped) return;
    running = true;
    void poll();
  }
  function stop() { pause(); stopped = true; }
  return { compatible, accept, invalidate, beginAction, endAction, readState, pause, resume, stop };
}
