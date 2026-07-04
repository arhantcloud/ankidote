#!/usr/bin/env python3
"""End-to-end calibration pipeline: questions.json -> py-irt -> catsim bank.

Runs the stages in order:
  1. make_dataset.py    -> responses.jsonlines + items.json  (ranking-based sim)
  2. py-irt train       -> pyirt_out/best_parameters.json
  3. to_catsim.py       -> item_bank.npy / .json / .csv       (+ optional CAT sim)
  4. build_app_bank.py  -> pylib/anki/ankidote/item_bank.json (live app bank)

Usage:
  python run_pipeline.py                         # 2PL, 400 examinees, 1000 epochs
  python run_pipeline.py --model 3pl --epochs 2000 --examinees 800
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def find_pyirt() -> str:
    """Locate the py-irt console script (prefer the current venv)."""
    candidate = os.path.join(os.path.dirname(sys.executable), "py-irt")
    if os.path.exists(candidate):
        return candidate
    found = shutil.which("py-irt")
    if not found:
        sys.exit("py-irt CLI not found. Install with: pip install py-irt "
                 "(requires Python 3.9-3.11).")
    return found


def run(cmd: list[str]) -> None:
    print(f"\n$ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--questions", default=os.path.join(HERE, "..", "gmat_questions.json"))
    parser.add_argument("--outdir", default=os.path.join(HERE, "artifacts"))
    parser.add_argument("--model", default="2pl", choices=["1pl", "2pl", "3pl"])
    parser.add_argument("--epochs", type=int, default=1000)
    parser.add_argument("--examinees", type=int, default=400)
    parser.add_argument("--guessing", default="auto",
                        help="Pseudo-guessing for the catsim bank (default auto=1/k).")
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--no-app-bank", action="store_true",
                        help="Skip writing the live app item bank (stage 4).")
    args = parser.parse_args()

    outdir = os.path.abspath(args.outdir)
    pyirt_out = os.path.join(outdir, "pyirt_out")
    responses = os.path.join(outdir, "responses.jsonlines")

    # Stage 1: build the (synthetic) response dataset.
    run([sys.executable, os.path.join(HERE, "make_dataset.py"),
         "--questions", os.path.abspath(args.questions),
         "--outdir", outdir,
         "--examinees", str(args.examinees),
         "--seed", str(args.seed)])

    # Stage 2: calibrate item parameters with py-irt.
    run([find_pyirt(), "train", args.model, responses, pyirt_out + os.sep,
         "--epochs", str(args.epochs)])

    # Stage 3: export a catsim-ready item bank and validate it.
    run([sys.executable, os.path.join(HERE, "to_catsim.py"),
         "--pyirt", os.path.join(pyirt_out, "best_parameters.json"),
         "--items", os.path.join(outdir, "items.json"),
         "--outdir", outdir,
         "--guessing", args.guessing,
         "--simulate"])

    # Stage 4: reshape into the live app's item bank (unless suppressed).
    if not args.no_app_bank:
        run([sys.executable, os.path.join(HERE, "build_app_bank.py"),
             "--in", os.path.join(outdir, "item_bank.json")])

    print(f"\nDone. catsim item bank written to {outdir}/item_bank.npy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
