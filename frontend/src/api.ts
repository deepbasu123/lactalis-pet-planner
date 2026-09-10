/**
 * Typed REST client for the PET Line Planner FastAPI backend.
 * All requests use credentials: "same-origin" so the session cookie
 * (required by edit endpoints) is forwarded on same-origin requests.
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

export interface RunMeta {
  sku_count: number;
  week_count: number;
  total_production: number; // total planned production (EA) across all SKUs/weeks
}

// ── GET /api/config ──────────────────────────────────────────────────────────

export interface ConfigResponse {
  skus: SKU[];
  weeks: Week[];
  parameters: Parameter[];
  run_meta: RunMeta;
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

export interface PlanCell {
  sku_code: string;
  week_key: string;
  planned_qty: number;
  orig_qty: number;
}

export interface CapacityFlag {
  week_key: string;
  rule: string;    // "R1" | "R2" | "R3" | "R4" | "R5"
  message: string;
}

export interface ProductionResponse {
  rows: PlanCell[];
  weekly_totals: Record<string, number>; // week_key -> total EA
  capacity_flags: CapacityFlag[];
}

// ── GET /api/summary ─────────────────────────────────────────────────────────

export interface ColourCount {
  colour: string;
  count: number;
}

export interface OrigVsPlan {
  original_total: number;
  plan_total: number;
  diff: number;
}

export interface SummaryResponse {
  colour_counts: ColourCount[];
  original_vs_plan: OrigVsPlan;
}

// ── POST /api/production/edit ────────────────────────────────────────────────

export interface EditCellRequest {
  sku_code: string;
  week_key: string;
  qty: number;
}

export interface EditCellResponse {
  ok: boolean;
  violations: string[];
}

// ── Generic ok response ───────────────────────────────────────────────────────

export interface OkResponse {
  ok: boolean;
}

// ── POST /api/production/reset-week ─────────────────────────────────────────

export interface ResetWeekRequest {
  week_key: string;
}

// ── PUT /api/parameters ───────────────────────────────────────────────────────

export interface ParameterUpdates {
  updates: Record<string, number>;
}

// ── PUT /api/weeks ────────────────────────────────────────────────────────────

export interface WeekUpdate {
  week_key: string;
  updates: Partial<Week>;
}

// ── PUT /api/skus ─────────────────────────────────────────────────────────────

export interface SKUUpdate {
  sku_code: string;
  updates: Partial<SKU>;
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

export const saveProduction = (): Promise<OkResponse> =>
  jsonPost<OkResponse>('/api/production/save', {});

export const discardProduction = (): Promise<OkResponse> =>
  jsonPost<OkResponse>('/api/production/discard', {});

export const resetWeek = (req: ResetWeekRequest): Promise<OkResponse> =>
  jsonPost<OkResponse>('/api/production/reset-week', req);

export const updateParameters = (req: ParameterUpdates): Promise<OkResponse> =>
  jsonPut<OkResponse>('/api/parameters', req);

export const updateWeek = (req: WeekUpdate): Promise<OkResponse> =>
  jsonPut<OkResponse>('/api/weeks', req);

export const updateSKU = (req: SKUUpdate): Promise<OkResponse> =>
  jsonPut<OkResponse>('/api/skus', req);

export const genieAsk = (req: GenieAskRequest): Promise<GenieAskResponse> =>
  jsonPost<GenieAskResponse>('/api/genie/ask', req);

export const geniePoll = (
  conversationId: string,
  messageId: string,
): Promise<GeniePollResponse> =>
  apiFetch<GeniePollResponse>(
    `/api/genie/poll?conversation_id=${encodeURIComponent(conversationId)}&message_id=${encodeURIComponent(messageId)}`,
  );

export const recalc = (): Promise<OkResponse> =>
  jsonPost<OkResponse>('/api/recalc', {});
