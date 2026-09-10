# Lactalis PET Line Planner

A Databricks App that rebuilds the NOVA PET Line Planner (originally a Power Apps canvas app on Dataverse) as a single deployable unit on Databricks.

Planners view a traffic-light supply grid for the 11 OAK and PAULS flavoured-milk SKUs across a 52-week horizon, edit production quantities, and ask questions of the data through an embedded Genie assistant.

## Architecture

```
Single Databricks App (deep-test-1)
  React (Vite) frontend     branded UI, editable grids, Genie panel
        | REST/JSON
  FastAPI backend            stock + capacity engine; reads/writes UC via SQL warehouse
        | Databricks SDK statement execution
  Unity Catalog: deep_test_1_catalog.lactalis_pet_planner    base tables + projection_snapshot
        |
  Genie Space over PET tables    embedded chat via Conversation API
```

The entire app is one deployable unit. All planning logic lives in the FastAPI backend (Python + pandas). All durable data lives in Unity Catalog Delta tables. The engine runs live on each user interaction so edits recolour the grid in real time. `projection_snapshot` is written back to UC after each save so Genie can reason about stock health using the latest computed values.

## Data model (Unity Catalog, `deep_test_1_catalog.lactalis_pet_planner`)

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
export DATABRICKS_CONFIG_PROFILE=deep-test-1
export DATABRICKS_WAREHOUSE_ID=<your-warehouse-id>
export PET_CATALOG=deep_test_1_catalog
export PET_SCHEMA=lactalis_pet_planner
```

then restart the backend.

## Deploy to Databricks Apps

Prerequisites: Databricks CLI authenticated as `deep-test-1`, Node 18+ (for the frontend build).

```bash
python deploy.py --profile deep-test-1
```

To skip the frontend build if `frontend/dist` is already fresh:

```bash
python deploy.py --profile deep-test-1 --skip-frontend-build
```

To use a specific SQL warehouse instead of auto-selection:

```bash
python deploy.py --profile deep-test-1 --warehouse-id <id>
```

The script is fully idempotent. Running it again overwrites the data and re-deploys the app without duplicating any resources.

## Deploy steps

1. Build the React frontend (`npm ci && npm run build` in `frontend/`).
2. Create schema `deep_test_1_catalog.lactalis_pet_planner` (IF NOT EXISTS).
3. Create 7 Delta tables (IF NOT EXISTS).
4. Load 11-SKU x 52-week synthetic data (TRUNCATE + INSERT).
5. Compute `projection_snapshot` via the supply engine and load it.
6. Create or reuse the Genie space by title ("Lactalis PET Line Planner").
7. Write resolved `app.yaml` with the real warehouse and Genie space IDs.
8. Create the Databricks App record (or verify it exists).
9. Sync source code to the workspace and deploy.
10. Grant the app service principal:
    - `USE CATALOG` on `deep_test_1_catalog`
    - `USE SCHEMA + SELECT + MODIFY` on `deep_test_1_catalog.lactalis_pet_planner`
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
