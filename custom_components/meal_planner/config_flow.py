"""Config and options flow for the Meal Planner integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_HORIZON_DAYS,
    CONF_HOUSEHOLD_SIZE,
    DEFAULT_HORIZON_DAYS,
    DEFAULT_HOUSEHOLD_SIZE,
    DOMAIN,
    MAX_HORIZON_DAYS,
    MAX_HOUSEHOLD_SIZE,
    MIN_HORIZON_DAYS,
    MIN_HOUSEHOLD_SIZE,
)


def _settings_schema(household: int, horizon: int) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOUSEHOLD_SIZE, default=household): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_HOUSEHOLD_SIZE,
                    max=MAX_HOUSEHOLD_SIZE,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(CONF_HORIZON_DAYS, default=horizon): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_HORIZON_DAYS,
                    max=MAX_HORIZON_DAYS,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
    )


def _clean(user_input: dict[str, Any]) -> dict[str, int]:
    return {
        CONF_HOUSEHOLD_SIZE: int(user_input[CONF_HOUSEHOLD_SIZE]),
        CONF_HORIZON_DAYS: int(user_input[CONF_HORIZON_DAYS]),
    }


class MealPlannerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-step setup; the manifest's single_config_entry blocks a second one."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="Meal Planner", data={}, options=_clean(user_input))
        return self.async_show_form(
            step_id="user",
            data_schema=_settings_schema(DEFAULT_HOUSEHOLD_SIZE, DEFAULT_HORIZON_DAYS),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MealPlannerOptionsFlow:
        return MealPlannerOptionsFlow()


class MealPlannerOptionsFlow(OptionsFlowWithReload):
    """Change household size / plan window; the entry reloads afterwards."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=_clean(user_input))
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=_settings_schema(
                int(options.get(CONF_HOUSEHOLD_SIZE, DEFAULT_HOUSEHOLD_SIZE)),
                int(options.get(CONF_HORIZON_DAYS, DEFAULT_HORIZON_DAYS)),
            ),
        )
