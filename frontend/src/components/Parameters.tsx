/**
 * Parameters — editable table of PET planning parameters.
 *
 * Shows name / value / description. The "value" column is inline-editable:
 * click a cell, type a number, press Enter (or click away) to commit.
 * Pressing Escape cancels without saving.
 *
 * On commit, calls PUT /api/parameters and then invokes onDataChange() so
 * the supply/production grids recompute (RULE-005).
 */

import { useState, useRef, useEffect, useCallback } from 'react';
import type { KeyboardEvent } from 'react';
import type { Parameter } from '../api';
import { updateParameter } from '../api';
import './ConfigTab.css';

// ── Types ─────────────────────────────────────────────────────────────────────

interface Props {
  parameters: Parameter[];
  onDataChange: () => void;
}

interface EditState {
  name: string;
  raw: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtParamValue(v: number): string {
  if (v >= 10_000) return v.toLocaleString('en-AU', { maximumFractionDigits: 0 });
  return String(v);
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function Parameters({ parameters: initialParams, onDataChange }: Props) {
  const [params, setParams] = useState<Parameter[]>(initialParams);
  const [editing, setEditing] = useState<EditState | null>(null);
  const [savingName, setSavingName] = useState<string | null>(null);
  const [inlineError, setInlineError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Sync when the parent refreshes config
  useEffect(() => {
    setParams(initialParams);
  }, [initialParams]);

  // Auto-focus and select text when edit starts
  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  const startEdit = useCallback((param: Parameter) => {
    setEditing({ name: param.name, raw: String(param.value) });
    setInlineError(null);
  }, []);

  const cancelEdit = useCallback(() => {
    setEditing(null);
  }, []);

  const commitEdit = useCallback(async () => {
    if (!editing) return;
    const value = parseFloat(editing.raw);
    if (isNaN(value) || !isFinite(value)) {
      setInlineError('Please enter a valid number.');
      return;
    }
    const { name } = editing;
    setEditing(null);
    setSavingName(name);
    setInlineError(null);
    try {
      await updateParameter({ name, value });
      setParams((prev) =>
        prev.map((p) => (p.name === name ? { ...p, value } : p))
      );
      onDataChange();
    } catch (err) {
      setInlineError(
        err instanceof Error ? err.message : 'Failed to save parameter.'
      );
    } finally {
      setSavingName(null);
    }
  }, [editing, onDataChange]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        void commitEdit();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        cancelEdit();
      }
    },
    [commitEdit, cancelEdit]
  );

  return (
    <div className="ct-card">
      {/* Header */}
      <div className="ct-card-header">
        <div>
          <div className="ct-card-title">Parameters</div>
          <div className="ct-card-subtitle">
            {params.length} planning parameters
          </div>
        </div>
      </div>

      {/* Rule caption */}
      <div className="ct-rule-caption">
        <strong>RULE-005:</strong> Capacity values are configurable. Changes take effect on next recalculation.
      </div>

      {/* Inline error */}
      {inlineError && (
        <div className="ct-inline-error">
          <span>{inlineError}</span>
        </div>
      )}

      {/* Table */}
      <div className="ct-table-wrap">
        <table className="ct-table" aria-label="Planning parameters">
          <thead>
            <tr>
              <th style={{ width: 220 }}>Name</th>
              <th style={{ width: 140 }}>Value</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            {params.map((param) => {
              const isEditing = editing?.name === param.name;
              const isSaving = savingName === param.name;

              return (
                <tr key={param.name}>
                  {/* Name column */}
                  <td>
                    <code
                      style={{
                        fontFamily: 'var(--font-mono)',
                        fontSize: 'var(--text-sm)',
                        color: 'var(--text-secondary)',
                        background: 'var(--surface-1)',
                        padding: '2px 6px',
                        borderRadius: 'var(--radius-sm)',
                      }}
                    >
                      {param.name}
                    </code>
                  </td>

                  {/* Value column — editable */}
                  <td
                    className={`ct-cell-editable${isEditing ? ' ct-cell-editing' : ''}`}
                    onClick={() => !isSaving && !isEditing && startEdit(param)}
                    role="gridcell"
                    aria-label={`Value for ${param.name}, press Enter to edit`}
                  >
                    {isSaving ? (
                      <span className="ct-spinner" aria-label="Saving" />
                    ) : isEditing ? (
                      <input
                        ref={inputRef}
                        type="number"
                        className="ct-input"
                        style={{ width: 120 }}
                        value={editing.raw}
                        onChange={(e) =>
                          setEditing((s) => s ? { ...s, raw: e.target.value } : s)
                        }
                        onBlur={() => void commitEdit()}
                        onKeyDown={handleKeyDown}
                        aria-label={`Edit value for ${param.name}`}
                      />
                    ) : (
                      <span className="ct-num">{fmtParamValue(param.value)}</span>
                    )}
                  </td>

                  {/* Description column */}
                  <td style={{ color: 'var(--text-secondary)' }}>{param.description}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
