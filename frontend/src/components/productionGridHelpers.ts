/**
 * Pure helper functions for the ProductionGrid component.
 * Exported for unit testing.
 *
 * The real backend returns week_flags as a dict keyed by week_key:
 *   { "2026-W35": { R1: bool, R2: bool, R3: bool, R4: bool, no_rule: bool, over: int }, … }
 * This module converts that dict into flat BreachEntry records for display.
 */

import type { PlanCell, WeekFlags } from '../api';

// ── BreachEntry ───────────────────────────────────────────────────────────────

/**
 * One flattened capacity violation, ready for display.
 * Derived from a (weekKey, WeekFlags) pair via collectBreaches().
 */
export interface BreachEntry {
  week_key: string;
  rule: string;   // "R1" | "R2" | "R3" | "R4" | "no_rule"
  over: number;   // > 0 only for R3
}

// ── collectBreaches ───────────────────────────────────────────────────────────

/**
 * Flatten the week_flags dict into an ordered list of BreachEntry records.
 * Weeks are iterated in insertion order (which matches the 1..52 horizon order
 * returned by the backend). Within a week, rule priority is R4 > R1 > R2 > R3 > no_rule.
 */
export function collectBreaches(
  weekFlags: Record<string, WeekFlags>,
): BreachEntry[] {
  const breaches: BreachEntry[] = [];
  for (const [weekKey, flags] of Object.entries(weekFlags ?? {})) {
    if (flags.R4) breaches.push({ week_key: weekKey, rule: 'R4', over: 0 });
    if (flags.R1) breaches.push({ week_key: weekKey, rule: 'R1', over: 0 });
    if (flags.R2) breaches.push({ week_key: weekKey, rule: 'R2', over: 0 });
    if (flags.R3) breaches.push({ week_key: weekKey, rule: 'R3', over: flags.over });
    if (flags.no_rule) breaches.push({ week_key: weekKey, rule: 'no_rule', over: 0 });
  }
  return breaches;
}

// ── actionableBreaches ────────────────────────────────────────────────────────

/**
 * Breaches the planner can actually resolve: everything collectBreaches finds,
 * minus locked (time-fenced) weeks. Locked weeks run the committed SNP baseline
 * (all SKUs, both packs) so they perpetually flag R1/R2, but they cannot be
 * edited or auto-fixed. Counting them as outstanding violations makes Auto-fix
 * look broken because the banner can never reach zero. This returns only the
 * breaches in editable weeks.
 */
export function actionableBreaches(
  weekFlags: Record<string, WeekFlags>,
  lockedWeeks: Set<string> | string[],
): BreachEntry[] {
  const locked = lockedWeeks instanceof Set ? lockedWeeks : new Set(lockedWeeks);
  return collectBreaches(weekFlags).filter((b) => !locked.has(b.week_key));
}

// ── formatBreachMessage ───────────────────────────────────────────────────────

/**
 * Format a BreachEntry into a short, human-readable breach message.
 * Uses no em dashes.
 */
export function formatBreachMessage(entry: BreachEntry): string {
  const { week_key: week, rule, over } = entry;
  switch (rule) {
    case 'R1':
      return `${week}: Multiple pack sizes planned in one week (R1)`;
    case 'R2':
      return `${week}: More than 3 producing SKUs in one week (R2)`;
    case 'R3': {
      const detail = over > 0 ? ` - over by ${over.toLocaleString('en-AU')} EA` : '';
      return `${week}: Over weekly capacity ceiling${detail} (R3)`;
    }
    case 'R4':
      return `${week}: Production planned in a full-maintenance week (R4)`;
    case 'no_rule':
      return `${week}: Producing but no capacity ceiling could be determined`;
    default:
      return `${week}: ${rule}`;
  }
}

// ── computeDirtyCount ─────────────────────────────────────────────────────────

/**
 * Count cells in dirtyMap whose value genuinely differs from the server row.
 * No-op edits (user typed the same value back) do not count as dirty.
 * Keys in dirtyMap are "skuCode|weekKey".
 */
export function computeDirtyCount(
  dirtyMap: Record<string, number>,
  serverRows: PlanCell[],
): number {
  const serverByKey = new Map<string, number>();
  for (const row of serverRows) {
    serverByKey.set(`${row.sku_code}|${row.week_key}`, row.planned_qty);
  }
  let count = 0;
  for (const [key, val] of Object.entries(dirtyMap)) {
    const serverVal = serverByKey.get(key) ?? 0;
    if (Math.round(val) !== Math.round(serverVal)) {
      count++;
    }
  }
  return count;
}

// ── buildServerMap ────────────────────────────────────────────────────────────

/**
 * Build a lookup map from "skuCode|weekKey" to the server PlanCell.
 * Kept separate so the ProductionGrid component can memoize it.
 */
export function buildServerMap(rows: PlanCell[]): Map<string, PlanCell> {
  const m = new Map<string, PlanCell>();
  for (const row of rows) {
    m.set(`${row.sku_code}|${row.week_key}`, row);
  }
  return m;
}
