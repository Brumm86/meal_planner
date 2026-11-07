"""Tests for precooked dishes: they add nothing to the shopping list."""

from __future__ import annotations

from datetime import timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.meal_planner.const import DOMAIN
from custom_components.meal_planner.coordinator import MealPlannerError


def _day(offset: int) -> str:
    return (dt_util.now().date() + timedelta(days=offset)).isoformat()


async def _recipe(c, name: str, ingredient: str, *, roles=None, side_ids=None) -> dict:
    data = {
        "name": name,
        "base_portions": 4,
        "ingredients": [{"name": ingredient, "amount": 500, "unit": "g"}],
    }
    if roles is not None:
        data["roles"] = roles
    if side_ids is not None:
        data["side_ids"] = side_ids
    result = await c.async_create_recipe_by_names(data)
    return c.recipes.data[result["id"]]


def _names(c) -> list[str]:
    return [p["name"] for p in c.compute_shopping(None, None)["positions"]]


async def test_precooked_dish_and_its_sides_add_nothing(hass, init_integration) -> None:
    c = init_integration.runtime_data
    reis = await _recipe(c, "Reis", "Reis", roles=["side"])
    gulasch = await _recipe(c, "Gulasch", "Rindfleisch", side_ids=[reis["id"]])
    suppe = await _recipe(c, "Suppe", "Kartoffeln")

    await c.async_add_meal(_day(1), recipe_id=gulasch["id"], side_ids=[reis["id"]], precooked=True)
    await c.async_add_meal(_day(1), recipe_id=suppe["id"])
    assert _names(c) == ["Kartoffeln"]  # only the dish that is not precooked

    view = c.get_plan(_day(1), _day(1))[0]["meals"]
    assert {m["name"]: m["precooked"] for m in view} == {"Gulasch": True, "Suppe": False}
    assert c.build_snapshot()["plan"][_day(1)][0]["precooked"] is True


async def test_toggle_precooked_on_existing_entry(hass, init_integration) -> None:
    c = init_integration.runtime_data
    gulasch = await _recipe(c, "Gulasch", "Rindfleisch")
    entry = await c.async_add_meal(_day(2), recipe_id=gulasch["id"])
    assert _names(c) == ["Rindfleisch"]

    await c.async_set_precooked(entry["id"], True)
    assert _names(c) == []
    await c.async_set_precooked(entry["id"], False)
    assert _names(c) == ["Rindfleisch"]


async def test_only_recipes_and_future_days(hass, init_integration) -> None:
    c = init_integration.runtime_data
    gulasch = await _recipe(c, "Gulasch", "Rindfleisch")
    text = await c.async_add_meal(_day(1), text="Restaurant")
    with pytest.raises(MealPlannerError) as err:
        await c.async_set_precooked(text["id"], True)
    assert err.value.code == "not_a_recipe"
    with pytest.raises(MealPlannerError):
        await c.async_add_meal(_day(1), text="Reste", precooked=True)

    past = await c.plan.async_create_item(
        {"date": _day(-1), "kind": "recipe", "recipe_id": gulasch["id"]}
    )
    with pytest.raises(MealPlannerError) as err:
        await c.async_set_precooked(past["id"], True)
    assert err.value.code == "date_in_past"
    with pytest.raises(MealPlannerError):
        await c.async_set_precooked("gibt-es-nicht", True)


async def test_entries_stored_before_the_flag_count_as_not_precooked(hass, init_integration) -> None:
    c = init_integration.runtime_data
    gulasch = await _recipe(c, "Gulasch", "Rindfleisch")
    entry = await c.async_add_meal(_day(1), recipe_id=gulasch["id"])
    c.plan.data[entry["id"]].pop("precooked")  # as stored by versions before 0.7.0
    assert c.build_snapshot()["plan"][_day(1)][0]["precooked"] is False
    assert _names(c) == ["Rindfleisch"]


async def test_apply_suggestions_keeps_the_flag(hass, init_integration) -> None:
    c = init_integration.runtime_data
    gulasch = await _recipe(c, "Gulasch", "Rindfleisch")
    suppe = await _recipe(c, "Suppe", "Kartoffeln")
    result = await c.async_apply_suggestions(
        [
            {"date": _day(1), "recipe_id": gulasch["id"], "precooked": True},
            {"date": _day(2), "recipe_id": suppe["id"]},
        ]
    )
    assert result == {"added": 2, "skipped": 0}
    assert _names(c) == ["Kartoffeln"]


async def test_changing_to_precooked_after_transfer_is_reported_as_surplus(hass, init_integration) -> None:
    c = init_integration.runtime_data
    gulasch = await _recipe(c, "Gulasch", "Rindfleisch")
    entry = await c.async_add_meal(_day(1), recipe_id=gulasch["id"])
    await c.transfers.async_create_item(
        {
            "date": _day(1),
            "lines": [{"ingredient_id": "rindfleisch", "dimension": "mass", "amount": 1000.0}],
        }
    )
    assert _names(c) == []
    await c.async_set_precooked(entry["id"], True)
    result = c.compute_shopping(None, None)
    assert result["positions"] == []
    assert [s["name"] for s in result["surplus"]] == ["Rindfleisch"]  # stays on Bring, just flagged


async def test_services(hass, init_integration) -> None:
    c = init_integration.runtime_data
    await _recipe(c, "Gulasch", "Rindfleisch")
    await hass.services.async_call(
        DOMAIN, "add_meal", {"date": _day(1), "recipe": "Gulasch", "precooked": True}, blocking=True
    )
    assert _names(c) == []
    await hass.services.async_call(
        DOMAIN, "set_precooked", {"date": _day(1), "recipe": "Gulasch", "precooked": False}, blocking=True
    )
    assert _names(c) == ["Rindfleisch"]
    await hass.services.async_call(
        DOMAIN, "set_precooked", {"date": _day(1), "recipe": "Gulasch"}, blocking=True
    )
    assert _names(c) == []
    await hass.services.async_call(
        DOMAIN,
        "apply_suggestions",
        {"suggestions": [{"date": _day(3), "recipe": "Gulasch", "precooked": True}]},
        blocking=True,
    )
    assert c.build_snapshot()["plan"][_day(3)][0]["precooked"] is True


async def test_websocket(hass, hass_ws_client, init_integration) -> None:
    c = init_integration.runtime_data
    gulasch = await _recipe(c, "Gulasch", "Rindfleisch")
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "meal_planner/plan/add", "date": _day(1), "recipe_id": gulasch["id"], "precooked": True}
    )
    msg = await client.receive_json()
    assert msg["success"], msg
    entry_id = msg["result"]["id"]
    assert _names(c) == []

    await client.send_json_auto_id(
        {"type": "meal_planner/plan/precooked", "entry_id": entry_id, "precooked": False}
    )
    assert (await client.receive_json())["success"]
    assert _names(c) == ["Rindfleisch"]
