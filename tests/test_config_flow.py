"""Tests for the Meal Planner config and options flow."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.meal_planner.const import (
    CONF_HORIZON_DAYS,
    CONF_HOUSEHOLD_SIZE,
    DOMAIN,
)


async def test_user_flow_creates_entry(hass) -> None:
    """The single setup step stores household size and horizon as options."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOUSEHOLD_SIZE: 3, CONF_HORIZON_DAYS: 10}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {}
    assert result["options"] == {CONF_HOUSEHOLD_SIZE: 3, CONF_HORIZON_DAYS: 10}


async def test_only_a_single_instance_is_allowed(hass, init_integration) -> None:
    """A second setup attempt must be aborted."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.ABORT


async def test_options_flow_updates_and_reloads(hass, init_integration) -> None:
    """Changing the options saves them (and reloads the entry)."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_HOUSEHOLD_SIZE: 2, CONF_HORIZON_DAYS: 14}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert init_integration.options == {CONF_HOUSEHOLD_SIZE: 2, CONF_HORIZON_DAYS: 14}
    assert init_integration.runtime_data.household_size == 2


async def test_bring_list_is_optional_and_can_be_set_and_cleared(hass, init_integration) -> None:
    """The Bring list lands in the options only when chosen."""
    from custom_components.meal_planner.const import CONF_BRING_ENTITY

    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_HOUSEHOLD_SIZE: 4, CONF_HORIZON_DAYS: 10, CONF_BRING_ENTITY: "todo.bring_einkauf"},
    )
    await hass.async_block_till_done()
    assert init_integration.options[CONF_BRING_ENTITY] == "todo.bring_einkauf"
    assert init_integration.runtime_data.bring_entity == "todo.bring_einkauf"

    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_HOUSEHOLD_SIZE: 4, CONF_HORIZON_DAYS: 10}
    )
    await hass.async_block_till_done()
    assert CONF_BRING_ENTITY not in init_integration.options
    assert init_integration.runtime_data.bring_entity is None
