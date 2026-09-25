/**
 * ArchitectureView — a diagram of the re-architected, Databricks-native data flow.
 *
 * The whole point of this screen: every projection, traffic-light colour and
 * capacity-rule breach is computed server-side in medallion tables. This app is
 * a thin serving layer. The inline SVG makes the two compute paths explicit —
 * the batch Lakeflow pipeline and the interactive serverless-SQL recompute —
 * both feeding the same Gold rule engine.
 */
import './ArchitectureView.css';

interface NodeProps {
  x: number; y: number; w?: number; h?: number;
  title: string; sub?: string; variant?: string;
}

function Node({ x, y, w = 150, h = 54, title, sub, variant = '' }: NodeProps) {
  return (
    <g className={`arch-node ${variant}`}>
      <rect x={x} y={y} width={w} height={h} rx={2} />
      <text x={x + w / 2} y={sub ? y + h / 2 - 4 : y + h / 2 + 4} className="arch-node-title">{title}</text>
      {sub && <text x={x + w / 2} y={y + h / 2 + 13} className="arch-node-sub">{sub}</text>}
    </g>
  );
}

function Arrow({ x1, y1, x2, y2 }: { x1: number; y1: number; x2: number; y2: number }) {
  return <line x1={x1} y1={y1} x2={x2} y2={y2} className="arch-arrow" markerEnd="url(#arrowhead)" />;
}

export default function ArchitectureView() {
  return (
    <div className="arch-view">
      <div className="arch-banner">
        <strong>The UI runs zero business logic.</strong> Every projection, traffic-light colour and
        capacity breach is computed in the backend medallion tables — by the Lakeflow pipeline for a
        workbook upload, and by the same Gold rule SQL on a serverless warehouse after a planner edit.
      </div>

      <div className="arch-svg-wrap">
        <svg viewBox="0 0 900 470" className="arch-svg" role="img"
             aria-label="Medallion architecture: upload and interactive-edit paths both feed the Gold rule engine that this app reads.">
          <defs>
            <marker id="arrowhead" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
              <path d="M0,0 L6,3 L0,6 Z" className="arch-arrowhead" />
            </marker>
          </defs>

          {/* swimlane labels */}
          <text x="16" y="22" className="arch-lane">BATCH — workbook upload</text>
          <text x="16" y="358" className="arch-lane">INTERACTIVE — planner edit (~1–2s)</text>

          {/* batch path */}
          <Node x={16}  y={40}  w={150} title="Upload .xlsx" sub="PET / SNP / MLOR" variant="in" />
          <Node x={200} y={40}  w={150} title="UC Volume" sub="bronze.landing" variant="uc" />
          <Node x={384} y={30}  w={172} h={74} title="Lakeflow Pipeline" sub="Bronze → Silver → Gold" variant="compute" />

          {/* interactive path */}
          <Node x={16}  y={376} w={150} title="Planner edit" sub="one cell" variant="in" />
          <Node x={200} y={376} w={150} title="silver.plan_overlay" sub="app-written" variant="uc" />
          <Node x={384} y={366} w={172} h={74} title="Serverless SQL" sub="re-runs gold_sql.py" variant="compute" />

          {/* medallion spine */}
          <Node x={590} y={30}  w={150} title="Bronze" sub="raw rows" variant="bronze" />
          <Node x={590} y={196} w={150} title="Silver" sub="conformed + DQ" variant="silver" />
          <Node x={590} y={352} w={150} title="Gold" sub="rule engine (SQL)" variant="gold" />

          {/* consumers */}
          <Node x={766} y={30}  w={118} title="This App" sub="reads Gold" variant="out" />
          <Node x={766} y={196} w={118} title="Genie" sub="NL over Gold" variant="out" />
          <Node x={766} y={352} w={118} title="AI/BI" sub="dashboard" variant="out" />

          {/* batch arrows */}
          <Arrow x1={166} y1={66} x2={200} y2={66} />
          <Arrow x1={350} y1={66} x2={384} y2={66} />
          <Arrow x1={556} y1={52}  x2={590} y2={56} />
          <Arrow x1={556} y1={72}  x2={590} y2={218} />
          <Arrow x1={556} y1={92}  x2={590} y2={372} />

          {/* medallion spine arrows */}
          <Arrow x1={665} y1={84}  x2={665} y2={196} />
          <Arrow x1={665} y1={250} x2={665} y2={352} />

          {/* interactive arrows */}
          <Arrow x1={166} y1={402} x2={200} y2={402} />
          <Arrow x1={350} y1={402} x2={384} y2={402} />
          <Arrow x1={556} y1={402} x2={620} y2={406} />

          {/* consumer arrows (Gold -> App / Genie / AI/BI) */}
          <Arrow x1={740} y1={366} x2={766} y2={70} />
          <Arrow x1={740} y1={379} x2={766} y2={223} />
          <Arrow x1={740} y1={379} x2={766} y2={379} />
        </svg>
      </div>

      <div className="arch-notes">
        <div className="arch-note">
          <h4>Medallion, all in Unity Catalog</h4>
          <p><code>bronze</code> keeps the raw workbook rows (parsed with openpyxl in the pipeline).
             <code>silver</code> holds conformed, data-quality-checked tables. <code>gold</code> is the
             rule engine: projection, traffic lights and capacity breaches as SQL.</p>
        </div>
        <div className="arch-note">
          <h4>One rule definition, two callers</h4>
          <p><code>medallion/gold_sql.py</code> is the single source of truth. The pipeline runs it for a
             bulk upload; a warm serverless SQL warehouse runs the same SQL after each edit for an
             instant refresh — verified to match the original engine number-for-number.</p>
        </div>
        <div className="arch-note">
          <h4>The 21 business rules, server-side</h4>
          <p>RULE-008 two-week QA lag, RULE-009/010 floor-at-zero carry-forward, RULE-011/012 traffic
             lights, RULE-013/014/015 max-cover, RULE-001–007 pack-size / SKU-count / ceiling /
             maintenance / changeover — all encoded in Gold, none in the browser.</p>
        </div>
      </div>
    </div>
  );
}
