/**
 * UploadWorkbook — drag-and-drop a "PET Traffic Lights" .xlsx and watch it flow
 * through the medallion pipeline.
 *
 * The browser does NO parsing. It POSTs the file to /api/upload; the backend
 * lands it in a Unity Catalog Volume and triggers the Lakeflow Declarative
 * Pipeline (Bronze -> Silver -> Gold). We poll /api/pipeline/status and render
 * the medallion stages advancing. On completion we bump the shared dataVersion
 * so every grid refetches the freshly-recomputed Gold.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { uploadWorkbook, pipelineStatus } from '../api';
import type { PipelineStatus } from '../api';
import './UploadWorkbook.css';

interface Props {
  /** Called once the pipeline reaches COMPLETED — App bumps dataVersion. */
  onComplete: () => void;
}

type Phase = 'idle' | 'uploading' | 'running' | 'done' | 'error';

const STAGES = ['BRONZE', 'SILVER', 'GOLD'] as const;
type Stage = (typeof STAGES)[number];

const STAGE_LABEL: Record<Stage, string> = { BRONZE: 'Bronze', SILVER: 'Silver', GOLD: 'Gold' };
const STAGE_DESC: Record<Stage, string> = {
  BRONZE: 'Raw workbook rows parsed into Unity Catalog',
  SILVER: 'Conformed + data-quality checked',
  GOLD: 'Rule engine recomputed as SQL',
};

const POLL_MS = 2500;
const MAX_POLLS = 160; // ~6.5 min ceiling

function stageStatus(stage: Stage, status: PipelineStatus | null, phase: Phase): 'done' | 'active' | 'pending' {
  if (phase === 'done') return 'done';
  const cur = (status?.stage ?? '').toUpperCase();
  const curIdx = STAGES.indexOf(cur as Stage);
  const myIdx = STAGES.indexOf(stage);
  if (curIdx < 0) return phase === 'running' ? 'active' : 'pending'; // no per-stage signal
  if (myIdx < curIdx) return 'done';
  if (myIdx === curIdx) return 'active';
  return 'pending';
}

export default function UploadWorkbook({ onComplete }: Props) {
  const [phase, setPhase] = useState<Phase>('idle');
  const [fileName, setFileName] = useState<string | null>(null);
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const mounted = useRef(true);

  useEffect(() => () => { mounted.current = false; }, []);

  const poll = useCallback(async (runId: string, attempt: number) => {
    if (!mounted.current) return;
    if (attempt > MAX_POLLS) {
      setPhase('error');
      setMessage('Pipeline is taking longer than expected. Check the Lakeflow pipeline in the workspace.');
      return;
    }
    try {
      const st = await pipelineStatus(runId);
      if (!mounted.current) return;
      setStatus(st);
      const state = (st.state ?? '').toUpperCase();
      if (state === 'COMPLETED' || state === 'SUCCEEDED' || state === 'DONE') {
        setPhase('done');
        setMessage('Workbook processed. Bronze → Silver → Gold complete; grids refreshed from Gold.');
        onComplete();
        return;
      }
      if (state === 'FAILED' || state === 'ERROR' || state === 'CANCELED') {
        setPhase('error');
        setMessage(st.detail || 'The pipeline run failed. See the Lakeflow pipeline in the workspace for details.');
        return;
      }
      setTimeout(() => void poll(runId, attempt + 1), POLL_MS);
    } catch (err) {
      if (!mounted.current) return;
      setPhase('error');
      setMessage(err instanceof Error ? err.message : 'Failed to read pipeline status.');
    }
  }, [onComplete]);

  const startUpload = useCallback(async (file: File) => {
    if (!file.name.toLowerCase().endsWith('.xlsx')) {
      setPhase('error');
      setMessage('Please choose a .xlsx workbook (the "PET Traffic Lights" export).');
      return;
    }
    setFileName(file.name);
    setStatus(null);
    setMessage(null);
    setPhase('uploading');
    try {
      const { run_id } = await uploadWorkbook(file);
      if (!mounted.current) return;
      setPhase('running');
      void poll(run_id, 0);
    } catch (err) {
      if (!mounted.current) return;
      setPhase('error');
      setMessage(err instanceof Error ? err.message : 'Upload failed.');
    }
  }, [poll]);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) void startUpload(file);
  }, [startUpload]);

  const busy = phase === 'uploading' || phase === 'running';

  return (
    <div className="upload-panel">
      <div className="upload-intro">
        <h2>Upload PET Traffic Lights workbook</h2>
        <p>
          Drop the SNP/APO Excel export here. The file lands in a Unity Catalog Volume and a
          Lakeflow Declarative Pipeline processes it end to end. Nothing is parsed or calculated
          in this app or your browser.
        </p>
      </div>

      <div
        className={`dropzone${dragging ? ' dropzone--drag' : ''}${busy ? ' dropzone--busy' : ''}`}
        onDragOver={(e) => { e.preventDefault(); if (!busy) setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={busy ? undefined : onDrop}
        onClick={() => !busy && inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => { if ((e.key === 'Enter' || e.key === ' ') && !busy) inputRef.current?.click(); }}
        aria-label="Upload a PET Traffic Lights .xlsx workbook"
      >
        <input
          ref={inputRef}
          type="file"
          accept=".xlsx"
          hidden
          onChange={(e) => { const f = e.target.files?.[0]; if (f) void startUpload(f); e.target.value = ''; }}
        />
        <svg className="dropzone-icon" viewBox="0 0 24 24" width="34" height="34" aria-hidden="true">
          <path d="M12 3v12m0-12l-4 4m4-4l4 4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
        <div className="dropzone-text">
          {fileName ? <strong>{fileName}</strong> : <strong>Drop .xlsx here or click to browse</strong>}
          <span>PET / SNP Dump / MLOR sheets</span>
        </div>
      </div>

      {(busy || phase === 'done' || phase === 'error') && (
        <div className="medallion-flow" aria-live="polite">
          <div className={`flow-node flow-node--source ${busy || phase === 'done' ? 'is-done' : ''}`}>
            <span className="flow-node-kicker">UC Volume</span>
            <span className="flow-node-title">Landing</span>
          </div>
          <div className="flow-arrow" aria-hidden="true" />
          {STAGES.map((stage, i) => {
            const s = stageStatus(stage, status, phase);
            return (
              <div key={stage} className="flow-seg">
                {i > 0 && <div className="flow-arrow" aria-hidden="true" />}
                <div className={`flow-node flow-node--${stage.toLowerCase()} is-${s}`}>
                  <span className="flow-node-kicker">{s === 'active' ? 'processing…' : STAGE_LABEL[stage]}</span>
                  <span className="flow-node-title">{STAGE_LABEL[stage]}</span>
                  <span className="flow-node-desc">{STAGE_DESC[stage]}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {message && (
        <div className={`upload-msg upload-msg--${phase === 'error' ? 'error' : phase === 'done' ? 'ok' : 'info'}`}>
          {message}
        </div>
      )}
    </div>
  );
}
