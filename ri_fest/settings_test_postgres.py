"""Opt-in integration tests against a separately provisioned PostgreSQL database."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "synthetic-postgres-test-key")

from .settings import *  # noqa: E402, F403

test_name = os.environ.get("DB_TEST_NAME", "ri_fest_test")
if not test_name.endswith("_test") or test_name == DATABASES["default"]["NAME"]:  # noqa: F405
    raise ValueError("DB_TEST_NAME must be a separate database ending in _test.")
DATABASES["default"]["TEST"] = {"NAME": test_name}  # noqa: F405
