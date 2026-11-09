/** @import {Asset, AssetOptions} from "./preview-types" */
/**
 * Page-context asset URLs and response-derived metadata, never published manifests.
 * @param {{headers: () => HeadersInit}} options
 */
export function createAssets({ headers }) {
  /** @type {Readonly<Record<string, Asset>>} */
  let manifests = {};
  let epoch = 0;
  /** @type {Map<string, string>} */
  const urls = new Map();
  /** @type {Map<string, Promise<string>>} */
  const loads = new Map();
  /** @type {Map<string, Partial<Asset>>} */
  const derived = new Map();
  /** @type {Record<string, number>} */
  let captureTimes = {};
  /** @type {Record<string, Omit<Asset, "mediaKind"> & {kind?: string}>} */
  let metadata = {};
  function reset() {
    manifests = {};
    epoch += 1;
    loads.clear();
    urls.forEach((url) => URL.revokeObjectURL(url));
    urls.clear();
    derived.clear();
    captureTimes = {};
    metadata = {};
  }
  /** @param {Readonly<Record<string, Asset>>} next */
  function update(next) {
    manifests = next;
    for (const [key, url] of urls) {
      const id = key.slice(0, key.indexOf(":"));
      if (manifests[id] && manifests[id].available !== false) continue;
      URL.revokeObjectURL(url);
      urls.delete(key);
      derived.delete(id);
      delete captureTimes[id];
      delete metadata[id];
    }
  }
  /** @param {string} id @returns {Asset} */
  function manifest(id) { return { ...manifests[id], ...derived.get(id) }; }
  /** @param {string} id @param {number} time */
  function recordTime(id, time) {
    captureTimes[id] = time;
    derived.set(id, { ...derived.get(id), effectiveMediaTime: time });
    recordMetadata(id);
  }
  /** @param {string} id */
  function recordMetadata(id) {
    const value = manifest(id);
    metadata[id] = {
      kind: value.mediaKind, duration: value.duration, sourceCodecs: value.sourceCodecs,
      displayCodecs: value.displayCodecs, transformation: value.transformation,
      qualityDifferences: value.qualityDifferences, effectiveMediaTime: value.effectiveMediaTime,
      workerMemoryLimited: value.workerMemoryLimited,
    };
  }
  /** @param {string} id @param {AssetOptions} [options] @returns {Promise<string>} */
  function load(id, { download = false, capture = false, poster = false, mediaTime = null } = {}) {
    if (!manifests[id] || manifests[id].available === false) return Promise.reject(new Error("asset is unavailable"));
    const params = new URLSearchParams();
    if (download) params.set("download", "1");
    if (capture) params.set("capture", "1");
    if (poster) params.set("poster", "1");
    if (mediaTime !== null) params.set("media_time", String(mediaTime));
    const suffix = params.size ? `?${params}` : "";
    const key = `${id}:${suffix}`;
    const cached = urls.get(key);
    if (cached) return Promise.resolve(cached);
    const admittedEpoch = epoch;
    const requestKey = `${epoch}:${key}`;
    const loading = loads.get(requestKey);
    if (loading) return loading;
    const pending = fetch(`/api/assets/${encodeURIComponent(id)}${suffix}`, { headers: headers(), cache: "no-store" }).then(async (response) => {
      if (!response.ok) throw new Error(`asset request failed (${response.status})`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      if (admittedEpoch !== epoch || !manifests[id] || manifests[id].available === false) {
        URL.revokeObjectURL(url);
        throw new Error("stale asset generation");
      }
      if (!download) {
        const header = response.headers.get("X-Simcord-Media-Metadata");
        /** @type {Partial<Asset>} */
        const value = header ? JSON.parse(header) : {};
        const width = Number(response.headers.get("X-Display-Width"));
        const height = Number(response.headers.get("X-Display-Height"));
        if (width > 0 && height > 0) Object.assign(value, { displayReady: true, displayWidth: width, displayHeight: height });
        derived.set(id, { ...derived.get(id), ...value });
        if (capture && mediaTime !== null) captureTimes[id] = Number(response.headers.get("X-Media-Time") || mediaTime);
        recordMetadata(id);
      }
      urls.set(key, url);
      return url;
    }).finally(() => { if (loads.get(requestKey) === pending) loads.delete(requestKey); });
    loads.set(requestKey, pending);
    return pending;
  }
  return {
    update, reset, load, manifest, recordTime,
    clearTimes: () => { captureTimes = {}; metadata = {}; },
    report: () => ({ mediaCaptureTimes: { ...captureTimes }, mediaMetadata: { ...metadata } }),
  };
}
