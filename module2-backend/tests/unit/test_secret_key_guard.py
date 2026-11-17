"""The boot-time SECRET_KEY checks.

A missing key is fatal; a weak one is loud but survivable. The distinction is
deliberate -- docker-compose.yml falls back to a published placeholder so a
clean clone comes up at all, so refusing to boot on a weak key would mean
refusing to boot a fresh clone. These tests pin both halves, because a warning
nobody asserts is one that can quietly stop firing.
"""

import logging
import os
import secrets

import pytest

from app.main import (
    _MIN_SECRET_KEY_LENGTH,
    _PLACEHOLDER_SECRET_KEYS,
    _warn_about_weak_secret_key,
    lifespan,
)


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records
            if r.levelno >= logging.WARNING and "SECRET_KEY" in r.getMessage()]


class TestAWeakKeyWarnsAndKeepsGoing:
    @pytest.mark.parametrize("placeholder", sorted(_PLACEHOLDER_SECRET_KEYS))
    def test_a_published_placeholder_is_called_out(self, placeholder, caplog):
        with caplog.at_level(logging.WARNING, logger="app.main"):
            _warn_about_weak_secret_key(placeholder)
        assert len(_warnings(caplog)) == 1
        assert "forge" in _warnings(caplog)[0]

    def test_a_short_key_is_called_out(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.main"):
            _warn_about_weak_secret_key("a" * (_MIN_SECRET_KEY_LENGTH - 1))
        assert len(_warnings(caplog)) == 1
        assert "characters" in _warnings(caplog)[0]

    def test_the_floor_itself_is_accepted(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.main"):
            _warn_about_weak_secret_key("b" * _MIN_SECRET_KEY_LENGTH)
        assert _warnings(caplog) == []

    def test_what_env_example_tells_you_to_generate_is_silent(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.main"):
            _warn_about_weak_secret_key(secrets.token_urlsafe(48))
        assert _warnings(caplog) == []

    def test_warning_is_not_an_exception(self):
        """The whole point: a fresh clone still boots."""
        for key in sorted(_PLACEHOLDER_SECRET_KEYS) + ["x"]:
            _warn_about_weak_secret_key(key)      # must not raise


class TestTheLengthCheckCannotReplaceTheList:
    def test_the_compose_placeholder_is_longer_than_the_floor(self):
        """Why both checks exist, and why the list cannot be dropped.

        "your-secret-key-change-in-production" is 36 characters against a
        32-character floor, so a length test alone would wave it through --
        a published key of any length is worthless.
        """
        assert any(len(p) >= _MIN_SECRET_KEY_LENGTH
                   for p in _PLACEHOLDER_SECRET_KEYS)


class TestAMissingKeyIsFatal:
    @pytest.mark.asyncio
    async def test_boot_refuses_without_a_key(self, monkeypatch):
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="SECRET_KEY is not set"):
            async with lifespan(None):
                pass

    @pytest.mark.asyncio
    async def test_an_empty_key_counts_as_missing(self, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "")
        with pytest.raises(RuntimeError, match="SECRET_KEY is not set"):
            async with lifespan(None):
                pass
