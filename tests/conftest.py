"""Global fixtures for the Meal Planner integration tests."""

from __future__ import annotations

import pytest

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.meal_planner.const import (
    CONF_HORIZON_DAYS,
    CONF_HOUSEHOLD_SIZE,
    DOMAIN,
)

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom_components/ discovery for every test in this suite."""
    yield


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a (not yet added) Meal Planner config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Meal Planner",
        data={},
        options={CONF_HOUSEHOLD_SIZE: 4, CONF_HORIZON_DAYS: 10},
    )


@pytest.fixture
async def init_integration(hass, mock_config_entry: MockConfigEntry) -> MockConfigEntry:
    """Set up the integration for a test and return its entry."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry
