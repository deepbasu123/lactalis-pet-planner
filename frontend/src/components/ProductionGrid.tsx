/**
 * ProductionGrid — Editable weekly PET production plan.
 *
 * Sits below the hero SupplyGrid on the Production Grid tab. Renders a
 * bounded-scroll table (its own overflow:auto container so sticky cells
 * don't conflict with the page-level sticky header). Each cell supports
 * direct numeric entry and +/- stepper buttons (step 50,000 EA). Dirty
 * cells are highlighted with an amber tint. Locked weeks are read-only.
 *
 * After any accepted edit it calls onDataChange() so the parent bumps
 * dataVersion, which causes SupplyGrid to re-fetch and recolour.
 */

import {
  useState,
  useEffect,
  useMemo,
  useRef,
  useCallback,
} from 'react';
import {
  fetchProduction,
  editCell,
  saveProduction,
  discardProduction,
  resetWeek,
} from '../api';
import type { PlanCell, WeekFlags, Week, SKU } from '../api';
import { formatValue, isoWeekLabel, fmtShortDate } from './supplyGridHelpers';
import {
  computeDirtyCount,
  formatBreachMessage,
  collectBreaches,
  buildServerMap,
} from './productionGridHelpers';
import './ProductionGrid.css';

// ── Constants ──────────────────────────────────────────────────────────────────

const STEP = 50_000;

// ── Lock SVG icon ──────────────────────────────────────────────────────────────

function LockIcon({ size = 12 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 12 14"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <rect x="1.5" y="6" width="9" height="7" rx="1" fill="currentColor" opacity="0.7" />
      <path
        d="M3.5 6V4a2.5 2.5 0 0 1 5 0v2"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        fill="none"
        opacity="0.7"
      />
    </svg>
  );
}

// ── Capacity validation banner ─────────────────────────────────────────────────

function ValidationBanner({ weekFlags }: { weekFlags: Record<string, WeekFlags> }) {
  const breaches = collectBreaches(weekFlags ?? {});
  if (breaches.length === 0) {
    return (
      <div className="pg-banner pg-banner--ok" role="status" aria-live="polite">
        <span className="pg-banner-icon" aria-hidden="true">&#10003;</span>
        <span>All validation rules pass</span>
      </div>
    );
  }
  return (
    <div className="pg-banner pg-banner--breach" role="alert">
      <span className="pg-banner-icon" aria-hidden="true">!</span>
      <div className="pg-banner-content">
        <span className="pg-banner-title">
          {breaches.length} capacity breach{breaches.length > 1 ? 'es' : ''} detected
        </span>
        <ul className="pg-banner-list">
          {breaches.map((b, i) => (
            <li key={i}>{formatBreachMessage(b)}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

// ── Loading skeleton ───────────────────────────────────────────────────────────

function GridSkeleton() {
  return (
    <div className="pg-card" style={{ marginTop: 'var(--sp-6)' }}>
      <div className="pg-skeleton-wrap">
        <div className="pg-skeleton-header">
          <div className="skeleton" style={{ width: 200, height: 22 }} />
          <div style={{ display: 'flex', gap: 8 }}>
            <div className="skeleton" style={{ width: 72, height: 30 }} />
            <div className="skeleton" style={{ width: 72, height: 30 }} />
            <div className="skeleton" style={{ width: 88, height: 30 }} />
          </div>
        </div>
        <div className="pg-skeleton-table">
          {Array.from({ length: 13 }).map((_, i) => (
            <div key={i} className="pg-skeleton-row" style={{ gap: 2 }}>
              <div
                className="skeleton"
                style={{ width: 220, height: i === 0 ? 52 : 38, animationDelay: `${i * 30}ms` }}
              />
              {Array.from({ length: 12 }).map((_, j) => (
                <div
                  key={j}
                  className="skeleton"
                  style={{
                    width: 108,
                    height: i === 0 ? 52 : 38,
                    animationDelay: `${(i * 12 + j) * 12}ms`,
                  }}
                />
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

interface ProductionGridProps {
  weeks: Week[];
  skus: SKU[];
  dataVersion: number;
  onDataChange: () => void;
}

export default function ProductionGrid({
  weeks,
  skus,
  dataVersion,
  onDataChange,
}: ProductionGridProps) {
  // ── Server state ─────────────────────────────────────────────────────────────
  const [serverRows, setServerRows] = useState<PlanCell[]>([]);
  const [serverTotals, setServerTotals] = useState<Record<string, number>>({});
  // week_flags is a dict keyed by week_key -> {R1,R2,R3,R4,no_rule,over}
  const [weekFlagsState, setWeekFlagsState] = useState<Record<string, WeekFlags>>({});
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);

  // ── Local edit state ──────────────────────────────────────────────────────────
  // key format: "skuCode|weekKey"  e.g. "60444|2026-W35"
  const [dirtyMap, setDirtyMap] = useState<Record<string, number>>({});
  const [editingCell, setEditingCell] = useState<{
    skuCode: string;
    weekKey: string;
  } | null>(null);
  const [editingValue, setEditingValue] = useState('');

  // Inline locked-week messages: key -> message text
  const [lockMessages, setLockMessages] = useState<Record<string, string>>({});

  // In-flight API calls per cell
  const [pendingKeys, setPendingKeys] = useState<Set<string>>(new Set());

  // Active week for Reset-Week action
  const [activeWeekKey, setActiveWeekKey] = useState<string | null>(null);

  // ── Action states ─────────────────────────────────────────────────────────────
  const [saving, setSaving] = useState(false);
  const [discarding, setDiscarding] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [saveToast, setSaveToast] = useState(false);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── Fetch production data ─────────────────────────────────────────────────────
  useEffect(() => {
    setLoading(true);
    setFetchError(null);
    void fetchProduction()
      .then((data) => {
        setServerRows(data.rows ?? []);
        setServerTotals(data.week_totals ?? {});
        setWeekFlagsState(data.week_flags ?? {});
      })
      .catch((err: unknown) => {
        setFetchError(err instanceof Error ? err.message : 'Failed to load production data');
      })
      .finally(() => {
        setLoading(false);
      });
  }, [dataVersion]);

  // ── Derived structures ────────────────────────────────────────────────────────
  const serverMap = useMemo(() => buildServerMap(serverRows), [serverRows]);

  const sortedSkus = useMemo(
    () => [...skus].sort((a, b) => a.priority - b.priority),
    [skus],
  );

  const weekMap = useMemo(() => {
    const m = new Map<string, Week>();
    for (const w of weeks) m.set(w.week_key, w);
    return m;
  }, [weeks]);

  // Adjusted weekly totals that reflect local dirty edits
  const computedTotals = useMemo(() => {
    const totals: Record<string, number> = { ...serverTotals };
    for (const [key, newQty] of Object.entries(dirtyMap)) {
      const [skuCode, weekKey] = key.split('|');
      if (!weekKey) continue;
      const serverRow = serverMap.get(`${skuCode}|${weekKey}`);
      const oldQty = serverRow?.planned_qty ?? 0;
      totals[weekKey] = (totals[weekKey] ?? 0) + newQty - oldQty;
    }
    return totals;
  }, [serverTotals, dirtyMap, serverMap]);

  const dirtyCount = useMemo(
    () => computeDirtyCount(dirtyMap, serverRows),
    [dirtyMap, serverRows],
  );

  // ── Helpers ───────────────────────────────────────────────────────────────────

  function displayedQty(skuCode: string, weekKey: string): number {
    const key = `${skuCode}|${weekKey}`;
    const override = dirtyMap[key];
    if (override !== undefined) return override;
    return serverMap.get(key)?.planned_qty ?? 0;
  }

  function isDirtyCell(skuCode: string, weekKey: string): boolean {
    const key = `${skuCode}|${weekKey}`;
    if (!(key in dirtyMap)) return false;
    const serverQty = serverMap.get(key)?.planned_qty ?? 0;
    return Math.round(dirtyMap[key]) !== Math.round(serverQty);
  }

  // ── Edit actions ──────────────────────────────────────────────────────────────

  const applyEdit = useCallback(
    async (skuCode: string, weekKey: string, newQty: number) => {
      const key = `${skuCode}|${weekKey}`;
      const clampedQty = Math.max(0, Math.round(newQty));
      const prevQty = dirtyMap[key] ?? (serverMap.get(key)?.planned_qty ?? 0);

      // Optimistic update
      setDirtyMap((prev) => ({ ...prev, [key]: clampedQty }));
      setPendingKeys((prev) => new Set([...prev, key]));

      try {
        await editCell({ sku_code: skuCode, week_key: weekKey, qty: clampedQty });
        // Success: trigger supply grid recolour
        onDataChange();
        setLockMessages((prev) => {
          const n = { ...prev };
          delete n[key];
          return n;
        });
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : '';
        if (msg.includes('409')) {
          // Locked week — revert optimistic change and show message
          setDirtyMap((prev) => {
            const n = { ...prev };
            const serverQty = serverMap.get(key)?.planned_qty ?? 0;
            if (prevQty === serverQty) {
              delete n[key];
            } else {
              n[key] = prevQty;
            }
            return n;
          });
          setLockMessages((prev) => ({ ...prev, [key]: 'Week is locked' }));
          const t = setTimeout(() => {
            setLockMessages((p) => {
              const n = { ...p };
              delete n[key];
              return n;
            });
          }, 2500);
          // Keep a ref for cleanup; accept closure over `t`
          void t;
        } else {
          // Other error: revert silently
          setDirtyMap((prev) => {
            const n = { ...prev };
            const serverQty = serverMap.get(key)?.planned_qty ?? 0;
            if (prevQty === serverQty) {
              delete n[key];
            } else {
              n[key] = prevQty;
            }
            return n;
          });
        }
      } finally {
        setPendingKeys((prev) => {
          const n = new Set(prev);
          n.delete(key);
          return n;
        });
      }
    },
    [dirtyMap, serverMap, onDataChange],
  );

  // Start inline editing a cell
  function startEdit(skuCode: string, weekKey: string) {
    const week = weekMap.get(weekKey);
    if (week?.is_locked) return;
    const qty = displayedQty(skuCode, weekKey);
    setEditingCell({ skuCode, weekKey });
    setEditingValue(String(Math.round(qty)));
  }

  // Commit the typed value
  function commitEdit() {
    if (!editingCell) return;
    const parsed = parseInt(editingValue, 10);
    if (!isNaN(parsed)) {
      void applyEdit(editingCell.skuCode, editingCell.weekKey, parsed);
    }
    setEditingCell(null);
    setEditingValue('');
  }

  function cancelEdit() {
    setEditingCell(null);
    setEditingValue('');
  }

  function handleInputKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') { e.preventDefault(); commitEdit(); }
    if (e.key === 'Escape') { e.preventDefault(); cancelEdit(); }
    // Tab: commit and let focus move naturally
    if (e.key === 'Tab') { commitEdit(); }
  }

  function handleStepUp(skuCode: string, weekKey: string) {
    const week = weekMap.get(weekKey);
    if (week?.is_locked) return;
    const current = displayedQty(skuCode, weekKey);
    void applyEdit(skuCode, weekKey, current + STEP);
  }

  function handleStepDown(skuCode: string, weekKey: string) {
    const week = weekMap.get(weekKey);
    if (week?.is_locked) return;
    const current = displayedQty(skuCode, weekKey);
    void applyEdit(skuCode, weekKey, Math.max(0, current - STEP));
  }

  // ── Save / Discard / Reset ────────────────────────────────────────────────────

  async function handleSave() {
    if (dirtyCount === 0 || saving) return;
    setSaving(true);
    try {
      await saveProduction();
      const fresh = await fetchProduction();
      setServerRows(fresh.rows ?? []);
      setServerTotals(fresh.week_totals ?? {});
      setWeekFlagsState(fresh.week_flags ?? {});
      setDirtyMap({});
      onDataChange();
      // Show success toast
      setSaveToast(true);
      if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
      toastTimerRef.current = setTimeout(() => setSaveToast(false), 3000);
    } catch (err) {
      console.error('[ProductionGrid] Save failed:', err);
    } finally {
      setSaving(false);
    }
  }

  async function handleDiscard() {
    if (dirtyCount === 0 || discarding) return;
    setDiscarding(true);
    try {
      await discardProduction();
      const fresh = await fetchProduction();
      setServerRows(fresh.rows ?? []);
      setServerTotals(fresh.week_totals ?? {});
      setWeekFlagsState(fresh.week_flags ?? {});
      setDirtyMap({});
      setEditingCell(null);
      onDataChange();
    } catch (err) {
      console.error('[ProductionGrid] Discard failed:', err);
    } finally {
      setDiscarding(false);
    }
  }

  async function handleResetWeek() {
    if (!activeWeekKey || resetting) return;
    setResetting(true);
    const targetWeek = activeWeekKey;
    try {
      await resetWeek({ week_key: targetWeek });
      const fresh = await fetchProduction();
      setServerRows(fresh.rows ?? []);
      setServerTotals(fresh.week_totals ?? {});
      setWeekFlagsState(fresh.week_flags ?? {});
      // Remove dirty entries for the reset week
      setDirtyMap((prev) => {
        const n = { ...prev };
        for (const key of Object.keys(n)) {
          const [, wk] = key.split('|');
          if (wk === targetWeek) delete n[key];
        }
        return n;
      });
      onDataChange();
    } catch (err) {
      console.error('[ProductionGrid] Reset week failed:', err);
    } finally {
      setResetting(false);
    }
  }

  // Toggle week selection for Reset Week
  function toggleActiveWeek(weekKey: string) {
    setActiveWeekKey((prev) => (prev === weekKey ? null : weekKey));
  }

  // ── Render: loading ───────────────────────────────────────────────────────────
  if (loading) return <GridSkeleton />;

  // ── Render: error ─────────────────────────────────────────────────────────────
  if (fetchError) {
    return (
      <div className="pg-card" style={{ marginTop: 'var(--sp-6)' }}>
        <div className="pg-error" role="alert">
          <span aria-hidden="true">&#9888;</span>
          <span>Could not load production data: {fetchError}</span>
        </div>
      </div>
    );
  }

  // ── Render: empty ─────────────────────────────────────────────────────────────
  if (serverRows.length === 0 || sortedSkus.length === 0) {
    return (
      <div className="pg-card" style={{ marginTop: 'var(--sp-6)' }}>
        <div className="pg-empty">
          <span aria-hidden="true">&#9675;</span>
          <span>No production data available.</span>
        </div>
      </div>
    );
  }

  // ── Render: grid ──────────────────────────────────────────────────────────────

  const actionWeekLabel = activeWeekKey ? isoWeekLabel(activeWeekKey) : null;

  return (
    <div className="pg-card">
      {/* ── Card header bar ─────────────────────────────── */}
      <div className="pg-card-header">
        <div className="pg-header-left">
          <span className="pg-title">Production Plan</span>
          <span className="pg-subtitle">PET line weekly production by SKU</span>
        </div>

        <div className="pg-action-bar">
          {/* Active-week chip */}
          {actionWeekLabel && (
            <span className="pg-active-week-chip" aria-label={`Active week: ${actionWeekLabel}`}>
              {actionWeekLabel} selected
            </span>
          )}

          {/* Reset week */}
          <button
            className="pg-action-btn pg-reset-btn"
            onClick={() => void handleResetWeek()}
            disabled={!activeWeekKey || resetting}
            aria-label={activeWeekKey ? `Reset ${actionWeekLabel ?? ''}` : 'Select a week header to enable reset'}
            title="Click a week header to select it, then reset that week to its original values"
          >
            {resetting ? 'Resetting...' : `Reset${actionWeekLabel ? ' ' + actionWeekLabel : ' week'}`}
          </button>

          {/* Discard */}
          <button
            className="pg-action-btn pg-discard-btn"
            onClick={() => void handleDiscard()}
            disabled={dirtyCount === 0 || discarding}
            aria-label="Discard all unsaved changes"
          >
            {discarding ? 'Discarding...' : 'Discard'}
          </button>

          {/* Save */}
          <button
            className={`pg-action-btn pg-save-btn${dirtyCount > 0 ? ' pg-save-btn--dirty' : ''}`}
            onClick={() => void handleSave()}
            disabled={dirtyCount === 0 || saving}
            aria-label={`Save ${dirtyCount} unsaved change${dirtyCount !== 1 ? 's' : ''}`}
          >
            {saving ? 'Saving...' : 'Save'}
            {dirtyCount > 0 && (
              <span className="pg-save-badge" aria-hidden="true">
                {dirtyCount}
              </span>
            )}
          </button>
        </div>
      </div>

      {/* ── Capacity validation banner ───────────────────── */}
      <ValidationBanner weekFlags={weekFlagsState} />

      {/* ── Success toast ────────────────────────────────── */}
      {saveToast && (
        <div className="pg-toast" role="status" aria-live="polite">
          <span className="pg-toast-icon" aria-hidden="true">&#10003;</span>
          Changes saved successfully
        </div>
      )}

      {/* ── Bounded scroll container ──────────────────────── */}
      <div
        className="pg-scroll-wrap"
        role="region"
        aria-label="Editable production plan grid"
      >
        <table className="pg-table" role="grid">
          <caption className="sr-only">
            Editable production plan: {sortedSkus.length} SKUs across {weeks.length} weeks.
            Click a cell value or use +/- buttons to edit production quantities.
            Locked weeks (first 3) are read-only.
          </caption>

          {/* ── Week headers ──────────────────────────────── */}
          <thead>
            <tr>
              {/* Corner cell: sticky top + left */}
              <th scope="col" className="pg-th pg-th-sku">
                <span className="pg-col-label">SKU</span>
              </th>

              {weeks.map((week) => (
                <th
                  key={week.week_key}
                  scope="col"
                  className={`pg-th pg-th-week${week.week_key === activeWeekKey ? ' pg-th-week--active' : ''}`}
                  onClick={() => toggleActiveWeek(week.week_key)}
                  aria-label={`${isoWeekLabel(week.week_key)} w/c ${fmtShortDate(week.week_commencing)}${week.is_locked ? ' (locked)' : ''}. Click to select for Reset Week.`}
                  title={`Click to select ${isoWeekLabel(week.week_key)} for Reset Week`}
                >
                  <span className="pg-week-iso">{isoWeekLabel(week.week_key)}</span>
                  <span className="pg-week-date">{fmtShortDate(week.week_commencing)}</span>
                  {week.is_locked && (
                    <span
                      className="pg-week-lock-badge"
                      title="Time-fence locked"
                      aria-label="Locked"
                    />
                  )}
                </th>
              ))}
            </tr>
          </thead>

          <tbody>
            {/* ── Weekly total row (sticky within container) ─ */}
            <tr className="pg-row pg-row--total">
              <td scope="row" className="pg-td pg-td-sku pg-td-total-label">
                Total Units Produced
              </td>
              {weeks.map((week) => (
                <td key={week.week_key} className="pg-td pg-td-total">
                  <span className="pg-cell-val tabnum">
                    {formatValue(computedTotals[week.week_key] ?? 0)}
                  </span>
                </td>
              ))}
            </tr>

            {/* ── SKU rows ────────────────────────────────── */}
            {sortedSkus.map((sku, rowIdx) => (
              <tr
                key={sku.sku_code}
                className={`pg-row${rowIdx % 2 === 1 ? ' pg-row--alt' : ''}`}
              >
                {/* Sticky SKU description column */}
                <td scope="row" className="pg-td pg-td-sku">
                  <div className="pg-td-sku-inner">
                    <span
                      className="pg-sku-priority"
                      aria-label={`Priority ${sku.priority}`}
                    >
                      {sku.priority}
                    </span>
                    <span
                      className="pg-sku-desc"
                      title={sku.description}
                    >
                      {sku.description}
                    </span>
                    <span className="pg-sku-pack">{sku.pack_size_ml}ml</span>
                  </div>
                </td>

                {/* Data cells */}
                {weeks.map((week) => {
                  const key = `${sku.sku_code}|${week.week_key}`;
                  const isLocked = week.is_locked;
                  const qty = displayedQty(sku.sku_code, week.week_key);
                  const dirty = isDirtyCell(sku.sku_code, week.week_key);
                  const isEditing =
                    editingCell?.skuCode === sku.sku_code &&
                    editingCell.weekKey === week.week_key;
                  const isPending = pendingKeys.has(key);
                  const lockMsg = lockMessages[key];

                  if (isLocked) {
                    return (
                      <td
                        key={week.week_key}
                        className="pg-td pg-td-cell pg-td-locked"
                        aria-label={`${sku.description} ${isoWeekLabel(week.week_key)}: ${formatValue(qty)} EA, locked`}
                      >
                        <div className="pg-locked-cell-inner">
                          <span className="pg-cell-val tabnum">{formatValue(qty)}</span>
                          <span
                            className="pg-lock-icon"
                            title="Time-fence locked — cannot be edited"
                          >
                            <LockIcon size={11} />
                          </span>
                        </div>
                      </td>
                    );
                  }

                  return (
                    <td
                      key={week.week_key}
                      className={[
                        'pg-td',
                        'pg-td-cell',
                        dirty ? 'pg-td-dirty' : '',
                        isPending ? 'pg-td-pending' : '',
                      ]
                        .filter(Boolean)
                        .join(' ')}
                      aria-label={`${sku.description} ${isoWeekLabel(week.week_key)}: ${formatValue(qty)} EA${dirty ? ' (unsaved)' : ''}`}
                    >
                      {/* Inline locked message (appears when server returns 409) */}
                      {lockMsg && (
                        <div className="pg-lock-msg" role="alert">
                          {lockMsg}
                        </div>
                      )}

                      {isEditing ? (
                        /* Input mode */
                        <div className="pg-edit-wrap">
                          <input
                            className="pg-input tabnum"
                            type="number"
                            min="0"
                            step={STEP}
                            value={editingValue}
                            onChange={(e) => setEditingValue(e.target.value)}
                            onBlur={commitEdit}
                            onKeyDown={handleInputKeyDown}
                            autoFocus
                            aria-label={`Edit production for ${sku.description} ${isoWeekLabel(week.week_key)}`}
                          />
                        </div>
                      ) : (
                        /* Display mode with stepper buttons */
                        <div className="pg-cell-inner">
                          <button
                            className="pg-stepper pg-stepper--down"
                            onClick={() => handleStepDown(sku.sku_code, week.week_key)}
                            aria-label={`Decrease ${sku.description} ${isoWeekLabel(week.week_key)} by ${STEP.toLocaleString()}`}
                            tabIndex={-1}
                          >
                            &minus;
                          </button>

                          <span
                            className="pg-cell-val-btn"
                            role="button"
                            tabIndex={0}
                            onClick={() => startEdit(sku.sku_code, week.week_key)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter' || e.key === ' ') {
                                e.preventDefault();
                                startEdit(sku.sku_code, week.week_key);
                              }
                            }}
                            aria-label={`${formatValue(qty)} EA. Press Enter or click to edit.`}
                          >
                            <span className="pg-cell-val tabnum">{formatValue(qty)}</span>
                          </span>

                          <button
                            className="pg-stepper pg-stepper--up"
                            onClick={() => handleStepUp(sku.sku_code, week.week_key)}
                            aria-label={`Increase ${sku.description} ${isoWeekLabel(week.week_key)} by ${STEP.toLocaleString()}`}
                            tabIndex={-1}
                          >
                            +
                          </button>
                        </div>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
