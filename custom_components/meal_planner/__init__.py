"""The Meal Planner integration: lunch recipes, a rolling plan, shopping lists."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import (
    CARD_FILENAME,
    CARD_URL_PATH,
    DOMAIN,
    SERVICE_ADD_MEAL,
    SERVICE_APPLY_SUGGESTIONS,
    SERVICE_CLEAR_DAY,
    SERVICE_CREATE_RECIPE,
    SERVICE_DELETE_RECIPE,
    SERVICE_GET_PLAN,
    SERVICE_REMOVE_MEAL,
    SERVICE_SUGGEST_MEALS,
    SERVICE_TRANSFER_TO_BRING,
    ROLES,
    UNITS,
)
from .coordinator import MealPlannerCoordinator, MealPlannerError
from .storage import async_load_collections
from .websocket_api import async_setup_websocket_api

# No entity platforms yet: calendar and sensors arrive in a later stage.
PLATFORMS: list = []

type MealPlannerConfigEntry = ConfigEntry[MealPlannerCoordinator]


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_PORTIONS = vol.All(vol.Coerce(int), vol.Range(min=1, max=50))

SERVICE_TRANSFER_SCHEMA = vol.Schema(
    {
        vol.Optional("start_date"): cv.date,
        vol.Optional("end_date"): cv.date,
    }
)
SERVICE_ADD_MEAL_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required("date"): cv.date,
            vol.Optional("recipe"): cv.string,
            vol.Optional("text"): cv.string,
            vol.Optional("portions"): _PORTIONS,
            vol.Optional("sides"): vol.All(cv.ensure_list, [cv.string]),
        }
    ),
    cv.has_at_least_one_key("recipe", "text"),
    cv.has_at_most_one_key("recipe", "text"),
)
SERVICE_REMOVE_MEAL_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required("date"): cv.date,
            vol.Optional("recipe"): cv.string,
            vol.Optional("text"): cv.string,
        }
    ),
    cv.has_at_least_one_key("recipe", "text"),
    cv.has_at_most_one_key("recipe", "text"),
)
SERVICE_CLEAR_DAY_SCHEMA = vol.Schema({vol.Required("date"): cv.date})
SERVICE_GET_PLAN_SCHEMA = SERVICE_TRANSFER_SCHEMA
SERVICE_SUGGEST_SCHEMA = vol.Schema(
    {
        vol.Optional("dates"): vol.All(cv.ensure_list, [cv.date]),
        vol.Optional("exclude_recipes", default=list): vol.All(cv.ensure_list, [cv.string]),
    }
)
SERVICE_APPLY_SUGGESTIONS_SCHEMA = vol.Schema(
    {
        vol.Required("suggestions"): [
            vol.Schema(
                {
                    vol.Required("date"): cv.date,
                    vol.Required("recipe"): cv.string,
                    vol.Optional("portions"): _PORTIONS,
                    vol.Optional("sides"): vol.All(cv.ensure_list, [cv.string]),
                }
            )
        ]
    }
)
SERVICE_CREATE_RECIPE_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Optional("base_portions"): _PORTIONS,
        vol.Optional("duration_min"): vol.Coerce(int),
        vol.Optional("tags"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("roles"): vol.All(cv.ensure_list, vol.Length(min=1), [vol.In(list(ROLES))]),
        vol.Optional("sides"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("notes"): cv.string,
        vol.Optional("instructions"): cv.string,
        vol.Optional("ingredients", default=list): [
            vol.Schema(
                {
                    vol.Required("name"): cv.string,
                    vol.Optional("amount"): vol.Coerce(float),
                    vol.Optional("unit"): vol.In(list(UNITS)),
                }
            )
        ],
    }
)
SERVICE_DELETE_RECIPE_SCHEMA = vol.Schema({vol.Required("recipe"): cv.string})


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _get_coordinator(hass: HomeAssistant) -> MealPlannerCoordinator:
    entries = hass.config_entries.async_entries(DOMAIN)
    entry = next((e for e in entries if e.state is ConfigEntryState.LOADED), None)
    if entry is None:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
    return entry.runtime_data


def _service(
    hass: HomeAssistant,
    func: Callable[[MealPlannerCoordinator, ServiceCall], Awaitable[Any]],
) -> Callable[[ServiceCall], Awaitable[Any]]:
    """Resolve the coordinator and turn domain/validation errors into service errors."""

    async def handler(call: ServiceCall) -> Any:
        coordinator = _get_coordinator(hass)
        try:
            return await func(coordinator, call)
        except MealPlannerError as err:
            raise ServiceValidationError(err.message) from err
        except vol.Invalid as err:
            raise ServiceValidationError(str(err)) from err

    return handler


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions (once, independent of the config entry)."""

    async def transfer_to_bring(c: MealPlannerCoordinator, call: ServiceCall) -> dict[str, int]:
        return await c.async_transfer_to_bring(
            _iso(call.data.get("start_date")), _iso(call.data.get("end_date"))
        )

    async def add_meal(c: MealPlannerCoordinator, call: ServiceCall) -> dict[str, str]:
        recipe_id = c.find_recipe(call.data["recipe"])["id"] if "recipe" in call.data else None
        text = call.data["text"].strip() if "text" in call.data else None
        entry = await c.async_add_meal(
            _iso(call.data["date"]),
            recipe_id=recipe_id,
            text=text or None,
            portions=call.data.get("portions"),
            side_ids=[c.find_recipe(ref)["id"] for ref in call.data.get("sides", [])],
        )
        return {"id": entry["id"]}

    async def remove_meal(c: MealPlannerCoordinator, call: ServiceCall) -> None:
        day = _iso(call.data["date"])
        if "recipe" in call.data:
            await c.async_remove_recipe_from_day(day, c.find_recipe(call.data["recipe"])["id"])
        else:
            await c.async_remove_text_from_day(day, call.data["text"])

    async def clear_day(c: MealPlannerCoordinator, call: ServiceCall) -> dict[str, int]:
        return {"removed": await c.async_clear_plan(_iso(call.data["date"]))}

    async def get_plan(c: MealPlannerCoordinator, call: ServiceCall) -> dict[str, Any]:
        return {"days": c.get_plan(_iso(call.data.get("start_date")), _iso(call.data.get("end_date")))}

    async def suggest_meals(c: MealPlannerCoordinator, call: ServiceCall) -> dict[str, Any]:
        dates = call.data.get("dates")
        return c.suggest_meals(
            [_iso(d) for d in dates] if dates is not None else None,
            [c.find_recipe(ref)["id"] for ref in call.data["exclude_recipes"]],
        )

    async def apply_suggestions(c: MealPlannerCoordinator, call: ServiceCall) -> dict[str, int]:
        return await c.async_apply_suggestions(
            [
                {
                    "date": _iso(item["date"]),
                    "recipe_id": c.find_recipe(item["recipe"])["id"],
                    "portions": item.get("portions"),
                    "side_ids": [c.find_recipe(ref)["id"] for ref in item.get("sides", [])],
                }
                for item in call.data["suggestions"]
            ]
        )

    async def create_recipe(c: MealPlannerCoordinator, call: ServiceCall) -> dict[str, Any]:
        data = dict(call.data)
        data.setdefault("base_portions", c.household_size)
        if "sides" in data:
            data["side_ids"] = [c.find_recipe(ref)["id"] for ref in data.pop("sides")]
        return await c.async_create_recipe_by_names(data)

    async def delete_recipe(c: MealPlannerCoordinator, call: ServiceCall) -> None:
        await c.async_delete_recipe(c.find_recipe(call.data["recipe"])["id"])

    for name, func, schema, response in (
        (SERVICE_TRANSFER_TO_BRING, transfer_to_bring, SERVICE_TRANSFER_SCHEMA, SupportsResponse.OPTIONAL),
        (SERVICE_ADD_MEAL, add_meal, SERVICE_ADD_MEAL_SCHEMA, SupportsResponse.OPTIONAL),
        (SERVICE_REMOVE_MEAL, remove_meal, SERVICE_REMOVE_MEAL_SCHEMA, SupportsResponse.NONE),
        (SERVICE_CLEAR_DAY, clear_day, SERVICE_CLEAR_DAY_SCHEMA, SupportsResponse.OPTIONAL),
        (SERVICE_GET_PLAN, get_plan, SERVICE_GET_PLAN_SCHEMA, SupportsResponse.ONLY),
        (SERVICE_SUGGEST_MEALS, suggest_meals, SERVICE_SUGGEST_SCHEMA, SupportsResponse.ONLY),
        (SERVICE_APPLY_SUGGESTIONS, apply_suggestions, SERVICE_APPLY_SUGGESTIONS_SCHEMA, SupportsResponse.OPTIONAL),
        (SERVICE_CREATE_RECIPE, create_recipe, SERVICE_CREATE_RECIPE_SCHEMA, SupportsResponse.OPTIONAL),
        (SERVICE_DELETE_RECIPE, delete_recipe, SERVICE_DELETE_RECIPE_SCHEMA, SupportsResponse.NONE),
    ):
        hass.services.async_register(
            DOMAIN, name, _service(hass, func), schema=schema, supports_response=response
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
