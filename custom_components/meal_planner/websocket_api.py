"""Websocket API used by the Lovelace card.

Everything is namespaced ``meal_planner/...``. The card subscribes once
(``meal_planner/subscribe``) and receives a full snapshot on every change; all
mutating commands just return success and let the subscription deliver the new
state. Commands are registered once, globally, and look up the coordinator of
the (single) config entry at call time.

No admin requirement on purpose: planning lunch is a household activity, any
logged-in Home Assistant user may edit recipes and the plan.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, WS_PREFIX
from .coordinator import MealPlannerCoordinator, MealPlannerError

_REGISTERED = f"{DOMAIN}_ws_registered"


def _coordinator(hass: HomeAssistant) -> MealPlannerCoordinator | None:
    for entry in hass.config_entries.async_entries(DOMAIN):
        coordinator = getattr(entry, "runtime_data", None)
        if isinstance(coordinator, MealPlannerCoordinator):
            return coordinator
    return None


def _handler(func):
    """Resolve the coordinator and translate domain/validation errors."""

    @websocket_api.async_response
    async def wrapper(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
        coordinator = _coordinator(hass)
        if coordinator is None:
            connection.send_error(msg["id"], "not_loaded", "Meal Planner ist nicht geladen")
            return
        try:
            result = await func(coordinator, msg)
        except MealPlannerError as err:
            connection.send_error(msg["id"], err.code, err.message)
        except vol.Invalid as err:
            connection.send_error(msg["id"], "invalid_format", str(err))
        else:
            connection.send_result(msg["id"], result)

    return wrapper


@websocket_api.websocket_command({vol.Required("type"): f"{WS_PREFIX}subscribe"})
@websocket_api.async_response
async def ws_subscribe(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Send the current snapshot now and again after every change."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Meal Planner ist nicht geladen")
        return

    @callback
    def _push() -> None:
        connection.send_message(
            websocket_api.event_message(msg["id"], coordinator.build_snapshot())
        )

    connection.subscriptions[msg["id"]] = coordinator.async_add_listener(_push)
    connection.send_result(msg["id"])
    _push()


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{WS_PREFIX}recipe/save",
        vol.Optional("recipe_id"): str,  # absent -> create ("id" is the ws message id)
        vol.Required("recipe"): dict,
    }
)
@_handler
async def ws_recipe_save(coordinator: MealPlannerCoordinator, msg: dict[str, Any]) -> dict:
    item = await coordinator.async_save_recipe(msg.get("recipe_id"), msg["recipe"])
    return {"id": item["id"]}


@websocket_api.websocket_command(
    {vol.Required("type"): f"{WS_PREFIX}recipe/delete", vol.Required("recipe_id"): str}
)
@_handler
async def ws_recipe_delete(coordinator: MealPlannerCoordinator, msg: dict[str, Any]) -> None:
    await coordinator.async_delete_recipe(msg["recipe_id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{WS_PREFIX}ingredient/save",
        vol.Optional("ingredient_id"): str,
        vol.Required("ingredient"): dict,
    }
)
@_handler
async def ws_ingredient_save(coordinator: MealPlannerCoordinator, msg: dict[str, Any]) -> dict:
    item = await coordinator.async_save_ingredient(msg.get("ingredient_id"), msg["ingredient"])
    return {"id": item["id"]}


@websocket_api.websocket_command(
    {vol.Required("type"): f"{WS_PREFIX}ingredient/delete", vol.Required("ingredient_id"): str}
)
@_handler
async def ws_ingredient_delete(coordinator: MealPlannerCoordinator, msg: dict[str, Any]) -> None:
    await coordinator.async_delete_ingredient(msg["ingredient_id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{WS_PREFIX}plan/set",
        vol.Required("date"): str,
        vol.Optional("recipe_id"): str,
        vol.Optional("text"): str,
        vol.Optional("portions"): vol.Any(None, vol.All(int, vol.Range(min=1, max=50))),
    }
)
@_handler
async def ws_plan_set(coordinator: MealPlannerCoordinator, msg: dict[str, Any]) -> None:
    text = (msg.get("text") or "").strip() or None
    await coordinator.async_set_plan(
        msg["date"],
        recipe_id=msg.get("recipe_id"),
        text=text,
        portions=msg.get("portions"),
    )


@websocket_api.websocket_command(
    {vol.Required("type"): f"{WS_PREFIX}plan/clear", vol.Required("date"): str}
)
@_handler
async def ws_plan_clear(coordinator: MealPlannerCoordinator, msg: dict[str, Any]) -> None:
    await coordinator.async_clear_plan(msg["date"])


@callback
def async_setup_websocket_api(hass: HomeAssistant) -> None:
    """Register the commands once per Home Assistant run."""
    if hass.data.get(_REGISTERED):
        return
    hass.data[_REGISTERED] = True
    for command in (
        ws_subscribe,
        ws_recipe_save,
        ws_recipe_delete,
        ws_ingredient_save,
        ws_ingredient_delete,
        ws_plan_set,
        ws_plan_clear,
    ):
        websocket_api.async_register_command(hass, command)
