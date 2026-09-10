/**
 * SkuPriority — editable SKU priority and status table.
 *
 * Columns: priority / SKU code / description / pack size / status.
 * Priority (number) and status (Active/Inactive) are inline-editable.
 *
 * On commit, calls PUT /api/skus and invokes onDataChange().
 * RULE-021: Higher priority SKUs get production preference when capacity
 * is constrained.
 */

import { useState, useRef, useEffect, useCallback } from 'react';
import type { KeyboardEvent } from 'react';
import type { SKU } from '../api';
import { updateSKU } from '../api';
import './ConfigTab.css';

// ── Types ─────────────────────────────────────────────────────────────────────

interface Props {
  skus: SKU[];
  onDataChange: () => void;
}

type EditField = 'priority' | 'status';

interface EditState {
  skuCode: string;
  field: EditField;
  raw: string;
}

const STATUS_OPTIONS = ['Active', 'Inactive'];

// ── Component ─────────────────────────────────────────────────────────────────

export default function SkuPriority({ skus: initialSkus, onDataChange }: Props) {
  const [skus, setSkus] = useState<SKU[]>([...initialSkus].sort((a, b) => a.priority - b.priority));
  const [editing, setEditing] = useState<EditState | null>(null);
  const [savingKey, setSavingKey] = useState<string | null>(null); // "skuCode|field"
  const [inlineError, setInlineError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setSkus([...initialSkus].sort((a, b) => a.priority - b.priority));
  }, [initialSkus]);

  useEffect(() => {
    if (editing?.field === 'priority' && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  const savingKeyFor = (skuCode: string, field: EditField) => `${skuCode}|${field}`;

  const startEdit = useCallback((sku: SKU, field: EditField) => {
    // Guard against null/undefined: String(null) == 'null' which would break the select.
    setEditing({ skuCode: sku.sku_code, field, raw: String(sku[field] ?? '') });
    setInlineError(null);
  }, []);

  const cancelEdit = useCallback(() => setEditing(null), []);

  const commitEdit = useCallback(async (override?: string) => {
    if (!editing) return;
    const raw = override ?? editing.raw;
    const { skuCode, field } = editing;
    setEditing(null);

    let updates: { priority?: number; status?: string } = {};
    if (field === 'priority') {
      const priority = parseInt(raw, 10);
      if (isNaN(priority) || priority < 1) {
        setInlineError('Priority must be a positive integer.');
        return;
      }
      updates = { priority };
    } else {
      updates = { status: raw };
    }

    const key = savingKeyFor(skuCode, field);
    setSavingKey(key);
    setInlineError(null);
    try {
      await updateSKU({ sku_code: skuCode, ...updates });
      setSkus((prev) =>
        [...prev.map((s) => (s.sku_code === skuCode ? { ...s, ...updates } : s))]
          .sort((a, b) => a.priority - b.priority)
      );
      onDataChange();
    } catch (err) {
      setInlineError(err instanceof Error ? err.message : 'Failed to save SKU.');
    } finally {
      setSavingKey(null);
    }
  }, [editing, onDataChange]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement | HTMLSelectElement>) => {
      if (e.key === 'Enter') { e.preventDefault(); void commitEdit(); }
      else if (e.key === 'Escape') { e.preventDefault(); cancelEdit(); }
    },
    [commitEdit, cancelEdit]
  );

  const handleStatusChange = useCallback((e: React.ChangeEvent<HTMLSelectElement>) => {
    void commitEdit(e.target.value);
  }, [commitEdit]);

  return (
    <div className="ct-card">
      {/* Header */}
      <div className="ct-card-header">
        <div>
          <div className="ct-card-title">SKU Priority</div>
          <div className="ct-card-subtitle">{skus.length} active SKUs</div>
        </div>
      </div>

      {/* Rule caption */}
      <div className="ct-rule-caption">
        <strong>RULE-021:</strong> Higher priority SKUs get production preference when capacity is constrained.
      </div>

      {inlineError && (
        <div className="ct-inline-error">{inlineError}</div>
      )}

      {/* Table */}
      <div className="ct-table-wrap">
        <table className="ct-table" aria-label="SKU priority ranking">
          <thead>
            <tr>
              <th style={{ width: 80, textAlign: 'center' }}>Priority</th>
              <th style={{ width: 110 }}>SKU Code</th>
              <th>Description</th>
              <th style={{ width: 100, textAlign: 'center' }}>Pack Size</th>
              <th style={{ width: 120 }}>Status</th>
            </tr>
          </thead>
          <tbody>
            {skus.map((sku) => {
              const editingPriority =
                editing?.skuCode === sku.sku_code && editing.field === 'priority';
              const editingStatus =
                editing?.skuCode === sku.sku_code && editing.field === 'status';
              const savingPriority =
                savingKey === savingKeyFor(sku.sku_code, 'priority');
              const savingStatus =
                savingKey === savingKeyFor(sku.sku_code, 'status');

              return (
                <tr key={sku.sku_code}>
                  {/* Priority — editable number */}
                  <td
                    className={`ct-cell-editable${editingPriority ? ' ct-cell-editing' : ''}`}
                    style={{ textAlign: 'center' }}
                    onClick={() =>
                      !savingPriority && !editingPriority && startEdit(sku, 'priority')
                    }
                    role="gridcell"
                    aria-label={`Priority ${sku.priority} for ${sku.sku_code}, click to edit`}
                  >
                    {savingPriority ? (
                      <span className="ct-spinner" />
                    ) : editingPriority ? (
                      <input
                        ref={inputRef}
                        type="number"
                        min={1}
                        max={99}
                        className="ct-input"
                        style={{ width: 60, textAlign: 'center' }}
                        value={editing!.raw}
                        onChange={(e) =>
                          setEditing((s) => s ? { ...s, raw: e.target.value } : s)
                        }
                        onBlur={() => void commitEdit()}
                        onKeyDown={handleKeyDown}
                      />
                    ) : (
                      <span
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          width: 28,
                          height: 28,
                          borderRadius: '50%',
                          background: 'var(--surface-2)',
                          fontWeight: 700,
                          fontSize: 'var(--text-md)',
                          color: 'var(--lac-blue)',
                          fontFeatureSettings: '"tnum" 1',
                        }}
                      >
                        {sku.priority}
                      </span>
                    )}
                  </td>

                  {/* SKU code */}
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
                      {sku.sku_code}
                    </code>
                  </td>

                  {/* Description */}
                  <td style={{ fontWeight: 500 }}>{sku.description}</td>

                  {/* Pack size */}
                  <td style={{ textAlign: 'center' }}>
                    <span className="ct-pack">{sku.pack_size_ml ?? '?'}ml</span>
                  </td>

                  {/* Status — editable select */}
                  <td
                    className={`ct-cell-editable${editingStatus ? ' ct-cell-editing' : ''}`}
                    onClick={() =>
                      !savingStatus && !editingStatus && startEdit(sku, 'status')
                    }
                    role="gridcell"
                    aria-label={`Status ${sku.status ?? ''} for ${sku.sku_code}, click to change`}
                  >
                    {savingStatus ? (
                      <span className="ct-spinner" />
                    ) : editingStatus ? (
                      <select
                        className="ct-select"
                        value={editing!.raw}
                        onChange={handleStatusChange}
                        onBlur={() => void commitEdit()}
                        onKeyDown={handleKeyDown}
                        autoFocus
                      >
                        {STATUS_OPTIONS.map((opt) => (
                          <option key={opt} value={opt}>{opt}</option>
                        ))}
                      </select>
                    ) : (
                      <span
                        className={`ct-badge ${
                          (sku.status ?? '').toLowerCase() === 'active'
                            ? 'ct-badge--active'
                            : 'ct-badge--inactive'
                        }`}
                      >
                        {sku.status ?? ''}
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
