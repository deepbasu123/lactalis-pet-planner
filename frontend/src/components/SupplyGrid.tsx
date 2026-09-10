/**
 * SupplyGrid — Traffic-light supply forecast grid.
 *
 * Hero component for the Production Grid tab. Renders 11 SKU rows across
 * a selectable window of the 52-week horizon. Each cell is filled with its
 * traffic-light colour; hover shows a tooltip with the full stock breakdown
 * and band meaning. Sticky first column (SKU) and sticky week header row.
 */

import { useState, useEffect, useCallback, useMemo } from 'react';
import { fetchSupply, fetchConfig } from '../api';
import type { SupplyCell, Week, SKU } from '../api';
import { COLOUR_HEX, colourFg } from '../colours';
import type { ColourName } from '../colours';
import { formatValue, isoWeekLabel, fmtShortDate } from './supplyGridHelpers';
import './SupplyGrid.css';

// ── Band metadata ─────────────────────────────────────────────────────────────

interface BandMeta {
  label: string;
  meaning: string;
}

const BAND_META: Record<ColourName, BandMeta> = {
  dark_blue: {
    label: 'Strong Cover',
    meaning: 'More than 3 weeks of forward cover',
  },
  light_blue: {
    label: 'Good Cover',
    meaning: '2 to 3 weeks of forward cover',
  },
  green: {
    label: 'Adequate',
    meaning: '1 to 2 weeks of forward cover',
  },
  amber: {
    label: 'Low Stock',
    meaning: 'Less than 1 week of forward cover',
  },
  red: {
    label: 'Stock-Out',
    meaning: 'Out of stock, beyond the reaction window',
  },
  dark_red: {
    label: 'Lost Sale Risk',
    meaning: 'Out of stock within the reaction window',
  },
  black: {
    label: 'Over-Cover',
    meaning: 'Exceeds maximum cover window (MLOR risk)',
  },
};

const BAND_ORDER: ColourName[] = [
  'dark_blue',
  'light_blue',
  'green',
  'amber',
  'red',
  'dark_red',
  'black',
];

// ── Helpers ───────────────────────────────────────────────────────────────────

function isValidColour(c: string): c is ColourName {
  return c in COLOUR_HEX;
}

function getDisplayValue(cell: SupplyCell): number {
  // Backend computes display_value = raw when negative, else close.
  if (cell.display_value !== undefined) return cell.display_value;
  return cell.raw < 0 ? cell.raw : cell.close;
}

// ── Tooltip ───────────────────────────────────────────────────────────────────

interface TooltipData {
  cell: SupplyCell;
  sku: SKU;
  week: Week;
  x: number;
  y: number;
}

const TOOLTIP_OFFSET_X = 16;
const TOOLTIP_OFFSET_Y = 14;

function Tooltip({ data }: { data: TooltipData }) {
  const { cell, sku, week } = data;
  const colour = isValidColour(cell.colour) ? cell.colour : ('green' as ColourName);
  const band = BAND_META[colour];
  const bg = COLOUR_HEX[colour];
  const fg = colourFg(colour);

  // Clamp so tooltip doesn't overflow right/bottom edge
  const vpW = window.innerWidth;
  const vpH = window.innerHeight;
  const tipW = 270; // max-width from CSS
  const tipH = 220; // approximate

  const rawX = data.x + TOOLTIP_OFFSET_X;
  const rawY = data.y + TOOLTIP_OFFSET_Y;
  const left = rawX + tipW > vpW ? data.x - tipW - 4 : rawX;
  const top = rawY + tipH > vpH ? data.y - tipH - 4 : rawY;

  return (
    <div
      className="sg-tooltip"
      style={{ left, top }}
      aria-hidden="true"
    >
      {/* SKU identity */}
      <div className="sg-tooltip-sku">
        <span className="sg-tooltip-code">{sku.sku_code}</span>
        <span className="sg-tooltip-name">{sku.description}</span>
      </div>

      {/* Week */}
      <div className="sg-tooltip-week">
        <span className="sg-tooltip-week-iso">{isoWeekLabel(week.week_key)}</span>
        <span className="sg-tooltip-week-wc">
          w/c {fmtShortDate(week.week_commencing)}
          {week.is_locked && ' (locked)'}
        </span>
      </div>

      {/* Stock breakdown */}
      <div className="sg-tooltip-rows">
        <div className="sg-tooltip-row">
          <span className="sg-tooltip-row-label">Opening</span>
          <span className="sg-tooltip-row-val tabnum">{formatValue(cell.opening)}</span>
        </div>
        <div className="sg-tooltip-row">
          <span className="sg-tooltip-row-label">Recv (QA cleared)</span>
          <span className="sg-tooltip-row-val tabnum">{formatValue(cell.recv)}</span>
        </div>
        <div className="sg-tooltip-row">
          <span className="sg-tooltip-row-label">Production</span>
          <span className="sg-tooltip-row-val tabnum">{formatValue(cell.prod)}</span>
        </div>
        <div className="sg-tooltip-row">
          <span className="sg-tooltip-row-label">Demand</span>
          <span className="sg-tooltip-row-val tabnum">{formatValue(cell.demand)}</span>
        </div>
        <div className="sg-tooltip-row sg-tooltip-row--closing">
          <span className="sg-tooltip-row-label">Closing</span>
          <span className="sg-tooltip-row-val tabnum">{formatValue(cell.close)}</span>
        </div>
      </div>

      {/* Band */}
      <div className="sg-tooltip-band" style={{ background: bg, color: fg }}>
        <span className="sg-tooltip-band-name">{band.label}</span>
        <span className="sg-tooltip-band-meaning">{band.meaning}</span>
      </div>
    </div>
  );
}

// ── Legend ────────────────────────────────────────────────────────────────────

function Legend() {
  return (
    <div className="sg-legend" aria-label="Traffic light colour legend">
      {BAND_ORDER.map((colour) => {
        const band = BAND_META[colour];
        return (
          <div key={colour} className="sg-legend-item">
            <span
              className="sg-legend-swatch"
              style={{ background: COLOUR_HEX[colour] }}
              aria-hidden="true"
            />
            <div className="sg-legend-text">
              <span className="sg-legend-label">{band.label}</span>
              <span className="sg-legend-meaning">{band.meaning}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Range controls ────────────────────────────────────────────────────────────

const WINDOW_OPTIONS = [
  { label: '12w', value: 12 },
  { label: '24w', value: 24 },
  { label: '36w', value: 36 },
  { label: 'All', value: 52 },
] as const;

interface RangeControlsProps {
  weeks: Week[];
  startIdx: number;
  visCount: number;
  onStartChange: (idx: number) => void;
  onVisCountChange: (count: number) => void;
}

function RangeControls({
  weeks,
  startIdx,
  visCount,
  onStartChange,
  onVisCountChange,
}: RangeControlsProps) {
  const totalWeeks = weeks.length;
  const effectiveVis = Math.min(visCount, totalWeeks);
  const maxStart = Math.max(0, totalWeeks - effectiveVis);

  const startWeek = weeks[startIdx];
  const endWeek = weeks[Math.min(startIdx + effectiveVis - 1, weeks.length - 1)];

  function handleVisCount(newCount: number) {
    onVisCountChange(newCount);
    const newMax = Math.max(0, totalWeeks - Math.min(newCount, totalWeeks));
    if (startIdx > newMax) onStartChange(newMax);
  }

  return (
    <div className="sg-controls">
      {/* Left: range summary */}
      <div className="sg-controls-left">
        <span className="sg-controls-tag">Showing</span>
        <span className="sg-controls-range tabnum">
          {startWeek ? isoWeekLabel(startWeek.week_key) : ''}
          {' – '}
          {endWeek ? isoWeekLabel(endWeek.week_key) : ''}
        </span>
        {startWeek && endWeek && (
          <>
            <span className="sg-controls-dot" aria-hidden="true">·</span>
            <span className="sg-controls-dates">
              {fmtShortDate(startWeek.week_commencing)} – {fmtShortDate(endWeek.week_commencing)}
            </span>
          </>
        )}
        <span className="sg-controls-count">
          ({effectiveVis} of {totalWeeks} weeks)
        </span>
      </div>

      {/* Right: width buttons + slider */}
      <div className="sg-controls-right">
        <div className="sg-window-btns" role="group" aria-label="Window width">
          {WINDOW_OPTIONS.map(({ label, value }) => (
            <button
              key={label}
              className={`sg-window-btn${visCount === value ? ' sg-window-btn--active' : ''}`}
              onClick={() => handleVisCount(value)}
              aria-pressed={visCount === value}
            >
              {label}
            </button>
          ))}
        </div>

        {maxStart > 0 && (
          <div className="sg-slider-wrap">
            <span className="sg-slider-label" aria-hidden="true">Scroll</span>
            <input
              type="range"
              className="sg-slider"
              min={0}
              max={maxStart}
              value={startIdx}
              onChange={(e) => onStartChange(Number(e.target.value))}
              aria-label="Scroll the visible week window"
            />
          </div>
        )}
      </div>
    </div>
  );
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function GridSkeleton() {
  return (
    <div className="sg-outer">
      <div className="sg-skeleton-outer">
        {/* Legend row */}
        <div className="sg-skeleton-legend">
          {Array.from({ length: 7 }).map((_, i) => (
            <div
              key={i}
              className="skeleton"
              style={{ width: 96, height: 28, animationDelay: `${i * 60}ms` }}
            />
          ))}
        </div>
        {/* Controls row */}
        <div className="sg-skeleton-controls">
          <div className="skeleton" style={{ width: 200, height: 22 }} />
          <div className="skeleton" style={{ width: 140, height: 22 }} />
        </div>
        {/* Table rows */}
        <div className="sg-skeleton-table">
          {/* Header */}
          <div className="sg-skeleton-header-row">
            <div className="skeleton" style={{ width: 240, height: 38 }} />
            {Array.from({ length: 12 }).map((_, i) => (
              <div key={i} className="skeleton" style={{ width: 90, height: 38, animationDelay: `${i * 30}ms` }} />
            ))}
          </div>
          {/* Body rows */}
          {Array.from({ length: 11 }).map((_, i) => (
            <div key={i} className="sg-skeleton-row" style={{ marginTop: 1 }}>
              <div className="skeleton" style={{ width: 240, height: 36, animationDelay: `${i * 40}ms` }} />
              {Array.from({ length: 12 }).map((_, j) => (
                <div
                  key={j}
                  className="skeleton"
                  style={{ width: 90, height: 36, marginLeft: 1, animationDelay: `${(i * 12 + j) * 15}ms` }}
                />
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

const DEFAULT_VIS_COUNT = 12;

interface SupplyGridProps {
  /** Increment to trigger a re-fetch of supply and config data. */
  dataVersion?: number;
}

export default function SupplyGrid({ dataVersion = 0 }: SupplyGridProps) {
  const [supply, setSupply] = useState<SupplyCell[] | null>(null);
  const [skus, setSkus] = useState<SKU[] | null>(null);
  const [weeks, setWeeks] = useState<Week[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);
  const [startIdx, setStartIdx] = useState(0);
  const [visCount, setVisCount] = useState(DEFAULT_VIS_COUNT);

  useEffect(() => {
    setLoading(true);
    setError(null);

    void Promise.all([fetchSupply(), fetchConfig()])
      .then(([supplyData, configData]) => {
        setSupply(supplyData.rows);
        setSkus(configData.skus);
        setWeeks(configData.weeks);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'Failed to load supply data');
      })
      .finally(() => {
        setLoading(false);
      });
  // dataVersion drives re-fetch; when it increments the supply grid recolours.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataVersion]);

  // Visible week window (memoised slice)
  const visibleWeeks = useMemo(() => {
    if (!weeks) return [];
    const count = Math.min(visCount, weeks.length);
    return weeks.slice(startIdx, startIdx + count);
  }, [weeks, startIdx, visCount]);

  // Cell lookup: "skuCode-weekKey" -> SupplyCell
  const cellMap = useMemo(() => {
    const m = new Map<string, SupplyCell>();
    if (supply) {
      for (const cell of supply) {
        m.set(`${cell.sku_code}-${cell.week_key}`, cell);
      }
    }
    return m;
  }, [supply]);

  // SKU lookup: sku_code -> SKU
  const skuMap = useMemo(() => {
    const m = new Map<string, SKU>();
    if (skus) {
      for (const sku of skus) m.set(sku.sku_code, sku);
    }
    return m;
  }, [skus]);

  // Week lookup: week_key -> Week
  const weekMap = useMemo(() => {
    const m = new Map<string, Week>();
    if (weeks) {
      for (const w of weeks) m.set(w.week_key, w);
    }
    return m;
  }, [weeks]);

  // Sorted SKUs by priority
  const sortedSkus = useMemo(() => {
    if (!skus) return [];
    return [...skus].sort((a, b) => a.priority - b.priority);
  }, [skus]);

  const handleCellMouseMove = useCallback(
    (e: React.MouseEvent, cell: SupplyCell) => {
      const sku = skuMap.get(cell.sku_code);
      const week = weekMap.get(cell.week_key);
      if (!sku || !week) return;
      setTooltip({ cell, sku, week, x: e.clientX, y: e.clientY });
    },
    [skuMap, weekMap],
  );

  const handleCellMouseLeave = useCallback(() => {
    setTooltip(null);
  }, []);

  // ── Render: loading ──────────────────────────────────────────────────────────

  if (loading) return <GridSkeleton />;

  // ── Render: error ────────────────────────────────────────────────────────────

  if (error) {
    return (
      <div className="sg-outer">
        <div className="sg-error" role="alert">
          <span className="sg-error-icon" aria-hidden="true">⚠</span>
          <span>Could not load supply grid: {error}</span>
        </div>
      </div>
    );
  }

  // ── Render: empty ─────────────────────────────────────────────────────────────

  if (!supply || !skus || !weeks || supply.length === 0 || sortedSkus.length === 0) {
    return (
      <div className="sg-outer">
        <div className="sg-empty">
          <span className="sg-empty-icon" aria-hidden="true">○</span>
          <span>No supply data available.</span>
        </div>
      </div>
    );
  }

  // ── Render: grid ──────────────────────────────────────────────────────────────

  return (
    <div className="sg-outer">
      {/* Section heading */}
      <div className="sg-header">
        <div className="sg-header-left">
          <span className="sg-title">Supply Forecast Grid</span>
          <span className="sg-subtitle">Forward cover by SKU and week</span>
        </div>
      </div>

      {/* Always-visible legend */}
      <Legend />

      {/* Week window controls */}
      <RangeControls
        weeks={weeks}
        startIdx={startIdx}
        visCount={visCount}
        onStartChange={setStartIdx}
        onVisCountChange={setVisCount}
      />

      {/* Scrollable grid */}
      <div className="sg-scroll-wrap" role="region" aria-label="Supply forecast grid">
        <table className="sg-table" role="grid">
          <caption className="sr-only">
            Supply forecast: {sortedSkus.length} SKUs across {visibleWeeks.length} weeks.
            Cells show forward cover as a traffic-light colour.
          </caption>

          <thead>
            <tr>
              {/* Corner: sticky left AND sticky top */}
              <th scope="col" className="sg-th sg-th-sku">
                <span className="sg-col-label">SKU</span>
              </th>

              {/* Week headers */}
              {visibleWeeks.map((week) => (
                <th
                  key={week.week_key}
                  scope="col"
                  className="sg-th sg-th-week"
                >
                  <span className="sg-week-iso">{isoWeekLabel(week.week_key)}</span>
                  <span className="sg-week-date">{fmtShortDate(week.week_commencing)}</span>
                  {week.is_locked && (
                    <span
                      className="sg-lock-badge"
                      title="Time-fence locked"
                      aria-label="Locked"
                    />
                  )}
                </th>
              ))}
            </tr>
          </thead>

          <tbody>
            {sortedSkus.map((sku, rowIdx) => (
              <tr
                key={sku.sku_code}
                className={`sg-row${rowIdx % 2 === 1 ? ' sg-row--alt' : ''}`}
              >
                {/* Sticky SKU description */}
                <td scope="row" className="sg-td sg-td-sku">
                  <div className="sg-td-sku-inner">
                    <span className="sg-sku-priority" aria-label={`Priority ${sku.priority}`}>
                      {sku.priority}
                    </span>
                    <span className="sg-sku-desc" title={sku.description}>
                      {sku.description}
                    </span>
                    <span className="sg-sku-pack">{sku.pack_size_ml}ml</span>
                  </div>
                </td>

                {/* Data cells */}
                {visibleWeeks.map((week) => {
                  const cell = cellMap.get(`${sku.sku_code}-${week.week_key}`);

                  if (!cell) {
                    return (
                      <td key={week.week_key} className="sg-td sg-td-cell sg-td-empty">
                        <span className="sg-cell-val" aria-label="No data">—</span>
                      </td>
                    );
                  }

                  const colour = isValidColour(cell.colour) ? cell.colour : ('green' as ColourName);
                  const bg = COLOUR_HEX[colour];
                  const fg = colourFg(colour);
                  const displayVal = getDisplayValue(cell);
                  const bandLabel = BAND_META[colour].label;

                  return (
                    <td
                      key={week.week_key}
                      className="sg-td sg-td-cell"
                      style={{ background: bg, color: fg }}
                      onMouseMove={(e) => handleCellMouseMove(e, cell)}
                      onMouseLeave={handleCellMouseLeave}
                      aria-label={`${sku.description} ${isoWeekLabel(week.week_key)}: ${formatValue(displayVal)} EA (${bandLabel})`}
                    >
                      <span className="sg-cell-val tabnum">
                        {formatValue(displayVal)}
                      </span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Floating tooltip (rendered on top via fixed positioning) */}
      {tooltip && <Tooltip data={tooltip} />}
    </div>
  );
}
