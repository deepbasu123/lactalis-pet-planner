/**
 * Pure helpers for the Traffic Light Summary tab.
 *
 * These are kept framework-free so they are trivially testable with vitest.
 */

/**
 * Compute the cell-count change between the SNP baseline and the current
 * working plan for each colour band.
 *
 * @param original  Colour -> count dict from original_vs_plan.original
 * @param working   Colour -> count dict from original_vs_plan.working
 * @returns         Colour -> (working - original) for every key present in
 *                  either dict; zero-change entries are included so the
 *                  summary table always shows every band.
 */
export function computeChanges(
  original: Record<string, number>,
  working: Record<string, number>,
): Record<string, number> {
  const allKeys = new Set([...Object.keys(original), ...Object.keys(working)]);
  const result: Record<string, number> = {};
  for (const key of allKeys) {
    result[key] = (working[key] ?? 0) - (original[key] ?? 0);
  }
  return result;
}

/**
 * Format a change value with a leading + for positive, - for negative,
 * and a plain "0" for no change.
 */
export function fmtChange(delta: number): string {
  if (delta > 0) return `+${delta}`;
  if (delta < 0) return `${delta}`;
  return '0';
}
