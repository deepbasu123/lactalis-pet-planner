/**
 * WeekConfig — editable week horizon table.
 *
 * Columns: week / horizon index / week commencing / maintenance type / locked / note.
 * Editable fields: maintenance_type (select), is_locked (checkbox), note (text).
 *
 * On commit, calls PUT /api/weeks and invokes onDataChange().
 * RULE-006: Full maintenance = 0 capacity.
 * RULE-020: Locked weeks cannot be modified.
 */

import { useState, useRef, useEffect, useCallback } from 'react';
import type { KeyboardEvent } from 'react';
import type { Week } from '../api';
import { updateWeek } from '../api';
import './ConfigTab.css';

// ── Types ─────────────────────────────────────────────────────────────────────

interface Props {
  weeks: Week[];
  onDataChange: () => void;
}

type EditField = 'maintenance_type' | 'note';

interface EditState {
  weekKey: string;
  field: EditField;
  raw: string;
}

const MAINTENANCE_OPTIONS = ['None', 'Partial', 'Full'];

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtWeekCommencing(iso: string): string {
  if (!iso) return '';
  const d = new Date(iso + 'T00:00:00');
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-AU', { day: 'numeric', month: 'short', year: 'numeric' });
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function WeekConfig({ weeks: initialWeeks, onDataChange }: Props) {
  const [weeks, setWeeks] = useState<Week[]>(
    [...initialWeeks].sort((a, b) => a.horizon_index - b.horizon_index)
  );
  const [editing, setEditing] = useState<EditState | null>(null);
  const [savingKey, setSavingKey] = useState<string | null>(null); // "weekKey|field"
  const [inlineError, setInlineError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setWeeks([...initialWeeks].sort((a, b) => a.horizon_index - b.horizon_index));
  }, [initialWeeks]);

  useEffect(() => {
    if (editing?.field === 'note' && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  const savingKeyFor = (weekKey: string, field: string) => `${weekKey}|${field}`;

  const startEdit = useCallback((week: Week, field: EditField) => {
    // For maintenance_type, default null -> 'None' so the select pre-selects correctly.
    // For note, default null/undefined -> '' so the text input is blank.
    const defaultVal = field === 'maintenance_type' ? 'None' : '';
    setEditing({ weekKey: week.week_key, field, raw: String(week[field] ?? defaultVal) });
    setInlineError(null);
  }, []);

  const cancelEdit = useCallback(() => setEditing(null), []);

  const commitEdit = useCallback(async (overrideRaw?: string) => {
    if (!editing) return;
    const raw = overrideRaw ?? editing.raw;
    const { weekKey, field } = editing;
    setEditing(null);

    const patch: { maintenance_type?: string; note?: string } =
      field === 'maintenance_type'
        ? { maintenance_type: raw }
        : { note: raw };

    const key = savingKeyFor(weekKey, field);
    setSavingKey(key);
    setInlineError(null);
    try {
      await updateWeek({ week_key: weekKey, ...patch });
      setWeeks((prev) =>
        prev.map((w) => (w.week_key === weekKey ? { ...w, ...patch } : w))
      );
      onDataChange();
    } catch (err) {
      setInlineError(err instanceof Error ? err.message : 'Failed to save week config.');
    } finally {
      setSavingKey(null);
    }
  }, [editing, onDataChange]);

  const commitLock = useCallback(async (weekKey: string, is_locked: boolean) => {
    const key = savingKeyFor(weekKey, 'is_locked');
    setSavingKey(key);
    setInlineError(null);
    try {
      await updateWeek({ week_key: weekKey, is_locked });
      setWeeks((prev) =>
        prev.map((w) => (w.week_key === weekKey ? { ...w, is_locked } : w))
      );
      onDataChange();
    } catch (err) {
      setInlineError(err instanceof Error ? err.message : 'Failed to save lock state.');
    } finally {
      setSavingKey(null);
    }
  }, [onDataChange]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement | HTMLSelectElement>) => {
      if (e.key === 'Enter') { e.preventDefault(); void commitEdit(); }
      else if (e.key === 'Escape') { e.preventDefault(); cancelEdit(); }
    },
    [commitEdit, cancelEdit]
  );

  return (
    <div className="ct-card">
      {/* Header */}
      <div className="ct-card-header">
        <div>
          <div className="ct-card-title">Week Config</div>
          <div className="ct-card-subtitle">{weeks.length}-week planning horizon</div>
        </div>
      </div>

      {/* Rule caption */}
      <div className="ct-rule-caption">
        <strong>RULE-006:</strong> Full maintenance = 0 capacity.{' '}
        <strong>RULE-020:</strong> Locked weeks cannot be modified.
      </div>

      {inlineError && (
        <div className="ct-inline-error">{inlineError}</div>
      )}

      {/* Table */}
      <div className="ct-table-wrap">
        <table className="ct-table" aria-label="Week horizon configuration">
          <thead>
            <tr>
              <th style={{ width: 100 }}>Week</th>
              <th style={{ width: 80, textAlign: 'center' }}>Horizon</th>
              <th style={{ width: 150 }}>Week Commencing</th>
              <th style={{ width: 160 }}>Maintenance Type</th>
              <th style={{ width: 100, textAlign: 'center' }}>Locked</th>
              <th>Note</th>
            </tr>
          </thead>
          <tbody>
            {weeks.map((week) => {
              const editingMaint =
                editing?.weekKey === week.week_key && editing.field === 'maintenance_type';
              const editingNote =
                editing?.weekKey === week.week_key && editing.field === 'note';
              const savingMaint =
                savingKey === savingKeyFor(week.week_key, 'maintenance_type');
              const savingNote =
                savingKey === savingKeyFor(week.week_key, 'note');
              const savingLock =
                savingKey === savingKeyFor(week.week_key, 'is_locked');

              return (
                <tr key={week.week_key}>
                  {/* Week key */}
                  <td>
                    <code
                      style={{
                        fontFamily: 'var(--font-mono)',
                        fontSize: 'var(--text-sm)',
                        fontWeight: 600,
                        color: 'var(--lac-blue)',
                      }}
                    >
                      {week.week_key}
                    </code>
                  </td>

                  {/* Horizon index */}
                  <td style={{ textAlign: 'center' }}>
                    <span className="ct-num" style={{ fontSize: 'var(--text-sm)' }}>
                      {week.horizon_index}
                    </span>
                  </td>

                  {/* Week commencing */}
                  <td style={{ color: 'var(--text-secondary)', fontSize: 'var(--text-sm)' }}>
                    {fmtWeekCommencing(week.week_commencing)}
                  </td>

                  {/* Maintenance type — editable select */}
                  <td
                    className={`ct-cell-editable${editingMaint ? ' ct-cell-editing' : ''}`}
                    onClick={() => !savingMaint && !editingMaint && startEdit(week, 'maintenance_type')}
                    role="gridcell"
                    aria-label={`Maintenance type ${week.maintenance_type ?? 'None'} for ${week.week_key}`}
                  >
                    {savingMaint ? (
                      <span className="ct-spinner" />
                    ) : editingMaint ? (
                      <select
                        className="ct-select"
                        value={editing!.raw}
                        onChange={(e) => void commitEdit(e.target.value)}
                        onBlur={() => void commitEdit()}
                        onKeyDown={handleKeyDown}
                        autoFocus
                      >
                        {MAINTENANCE_OPTIONS.map((opt) => (
                          <option key={opt} value={opt}>{opt}</option>
                        ))}
                      </select>
                    ) : (
                      <span
                        className={`ct-maint ct-maint--${(week.maintenance_type ?? 'None').toLowerCase()}`}
                      >
                        {week.maintenance_type ?? 'None'}
                      </span>
                    )}
                  </td>

                  {/* is_locked — checkbox (instant commit) */}
                  <td style={{ textAlign: 'center' }}>
                    {savingLock ? (
                      <span className="ct-spinner" />
                    ) : (
                      <label className="ct-checkbox-wrap" style={{ justifyContent: 'center', cursor: 'pointer' }}>
                        <input
                          type="checkbox"
                          className="ct-checkbox"
                          checked={Boolean(week.is_locked)}
                          onChange={(e) => void commitLock(week.week_key, e.target.checked)}
                          aria-label={`Lock week ${week.week_key}`}
                        />
                        <span
                          className={`ct-lock-badge ${
                            week.is_locked ? 'ct-lock-badge--locked' : 'ct-lock-badge--unlocked'
                          }`}
                          style={{ pointerEvents: 'none' }}
                        >
                          {week.is_locked ? 'Locked' : 'Open'}
                        </span>
                      </label>
                    )}
                  </td>

                  {/* Note — editable text */}
                  <td
                    className={`ct-cell-editable${editingNote ? ' ct-cell-editing' : ''}`}
                    onClick={() => !savingNote && !editingNote && startEdit(week, 'note')}
                    role="gridcell"
                    aria-label={`Note for ${week.week_key}, click to edit`}
                  >
                    {savingNote ? (
                      <span className="ct-spinner" />
                    ) : editingNote ? (
                      <input
                        ref={inputRef}
                        type="text"
                        className="ct-input ct-input-text"
                        value={editing!.raw}
                        onChange={(e) =>
                          setEditing((s) => s ? { ...s, raw: e.target.value } : s)
                        }
                        onBlur={() => void commitEdit()}
                        onKeyDown={handleKeyDown}
                        aria-label={`Note for ${week.week_key}`}
                      />
                    ) : (
                      <span style={{ color: week.note ? 'var(--text-primary)' : 'var(--text-muted)', fontStyle: week.note ? 'normal' : 'italic' }}>
                        {week.note || 'Add a note...'}
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
