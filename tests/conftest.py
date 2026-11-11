import os

import pytest


@pytest.fixture(scope="session")
def erp_test_url() -> str:
    """The test database. Tests empty its reference ERP tables, so it must be a database made only for tests."""
    url = os.environ.get("ERP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("ERP_TEST_DATABASE_URL is not set")

    name = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    if not name.endswith("_test"):
        pytest.exit(f'Refusing to run database tests on "{name}". The database name must end in _test.', returncode=2)

    return url
