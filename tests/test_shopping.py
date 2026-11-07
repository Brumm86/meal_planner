"""Tests for the shopping preview and the hand-over to Bring."""

from __future__ import annotations

from datetime import timedelta

import pytest

from homeassistant.components.todo import TodoListEntityFeature
from homeassistant.core import ServiceCall
from homeassistant.util import dt as dt_util

from custom_components.meal_planner import shopping
from custom_components.meal_planner.const import CONF_BRING_ENTITY, DOMAIN
from custom_components.meal_planner.coordinator import MealPlannerError

BRING = "todo.bring_einkauf"


def _day(offset: int) -> str:
    return (dt_util.now().date() + timedelta(days=offset)).isoformat()


async def _plan(coordinator, day: str, **kwargs) -> None:
    """Replace whatever is planned on ``day`` by one dish."""
    await coordinator.async_clear_plan(day)
    await coordinator.async_add_meal(day, **kwargs)


async def _ing(coordinator, name: str, unit: str | None = None) -> dict:
    existing = coordinator.ingredients.find_by_name(name)
    if existing is not None:
        return existing
    return await coordinator.async_save_ingredient(None, {"name": name, "default_unit": unit})


async def _recipe(coordinator, name: str, lines: list[tuple[str, float | None, str | None]], portions=2):
    payload = []
    for ing_name, amount, unit in lines:
        ing = await _ing(coordinator, ing_name, unit)
        payload.append({"ingredient_id": ing["id"], "amount": amount, "unit": unit})
    return await coordinator.async_save_recipe(
        None, {"name": name, "base_portions": portions, "ingredients": payload}
    )


@pytest.fixture
def bring(hass):
    """A fake Bring list: records add_item calls, serves get_items."""
    state = {"open": [], "added": []}
    hass.states.async_set(BRING, "0", {"supported_features": int(TodoListEntityFeature.CREATE_TODO_ITEM | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM)})

    async def get_items(call: ServiceCall):
        return {BRING: {"items": [{"summary": s, "status": "needs_action"} for s in state["open"]]}}

    async def add_item(call: ServiceCall):
        state["added"].append(dict(call.data))

    from homeassistant.core import SupportsResponse

    hass.services.async_register("todo", "get_items", get_items, supports_response=SupportsResponse.ONLY)
    hass.services.async_register("todo", "add_item", add_item)
    return state


@pytest.fixture
async def with_bring(hass, init_integration, bring):
    hass.config_entries.async_update_entry(
        init_integration, options={**init_integration.options, CONF_BRING_ENTITY: BRING}
    )
    await hass.async_block_till_done()
    return init_integration.runtime_data


# ------------------------------------------------------------------ pure math


def test_units_are_summed_within_a_dimension() -> None:
    assert shopping.to_base(1.5, "kg") == ("mass", 1500.0)
    assert shopping.from_base("mass", 1500.0) == ("kg", 1.5)
    assert shopping.from_base("mass", 400.0) == ("g", 400.0)
    assert shopping.from_base("volume", 2500.0) == ("l", 2.5)
    assert shopping.from_base("piece", 3.0) == ("Stück", 3.0)
    assert shopping.to_base(None, None) == ("", None)


def test_diff_only_adds_the_missing_part() -> None:
    key = ("mehl", "mass")
    assert shopping.diff({key: 500.0}, {key: 200.0}) == ({key: 300.0}, {})
    assert shopping.diff({key: 100.0}, {key: 200.0}) == ({}, {key: 100.0})
    assert shopping.diff({key: 200.0}, {key: 200.0}) == ({}, {})
    salt = ("salz", "")
    assert shopping.diff({salt: None}, {}) == ({salt: None}, {})
    assert shopping.diff({}, {salt: None}) == ({}, {salt: None})
    assert shopping.diff({salt: None}, {salt: None}) == ({}, {})


def test_raised_never_lowers_the_record() -> None:
    key = ("mehl", "mass")
    assert shopping.raised({key: 100.0}, {key: 200.0}) == {key: 200.0}
    assert shopping.raised({key: 300.0}, {key: 200.0}) == {key: 300.0}


def test_format_quantity() -> None:
    assert shopping.format_quantity(400.0, "g") == "400 g"
    assert shopping.format_quantity(1.5, "kg") == "1,5 kg"
    assert shopping.format_quantity(None, None) == ""


# -------------------------------------------------------------------- preview


async def test_preview_scales_sums_and_converts(hass, init_integration) -> None:
    c = init_integration.runtime_data  # household 4
    pasta = await _recipe(c, "Pasta", [("Nudeln", 500, "g"), ("Zwiebel", 2, "Stück"), ("Salz", None, None)])
    stew = await _recipe(c, "Eintopf", [("Nudeln", 1, "kg"), ("Zwiebel", 100, "g")], portions=4)
    await _plan(c, _day(0), recipe_id=pasta["id"])  # 4 portions -> x2
    await _plan(c, _day(1), recipe_id=stew["id"])  # 4 portions -> x1
    await _plan(c, _day(2), recipe_id=pasta["id"], portions=1)  # x0.5
    await _plan(c, _day(3), text="Reste")

    result = c.compute_shopping(None, None)
    by = {(p["name"], p["unit"]): p["amount"] for p in result["positions"]}
    assert by[("Nudeln", "kg")] == 2.25  # 1000 + 1000 + 250 g
    assert by[("Zwiebel", "Stück")] == 5.0  # 4 + 1
    assert by[("Zwiebel", "g")] == 100.0  # other dimension stays separate
    assert by[("Salz", None)] is None  # nach Bedarf, once
    assert [p["name"] for p in result["positions"]] == ["Nudeln", "Salz", "Zwiebel", "Zwiebel"]
    assert result["surplus"] == []


async def test_range_is_validated(hass, init_integration) -> None:
    c = init_integration.runtime_data
    with pytest.raises(MealPlannerError) as err:
        c.compute_shopping(_day(5), _day(2))
    assert err.value.code == "invalid_range"
    with pytest.raises(MealPlannerError) as err:
        c.compute_shopping(_day(0), _day(60))
    assert err.value.code == "range_too_long"
    # A start in the past is clamped to today.
    assert c.compute_shopping(_day(-5), _day(1))["start"] == _day(0)


async def test_preview_flags_what_bring_has_open(hass, with_bring, bring) -> None:
    c = with_bring
    await _recipe(c, "Pasta", [("Zwiebel", 2, "Stück"), ("Nudeln", 500, "g")])
    await _plan(c, _day(1), recipe_id="pasta")
    bring["open"] = ["Zwiebeln", "Milch"]  # plural form still matches

    preview = await c.async_shopping_preview(None, None)
    flags = {p["name"]: p["on_list"] for p in preview["positions"]}
    assert flags == {"Nudeln": False, "Zwiebel": True}
    assert preview["bring"] == {"configured": True, "error": None}


async def test_preview_without_bring_list(hass, init_integration) -> None:
    c = init_integration.runtime_data
    preview = await c.async_shopping_preview(None, None)
    assert preview["bring"] == {"configured": False, "error": None}
    with pytest.raises(MealPlannerError) as err:
        await c.async_transfer_to_bring(None, None)
    assert err.value.code == "bring_not_configured"


# ------------------------------------------------------------------- transfer


async def test_transfer_adds_items_with_quantity_and_remembers_days(hass, with_bring, bring) -> None:
    c = with_bring
    await _recipe(c, "Pasta", [("Nudeln", 500, "g"), ("Salz", None, None)])
    await _plan(c, _day(1), recipe_id="pasta")  # 4 portions -> 1000 g
    items = [
        {"ingredient_id": "nudeln", "amount": 1.0, "unit": "kg"},
        {"ingredient_id": "salz", "amount": None, "unit": None},
    ]
    result = await c.async_transfer_to_bring(None, None, items=items, extras=["Backpapier", "  "])
    assert result == {"added": 3}
    assert bring["added"] == [
        {"entity_id": BRING, "item": "Nudeln", "description": "1 kg"},
        {"entity_id": BRING, "item": "Salz"},
        {"entity_id": BRING, "item": "Backpapier"},
    ]
    # Everything is transferred now: nothing left to buy.
    assert c.compute_shopping(None, None)["positions"] == []


async def test_later_changes_only_add_the_difference(hass, with_bring, bring) -> None:
    c = with_bring
    await _recipe(c, "Pasta", [("Nudeln", 500, "g")])
    await _plan(c, _day(1), recipe_id="pasta", portions=2)  # 500 g
    await c.async_transfer_to_bring(None, None, items=[{"ingredient_id": "nudeln", "amount": 500, "unit": "g"}])

    await _plan(c, _day(1), recipe_id="pasta", portions=6)  # now 1500 g
    result = c.compute_shopping(None, None)
    assert [(p["name"], p["amount"], p["unit"]) for p in result["positions"]] == [("Nudeln", 1.0, "kg")]

    await _plan(c, _day(1), recipe_id="pasta", portions=1)  # only 250 g needed
    result = c.compute_shopping(None, None)
    assert result["positions"] == []
    assert [(s["date"], s["name"], s["amount"], s["unit"]) for s in result["surplus"]] == [
        (_day(1), "Nudeln", 250.0, "g")
    ]

    # Clearing the day and planning again does not forget what Bring got.
    await c.async_clear_plan(_day(1))
    await _plan(c, _day(1), recipe_id="pasta", portions=2)
    assert c.compute_shopping(None, None)["positions"] == []


async def test_service_transfers_unedited_list_and_skips_open_items(hass, with_bring, bring) -> None:
    c = with_bring
    await _recipe(c, "Pasta", [("Nudeln", 500, "g"), ("Zwiebel", 2, "Stück")])
    await _plan(c, _day(1), recipe_id="pasta", portions=2)
    bring["open"] = ["Zwiebel"]

    response = await hass.services.async_call(DOMAIN, "transfer_to_bring", {}, blocking=True, return_response=True)
    assert response == {"added": 1}
    assert bring["added"] == [{"entity_id": BRING, "item": "Nudeln", "description": "500 g"}]


async def test_item_without_description_support_folds_quantity_into_name(hass, with_bring, bring) -> None:
    c = with_bring
    hass.states.async_set(BRING, "0", {"supported_features": int(TodoListEntityFeature.CREATE_TODO_ITEM)})
    await _recipe(c, "Pasta", [("Nudeln", 500, "g")])
    await _plan(c, _day(1), recipe_id="pasta", portions=2)
    await c.async_transfer_to_bring(None, None, items=[{"ingredient_id": "nudeln", "amount": 500, "unit": "g"}])
    assert bring["added"] == [{"entity_id": BRING, "item": "Nudeln (500 g)"}]


async def test_failed_transfer_remembers_nothing(hass, with_bring, bring) -> None:
    c = with_bring
    await _recipe(c, "Pasta", [("Nudeln", 500, "g")])
    await _plan(c, _day(1), recipe_id="pasta", portions=2)

    from homeassistant.exceptions import HomeAssistantError

    async def boom(call):
        raise HomeAssistantError("offline")

    hass.services.async_register("todo", "add_item", boom)
    with pytest.raises(MealPlannerError) as err:
        await c.async_transfer_to_bring(None, None, items=[{"ingredient_id": "nudeln", "amount": 500, "unit": "g"}])
    assert err.value.code == "bring_unavailable"
    assert len(c.compute_shopping(None, None)["positions"]) == 1  # still to buy


async def test_merge_keeps_transfer_record(hass, with_bring, bring) -> None:
    c = with_bring
    await _recipe(c, "Pasta", [("Zwiebel", 2, "Stück")])
    await _ing(c, "Zwiebeln", "Stück")
    await _plan(c, _day(1), recipe_id="pasta", portions=2)
    await c.async_transfer_to_bring(None, None, items=[{"ingredient_id": "zwiebel", "amount": 2, "unit": "Stück"}])

    await c.async_merge_ingredients("zwiebel", "zwiebeln")
    assert c.compute_shopping(None, None)["positions"] == []  # nothing is bought twice


async def test_websocket_preview_and_transfer(hass, hass_ws_client, with_bring, bring) -> None:
    c = with_bring
    await _recipe(c, "Pasta", [("Nudeln", 500, "g")])
    await _plan(c, _day(1), recipe_id="pasta", portions=2)
    client = await hass_ws_client(hass)

    await client.send_json({"id": 1, "type": "meal_planner/shopping/preview"})
    msg = await client.receive_json()
    assert msg["success"]
    assert [(p["name"], p["amount"], p["unit"], p["on_list"]) for p in msg["result"]["positions"]] == [
        ("Nudeln", 500.0, "g", False)
    ]

    await client.send_json(
        {
            "id": 2,
            "type": "meal_planner/shopping/transfer",
            "start": msg["result"]["start"],
            "end": msg["result"]["end"],
            "items": [{"ingredient_id": "nudeln", "amount": 450, "unit": "g"}],
            "extras": ["Backpapier"],
        }
    )
    msg = await client.receive_json()
    assert msg["success"] and msg["result"] == {"added": 2}
    assert bring["added"][0]["description"] == "450 g"

    await client.send_json({"id": 3, "type": "meal_planner/shopping/preview", "start": _day(5), "end": _day(1)})
    msg = await client.receive_json()
    assert not msg["success"] and msg["error"]["code"] == "invalid_range"
