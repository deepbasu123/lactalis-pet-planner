import pytest
from backend.data_gen import generate


@pytest.fixture(scope="session")
def dataset():
    """Full synthetic PET dataset; seed=42 for reproducibility."""
    return generate()
