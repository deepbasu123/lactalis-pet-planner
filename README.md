# Lactalis PET Line Planner

A Databricks App that rebuilds the NOVA PET Line Planner (originally a Power Apps canvas app on Dataverse) as a single deployable unit on Databricks.

Planners view a traffic-light supply grid for the 11 OAK and PAULS flavoured-milk SKUs across a 52-week horizon, edit production quantities, and ask questions of the data through an embedded Genie assistant.

## Architecture

```
Single Databricks App
  React (Vite) frontend     branded UI, editable grids, Genie panel
        | REST/JSON
  FastAPI backend            stock + capacity engine; reads/writes UC via SQL warehouse
        | Databricks SDK statement execution
  Unity Catalog: <catalog>.<schema>    base tables + projection_snapshot
        |
  Genie Space over PET tables    embedded chat via Conversation API
```

`<catalog>` and `<schema>` are chosen at deploy time (`--catalog` / `--schema`); nothing is hard-coded to one workspace.

The entire app is one deployable unit. All planning logic lives in the FastAPI backend (Python + pandas). All durable data lives in Unity Catalog Delta tables. The engine runs live on each user interaction so edits recolour the grid in real time. `projection_snapshot` is written back to UC after each save so Genie can reason about stock health using the latest computed values.

## Data model (Unity Catalog, `<catalog>.<schema>`)

| Table | Rows | Notes |
|---|---|---|
| sku | 11 | SKU master: code, description, pack size, priority, shelf life |
| week | 52 | Planning horizon: week key, maintenance type, time-fence lock |
| parameter | 10 | Capacity and planning parameters |
| demand | 572 | Forecast + sales order per SKU per week |
| plan_line | 572 | Production schedule (editable) with SNP baseline |
| opening_stock | 11 | SOH at horizon start |
| projection_snapshot | 572 | Computed supply projection, written by the engine on each save |

## Local development

Requirements: Python 3.11+, Node 18+.

```bash
# Create and activate the venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run the backend (synthetic data mode -- no workspace needed)
uvicorn backend.main:app --reload --port 8000

# In a second terminal, run the frontend dev server
cd frontend
npm install
npm run dev      # proxies /api to http://localhost:8000
```

Open http://localhost:5173. With `PET_LIVE` unset (the default), the app uses fully synthetic data generated in memory. No Databricks connection is required.

To run against a live workspace locally, set:

```bash
export PET_LIVE=1
export DATABRICKS_CONFIG_PROFILE=<your-cli-profile>
export DATABRICKS_WAREHOUSE_ID=<your-warehouse-id>
export PET_CATALOG=<your-catalog>
export PET_SCHEMA=<your-schema>
```

then restart the backend.

## Deploy to your own Databricks workspace

The app is designed to drop into any Unity Catalog workspace. One command builds the frontend, provisions the schema, tables, synthetic data, and a Genie space, deploys the app, and grants the app service principal everything it needs.

**Prerequisites**

- **Databricks CLI** (v0.230+) authenticated to your workspace: `databricks auth login --profile <name>`. The signed-in user needs permission to create apps and grant Unity Catalog privileges.
- A **Unity Catalog catalog** you can create schemas in (`CREATE SCHEMA`). Pass it with `--catalog`.
- A **running SQL warehouse** (any size). The script auto-selects one, or pass `--warehouse-id`.
- **Genie** enabled in the workspace (for the embedded chat panel).
- **Python 3.11+** and **Node 18+** on the machine running the deploy (for the frontend build). Then: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.

**Deploy**

```bash
python deploy.py --profile <your-cli-profile> --catalog <your-catalog>
```

That is the whole thing. `--schema` (default `lactalis_pet_planner`) and `--app-name` (default `lactalis-pet-planner`) are optional:

```bash
python deploy.py --profile prod --catalog main --schema pet_planner --app-name pet-planner
```

Other options:

```bash
python deploy.py --profile <p> --catalog <c> --warehouse-id <id>   # pin a warehouse
python deploy.py --profile <p> --catalog <c> --skip-frontend-build # reuse frontend/dist
python deploy.py --profile <p> --catalog <c> --data-file <path.xlsx> # load real data instead of the synthetic demo
```

### Loading real data instead of the synthetic demo

By default `deploy.py` loads the built-in synthetic dataset. To load real
operational data instead, pass `--data-file` pointing at a local
"PET Traffic Lights" workbook (the weekly SNP/APO export + production plan
this app is modelled on). `backend/excel_source.py` parses it into the same
six tables the synthetic generator produces -- see that module's docstring
for the exact sheet/column shape it expects, and what it does when a value
is missing (real files aren't always complete: shelf-life/MLOR gaps are
imputed from the closest analogous SKU in the same file, and blank
production cells load as 0, not a guess).

The workbook itself is read locally and is **never** uploaded, copied into
the repo, or committed -- only the parsed table data reaches Unity Catalog.
`*.xlsx` is gitignored for exactly this reason.

The script is fully idempotent: running it again overwrites the data and re-deploys the app without duplicating resources. When it finishes it prints the app URL.

## Deploy steps

1. Build the React frontend (`npm ci && npm run build` in `frontend/`). The built assets are force-included in the workspace sync (they are gitignored, so a plain sync would skip them).
2. Create schema `<catalog>.<schema>` if it does not exist.
3. `CREATE OR REPLACE` the 7 Delta tables with the correct schemas.
4. Load 11-SKU x 52-week synthetic data (TRUNCATE + INSERT).
5. Compute `projection_snapshot` via the supply engine and load it.
6. Create or reuse the Genie space (title is scoped to the schema).
7. Write resolved `app.yaml` with the real catalog, schema, warehouse, and Genie space IDs.
8. Create the Databricks App record (or verify it exists).
9. Sync source code to the workspace and deploy.
10. Grant the app service principal:
    - `USE CATALOG` on `<catalog>`
    - `USE SCHEMA + SELECT + MODIFY` on `<catalog>.<schema>`
    - `CAN_USE` on the SQL warehouse
    - `CAN RUN` on the Genie space
11. Poll `GET /api/health` until `{"status":"ok"}` or 60 s timeout.

## Environment variables

| Variable | Source | Description |
|---|---|---|
| `PET_LIVE` | app.yaml / deploy.py | Set to `1` to read from UC. Unset for synthetic data. |
| `PET_CATALOG` | app.yaml / deploy.py | Unity Catalog catalog name. |
| `PET_SCHEMA` | app.yaml / deploy.py | Schema name within catalog. |
| `DATABRICKS_WAREHOUSE_ID` | app.yaml / deploy.py | SQL warehouse used for all reads and writes. |
| `PET_GENIE_SPACE_ID` | app.yaml / deploy.py | Genie space ID for the embedded chat panel. |
| `DATABRICKS_HOST` | Injected by Databricks Apps runtime | Workspace hostname (no `https://` prefix in the injected value). |
| `DATABRICKS_CLIENT_ID` | Injected by Databricks Apps runtime | App service principal client ID. |
| `DATABRICKS_CLIENT_SECRET` | Injected by Databricks Apps runtime | App service principal secret. |

Do not put secrets in `app.yaml` or commit them to the repository.

## Running tests

```bash
pytest
```

All tests are offline (no workspace required). The test suite covers the calculation engine, data generation, DB helpers, API endpoints, Genie provisioning, and deploy helpers.

## Logs

Access live application logs at:

```
https://<app-url>/logz
```
