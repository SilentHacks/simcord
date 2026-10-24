/** @import {ActionContext, ActionBody, ActionMetadata, ActionReport, AdmittedAction, QueryIntent, Receipt, Snapshot, Diagnostic} from "./preview-types" */
/**
 * One admission/sequence owner for callbacks, page intents, and control queries.
 * @param {{
 * session: {beginAction: (invalidate: boolean) => void, endAction: (rejected?: boolean) => void, readState: () => Promise<boolean>},
 * send: (body: ActionBody, files: Readonly<Record<string, File>>) => Promise<Receipt>,
 * readResult: (action: AdmittedAction) => Promise<Receipt>,
 * changed: (report: ActionReport) => void, render: () => void, healthy: () => void,
 * failure: (code: string, requestId?: string | null, sequence?: number | null) => void,
 * diagnostic: (item: Diagnostic) => void,
 * outcome: (action: AdmittedAction, receipt: Receipt | {rejected: true}, body: ActionBody | null, metadata: ActionMetadata | null | undefined) => void,
 * completeDraft: (action: AdmittedAction, receipt: Receipt) => void, idle: () => void,
 * rememberReceipt: (receipt: Receipt, kind: string) => void, terminate: (message: string) => void,
 * acceptedClose: () => void, beforePage: (kind: string, body: ActionBody) => void, recovered: (own: boolean) => void
 * }} options
 */
export function createActions({ session, send, readResult, changed, render, healthy, failure, diagnostic,
  outcome, completeDraft, idle, rememberReceipt, terminate, acceptedClose, beforePage, recovered }) {
  /** @type {ActionContext} */
  let context = { contextId: null, contextGeneration: 0, botGeneration: 0, publishedRevision: 0, observedRevision: 0,
    viewerId: null, targetId: null, closed: false, authorized: false, protocolCompatible: true, pinnedCapture: false };
  let sequence = 0;
  /** @type {AdmittedAction | null} */
  let pending = null;
  /** @type {AdmittedAction | null} */
  let uncertain = null;
  /** @type {number | null} */
  let awaitingRevision = null;
  /** @type {number | null} */
  let queryResultRevision = null;
  /** @type {Receipt | null} */
  let lastReceipt = null;
  let querySequence = 0;
  let busyDeferred = false;
  /** @type {string | null} */
  let closeAttemptFor = null;
  /** @type {Map<string, QueryIntent>} */
  const queries = new Map();
  /** @type {Map<string, number>} */
  const latest = new Map();
  /** @type {Record<string, ActionBody>} */
  let pages = {};
  /** @returns {Readonly<ActionReport>} */
  function report() {
    return Object.freeze({ pendingAction: pending, uncertainAction: uncertain,
      pendingQuery: pending?.queryPending ? pending.queryStatus : null,
      queuedQuery: queries.values().next().value || null,
      queuedQueryCount: queries.size, queuedPageIntents: Object.freeze({ ...pages }),
      awaitingRevision, queryResultRevision });
  }
  function publish() { changed(report()); }
  /** @param {string} kind */
  function pageAction(kind) { return ["close", "refresh", "focus", "viewer", "configure_presentation"].includes(kind); }
  /** @param {QueryIntent} intent */
  function currentQuery(intent) { return latest.get(intent.key) === intent.token; }
  /** @param {string} kind @param {ActionBody} body */
  function pageIntent(kind, body) { pages[kind] = body; publish(); }
  /** @param {string} key @param {string} kind @param {ActionBody} body @param {ActionMetadata | null} [metadata] */
  function queueQuery(key, kind, body, metadata = null) {
    const intent = Object.freeze({ key, kind, body, metadata, scope: `${context.viewerId}:${context.contextGeneration}`, token: ++querySequence });
    latest.set(key, intent.token);
    queries.set(key, intent);
    publish();
    drain();
  }
  function drain() {
    if (busyDeferred && !Object.keys(pages).length) return;
    const canClose = uncertain && "close" in pages && closeAttemptFor !== uncertain.requestId;
    if (pending || context.closed || context.pinnedCapture || !context.protocolCompatible || !context.contextId
      || (uncertain && !canClose)
      || (awaitingRevision !== null && awaitingRevision > context.observedRevision && !Object.keys(pages).length)) return;
    for (const kind of ["close", "viewer", "refresh", "focus", "configure_presentation"]) {
      if (!(kind in pages) || (uncertain && kind !== "close")
        || (!context.authorized && !["close", "viewer", "refresh"].includes(kind))) continue;
      const body = pages[kind]; delete pages[kind]; dispatch(kind, body); return;
    }
    if (!context.authorized || uncertain) return;
    const next = queries.entries().next().value;
    if (!next) return;
    const [key, intent] = next;
    queries.delete(key);
    if (intent.scope !== `${context.viewerId}:${context.contextGeneration}`) { publish(); drain(); return; }
    void perform(intent.kind, intent.body, intent, intent.metadata);
  }
  /** @param {string} kind @param {ActionBody} extra @param {QueryIntent | null} [intent] @param {ActionMetadata | null} [metadata] */
  async function perform(kind, extra, intent = null, metadata = null) {
    session.beginAction(kind === "viewer" || kind === "focus");
    const requestId = globalThis.crypto?.randomUUID?.() || `preview-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const submission = { ...extra,
      ...(extra.values ? { values: Array.isArray(extra.values)
        ? Object.freeze([...extra.values])
        : Object.freeze(Object.fromEntries(Object.entries(extra.values).map(([key, value]) => [
          key, Array.isArray(value) ? Object.freeze([...value]) : value,
        ]))) } : {}),
      ...(extra.path ? { path: Object.freeze([...extra.path]) } : {}),
      ...(extra.options ? { options: Object.freeze({ ...extra.options }) } : {}),
      ...(extra.selected_values ? { selected_values: Object.freeze([...extra.selected_values]) } : {}),
    };
    const body = { ...submission, protocol_version: 3, sequence: sequence + 1, request_id: requestId,
      generation: context.contextGeneration, bot_generation: context.botGeneration,
      published_revision: context.observedRevision || context.publishedRevision, kind };
    if (["click", "select"].includes(kind)) body.target_id = /^message:(\d+):component:/.exec(extra.control_key || "")?.[1] ?? context.targetId;
    const action = Object.freeze({ kind, contextId: context.contextId, requestId, sequence: body.sequence,
      controlKey: extra.control_key || null, targetId: body.target_id || null,
      contextGeneration: body.generation, publishedRevision: body.published_revision,
      submittedValues: body.values || null,
      commandEnvelope: kind === "run_command" ? submission : null,
      commandDraftId: metadata?.commandDraftId ?? null, commandDraftVersion: metadata?.commandDraftVersion ?? null,
      uploads: Object.freeze({ ...metadata?.uploads }),
      queryBody: intent ? submission : null, queryMetadata: intent?.metadata || null, queryIntent: intent,
      botGeneration: body.bot_generation, draftVersion: metadata?.draftVersion || 0,
      closeProbe: Boolean(kind === "close" && uncertain && closeAttemptFor === uncertain.requestId),
      queryPending: Boolean(intent), queryStatus: intent ? Object.freeze({ kind, key: intent.key,
        controlKey: extra.control_key || null, requestId, sequence: body.sequence, query: extra.query ?? extra.value ?? "" }) : null,
    });
    pending = action;
    publish(); render();
    let receipt;
    try { receipt = await send(body, action.uploads); }
    catch (error) {
      session.endAction();
      if (context.closed) return;
      uncertain = action; pending = null; publish();
      failure("action-response-unavailable", requestId, body.sequence);
      if (error instanceof Error && "status" in error && (error.status === 401 || error.status === 410)) { terminate("Preview session is unavailable. Reload the active Preview URL."); return; }
      render(); return;
    }
    const closed = kind === "close" && receipt?.requestId === requestId && !receipt.rejected && receipt.settlement === "settled";
    session.endAction(receipt?.requestId === requestId && receipt?.rejected === true);
    if (context.closed && !closed) return;
    if (!receipt || typeof receipt !== "object" || receipt.requestId !== requestId) {
      uncertain = action; pending = null; publish(); failure("action-receipt-unavailable", requestId, body.sequence); render(); return;
    }
    healthy();
    const busy = Boolean(intent && receipt.rejected && receipt.diagnostics?.some((item) => item.code === "busy"));
    if (!busy) outcome(action, receipt, extra, kind === "run_command"
      ? { draftId: action.commandDraftId, draftVersion: action.commandDraftVersion } : intent?.metadata);
    if (busy) receipt = { ...receipt, diagnostics: (receipt.diagnostics || []).filter((item) => item.code !== "busy") };
    if (typeof receipt.expectedSequence === "number" && Number.isInteger(receipt.expectedSequence)) sequence = receipt.expectedSequence;
    if (Number.isInteger(receipt.revision) && receipt.revision > context.publishedRevision) awaitingRevision = receipt.revision;
    if (busy && intent) { queries.set(intent.key, queries.get(intent.key) || intent); busyDeferred = true; }
    if (Array.isArray(receipt.diagnostics)) receipt.diagnostics.forEach(diagnostic);
    if (kind === "close" && receipt.rejected && action.closeProbe) pages.close = extra;
    if (closed && uncertain && receipt.sequence === uncertain.sequence) {
      const own = uncertain.requestId === requestId;
      uncertain = null; closeAttemptFor = null; recovered(own);
    }
    if (closed) {
      pending = null; queries.clear(); pages = {}; awaitingRevision = null; publish(); acceptedClose(); return;
    }
    if (!intent) completeDraft(action, receipt);
    lastReceipt = receipt;
    rememberReceipt(receipt, kind);
    pending = Object.freeze({ ...action, queryPending: false }); publish();
    if (!await session.readState() && !context.closed) render();
    if (context.closed) return;
    pending = null; publish();
    idle();
    drain();
  }
  /** @param {string} kind @param {ActionBody} [extra] @param {ActionMetadata | null} [metadata] */
  function dispatch(kind, extra = {}, metadata = null) {
    if (["browse_messages", "browse_candidates", "autocomplete_command"].includes(kind)) {
      const key = kind === "browse_messages" ? "messages" : kind === "browse_candidates"
        ? `candidate:${extra.control_key}:${extra.modal_handle || ""}`
        : `command-autocomplete:${extra.command_id}:${(extra.path || []).join(".")}:${extra.focused}`;
      queueQuery(key, kind, extra, metadata); return;
    }
    if (context.closed || !context.contextId || !context.protocolCompatible || context.pinnedCapture) return;
    if (!context.authorized && !["close", "viewer", "refresh"].includes(kind)) return;
    if (pending || (uncertain && kind !== "close")) { if (pageAction(kind)) pageIntent(kind, extra); return; }
    if (uncertain) {
      if (closeAttemptFor === uncertain.requestId) { pageIntent(kind, extra); return; }
      closeAttemptFor = uncertain.requestId;
    }
    if (!pageAction(kind) && awaitingRevision !== null && awaitingRevision > context.publishedRevision) return;
    beforePage(kind, extra);
    void perform(kind, extra, null, metadata);
  }
  /** @param {Snapshot} snapshot */
  function observe(snapshot) {
    const requestId = uncertain?.requestId;
    const receipt = requestId && [snapshot?.lastAction, ...(snapshot?.activity || [])].find((item) => item?.requestId === requestId);
    if (receipt && uncertain && (receipt.rejected === true || typeof receipt.settlement === "string" && receipt.settlement !== "pending")) {
      const action = uncertain;
      if (action.kind === "autocomplete_command" && action.queryBody && action.queryMetadata) {
        const identity = `${context.contextId}:${snapshot.context?.generation}`;
        void readResult(action).then((result) => {
          if (context.authorized && `${context.contextId}:${context.contextGeneration}` === identity) outcome(action, result, action.queryBody, action.queryMetadata);
        }).catch(() => {
          if (context.authorized && `${context.contextId}:${context.contextGeneration}` === identity) outcome(action, { rejected: true }, action.queryBody, action.queryMetadata);
        });
      }
      if (action.kind === "run_command") outcome(action, receipt, action.commandEnvelope,
        { draftId: action.commandDraftId, draftVersion: action.commandDraftVersion });
      completeDraft(action, receipt);
      lastReceipt = receipt;
      rememberReceipt(receipt, action.kind);
      if (typeof receipt.expectedSequence === "number" && Number.isInteger(receipt.expectedSequence)) sequence = receipt.expectedSequence;
      uncertain = null; closeAttemptFor = null; recovered(true);
    }
    if (awaitingRevision !== null && snapshot.publishedRevision >= awaitingRevision) { awaitingRevision = null; queryResultRevision = null; }
    publish();
  }
  /** @param {Snapshot} snapshot @param {(key: string) => readonly string[]} values */
  function validatedCandidate(snapshot, values) {
    const action = pending;
    const query = action?.queryIntent;
    const receipt = lastReceipt;
    if (!action || !query || action.kind !== "browse_candidates" || action.queryPending
      || !receipt || receipt.requestId !== action.requestId || receipt.rejected || receipt.settlement !== "settled"
      || snapshot.publishedRevision < receipt.revision || snapshot.botGeneration !== action.botGeneration
      || !currentQuery(query) || query.scope !== `${snapshot.viewerId}:${snapshot.context.generation}`
      || (query.body.modal_handle && snapshot.modal?.handle !== query.body.modal_handle)
      || !Array.isArray(query.body.selected_values) || typeof query.body.control_key !== "string"
      || JSON.stringify(query.body.selected_values) !== JSON.stringify(values(query.body.control_key))) return null;
    return query.body.control_key;
  }
  function admissionBlocked() { return Boolean(pending || uncertain
    || (awaitingRevision !== null && awaitingRevision > context.publishedRevision)); }
  /** @param {{receipt: Receipt | null, kind: string | null, externalNotice: string | null, ready: boolean,
   * transport: {state: string, uncertainRequestId: string | null}}} status */
  function message({ receipt, kind, externalNotice, ready, transport }) {
    if (externalNotice) return externalNotice;
    if (context.closed) return "Preview session ended.";
    if (transport.uncertainRequestId || transport.state === "uncertain") return "Action outcome uncertain. It will not be retried.";
    if (transport.state === "error") return "Connection interrupted; trying again…";
    const pendingLabels = /** @type {Record<string, string>} */ ({
      browse_messages: "Updating message results…", browse_candidates: "Updating options…",
      send_message: "Sending message…", edit_message: "Saving message…", delete_message: "Deleting message…",
      viewer: "Switching viewer…", focus: "Opening message…", refresh: "Updating preview…",
      configure_presentation: "Updating viewport…", history: "Loading conversation…",
    });
    if (pending) return pendingLabels[pending.kind] || "Completing action…";
    if ((awaitingRevision !== null && awaitingRevision > context.publishedRevision) || queries.size || Object.keys(pages).length) return "Updating preview…";
    if (receipt) {
      if (receipt.uncertain) return "Action outcome uncertain. It will not be retried.";
      if (receipt.rejected || ["rejected", "failed", "timeout", "cancelled"].includes(receipt.settlement)
        || receipt.acknowledgement === "unacknowledged") return "Action was not completed.";
      if (receipt.settlement !== "settled") return "Action is awaiting a confirmed result.";
      const labels = /** @type {Record<string, string>} */ ({
        send_message: "Message sent", edit_message: "Message updated", delete_message: "Message deleted",
        set_reaction: "Reaction updated", set_poll_votes: "Poll updated", set_pinned: "Pin updated",
        viewer: "Viewer switched", focus: "Message opened", history: "Conversation updated", refresh: "Preview refreshed",
        configure_presentation: "Viewport updated", browse_messages: "Message results updated", browse_candidates: "Options updated",
        click: "Callback completed", select: "Callback completed", modal_submit: "Callback completed",
      });
      return labels[kind || ""] || "Action completed";
    }
    return !context.authorized ? "Access to this preview is unavailable." : ready ? "Preview ready" : "Loading preview…";
  }
  return {
    dispatch, queueQuery, pageIntent, drain, observe, report, currentQuery, validatedCandidate, admissionBlocked, message,
    /** @param {string} key */
    hasQuery: (key) => queries.has(key),
    /** @param {ActionContext} next */
    setContext: (next) => { context = Object.freeze({ ...next }); },
    tick: () => { busyDeferred = false; drain(); },
    resetInteraction: () => { queries.clear(); latest.clear(); delete pages.focus; busyDeferred = false; publish(); },
    redact: () => { pages = {}; queries.clear(); latest.clear(); publish(); },
    stop: () => { uncertain ||= pending; pending = null; awaitingRevision = null; context = { ...context, closed: true }; publish(); },
  };
}
