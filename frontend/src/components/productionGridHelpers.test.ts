import { describe, it, expect } from 'vitest';
import { computeDirtyCount, formatBreachMessage, buildServerMap } from './productionGridHelpers';
import type { PlanCell, CapacityFlag } from '../api';

// ── helpers ────────────────────────────────────────────────────────────────────

function row(skuCode: string, weekKey: string, qty: number): PlanCell {
  return { sku_code: skuCode, week_key: weekKey, planned_qty: qty, orig_qty: qty };
}

function flag(weekKey: string, rule: string, message = ''): CapacityFlag {
  return { week_key: weekKey, rule, message };
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

// ── formatBreachMessage ───────────────────────────────────────────────────────

describe('formatBreachMessage', () => {
  it('formats R1 correctly', () => {
    const msg = formatBreachMessage(flag('2026-W35', 'R1'));
    expect(msg).toContain('2026-W35');
    expect(msg).toContain('R1');
    expect(msg).toContain('pack size');
  });

  it('formats R2 correctly', () => {
    const msg = formatBreachMessage(flag('2026-W36', 'R2'));
    expect(msg).toContain('2026-W36');
    expect(msg).toContain('R2');
    expect(msg).toContain('3');
  });

  it('formats R3 without detail when message is empty', () => {
    const msg = formatBreachMessage(flag('2026-W37', 'R3'));
    expect(msg).toContain('2026-W37');
    expect(msg).toContain('R3');
    expect(msg).toContain('capacity ceiling');
  });

  it('formats R3 with over-capacity detail from message', () => {
    const msg = formatBreachMessage(flag('2026-W38', 'R3', 'over by 50,000 EA'));
    expect(msg).toContain('over by 50,000 EA');
    expect(msg).toContain('R3');
  });

  it('formats R4 correctly', () => {
    const msg = formatBreachMessage(flag('2026-W40', 'R4'));
    expect(msg).toContain('R4');
    expect(msg).toContain('maintenance');
  });

  it('formats R5 correctly', () => {
    const msg = formatBreachMessage(flag('2026-W42', 'R5'));
    expect(msg).toContain('R5');
  });

  it('handles unknown rules gracefully', () => {
    const msg = formatBreachMessage(flag('2026-W43', 'R9', 'custom violation'));
    expect(msg).toContain('2026-W43');
    expect(msg).toContain('R9');
    expect(msg).toContain('custom violation');
  });

  it('never uses em dashes', () => {
    const flags: CapacityFlag[] = ['R1', 'R2', 'R3', 'R4', 'R5'].map(r =>
      flag('2026-W35', r, 'some detail'),
    );
    for (const f of flags) {
      expect(formatBreachMessage(f)).not.toContain('—'); // em dash
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
