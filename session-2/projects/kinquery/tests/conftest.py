import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kinquery.hpo import HPO  # noqa: E402
from kinquery.matcher import Matcher  # noqa: E402
from kinquery.registry import Registry  # noqa: E402

DATA = ROOT.parents[2] / "session-2" / "data" / "phenopackets.jsonl"


@pytest.fixture(scope="session")
def registry() -> Registry:
    return Registry.load(DATA)


@pytest.fixture(scope="session")
def hpo() -> HPO:
    return HPO.load()


@pytest.fixture(scope="session")
def matcher(registry, hpo) -> Matcher:
    return Matcher(registry, hpo)
