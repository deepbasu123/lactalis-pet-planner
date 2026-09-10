/**
 * TrafficLightSummary — traffic-light colour distribution and plan comparison.
 *
 * Shows:
 *   1. KPI tiles — one per colour band with count and swatch (from /api/summary counts)
 *   2. Original (SNP) vs Production Plan comparison table — original_vs_plan
 *
 * Fetches /api/summary directly so it always shows live data.
 * Re-fetches whenever dataVersion increments.
 */

import { useState, useEffect } from 'react';
import { fetchSummary } from '../api';
import type { SummaryResponse } from '../api';
import type { ColourName } from '../colours';
import { computeChanges, fmtChange } from './summaryHelpers';
import './ConfigTab.css';

// ── Types ─────────────────────────────────────────────────────────────────────

interface Props {
  dataVersion: number;
}

// ── Band metadata: ordered for display, with human-friendly labels ─────────────

interface BandMeta {
  key: ColourName;
  label: string;
  hex: string;
  textHex: string; // foreground colour on the swatch tile
  description: string;
}

const BANDS: BandMeta[] = [
  {
    key: 'green',
    label: 'Green',
    hex: '#2E9E5B',
    textHex: '#ffffff',
    description: 'Cover 1-2 weeks',
  },
  {
    key: 'dark_blue',
    label: 'Dark Blue',
    hex: '#1565C0',
    textHex: '#ffffff',
    description: 'Cover > 3 weeks',
  },
  {
    key: 'light_blue',
    label: 'Light Blue',
    hex: '#5BC5F2',
    textHex: '#003d70',
    description: 'Cover 2-3 weeks',
  },
  {
    key: 'amber',
    label: 'Amber',
    hex: '#E8A317',
    textHex: '#1a2733',
    description: 'Cover 0-1 weeks',
  },
  {
    key: 'red',
    label: 'Red',
    hex: '#E1382D',
    textHex: '#ffffff',
    description: 'Stock-out (recoverable)',
  },
  {
    key: 'dark_red',
    label: 'Dark Red',
    hex: '#8B1A1A',
    textHex: '#ffffff',
    description: 'Lost sale risk',
  },
  {
    key: 'black',
    label: 'Black',
    hex: '#2B2B2B',
    textHex: '#ffffff',
    description: 'Over-cover (past MLOR)',
  },
];

// ── KPI tile ──────────────────────────────────────────────────────────────────

interface TileProps {
  band: BandMeta;
  count: number;
  total: number;
}

function ColourTile({ band, count, total }: TileProps) {
  const pct = total > 0 ? Math.round((count / total) * 100) : 0;
  return (
    <div
      style={{
        background: '#fff',
        border: '1px solid var(--border-light)',
        borderTop: `4px solid ${band.hex}`,
        borderRadius: 'var(--radius-sm)',
        padding: '16px 20px',
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
        minWidth: 120,
        transition: 'box-shadow var(--ease-base)',
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLElement).style.boxShadow = 'var(--shadow-md)';
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLElement).style.boxShadow = 'none';
      }}
    >
      {/* Swatch + label row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span
          style={{
            display: 'inline-block',
            width: 12,
            height: 12,
            background: band.hex,
            borderRadius: 2,
            flexShrink: 0,
          }}
          aria-hidden="true"
        />
        <span
          style={{
            fontSize: 'var(--text-sm)',
            fontWeight: 600,
            color: 'var(--text-secondary)',
            letterSpacing: '0.02em',
          }}
        >
          {band.label}
        </span>
      </div>

      {/* Big count */}
      <div
        style={{
          fontSize: 36,
          fontWeight: 800,
          lineHeight: 1,
          color: 'var(--text-primary)',
          fontFeatureSettings: '"tnum" 1',
          fontVariantNumeric: 'tabular-nums',
          letterSpacing: '-0.03em',
          marginTop: 4,
        }}
      >
        {count.toLocaleString('en-AU')}
      </div>

      {/* Percentage + description */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', marginTop: 2 }}>
        <span
          style={{
            fontSize: 'var(--text-sm)',
            fontWeight: 600,
            color: band.hex,
            fontFeatureSettings: '"tnum" 1',
          }}
        >
          {pct}%
        </span>
        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
          {band.description}
        </span>
      </div>
    </div>
  );
}

// ── Change cell ────────────────────────────────────────────────────────────────

function ChangeCell({ delta }: { delta: number }) {
  const colour =
    delta > 0 ? '#2E9E5B' : delta < 0 ? '#E1382D' : 'var(--text-muted)';
  return (
    <span
      style={{
        fontWeight: 700,
        color: colour,
        fontFeatureSettings: '"tnum" 1',
        fontVariantNumeric: 'tabular-nums',
      }}
    >
      {fmtChange(delta)}
    </span>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function TrafficLightSummary({ dataVersion }: Props) {
  const [summary, setSummary] = useState<SummaryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    void fetchSummary()
      .then(setSummary)
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'Failed to load summary');
      })
      .finally(() => setLoading(false));
  }, [dataVersion]);

  if (loading) {
    return (
      <div className="ct-card">
        <div className="ct-card-header">
          <div className="ct-card-title">Traffic Light Summary</div>
        </div>
        <div className="ct-state-cell" role="status" aria-label="Loading summary">
          <span className="ct-spinner" style={{ width: 20, height: 20, borderWidth: 3 }} />
          <p style={{ marginTop: 12, color: 'var(--text-muted)' }}>Loading summary data...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="ct-card">
        <div className="ct-card-header">
          <div className="ct-card-title">Traffic Light Summary</div>
        </div>
        <div className="ct-inline-error" style={{ borderTop: 'none', padding: 'var(--sp-6)' }}>
          {error}
        </div>
      </div>
    );
  }

  if (!summary) return null;

  const { counts, original_vs_plan } = summary;
  const total = Object.values(counts ?? {}).reduce((s, v) => s + v, 0);
  // Guard: original_vs_plan.original/working are required by the backend model but
  // optional-chain defensively in case the API ever returns an unexpected shape.
  const safeOriginal: Record<string, number> = original_vs_plan?.original ?? {};
  const safeWorking: Record<string, number> = original_vs_plan?.working ?? {};
  const changes = computeChanges(safeOriginal, safeWorking);

  // Count how many bands actually changed
  const changedBands = BANDS.filter((b) => changes[b.key] !== 0);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-6)' }}>
      {/* ── Colour count tiles ─────────────────────────────────────── */}
      <div className="ct-card">
        <div className="ct-card-header">
          <div>
            <div className="ct-card-title">Traffic Light Distribution</div>
            <div className="ct-card-subtitle">
              {total} cells across {BANDS.length} bands
            </div>
          </div>
          {changedBands.length > 0 && (
            <div
              style={{
                fontSize: 'var(--text-sm)',
                color: '#2E9E5B',
                fontWeight: 600,
                background: '#e6f5ec',
                padding: '4px 12px',
                borderRadius: 'var(--radius-sm)',
              }}
            >
              {changedBands.length} band{changedBands.length > 1 ? 's' : ''} changed vs SNP
            </div>
          )}
        </div>

        <div
          style={{
            padding: 'var(--sp-5)',
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))',
            gap: 'var(--sp-4)',
          }}
        >
          {BANDS.map((band) => (
            <ColourTile
              key={band.key}
              band={band}
              count={counts[band.key] ?? 0}
              total={total}
            />
          ))}
        </div>
      </div>

      {/* ── Original vs Production Plan comparison ─────────────────── */}
      <div className="ct-card">
        <div className="ct-card-header">
          <div>
            <div className="ct-card-title">Original (SNP) vs Production Plan</div>
            <div className="ct-card-subtitle">
              Cell count comparison by colour band. Positive change = improvement (more healthy cells).
            </div>
          </div>
        </div>

        <div className="ct-table-wrap">
          <table className="ct-table" aria-label="SNP vs Production Plan colour comparison">
            <thead>
              <tr>
                <th style={{ width: 160 }}>Status</th>
                <th style={{ width: 130, textAlign: 'right' }}>Original (SNP)</th>
                <th style={{ width: 130, textAlign: 'right' }}>Production Plan</th>
                <th style={{ width: 100, textAlign: 'right' }}>Change</th>
              </tr>
            </thead>
            <tbody>
              {BANDS.map((band) => {
                const orig = safeOriginal[band.key] ?? 0;
                const work = safeWorking[band.key] ?? 0;
                const delta = changes[band.key] ?? 0;
                return (
                  <tr key={band.key}>
                    {/* Status label with swatch */}
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span
                          style={{
                            display: 'inline-block',
                            width: 14,
                            height: 14,
                            background: band.hex,
                            borderRadius: 2,
                            flexShrink: 0,
                          }}
                          aria-hidden="true"
                        />
                        <span style={{ fontWeight: 500 }}>{band.label}</span>
                      </div>
                      <div
                        style={{
                          fontSize: 'var(--text-xs)',
                          color: 'var(--text-muted)',
                          marginTop: 1,
                          paddingLeft: 22,
                        }}
                      >
                        {band.description}
                      </div>
                    </td>

                    {/* Original */}
                    <td style={{ textAlign: 'right' }}>
                      <span className="ct-num">{orig.toLocaleString('en-AU')}</span>
                    </td>

                    {/* Working */}
                    <td style={{ textAlign: 'right' }}>
                      <span className="ct-num">{work.toLocaleString('en-AU')}</span>
                    </td>

                    {/* Change */}
                    <td style={{ textAlign: 'right' }}>
                      <ChangeCell delta={delta} />
                    </td>
                  </tr>
                );
              })}

              {/* Totals row */}
              <tr
                style={{
                  background: 'var(--surface-1)',
                  borderTop: '2px solid var(--border)',
                }}
              >
                <td style={{ fontWeight: 700, color: 'var(--text-primary)' }}>Total</td>
                <td style={{ textAlign: 'right' }}>
                  <span className="ct-num">
                    {Object.values(safeOriginal)
                      .reduce((s, v) => s + v, 0)
                      .toLocaleString('en-AU')}
                  </span>
                </td>
                <td style={{ textAlign: 'right' }}>
                  <span className="ct-num">
                    {Object.values(safeWorking)
                      .reduce((s, v) => s + v, 0)
                      .toLocaleString('en-AU')}
                  </span>
                </td>
                <td style={{ textAlign: 'right' }}>
                  <ChangeCell
                    delta={
                      Object.values(safeWorking).reduce((s, v) => s + v, 0) -
                      Object.values(safeOriginal).reduce((s, v) => s + v, 0)
                    }
                  />
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
