/** @import {ActionBody, Receipt, DraftValue} from "./preview-types" */
/** Capability-bearing HTTP only; action admission and widgets live elsewhere.
 * @param {string} capability
 */
export function createTransport(capability) {
  /** @type {string | null} */
  let contextId = null;
  /** @param {string | null} [context] @returns {Record<string, string>} */
  function authHeaders(context = contextId) {
    return { "X-Simcord-Capability": capability, ...(context ? { "X-Simcord-Context": context } : {}) };
  }
  /** @template T @param {Response} response @returns {Promise<T>} */
  async function responseValue(response) {
    if (!response.ok) throw Object.assign(new Error("preview request failed"), { status: response.status });
    return response.status === 204 ? /** @type {T} */ (null) : response.json();
  }
  /** @template T @param {string} path @param {string} [method] @param {unknown} [body] @param {string | null} [context] @param {AbortSignal} [signal] @returns {Promise<T>} */
  async function request(path, method = "GET", body, context = contextId, signal) {
    const headers = authHeaders(context);
    return responseValue(await fetch(path, {
      method, headers: body === undefined ? headers : { ...headers, "Content-Type": "application/json" },
      cache: "no-store", signal, ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    }));
  }
  /** @param {ActionBody} body @param {Readonly<Record<string, File>>} [files] @returns {Promise<Receipt>} */
  async function requestAction(body, files = {}) {
    /** @type {[string, File][]} */
    const uploads = Object.entries(files).filter(([, file]) => file instanceof File);
    const payload = { ...body };
    if (body.kind !== "run_command" && body.values && !Array.isArray(body.values)) {
      const modalValues = /** @type {Readonly<Record<string, DraftValue>>} */ (body.values);
      const values = { ...modalValues };
      payload.values = values;
      for (const [id, value] of Object.entries(modalValues)) {
        if (!Array.isArray(value) || !value.some((item) => item instanceof File)) continue;
        values[id] = [];
        for (const file of value) if (file instanceof File) uploads.push([id, file]);
      }
    }
    if (!uploads.length) return request("/api/action", "POST", body);
    const form = new FormData();
    form.append("payload", JSON.stringify(payload));
    for (const [name, file] of uploads) form.append(`file:${name}`, file, file.name);
    return responseValue(await fetch("/api/action", { method: "POST", headers: authHeaders(), body: form, cache: "no-store" }));
  }
  return {
    /** @param {string | null} id */
    setContext: (id) => { contextId = id; }, authHeaders, request, requestAction,
  };
}
