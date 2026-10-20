import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings

ROOT = Path(__file__).resolve().parent.parent
TEST_KEY = "synthetic-config-test-key"
TEST_DATABASE = {
    "DB_NAME": "synthetic_db",
    "DB_USER": "synthetic_user",
    "DB_PASSWORD": "$synthetic@password#%:/",
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "65432",
}


def load_settings(overrides, *, without_dotenv=False, inspect_database=False):
    environment = os.environ.copy()
    environment.update(
        DJANGO_SETTINGS_MODULE="ri_fest.settings",
        DJANGO_SECRET_KEY=TEST_KEY,
        **TEST_DATABASE,
    )
    for name, value in overrides.items():
        if value is None:
            environment.pop(name, None)
        else:
            environment[name] = value

    script = ""
    if without_dotenv:
        script = "import environ; environ.Env.read_env = lambda *args, **kwargs: None; "
    script += (
        "import json; import django; django.setup(); "
        "from django.conf import settings; "
        "data = {'debug': settings.DEBUG, "
        "'hosts': settings.ALLOWED_HOSTS, "
        "'synthetic_key': settings.SECRET_KEY == 'synthetic-config-test-key'}; "
    )
    if inspect_database:
        script += (
            "database = settings.DATABASES['default']; "
            "data['database'] = {key: database.get(key) for key in "
            "('ENGINE', 'NAME', 'USER', 'HOST', 'PORT')}; "
            "data['database']['password_matches'] = "
            "database['PASSWORD'] == '$synthetic@password#%:/'; "
            "data['database']['connect_timeout'] = "
            "database['OPTIONS']['connect_timeout']; "
        )
    script += "print(json.dumps(data))"
    return subprocess.run(
        [sys.executable, "-s", "-B", "-c", script],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


@pytest.mark.parametrize("debug", ["True", "False"])
def test_process_environment_overrides_dotenv_and_parses_types(debug):
    result = load_settings(
        {
            "DJANGO_DEBUG": debug,
            "DJANGO_ALLOWED_HOSTS": "first.invalid,second.invalid",
        }
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "debug": debug == "True",
        "hosts": ["first.invalid", "second.invalid"],
        "synthetic_key": True,
    }


def test_safe_defaults_without_dotenv():
    result = load_settings(
        {"DJANGO_DEBUG": None, "DJANGO_ALLOWED_HOSTS": None},
        without_dotenv=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "debug": False,
        "hosts": [],
        "synthetic_key": True,
    }


@pytest.mark.parametrize("key", ["", "   ", None])
def test_secret_key_is_required_and_nonempty(key):
    result = load_settings({"DJANGO_SECRET_KEY": key}, without_dotenv=True)

    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "DJANGO_SECRET_KEY" in result.stderr


def test_postgresql_fields_preserve_password_without_connecting():
    result = load_settings({}, inspect_database=True)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["database"] == {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "synthetic_db",
        "USER": "synthetic_user",
        "HOST": "127.0.0.1",
        "PORT": 65432,
        "password_matches": True,
        "connect_timeout": 5,
    }


@pytest.mark.parametrize("variable", ["DB_NAME", "DB_USER", "DB_PASSWORD"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_database_credentials_are_required(variable, value):
    result = load_settings({variable: value}, without_dotenv=True)

    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert variable in result.stderr
    assert TEST_DATABASE["DB_PASSWORD"] not in result.stderr


@pytest.mark.parametrize("port", ["", "bad", "0", "-1", "65536"])
def test_database_port_rejects_invalid_values_without_echoing_secrets(port):
    result = load_settings({"DB_PORT": port}, without_dotenv=True)

    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "DB_PORT must be an integer from 1 to 65535" in result.stderr
    assert TEST_DATABASE["DB_PASSWORD"] not in result.stderr


def test_database_host_and_port_have_local_defaults():
    result = load_settings(
        {"DB_HOST": None, "DB_PORT": None},
        without_dotenv=True,
        inspect_database=True,
    )

    assert result.returncode == 0, result.stderr
    database = json.loads(result.stdout)["database"]
    assert database["HOST"] == "127.0.0.1"
    assert database["PORT"] == 5432


@pytest.mark.parametrize("host", ["", "   "])
def test_database_host_must_not_be_empty(host):
    result = load_settings({"DB_HOST": host}, without_dotenv=True)

    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "DB_HOST must not be empty" in result.stderr


def test_utc_storage_and_krasnoyarsk_calendar_boundary():
    assert settings.USE_TZ is True
    assert settings.TIME_ZONE == "UTC"
    first_day = datetime(2027, 1, 28, tzinfo=ZoneInfo("Asia/Krasnoyarsk"))
    assert first_day.astimezone(UTC) == datetime(2027, 1, 27, 17, tzinfo=UTC)
