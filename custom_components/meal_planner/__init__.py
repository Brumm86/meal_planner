"""The Meal Planner integration: lunch recipes, a rolling plan, shopping lists."""

from __future__ import annotations

from pathlib import Path

import voluptuous as vol

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import CARD_FILENAME, CARD_URL_PATH, DOMAIN, SERVICE_TRANSFER_TO_BRING
from .coordinator import MealPlannerCoordinator, MealPlannerError
from .storage import async_load_collections
from .websocket_api import async_setup_websocket_api

# No entity platforms yet: calendar and sensors arrive in a later stage.
PLATFORMS: list = []

type MealPlannerConfigEntry = ConfigEntry[MealPlannerCoordinator]


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_TRANSFER_SCHEMA = vol.Schema(
    {
        vol.Optional("start_date"): cv.date,
        vol.Optional("end_date"): cv.date,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions (once, independent of the config entry)."""

    async def _transfer_to_bring(call: ServiceCall) -> dict[str, int]:
        entries = hass.config_entries.async_entries(DOMAIN)
        entry = next((e for e in entries if e.state is ConfigEntryState.LOADED), None)
        if entry is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="not_loaded"
            )
        start = call.data.get("start_date")
        end = call.data.get("end_date")
        try:
            return await entry.runtime_data.async_transfer_to_bring(
                start.isoformat() if start else None, end.isoformat() if end else None
            )
        except MealPlannerError as err:
            raise ServiceValidationError(err.message) from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_TRANSFER_TO_BRING,
        _transfer_to_bring,
        schema=SERVICE_TRANSFER_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MealPlannerConfigEntry) -> bool:
    """Load the storage collections, build the coordinator, expose the API + card."""
    recipes, ingredients, plan, transfers = await async_load_collections(hass)
    coordinator = MealPlannerCoordinator(hass, entry, recipes, ingredients, plan, transfers)
    await coordinator.async_config_entry_first_refresh()
    coordinator.async_start()
    entry.runtime_data = coordinator

    async_setup_websocket_api(hass)
    await _async_register_frontend(hass)

    if PLATFORMS:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MealPlannerConfigEntry) -> bool:
    """Unload; the websocket commands and the card stay registered (global)."""
    if PLATFORMS:
        return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return True


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the card and inject it into every dashboard (no manual resource).

    Same recipe that works for Family Tasks on the household's phones:

    * a "?v=<integration version>" cache-buster, because companion apps cache
      same-URL requests aggressively regardless of response headers;
    * the URL is registered a second time with es5=True - Home Assistant's
      frontend only dynamic-imports module URLs on browsers it classifies as
      "latest" and loads es5 URLs as plain scripts otherwise. The card file
      therefore must not use import/export statements.
    """
    key = f"{DOMAIN}_frontend_registered"
    if hass.data.get(key):
        return
    hass.data[key] = True

    integration = await async_get_integration(hass, DOMAIN)
    www_dir = Path(__file__).parent / "www"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL_PATH, str(www_dir / CARD_FILENAME), cache_headers=False)]
    )
    card_url = f"{CARD_URL_PATH}?v={integration.version}"
    add_extra_js_url(hass, card_url)
    add_extra_js_url(hass, card_url, es5=True)
