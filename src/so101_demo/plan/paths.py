from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS_DIR = REPO_ROOT / "contracts"
PLAN_SCHEMA_PATH = CONTRACTS_DIR / "plan.schema.json"
REGISTRY_PATH = CONTRACTS_DIR / "registry.json"
SAMPLES_DIR = CONTRACTS_DIR / "samples"
FIXTURES_DIR = CONTRACTS_DIR / "fixtures"
FIXTURES_4A_DIR = FIXTURES_DIR / "4a"
FIXTURES_G1_DIR = FIXTURES_DIR / "g1"
G1_MANIFEST_PATH = FIXTURES_G1_DIR / "manifest.json"
FIXTURES_S1_DIR = FIXTURES_DIR / "s1"
S1_MANIFEST_PATH = FIXTURES_S1_DIR / "videos.json"
FIXTURES_UC_DIR = FIXTURES_DIR / "uc"
UC_MANIFEST_PATH = FIXTURES_UC_DIR / "videos.json"
