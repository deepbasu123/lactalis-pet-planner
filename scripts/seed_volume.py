"""scripts/seed_volume.py

Generate a synthetic "PET Traffic Lights" workbook (exact PET/SNP/MLOR shape)
and upload it to the landing Volume so the pipeline has something to parse. This
is the demo seed; a real workbook uploaded through the app UI flows through the
identical path.

    .venv/bin/python -m scripts.seed_volume --profile deep-test-1
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile

from scripts.seed_and_verify import make_client
from medallion.synth_workbook import write_synth_workbook

DEFAULT_UPLOADS = "/Volumes/deep_test_1_catalog/lactalis_pet_bronze/landing/uploads"


def upload_synth(w, uploads_path: str, seed: int = 42, name: str = "synth_seed.xlsx") -> str:
    tmp = os.path.join(tempfile.gettempdir(), name)
    write_synth_workbook(tmp, seed=seed)
    dest = f"{uploads_path.rstrip('/')}/{name}"
    with open(tmp, "rb") as f:
        w.files.upload(dest, f, overwrite=True)
    return dest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--uploads-path", default=DEFAULT_UPLOADS)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    w = make_client(args.profile)
    dest = upload_synth(w, args.uploads_path, args.seed)
    print(f"uploaded synthetic workbook -> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
