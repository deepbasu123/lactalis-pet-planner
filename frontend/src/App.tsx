import { useState, useEffect } from 'react';
import './theme.css';
import Header from './components/Header';
import SupplyGrid from './components/SupplyGrid';
import { fetchConfig } from './api';
import type { ConfigResponse } from './api';

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
        <Header runMeta={undefined} />
        <ErrorShell message={error} onRetry={handleRetry} />
      </>
    );
  }

  const activeTabDef = TABS.find((t) => t.id === activeTab) ?? TABS[0];

  return (
    <div className="app-layout">
      {/* ── Fixed header ──────────────────────────────── */}
      <Header runMeta={config?.run_meta} />

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
        {activeTab === 'production' ? (
          <>
            {/* Supply grid is the hero at the top of the Production Grid tab */}
            <SupplyGrid />
            {/* Editable production grid fills this space in a later task */}
            <div style={{ marginTop: 'var(--sp-6)' }}>
              <PlaceholderPanel
                title="Production Grid"
                desc={activeTabDef.desc}
              />
            </div>
          </>
        ) : (
          <PlaceholderPanel
            title={activeTabDef.label}
            desc={activeTabDef.desc}
          />
        )}
      </main>
    </div>
  );
}
