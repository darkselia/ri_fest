import pytest
from django.db import connection


@pytest.mark.django_db
def test_database_query_uses_temporary_sqlite():
    assert connection.vendor == "sqlite"
    assert "memory" in str(connection.settings_dict["NAME"])
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        assert cursor.fetchone() == (1,)
