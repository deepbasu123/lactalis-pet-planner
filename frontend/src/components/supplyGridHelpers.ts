/**
 * Pure helper functions for the SupplyGrid component.
 * Exported for unit testing.
 */

/**
 * Format a supply grid numeric value with thousands separators.
 * Negative values are displayed with a leading minus: "-43,558".
 * Values are rounded to 0 decimal places.
 */
export function formatValue(n: number): string {
  const rounded = Math.round(n);
  const abs = Math.abs(rounded);
  const formatted = new Intl.NumberFormat('en-AU', { maximumFractionDigits: 0 }).format(abs);
  return rounded < 0 ? `-${formatted}` : formatted;
}

/**
 * Extract the ISO week label from a week_key string.
 * "2026-W35" -> "W35",  "2026-W04" -> "W04"
 */
export function isoWeekLabel(weekKey: string): string {
  const m = weekKey.match(/W(\d+)$/);
  return m ? `W${m[1]}` : weekKey;
}

/**
 * Format an ISO date string as a short display date.
 * "2026-08-24" -> "24 Aug"
 */
export function fmtShortDate(dateStr: string): string {
  if (!dateStr) return '';
  // Append T00:00:00 to avoid UTC vs local timezone off-by-one on the date
  const d = new Date(`${dateStr}T00:00:00`);
  if (isNaN(d.getTime())) return dateStr;
  return d.toLocaleDateString('en-AU', { day: 'numeric', month: 'short' });
}
