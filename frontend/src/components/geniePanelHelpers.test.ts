import { describe, it, expect } from 'vitest';
import {
  shouldContinuePolling,
  capRows,
  isGenieNotConfigured,
} from './geniePanelHelpers';

// ── shouldContinuePolling ─────────────────────────────────────────────────────

describe('shouldContinuePolling', () => {
  it('returns true for null (not yet assigned)', () => {
    expect(shouldContinuePolling(null)).toBe(true);
  });

  it('returns true for undefined', () => {
    expect(shouldContinuePolling(undefined)).toBe(true);
  });

  it('returns true for empty string', () => {
    expect(shouldContinuePolling('')).toBe(true);
  });

  it('returns true for unknown in-progress statuses', () => {
    for (const status of ['PENDING', 'EXECUTING_QUERY', 'WAITING', 'IN_PROGRESS']) {
      expect(shouldContinuePolling(status)).toBe(true);
    }
  });

  it('returns false for COMPLETED', () => {
    expect(shouldContinuePolling('COMPLETED')).toBe(false);
  });

  it('returns false for FAILED', () => {
    expect(shouldContinuePolling('FAILED')).toBe(false);
  });

  it('returns false for CANCELED', () => {
    expect(shouldContinuePolling('CANCELED')).toBe(false);
  });

  it('returns false for CANCELLED (alternate spelling)', () => {
    expect(shouldContinuePolling('CANCELLED')).toBe(false);
  });

  it('is case-sensitive: lowercase "completed" is treated as in-progress', () => {
    // Guard against accidental case folding -- the SDK returns uppercase values.
    expect(shouldContinuePolling('completed')).toBe(true);
  });
});

// ── capRows ───────────────────────────────────────────────────────────────────

describe('capRows', () => {
  const rows = Array.from({ length: 25 }, (_, i) => [i]);

  it('returns the original array when length <= maxRows', () => {
    const r = capRows(rows, 25);
    expect(r).toBe(rows); // same reference -- no copy
  });

  it('returns a sliced array when length > maxRows', () => {
    const r = capRows(rows, 20);
    expect(r).toHaveLength(20);
    expect(r).not.toBe(rows);
  });

  it('caps to exactly maxRows', () => {
    expect(capRows(rows, 1)).toHaveLength(1);
    expect(capRows(rows, 20)).toHaveLength(20);
  });

  it('handles an empty array', () => {
    expect(capRows([], 10)).toHaveLength(0);
  });

  it('preserves row content after capping', () => {
    const r = capRows(rows, 3);
    expect(r[0]).toEqual([0]);
    expect(r[2]).toEqual([2]);
  });
});

// ── isGenieNotConfigured ──────────────────────────────────────────────────────

describe('isGenieNotConfigured', () => {
  it('returns true for a 503 error message from apiFetch', () => {
    const err = new Error('API 503 Service Unavailable: Genie is not configured yet.');
    expect(isGenieNotConfigured(err)).toBe(true);
  });

  it('returns false for a 502 error', () => {
    const err = new Error('API 502 Bad Gateway: Genie could not answer right now.');
    expect(isGenieNotConfigured(err)).toBe(false);
  });

  it('returns false for a 404 error', () => {
    const err = new Error('API 404 Not Found');
    expect(isGenieNotConfigured(err)).toBe(false);
  });

  it('returns false for a generic network error (no status code)', () => {
    const err = new Error('Failed to fetch');
    expect(isGenieNotConfigured(err)).toBe(false);
  });

  it('returns false for non-Error values', () => {
    expect(isGenieNotConfigured('503')).toBe(false);
    expect(isGenieNotConfigured(503)).toBe(false);
    expect(isGenieNotConfigured(null)).toBe(false);
    expect(isGenieNotConfigured(undefined)).toBe(false);
  });
});
