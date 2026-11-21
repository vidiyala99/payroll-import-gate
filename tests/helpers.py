"""Shared paths. All datasets used by the tests are MOCK/SAMPLE data written for this repo."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLEAN = ROOT / "samples" / "acme-switch-clean"
DEMO = ROOT / "samples" / "acme-switch"
EVALS = ROOT / "samples" / "evals"
