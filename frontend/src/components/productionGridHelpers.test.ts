import { describe, it, expect } from 'vitest';
import {
  computeDirtyCount,
  formatBreachMessage,
  collectBreaches,
  actionableBreaches,
  buildServerMap,
} from './productionGridHelpers';
import type { BreachEntry } from './productionGridHelpers';
import type { PlanCell, WeekFlags } from '../api';

// ── helpers ────────────────────────────────────────────────────────────────────

function row(skuCode: string, weekKey: string, qty: number): PlanCell {
  return { sku_code: skuCode, week_key: weekKey, planned_qty: qty };
}

function allClear(): WeekFlags {
  return { R1: false, R2: false, R3: false, R4: false, no_rule: false, over: 0 };
}

function breach(entry: Partial<WeekFlags>): WeekFlags {
  return { ...allClear(), ...entry };
}

function entry(week_key: string, rule: string, over = 0): BreachEntry {
  return { week_key, rule, over };
}

// ── computeDirtyCount ─────────────────────────────────────────────────────────

describe('computeDirtyCount', () => {
  const serverRows: PlanCell[] = [
    row('60444', '2026-W35', 100_000),
    row('60444', '2026-W36', 200_000),
    row('61108', '2026-W35', 150_000),
  ];

  it('returns 0 for empty dirty map', () => {
    expect(computeDirtyCount({}, serverRows)).toBe(0);
  });

  it('returns 0 when dirty value matches server value (no-op edit)', () => {
    const dirty = { '60444|2026-W35': 100_000 };
    expect(computeDirtyCount(dirty, serverRows)).toBe(0);
  });

  it('counts 1 when one cell differs from server', () => {
    const dirty = { '60444|2026-W35': 150_000 };
    expect(computeDirtyCount(dirty, serverRows)).toBe(1);
  });

  it('counts multiple distinct dirty cells', () => {
    const dirty = {
      '60444|2026-W35': 150_000,
      '61108|2026-W35': 200_000,
    };
    expect(computeDirtyCount(dirty, serverRows)).toBe(2);
  });

  it('does not count a cell whose dirty value rounds to the same as server', () => {
    // 100_000.4 rounds to 100_000 — same as server
    const dirty = { '60444|2026-W35': 100_000.4 };
    expect(computeDirtyCount(dirty, serverRows)).toBe(0);
  });

  it('treats keys not present in serverRows as dirty (new cells)', () => {
    const dirty = { '99999|2026-W35': 50_000 };
    // serverByKey.get('99999|2026-W35') = undefined -> 0; 50_000 !== 0
    expect(computeDirtyCount(dirty, serverRows)).toBe(1);
  });

  it('returns 0 for empty serverRows when dirtyMap is also empty', () => {
    expect(computeDirtyCount({}, [])).toBe(0);
  });
});

// ── collectBreaches ───────────────────────────────────────────────────────────

describe('collectBreaches', () => {
  it('returns empty array when all flags are clear', () => {
    const flags = { '2026-W35': allClear(), '2026-W36': allClear() };
    expect(collectBreaches(flags)).toHaveLength(0);
  });

  it('returns an entry for each breaching rule', () => {
    const flags = { '2026-W35': breach({ R1: true, R2: true }) };
    const result = collectBreaches(flags);
    expect(result).toHaveLength(2);
    expect(result.some(e => e.rule === 'R1')).toBe(true);
    expect(result.some(e => e.rule === 'R2')).toBe(true);
    expect(result.every(e => e.week_key === '2026-W35')).toBe(true);
  });

  it('includes the over amount for R3', () => {
    const flags = { '2026-W37': breach({ R3: true, over: 75_000 }) };
    const result = collectBreaches(flags);
    const r3 = result.find(e => e.rule === 'R3');
    expect(r3).toBeDefined();
    expect(r3?.over).toBe(75_000);
  });

  it('handles no_rule flag', () => {
    const flags = { '2026-W38': breach({ no_rule: true }) };
    const result = collectBreaches(flags);
    expect(result.some(e => e.rule === 'no_rule')).toBe(true);
  });

  it('collects breaches from multiple weeks', () => {
    const flags = {
      '2026-W35': breach({ R1: true }),
      '2026-W36': allClear(),
      '2026-W37': breach({ R4: true }),
    };
    const result = collectBreaches(flags);
    expect(result).toHaveLength(2);
  });

  it('returns empty array for empty input', () => {
    expect(collectBreaches({})).toHaveLength(0);
  });

  it('handles undefined/null input gracefully (hardened null-safety)', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect(collectBreaches(undefined as any)).toHaveLength(0);
  });
});

// ── formatBreachMessage ───────────────────────────────────────────────────────

describe('formatBreachMessage', () => {
  it('formats R1 correctly', () => {
    const msg = formatBreachMessage(entry('2026-W35', 'R1'));
    expect(msg).toContain('2026-W35');
    expect(msg).toContain('R1');
    expect(msg).toContain('pack size');
  });

  it('formats R2 correctly', () => {
    const msg = formatBreachMessage(entry('2026-W36', 'R2'));
    expect(msg).toContain('2026-W36');
    expect(msg).toContain('R2');
    expect(msg).toContain('3');
  });

  it('formats R3 without detail when over is 0', () => {
    const msg = formatBreachMessage(entry('2026-W37', 'R3', 0));
    expect(msg).toContain('2026-W37');
    expect(msg).toContain('R3');
    expect(msg).toContain('capacity ceiling');
    expect(msg).not.toContain('over by');
  });

  it('formats R3 with over-capacity detail when over > 0', () => {
    const msg = formatBreachMessage(entry('2026-W38', 'R3', 75_000));
    expect(msg).toContain('over by');
    expect(msg).toContain('75,000');
    expect(msg).toContain('R3');
  });

  it('formats R4 correctly', () => {
    const msg = formatBreachMessage(entry('2026-W40', 'R4'));
    expect(msg).toContain('R4');
    expect(msg).toContain('maintenance');
  });

  it('formats no_rule correctly', () => {
    const msg = formatBreachMessage(entry('2026-W42', 'no_rule'));
    expect(msg).toContain('2026-W42');
    expect(msg).toContain('ceiling');
  });

  it('handles unknown rules gracefully', () => {
    const msg = formatBreachMessage(entry('2026-W43', 'R9'));
    expect(msg).toContain('2026-W43');
    expect(msg).toContain('R9');
  });

  it('never uses em dashes', () => {
    const rules = ['R1', 'R2', 'R3', 'R4', 'no_rule'];
    for (const rule of rules) {
      expect(formatBreachMessage(entry('2026-W35', rule, 50_000))).not.toContain('—');
    }
  });
});

// ── buildServerMap ────────────────────────────────────────────────────────────

describe('buildServerMap', () => {
  it('returns an empty map for empty input', () => {
    const m = buildServerMap([]);
    expect(m.size).toBe(0);
  });

  it('keys rows by skuCode|weekKey', () => {
    const rows = [row('60444', '2026-W35', 100_000)];
    const m = buildServerMap(rows);
    expect(m.has('60444|2026-W35')).toBe(true);
    expect(m.get('60444|2026-W35')?.planned_qty).toBe(100_000);
  });

  it('handles multiple rows', () => {
    const rows = [
      row('60444', '2026-W35', 100_000),
      row('61108', '2026-W35', 200_000),
      row('60444', '2026-W36', 150_000),
    ];
    const m = buildServerMap(rows);
    expect(m.size).toBe(3);
    expect(m.get('61108|2026-W35')?.planned_qty).toBe(200_000);
  });
});

describe('actionableBreaches', () => {
  const flags = (o: Partial<WeekFlags>): WeekFlags => ({
    R1: false, R2: false, R3: false, R4: false, no_rule: false, over: 0, ...o,
  });

  it('drops breaches in locked weeks', () => {
    const wf: Record<string, WeekFlags> = {
      '2026-W35': flags({ R1: true, R2: true }), // locked
      '2026-W40': flags({ R1: true, R2: true }), // editable
    };
    const result = actionableBreaches(wf, new Set(['2026-W35']));
    expect(result.every((b) => b.week_key !== '2026-W35')).toBe(true);
    expect(result.filter((b) => b.week_key === '2026-W40').length).toBe(2);
  });

  it('returns empty when only locked weeks breach', () => {
    const wf: Record<string, WeekFlags> = {
      '2026-W35': flags({ R1: true, R2: true }),
      '2026-W36': flags({ R1: true }),
    };
    expect(actionableBreaches(wf, ['2026-W35', '2026-W36'])).toEqual([]);
  });

  it('accepts an array of locked weeks', () => {
    const wf: Record<string, WeekFlags> = {
      '2026-W41': flags({ R3: true, over: 5 }),
    };
    expect(actionableBreaches(wf, []).length).toBe(1);
    expect(actionableBreaches(wf, ['2026-W41'])).toEqual([]);
  });
});
