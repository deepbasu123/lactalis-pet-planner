/**
 * Unit tests for summaryHelpers — pure functions, no DOM or React needed.
 */

import { describe, it, expect } from 'vitest';
import { computeChanges, fmtChange } from './summaryHelpers';

describe('computeChanges', () => {
  it('returns zero change when original and working are identical', () => {
    const counts = { green: 50, amber: 10, red: 5 };
    const result = computeChanges(counts, counts);
    expect(result).toEqual({ green: 0, amber: 0, red: 0 });
  });

  it('computes positive change when working has more cells in a band', () => {
    const original = { green: 40, amber: 10 };
    const working  = { green: 50, amber: 10 };
    const result = computeChanges(original, working);
    expect(result.green).toBe(10);
    expect(result.amber).toBe(0);
  });

  it('computes negative change when working has fewer cells in a band', () => {
    const original = { red: 20, green: 30 };
    const working  = { red: 15, green: 35 };
    const result = computeChanges(original, working);
    expect(result.red).toBe(-5);
    expect(result.green).toBe(5);
  });

  it('includes keys present in working but absent from original', () => {
    const original = { green: 50 };
    const working  = { green: 40, amber: 10 };
    const result = computeChanges(original, working);
    expect(result.amber).toBe(10);
  });

  it('includes keys present in original but absent from working (as negative)', () => {
    const original = { dark_blue: 30, green: 20 };
    const working  = { dark_blue: 30 };
    const result = computeChanges(original, working);
    expect(result.green).toBe(-20);
  });

  it('handles completely different band sets', () => {
    const original = { red: 100 };
    const working  = { green: 100 };
    const result = computeChanges(original, working);
    expect(result.red).toBe(-100);
    expect(result.green).toBe(100);
  });
});

describe('fmtChange', () => {
  it('prefixes positive values with +', () => {
    expect(fmtChange(7)).toBe('+7');
  });

  it('negative values include the minus sign', () => {
    expect(fmtChange(-3)).toBe('-3');
  });

  it('returns "0" for no change', () => {
    expect(fmtChange(0)).toBe('0');
  });
});
