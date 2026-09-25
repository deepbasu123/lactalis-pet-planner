import { useState, useEffect, useCallback, Component } from 'react';
import type { ReactNode, ErrorInfo } from 'react';
import './theme.css';
import Header from './components/Header';
import SupplyGrid from './components/SupplyGrid';
import ProductionGrid from './components/ProductionGrid';
import Parameters from './components/Parameters';
import SkuPriority from './components/SkuPriority';
import WeekConfig from './components/WeekConfig';
import TrafficLightSummary from './components/TrafficLightSummary';
import GeniePanel from './components/GeniePanel';
import UploadWorkbook from './components/UploadWorkbook';
import ArchitectureView from './components/ArchitectureView';
import { fetchConfig } from './api';
import type { ConfigResponse } from './api';

// ── Error boundary ────────────────────────────────────────────────────────────

interface EBState { hasError: boolean; message: string }

class ErrorBoundary extends Component<{ children: ReactNode }, EBState> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { hasError: false, message: '' };
  }

  static getDerivedStateFromError(err: unknown): EBState {
    const message = err instanceof Error ? err.message : String(err);
    return { hasError: true, message };
  }

  componentDidCatch(err: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary]', err, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-card" style={{ margin: 'var(--sp-8) auto', maxWidth: 560 }}>
          <h2>Something went wrong</h2>
          <p>{this.state.message}</p>
          <button
            className="retry-btn"
            onClick={() => this.setState({ hasError: false, message: '' })}
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

// ── Tab definitions ───────────────────────────────────────────────────────────

const TABS = [
  {
    id: 'production' as const,
    label: 'Production Grid',
    desc: 'Editable PET production plan by SKU and week, with weekly totals and capacity-rule validation. Direct-entry cells, Save / Discard / Reset-week flow.',
  },
  {
    id: 'week-config' as const,
    label: 'Week Config',
    desc: 'Week horizon settings, maintenance-window types (Full / Partial), time-fence locks, and week-level notes.',
  },
  {
    id: 'sku-priority' as const,
    label: 'SKU Priority',
    desc: 'SKU priority ranking (RULE-021), pack-size assignments, shelf-life and MLOR parameters, and active/inactive status.',
  },
  {
    id: 'parameters' as const,
    label: 'Parameters',
    desc: 'Planning parameters: capacity ceilings, QA hold weeks, target and maximum cover weeks, demand horizon, and reaction window.',
  },
  {
    id: 'summary' as const,
    label: 'Traffic Light Summary',
    desc: 'Traffic-light colour-band distribution across the full 52-week horizon. Original (SNP baseline) versus Production Plan comparison, with total-volume delta.',
  },
  {
    id: 'upload' as const,
    label: 'Upload Workbook',
    desc: 'Drag and drop a PET Traffic Lights .xlsx. The file lands in a Unity Catalog Volume and a Lakeflow Declarative Pipeline processes it Bronze to Silver to Gold; nothing is parsed in the browser.',
  },
  {
    id: 'architecture' as const,
    label: 'Architecture',
    desc: 'How the medallion backend computes every projection, colour and breach; the app is a thin serving layer.',
  },
] as const;

type TabId = (typeof TABS)[number]['id'];

// ── Loading skeleton ──────────────────────────────────────────────────────────

function LoadingShell() {
  return (
    <div className="loading-shell" role="status" aria-label="Loading planner data">
      {/* Header skeleton */}
      <div className="loading-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div className="skeleton" style={{ width: 120, height: 36 }} />
          <div className="skeleton" style={{ width: 160, height: 20 }} />
        </div>
        <div style={{ display: 'flex', gap: 12 }}>
          <div className="skeleton" style={{ width: 56, height: 32 }} />
          <div className="skeleton" style={{ width: 64, height: 32 }} />
          <div className="skeleton" style={{ width: 120, height: 32 }} />
        </div>
      </div>
      {/* Tab bar skeleton */}
      <div className="loading-tabs">
        {[160, 110, 100, 100, 180].map((w, i) => (
          <div
            key={i}
            className="skeleton"
            style={{ width: w, height: 20, animationDelay: `${i * 80}ms` }}
          />
        ))}
      </div>
      {/* Tab content skeleton (mirrors the production tab with SupplyGrid at top) */}
      <div className="tab-content">
        <div className="skeleton" style={{ height: 340, marginBottom: 16, borderRadius: 2 }} />
        <div className="skeleton" style={{ width: 280, height: 24, marginBottom: 12 }} />
        <div className="skeleton" style={{ width: 420, height: 16, marginBottom: 8 }} />
        <div className="skeleton" style={{ width: 360, height: 16 }} />
      </div>
    </div>
  );
}

// ── Error state ───────────────────────────────────────────────────────────────

interface ErrorShellProps {
  message: string;
  onRetry: () => void;
}

function ErrorShell({ message, onRetry }: ErrorShellProps) {
  return (
    <div className="error-shell">
      <div className="error-card">
        <h2>Could not load planner data</h2>
        <p>{message}</p>
        <button className="retry-btn" onClick={onRetry}>
          Try again
        </button>
      </div>
    </div>
  );
}

// ── Placeholder panel (per tab) ───────────────────────────────────────────────

interface PlaceholderPanelProps {
  title: string;
  desc: string;
}

function PlaceholderPanel({ title, desc }: PlaceholderPanelProps) {
  return (
    <div className="placeholder-panel">
      <h2>{title}</h2>
      <p>{desc}</p>
    </div>
  );
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>('production');
  const [retryCount, setRetryCount] = useState(0);
  const [genieOpen, setGenieOpen] = useState(false);
  const toggleGenie = useCallback(() => setGenieOpen((v) => !v), []);

  /**
   * dataVersion — a monotonically increasing counter shared between
   * SupplyGrid and ProductionGrid. When ProductionGrid accepts an edit
   * (or save/discard/reset), it calls bumpDataVersion() which increments
   * this counter; SupplyGrid includes it in its useEffect deps so it
   * re-fetches /api/supply and recolours without a full page reload.
   */
  const [dataVersion, setDataVersion] = useState(0);
  const bumpDataVersion = useCallback(() => {
    setDataVersion((v) => v + 1);
  }, []);

  /**
   * liveTotal — current total production (EA) as reported by ProductionGrid
   * via its onTotalUpdate callback.  Overrides the static meta.total_production
   * in the header chip so it reflects the current working plan.
   */
  const [liveTotal, setLiveTotal] = useState<number | null>(null);
  const handleTotalUpdate = useCallback((t: number) => setLiveTotal(t), []);

  useEffect(() => {
    setLoading(true);
    setError(null);
    void fetchConfig()
      .then((data) => {
        setConfig(data);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'Failed to load config');
      })
      .finally(() => {
        setLoading(false);
      });
  }, [retryCount]);

  const handleRetry = () => setRetryCount((c) => c + 1);

  if (loading) {
    return <LoadingShell />;
  }

  if (error) {
    return (
      <>
        <Header runMeta={undefined} genieOpen={genieOpen} onGenieToggle={toggleGenie} />
        <ErrorShell message={error} onRetry={handleRetry} />
        <ErrorBoundary>
          <GeniePanel isOpen={genieOpen} onClose={() => setGenieOpen(false)} />
        </ErrorBoundary>
      </>
    );
  }

  const activeTabDef = TABS.find((t) => t.id === activeTab) ?? TABS[0];

  // Merge liveTotal into meta so the header chip reflects the current editing state.
  const displayMeta = config?.meta
    ? { ...config.meta, total_production: liveTotal ?? config.meta.total_production }
    : undefined;

  return (
    <>
    <div className="app-layout">
      {/* ── Fixed header ──────────────────────────────── */}
      <Header runMeta={displayMeta} genieOpen={genieOpen} onGenieToggle={toggleGenie} />

      {/* ── Tab navigation ────────────────────────────── */}
      <nav className="tab-nav" role="tablist" aria-label="Planner tabs">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            role="tab"
            aria-selected={activeTab === tab.id}
            aria-controls={`tabpanel-${tab.id}`}
            id={`tab-${tab.id}`}
            className={`tab-btn${activeTab === tab.id ? ' tab-btn--active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      {/* ── Tab content ───────────────────────────────── */}
      <main
        id={`tabpanel-${activeTab}`}
        role="tabpanel"
        aria-labelledby={`tab-${activeTab}`}
        className="tab-content"
      >
      <ErrorBoundary>
        {activeTab === 'production' ? (
          <>
            {/* Hero supply grid — re-fetches when dataVersion increments */}
            <SupplyGrid dataVersion={dataVersion} />

            {/* Editable production grid — bumps dataVersion on each accepted edit */}
            <ProductionGrid
              weeks={config?.weeks ?? []}
              skus={config?.skus ?? []}
              dataVersion={dataVersion}
              onDataChange={bumpDataVersion}
              onTotalUpdate={handleTotalUpdate}
            />
          </>
        ) : activeTab === 'parameters' ? (
          <Parameters
            parameters={config?.parameters ?? []}
            onDataChange={bumpDataVersion}
          />
        ) : activeTab === 'sku-priority' ? (
          <SkuPriority
            skus={config?.skus ?? []}
            onDataChange={bumpDataVersion}
          />
        ) : activeTab === 'week-config' ? (
          <WeekConfig
            weeks={config?.weeks ?? []}
            onDataChange={bumpDataVersion}
          />
        ) : activeTab === 'summary' ? (
          <TrafficLightSummary dataVersion={dataVersion} />
        ) : activeTab === 'upload' ? (
          <UploadWorkbook onComplete={bumpDataVersion} />
        ) : activeTab === 'architecture' ? (
          <ArchitectureView />
        ) : (
          <PlaceholderPanel
            title={activeTabDef.label}
            desc={activeTabDef.desc}
          />
        )}
      </ErrorBoundary>
      </main>
    </div>

      {/* ── Genie panel -- app-level fixed overlay, closed by default ── */}
      <ErrorBoundary>
        <GeniePanel isOpen={genieOpen} onClose={() => setGenieOpen(false)} />
      </ErrorBoundary>
    </>
  );
}
