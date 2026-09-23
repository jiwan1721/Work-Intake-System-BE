"""Scaffold smoke tests: Django boots and the database is reachable (SETUP-01)."""

import pytest
from django.conf import settings
from django.db import connection


def test_django_settings_load() -> None:
    assert settings.INSTALLED_APPS
    assert "work_items" in settings.INSTALLED_APPS
    assert settings.TIME_ZONE == "UTC"
    assert settings.USE_TZ is True


def test_ai_settings_have_dev_safe_defaults() -> None:
    assert settings.AI_PROVIDER == "mock"
    assert settings.AI_MAX_ATTEMPTS >= 1
    assert settings.AI_TIMEOUT_SECONDS > 0


@pytest.mark.django_db
def test_database_is_reachable() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        assert cursor.fetchone() == (1,)
