import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.config import settings

app = FastAPI(title="Lactalis PET Line Planner")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# Mount the compiled frontend only when it has been built.
# This allows the backend to start independently (e.g. during tests or
# early development) before `npm run build` has been executed.
_dist_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(_dist_dir):
    app.mount("/", StaticFiles(directory=_dist_dir, html=True), name="static")
