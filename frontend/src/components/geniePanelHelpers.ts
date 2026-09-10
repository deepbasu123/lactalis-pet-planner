/**
 * geniePanelHelpers.ts
 *
 * Pure helpers for GeniePanel polling state and row formatting.
 * Extracted for testability -- no React imports, no side effects.
 */

/**
 * Genie MessageStatus values that indicate a terminal state.
 * Polling should stop once one of these is received.
 *
 * The SDK enum can produce additional strings in future SDK versions.
 * We treat anything NOT in this set (including null / undefined) as
 * "still in progress" so the polling loop continues safely.
 */
const TERMINAL_STATUSES = new Set([
  'COMPLETED',
  'FAILED',
  'CANCELED',
  'CANCELLED', // alternate spelling guard
]);

/**
 * Returns true when the polling loop should continue waiting for a response.
 * Returns false when the message has reached a terminal state.
 *
 * Null / undefined status means "not yet assigned" -- treat as in-progress.
 */
export function shouldContinuePolling(status: string | null | undefined): boolean {
  if (status == null || status === '') return true;
  return !TERMINAL_STATUSES.has(status);
}

/**
 * Returns the first `maxRows` rows from a data array.
 * Returns the original array unchanged when it is already within the limit,
 * so callers can use reference equality to check whether capping occurred.
 */
export function capRows<T>(rows: T[], maxRows: number): T[] {
  return rows.length > maxRows ? rows.slice(0, maxRows) : rows;
}

/**
 * Returns true if an API error represents a 503 "Genie not configured" response.
 * This distinguishes the "no space ID set yet" state from transient network errors.
 */
export function isGenieNotConfigured(err: unknown): boolean {
  if (!(err instanceof Error)) return false;
  // apiFetch throws errors with messages like "API 503 Service Unavailable: ..."
  return err.message.includes('503');
}
