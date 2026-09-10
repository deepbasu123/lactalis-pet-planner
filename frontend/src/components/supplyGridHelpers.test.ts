import { describe, it, expect } from 'vitest';
import { formatValue, isoWeekLabel, fmtShortDate } from './supplyGridHelpers';

// ── formatValue ───────────────────────────────────────────────────────────────

describe('formatValue', () => {
  it('formats a positive integer with thousands separator', () => {
    expect(formatValue(125400)).toBe('125,400');
  });

  it('formats a negative value with leading minus and separator', () => {
    expect(formatValue(-43558)).toBe('-43,558');
  });

  it('formats zero as 0', () => {
    expect(formatValue(0)).toBe('0');
  });

  it('rounds positive decimals', () => {
    expect(formatValue(125400.6)).toBe('125,401');
  });

  it('rounds negative decimals (rounds away from 0 for .5)', () => {
    // Math.round(-0.5) = 0; but -43558.6 rounds to -43559
    expect(formatValue(-43558.6)).toBe('-43,559');
  });

  it('handles small numbers without a separator', () => {
    expect(formatValue(999)).toBe('999');
  });

  it('formats large values correctly', () => {
    expect(formatValue(1000000)).toBe('1,000,000');
  });
});

// ── isoWeekLabel ──────────────────────────────────────────────────────────────

describe('isoWeekLabel', () => {
  it('extracts the week part from a standard week_key', () => {
    expect(isoWeekLabel('2026-W35')).toBe('W35');
  });

  it('preserves zero-padded single-digit week numbers', () => {
    expect(isoWeekLabel('2026-W04')).toBe('W04');
  });

  it('returns the full string when the format is not recognised', () => {
    expect(isoWeekLabel('unknown')).toBe('unknown');
  });

  it('works for week 52', () => {
    expect(isoWeekLabel('2026-W52')).toBe('W52');
  });
});

// ── fmtShortDate ──────────────────────────────────────────────────────────────

describe('fmtShortDate', () => {
  it('returns empty string for empty input', () => {
    expect(fmtShortDate('')).toBe('');
  });

  it('returns the raw string when the date is not parseable', () => {
    expect(fmtShortDate('not-a-date')).toBe('not-a-date');
  });

  it('formats a valid ISO date as day-month short', () => {
    // "24 Aug" — exact string may vary slightly by runtime locale; test structure
    const result = fmtShortDate('2026-08-24');
    expect(result).toMatch(/24/);
    expect(result).toMatch(/Aug/);
  });
});
