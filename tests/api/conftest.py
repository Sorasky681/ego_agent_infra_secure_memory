from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    application = create_app(str(tmp_path / "researchops.sqlite3"))
    with TestClient(application) as test_client:
        yield test_client
