/**
 * Pure helper functions for the ProductionGrid component.
 * Exported for unit testing.
 */

import type { CapacityFlag, PlanCell } from '../api';

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

/**
 * Format a capacity flag into a short, human-readable breach message.
 * Uses no em dashes. For R3, includes the over-capacity detail from flag.message.
 */
export function formatBreachMessage(flag: CapacityFlag): string {
  const week = flag.week_key;
  switch (flag.rule) {
    case 'R1':
      return `${week}: Multiple pack sizes planned in one week (R1)`;
    case 'R2':
      return `${week}: More than 3 producing SKUs in one week (R2)`;
    case 'R3': {
      const detail = flag.message ? ` - ${flag.message}` : '';
      return `${week}: Over weekly capacity ceiling${detail} (R3)`;
    }
    case 'R4':
      return `${week}: Production planned in a full-maintenance week (R4)`;
    case 'R5':
      return `${week}: Pack size changeover noted (R5)`;
    default:
      return `${week}: ${flag.rule}${flag.message ? ' - ' + flag.message : ''}`;
  }
}

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
