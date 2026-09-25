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

// ── POST /api/production/autofix ─────────────────────────────────────────────
// Strict-trim rule resolver. Writes the corrective changes into the session
// overlay (staged as unsaved edits) and returns the changed cells + a report.

export interface AutoFixReport {
  weeks_changed: number;
  cells_zeroed: number;
  cells_trimmed: number;
  volume_dropped: number;
  locked_weeks_skipped: number;
}

export interface AutoFixResponse {
  status?: string;            // "ok"
  changed?: PlanCell[];       // classic backend: cells whose planned_qty changed
  report?: AutoFixReport;     // classic backend: strict-trim report
  // Re-architected backend stages the fix into the overlay server-side and may
  // instead return the refreshed grids. Both shapes are tolerated by callers.
  supply?: SupplyCell[] | { rows: SupplyCell[] };
  production?: ProductionResponse;
  summary?: SummaryResponse;
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
// Backend: body {question, conversation_id?} -> {conversation_id, message_id}
// HTTP 503 when PET_GENIE_SPACE_ID is not configured.

export interface GenieAskRequest {
  question: string;
  conversation_id?: string;  // omit to start a new conversation; include to continue
}

export interface GenieAskResponse {
  conversation_id: string;
  message_id: string;
}

// ── GET /api/genie/poll ───────────────────────────────────────────────────────
// Backend: {status, text, sql?, rows?}
//   status   -- Genie MessageStatus.value: "COMPLETED" | "FAILED" | "EXECUTING_QUERY" | ...
//   text     -- narrative answer text, null until the message is complete
//   sql      -- SQL generated by Genie, present when a query attachment exists
//   rows     -- {columns: string[], data: unknown[][]} capped to 100 rows
// HTTP 503 when PET_GENIE_SPACE_ID is not configured.

export interface GenieRowsPayload {
  columns: string[];
  data: unknown[][];
}

export interface GeniePollResponse {
  status: string | null;  // MessageStatus.value from the SDK; null when not yet set
  text: string | null;    // narrative answer text; null while still processing
  sql?: string;           // SQL attachment, when present
  rows?: GenieRowsPayload; // query result rows, when present and execution succeeded
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

// ── Scenario ──────────────────────────────────────────────────────────────────
// The planner works in a single named scenario. The re-architected backend
// applies each edit as an overlay over the pipeline-seeded Silver baseline and
// re-runs the SAME Gold rule SQL on a warm serverless warehouse (~1-2s). The UI
// runs zero business logic — it only reads Gold and writes edits.
export const SCENARIO = 'working';

// ── Supply normalisation ────────────────────────────────────────────────────
// The /api/supply payload (and the "supply" field of an edit/autofix bundle)
// may arrive as a bare array of rows or as { rows: [...] }. Normalise to rows.
export function normalizeSupply(payload: unknown): SupplyCell[] {
  if (Array.isArray(payload)) return payload as SupplyCell[];
  if (payload && typeof payload === 'object' && Array.isArray((payload as { rows?: unknown }).rows)) {
    return (payload as { rows: SupplyCell[] }).rows;
  }
  return [];
}

// ── Exported API functions ────────────────────────────────────────────────────

export const fetchConfig = async (): Promise<ConfigResponse> => {
  // New backend endpoint is /api/meta; tolerate a missing derived meta block.
  const data = await apiFetch<Partial<ConfigResponse> & { total_production?: number }>('/api/meta');
  const meta: RunMeta = data.meta ?? {
    sku_count: (data.skus ?? []).length,
    week_count: (data.weeks ?? []).length,
    total_production: data.total_production ?? 0,
  };
  return {
    skus: data.skus ?? [],
    weeks: data.weeks ?? [],
    parameters: data.parameters ?? [],
    meta,
  };
};

export const fetchSupply = async (): Promise<SupplyResponse> => {
  const data = await apiFetch<unknown>(`/api/supply?scenario=${encodeURIComponent(SCENARIO)}`);
  return { rows: normalizeSupply(data) };
};

export const fetchProduction = (): Promise<ProductionResponse> =>
  apiFetch<ProductionResponse>(`/api/production?scenario=${encodeURIComponent(SCENARIO)}`);

export const fetchSummary = (): Promise<SummaryResponse> =>
  apiFetch<SummaryResponse>(`/api/summary?scenario=${encodeURIComponent(SCENARIO)}`);

export const editCell = (req: EditCellRequest): Promise<EditCellResponse> =>
  jsonPost<EditCellResponse>('/api/plan/edit', {
    sku_code: req.sku_code,
    week_key: req.week_key,
    planned_qty: req.qty,
    scenario: SCENARIO,
  });

export const saveProduction = (): Promise<SaveResponse> =>
  jsonPost<SaveResponse>('/api/plan/save', { scenario: SCENARIO });

export const discardProduction = (): Promise<DiscardResponse> =>
  jsonPost<DiscardResponse>('/api/plan/discard', { scenario: SCENARIO });

export const resetWeek = (req: ResetWeekRequest): Promise<DiscardResponse> =>
  jsonPost<DiscardResponse>('/api/plan/reset-week', { week_key: req.week_key, scenario: SCENARIO });

export const autoFixBreaches = (): Promise<AutoFixResponse> =>
  jsonPost<AutoFixResponse>('/api/autofix', { scenario: SCENARIO });

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

// ── GET /api/export/excel, GET /api/export/pdf ───────────────────────────────
// Both return a binary file (not JSON): a multi-sheet .xlsx workbook and a
// formatted .pdf report respectively, built from the current session's
// working plan (including unsaved edits). triggerDownload() fetches the
// blob, derives the filename from the Content-Disposition header the
// backend sets (falling back to a fixed name if that header is ever
// missing), and programmatically clicks a temporary <a> to save it --
// this keeps error handling (non-2xx, network failure) inside the normal
// try/catch call sites use for every other api.ts function.

/** Extract the filename="..." token from a Content-Disposition header value. */
export function filenameFromContentDisposition(
  header: string | null | undefined,
  fallback: string,
): string {
  if (!header) return fallback;
  const match = /filename="?([^";]+)"?/i.exec(header);
  return match?.[1] ?? fallback;
}

async function triggerDownload(url: string, fallbackFilename: string): Promise<void> {
  const res = await fetch(url, { credentials: 'same-origin' });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`API ${res.status} ${res.statusText}${body ? ': ' + body : ''}`);
  }
  const blob = await res.blob();
  const filename = filenameFromContentDisposition(
    res.headers.get('Content-Disposition'),
    fallbackFilename,
  );
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(objectUrl);
}

// ── POST /api/upload, GET /api/pipeline/status ───────────────────────────────
// Upload a "PET Traffic Lights" .xlsx. The app writes it to a Unity Catalog
// Volume and triggers the Lakeflow Declarative Pipeline (Bronze -> Silver ->
// Gold). No workbook parsing happens in the browser or the app process.

export interface UploadResponse {
  run_id: string;
}

export interface PipelineStatus {
  state: string;   // QUEUED | RUNNING | COMPLETED | FAILED (or a stage name)
  stage?: string;  // BRONZE | SILVER | GOLD, when the backend reports it
  detail?: string; // human-readable status / error message
}

export const uploadWorkbook = async (file: File): Promise<UploadResponse> => {
  const form = new FormData();
  form.append('file', file);
  // Note: do NOT set Content-Type — the browser adds the multipart boundary.
  const res = await fetch('/api/upload', {
    method: 'POST',
    credentials: 'same-origin',
    body: form,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`API ${res.status} ${res.statusText}${body ? ': ' + body : ''}`);
  }
  return res.json() as Promise<UploadResponse>;
};

export const pipelineStatus = (runId: string): Promise<PipelineStatus> =>
  apiFetch<PipelineStatus>(`/api/pipeline/status?run_id=${encodeURIComponent(runId)}`);

export const exportExcel = (): Promise<void> =>
  triggerDownload(
    `/api/export?format=xlsx&scenario=${encodeURIComponent(SCENARIO)}`,
    'pet_line_planner_export.xlsx',
  );

export const exportPdf = (): Promise<void> =>
  triggerDownload(
    `/api/export?format=pdf&scenario=${encodeURIComponent(SCENARIO)}`,
    'pet_line_planner_report.pdf',
  );
