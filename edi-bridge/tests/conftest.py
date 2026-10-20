from pathlib import Path

import pytest

from edi_bridge import x12

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def read_sample(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="latin-1")


def codes(errors) -> list[str]:
    return [e.code for e in errors]


def parse_ok(text: str) -> x12.Interchange:
    """Parse text that must be valid; fail the test with the errors otherwise."""
    ic, errors = x12.read_x12(text)
    assert errors == [], [str(e) for e in errors]
    return ic


@pytest.fixture
def good() -> str:
    return read_sample("850_good.x12")
