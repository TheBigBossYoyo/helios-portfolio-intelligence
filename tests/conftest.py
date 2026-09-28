"""Test-wide isolation from the machine running the tests.

Helios reads credentials from the OS keyring when the environment does not supply them. That is
right in production and wrong in a test: a suite that passes because the developer's real
Trading 212 key happened to be in Credential Manager tells you nothing about whether it will
pass anywhere else. Worse, it would reach the real broker during a test run.

Setting ``HELIOS_DISABLE_KEYRING`` for the whole session closes that off at the source, so every
``Settings()`` built in a test sees only what the test itself provides. ``HELIOS_DISABLE_DOTENV``
does the same for a `.env` in the directory pytest runs from -- the repository root, where the
developer's real one lives.
"""

from __future__ import annotations

import pytest
from pytest import MonkeyPatch


@pytest.fixture(autouse=True, scope="session")
def _disable_os_keyring() -> object:
    """Keep the developer's real credentials out of every test in the suite."""

    patcher = MonkeyPatch()
    patcher.setenv("HELIOS_DISABLE_KEYRING", "1")
    patcher.setenv("HELIOS_DISABLE_DOTENV", "1")
    # Provider pacing exists for real free-plan quotas; a fake transport has none, and a
    # suite sleeping 7.6s between mocked requests would take minutes for nothing.
    patcher.setenv("HELIOS_TWELVEDATA_MIN_INTERVAL_SECONDS", "0")
    patcher.setenv("HELIOS_ALPHAVANTAGE_MIN_INTERVAL_SECONDS", "0")
    # The automatic replay + news refresh after a sync is exercised by its own tests; elsewhere a
    # stubbed sync must not quietly start a real replay against the test database.
    patcher.setenv("HELIOS_REFRESH_AFTER_SYNC", "0")
    # An automatic restart re-execs or signals the process -- in a test run, the runner.
    patcher.setenv("HELIOS_AUTO_APPLY_SETTINGS", "0")
    # Requesting a card export is a real POST that notifies the account holder's phone.
    patcher.setenv("HELIOS_CARD_HISTORY_ENABLED", "0")
    yield
    patcher.undo()
