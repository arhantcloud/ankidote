#!/usr/bin/env python3
"""Convert py-irt calibrated parameters into a catsim item bank.

catsim represents an item bank as an (n x 4) numpy array with columns
[a, b, c, d] = [discrimination, difficulty, pseudo-guessing, upper asymptote].
py-irt's `best_parameters.json` (2PL) provides `disc` (a) and `diff` (b) per
item, plus an `item_ids` map from array position -> item_id.

This script joins those parameters back to the question metadata (items.json)
and emits:
  * item_bank.npy   -- the (n x 4) matrix catsim consumes directly
  * item_bank.json  -- self-contained records: catsim params + question content
  * item_bank.csv   -- flat table for eyeballing / spreadsheets

With --simulate it also runs a short CAT with catsim to prove the bank loads
and produces sensible ability estimates.
"""
from __future__ import annotations

import argparse
import csv
import json
import os

import numpy as np
from catsim.irt import normalize_item_bank, validate_item_bank


def load_pyirt_params(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def build_matrix(params: dict, guessing: str) -> tuple[np.ndarray, list[str]]:
    """Return (n x 4 [a,b,c,d] matrix, ordered item_ids) from py-irt output.

    `guessing` is either "0" (faithful to a 2PL fit) or "auto" (1/num_choices,
    resolved later from metadata) -- here we emit column c=0 and let the caller
    overwrite it when metadata is available.
    """
    diff = params["diff"]
    disc = params.get("disc")
    id_map = params["item_ids"]  # {"0": "item_id", ...}
    n = len(diff)

    if disc is None:  # 1PL: discrimination fixed at 1.0
        disc = [1.0] * n

    item_ids = [id_map[str(i)] for i in range(n)]
    a = np.asarray(disc, dtype=float)
    b = np.asarray(diff, dtype=float)
    c = np.zeros(n, dtype=float)
    d = np.ones(n, dtype=float)
    matrix = np.column_stack([a, b, c, d])
    return matrix, item_ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pyirt", default="artifacts/pyirt_out/best_parameters.json",
                        help="py-irt best_parameters.json path.")
    parser.add_argument("--items", default="artifacts/items.json",
                        help="items.json produced by make_dataset.py.")
    parser.add_argument("--outdir", default="artifacts",
                        help="Output directory for the item bank files.")
    parser.add_argument("--guessing", default="auto",
                        help="Pseudo-guessing c: 'auto' = 1/num_choices, or a float "
                             "like 0.0 (default: auto).")
    parser.add_argument("--simulate", action="store_true",
                        help="Run a short catsim CAT to validate the bank.")
    args = parser.parse_args()

    params = load_pyirt_params(args.pyirt)
    with open(args.items, encoding="utf-8") as fh:
        items_meta = {it["item_id"]: it for it in json.load(fh)}

    matrix, item_ids = build_matrix(params, args.guessing)

    # Fill the guessing column.
    if args.guessing == "auto":
        for i, iid in enumerate(item_ids):
            k = items_meta.get(iid, {}).get("num_choices", 5)
            matrix[i, 2] = round(1.0 / max(k, 2), 4)
    else:
        matrix[:, 2] = float(args.guessing)

    # Let catsim validate/normalize (ensures a>0, 0<=c<=1, 0<=d<=1, shape n x 4).
    matrix = normalize_item_bank(matrix)
    validate_item_bank(matrix)

    os.makedirs(args.outdir, exist_ok=True)
    npy_path = os.path.join(args.outdir, "item_bank.npy")
    np.save(npy_path, matrix)

    # Self-contained records: catsim parameters + the question they belong to.
    records = []
    for i, iid in enumerate(item_ids):
        a, b, c, d = matrix[i].tolist()
        meta = items_meta.get(iid, {})
        records.append({
            "item_id": iid,
            "scraped_section": meta.get("scraped_section"),
            "section": meta.get("section"),
            "topic": meta.get("topic"),
            "subtopic": meta.get("subtopic"),
            "question_name": meta.get("question_name"),
            "difficulty_label": meta.get("difficulty_label"),
            "irt": {"a_discrimination": round(a, 6), "b_difficulty": round(b, 6),
                    "c_guessing": round(c, 6), "d_upper_asymptote": round(d, 6)},
            "question_text": meta.get("question_text"),
            "answers": meta.get("answers"),
            "correct_answer": meta.get("correct_answer"),
            "correct_index": meta.get("correct_index"),
            "choice_order": meta.get("choice_order"),
        })

    json_path = os.path.join(args.outdir, "item_bank.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(records, fh, ensure_ascii=False, indent=2)

    csv_path = os.path.join(args.outdir, "item_bank.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["item_id", "section", "difficulty_label", "a", "b", "c", "d"])
        for i, iid in enumerate(item_ids):
            a, b, c, d = matrix[i].tolist()
            meta = items_meta.get(iid, {})
            writer.writerow([iid, meta.get("section"), meta.get("difficulty_label"),
                             round(a, 6), round(b, 6), round(c, 6), round(d, 6)])

    print(f"catsim item bank: {matrix.shape[0]} items x {matrix.shape[1]} params")
    print(f"  b (difficulty) range: [{matrix[:,1].min():.2f}, {matrix[:,1].max():.2f}]")
    print(f"  a (discrimination) range: [{matrix[:,0].min():.2f}, {matrix[:,0].max():.2f}]")
    print(f"  wrote {npy_path}")
    print(f"  wrote {json_path}")
    print(f"  wrote {csv_path}")

    if args.simulate:
        run_simulation(matrix)
    return 0


def run_simulation(matrix: np.ndarray) -> None:
    """Prove the bank drives a catsim CAT."""
    from catsim.initialization import RandomInitializer
    from catsim.selection import MaxInfoSelector
    from catsim.estimation import NumericalSearchEstimator
    from catsim.stopping import TestLengthStopper
    from catsim.simulation import Simulator
    from catsim.item_bank import ItemBank

    n_items = min(15, matrix.shape[0])
    print(f"\nRunning a catsim CAT: 20 examinees, max {n_items} items each ...")
    sim = Simulator(ItemBank(matrix), 20)
    sim.simulate(
        RandomInitializer(),
        MaxInfoSelector(),
        NumericalSearchEstimator(),
        TestLengthStopper(n_items),
        verbose=False,
    )
    thetas = np.asarray(sim.latest_estimations, dtype=float)
    print(f"  estimated abilities theta: mean={thetas.mean():.3f}, "
          f"sd={thetas.std():.3f}, range=[{thetas.min():.2f}, {thetas.max():.2f}]")
    print("  catsim consumed the bank successfully.")


if __name__ == "__main__":
    raise SystemExit(main())
