/**
 * Typed REST client for the PET Line Planner FastAPI backend.
 * All requests use credentials: "same-origin" so the session cookie
 * (required by edit endpoints) is forwarded on same-origin requests.
 *
 * Types are aligned to the real backend response models (backend/models.py).
 * Key differences from naive assumptions:
 *   - GET /api/config  -> "meta" (not "run_meta")
 *   - GET /api/production -> "week_totals" (not "weekly_totals"),
 *                            "week_flags"  (not "capacity_flags"),
 *                            week_flags is a dict keyed by week_key (not an array)
 *   - GET /api/summary -> "counts" dict (not "colour_counts" array),
 *                         "original_vs_plan.original/working" colour-count dicts
 */

// ── Domain types ─────────────────────────────────────────────────────────────

export interface SKU {
  sku_code: string;
  description: string;
  pack_size_ml: number;
  priority: number;
  status: string;
  shelf_life_days: number;
  mlor_days: number;
  max_cover_weeks: number;
}

export interface Week {
  week_key: string;         // e.g. "2026-W35"
  horizon_index: number;    // 1..52
  week_commencing: string;  // ISO-8601 date string (Monday)
  maintenance_type: string; // "None" | "Partial" | "Full"
  is_locked: boolean;
  note: string;
}

export interface Parameter {
  name: string;
  value: number;
  description: string;
}

/** Matches backend ConfigMeta model. */
export interface RunMeta {
  sku_count: number;
  week_count: number;
  total_production: number;
}

// ── GET /api/config ──────────────────────────────────────────────────────────
// Backend field is "meta" (not "run_meta").

export interface ConfigResponse {
  skus: SKU[];
  weeks: Week[];
  parameters: Parameter[];
  meta: RunMeta;   // ← "meta" in the real backend response
}

// ── GET /api/supply ──────────────────────────────────────────────────────────

export interface SupplyCell {
  sku_code: string;
  week_key: string;
  horizon_index: number;
  opening: number;
  recv: number;          // QA-cleared receipts this week
  prod: number;          // production planned this week
  demand: number;        // demand consumed this week
  raw: number;           // closePrev + recv - demand (can be negative)
  close: number;         // max(0, raw)
  cover_weeks: number;
  severity: number;
  colour: string;        // ColourName
  display_value?: number; // raw when negative (shortage gap), else close
}

export interface SupplyResponse {
  rows: SupplyCell[];
}

// ── GET /api/production ──────────────────────────────────────────────────────
// "week_totals" and "week_flags" are the real field names.
// "week_flags" is a dict keyed by week_key; each value carries R1..R4 booleans
// and an "over" integer (units over ceiling when R3 fires, else 0).

export interface PlanCell {
  sku_code: string;
  week_key: string;
  planned_qty: number;
  orig_qty?: number;   // present only in live UC mode; absent from synthetic data
}

/** Capacity flags for one week — returned as a flat dict value in week_flags. */
export interface WeekFlags {
  R1: boolean;        // multiple pack sizes in one week
  R2: boolean;        // more than 3 producing SKUs
  R3: boolean;        // total production exceeds ceiling
  R4: boolean;        // Full maintenance week with any production
  no_rule: boolean;   // producing but no ceiling could be determined
  over: number;       // units above ceiling (> 0 only when R3 is true)
}

export interface ProductionResponse {
  rows: PlanCell[];
  week_totals: Record<string, number>;   // week_key -> total EA
  week_flags: Record<string, WeekFlags>; // week_key -> flag set
  changeovers: number;
}

// ── GET /api/summary ─────────────────────────────────────────────────────────
// "counts" is a plain dict (colour -> count), not an array.
// "original_vs_plan" holds "original" and "working" colour-count dicts.

export interface SummaryResponse {
  counts: Record<string, number>;            // colour -> count
  original_vs_plan: {
    original: Record<string, number>;        // colour -> count (SNP baseline)
    working: Record<string, number>;         // colour -> count (current working copy)
  };
}

// ── POST /api/production/edit ────────────────────────────────────────────────

export interface EditCellRequest {
  sku_code: string;
  week_key: string;
  qty: number;
}

/** Backend returns status + echoed fields, NOT {ok, violations}. */
export interface EditCellResponse {
  status: string;    // "ok"
  sku_code: string;
  week_key: string;
  qty: number;
}

// ── POST /api/production/save ────────────────────────────────────────────────

export interface SaveResponse {
  status: string;  // "ok"
  saved: number;   // rows written (or would-be in demo mode)
}

// ── POST /api/production/discard, POST /api/production/reset-week ────────────

export interface DiscardResponse {
  status: string;  // "ok"
  cleared: number; // overlay entries removed
}

// ── POST /api/production/reset-week (request body) ───────────────────────────

export interface ResetWeekRequest {
  week_key: string;
}

// ── PUT /api/parameters ───────────────────────────────────────────────────────

export interface PutParameterRequest {
  name: string;
  value: number;
}

// ── PUT /api/weeks ────────────────────────────────────────────────────────────

export interface PutWeekRequest {
  week_key: string;
  maintenance_type?: string;
  is_locked?: boolean;
  note?: string;
}

// ── PUT /api/skus ─────────────────────────────────────────────────────────────

export interface PutSKURequest {
  sku_code: string;
  priority?: number;
  status?: string;
}

// ── PUT response (all three PUT endpoints return {status: "ok"}) ──────────────

export interface PutResponse {
  status: string;  // "ok"
}

// ── POST /api/genie/ask ───────────────────────────────────────────────────────

export interface GenieAskRequest {
  question: string;
}

export interface GenieAskResponse {
  conversation_id: string;
  message_id: string;
}

// ── GET /api/genie/poll ───────────────────────────────────────────────────────

export interface GeniePollResponse {
  status: string;  // "pending" | "complete" | "error"
  answer?: string;
}

// ── Fetch primitives ──────────────────────────────────────────────────────────

async function apiFetch<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    credentials: 'same-origin',
    ...options,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`API ${res.status} ${res.statusText}${body ? ': ' + body : ''}`);
  }
  return res.json() as Promise<T>;
}

function jsonPost<T>(url: string, body: unknown): Promise<T> {
  return apiFetch<T>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

function jsonPut<T>(url: string, body: unknown): Promise<T> {
  return apiFetch<T>(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

// ── Exported API functions ────────────────────────────────────────────────────

export const fetchConfig = (): Promise<ConfigResponse> =>
  apiFetch<ConfigResponse>('/api/config');

export const fetchSupply = (): Promise<SupplyResponse> =>
  apiFetch<SupplyResponse>('/api/supply');

export const fetchProduction = (): Promise<ProductionResponse> =>
  apiFetch<ProductionResponse>('/api/production');

export const fetchSummary = (): Promise<SummaryResponse> =>
  apiFetch<SummaryResponse>('/api/summary');

export const editCell = (req: EditCellRequest): Promise<EditCellResponse> =>
  jsonPost<EditCellResponse>('/api/production/edit', req);

export const saveProduction = (): Promise<SaveResponse> =>
  jsonPost<SaveResponse>('/api/production/save', {});

export const discardProduction = (): Promise<DiscardResponse> =>
  jsonPost<DiscardResponse>('/api/production/discard', {});

export const resetWeek = (req: ResetWeekRequest): Promise<DiscardResponse> =>
  jsonPost<DiscardResponse>('/api/production/reset-week', req);

export const updateParameter = (req: PutParameterRequest): Promise<PutResponse> =>
  jsonPut<PutResponse>('/api/parameters', req);

export const updateWeek = (req: PutWeekRequest): Promise<PutResponse> =>
  jsonPut<PutResponse>('/api/weeks', req);

export const updateSKU = (req: PutSKURequest): Promise<PutResponse> =>
  jsonPut<PutResponse>('/api/skus', req);

export const genieAsk = (req: GenieAskRequest): Promise<GenieAskResponse> =>
  jsonPost<GenieAskResponse>('/api/genie/ask', req);

export const geniePoll = (
  conversationId: string,
  messageId: string,
): Promise<GeniePollResponse> =>
  apiFetch<GeniePollResponse>(
    `/api/genie/poll?conversation_id=${encodeURIComponent(conversationId)}&message_id=${encodeURIComponent(messageId)}`,
  );

export const recalc = (): Promise<unknown> =>
  jsonPost<unknown>('/api/recalc', {});
