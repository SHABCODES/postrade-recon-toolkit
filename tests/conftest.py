import pytest

from postrade_recon.datagen import generate
from postrade_recon.recon import run_recon

AS_OF = "2026-10-07"


@pytest.fixture(scope="session")
def data():
    return generate(AS_OF, seed=42)


@pytest.fixture(scope="session")
def result(data):
    return run_recon(data, AS_OF)
