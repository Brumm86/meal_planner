"""Tests for several dishes per day, the suggestions and the actions (services)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.meal_planner.const import DOMAIN
from custom_components.meal_planner.coordinator import MealPlannerError


def _day(offset: int) -> str:
    return (dt_util.now().date() + timedelta(days=offset)).isoformat()


async def _recipe(c, name: str, *, last_planned: str | None = None, ingredients=None) -> dict:
    result = await c.async_create_recipe_by_names(
        {
            "name": name,
            "base_portions": 4,
            "ingredients": ingredients or [{"name": "Nudeln", "amount": 400, "unit": "g"}],
            "last_planned": last_planned,
        }
    )
    return c.recipes.data[result["id"]]


async def _call(hass, service: str, data: dict, *, response: bool = False):
    return await hass.services.async_call(
        DOMAIN, service, data, blocking=True, return_response=response
    )


# ------------------------------------------------------------ shopping / roll-over


async def test_shopping_sums_all_meals_of_a_day(hass, init_integration) -> None:
    c = init_integration.runtime_data  # household 4
    a = await _recipe(c, "A", ingredients=[{"name": "Nudeln", "amount": 400, "unit": "g"}])
    b = await _recipe(c, "B", ingredients=[{"name": "Nudeln", "amount": 100, "unit": "g"}])
    await c.async_add_meal(_day(1), recipe_id=a["id"])
    await c.async_add_meal(_day(1), recipe_id=b["id"], portions=8)  # x2 -> 200 g

    positions = c.compute_shopping(_day(1), _day(1))["positions"]
    assert [(p["name"], p["amount"], p["unit"]) for p in positions] == [("Nudeln", 600.0, "g")]


async def test_every_meal_of_a_past_day_counts_for_last_planned(hass, init_integration) -> None:
    c = init_integration.runtime_data
    a = await _recipe(c, "A")
    b = await _recipe(c, "B")
    for recipe in (a, b):
        await c.plan.async_create_item(
            {"date": _day(-2), "kind": "recipe", "recipe_id": recipe["id"]}
        )
    await c.async_refresh()
    assert c.recipes.data[a["id"]]["last_planned"] == _day(-2)
    assert c.recipes.data[b["id"]]["last_planned"] == _day(-2)


# ------------------------------------------------------------------ suggestions


async def test_suggestions_fill_free_days_oldest_first(hass, init_integration) -> None:
    c = init_integration.runtime_data  # horizon 10
    old = await _recipe(c, "Alt", last_planned=_day(-30))
    mid = await _recipe(c, "Mittel", last_planned=_day(-5))
    never = await _recipe(c, "Neu")  # never planned comes first
    planned = await _recipe(c, "Geplant")
    await c.async_add_meal(_day(0), recipe_id=planned["id"])

    result = c.suggest_meals()
    # Day 0 is taken; the next free days get the recipes in "longest ago" order,
    # and the recipe already in the window is not proposed.
    assert [(s["date"], s["recipe_id"]) for s in result["suggestions"]] == [
        (_day(1), never["id"]),
        (_day(2), old["id"]),
        (_day(3), mid["id"]),
    ]
    assert result["unfilled"] == [_day(i) for i in range(4, 10)]
    assert c.plan.get_by_date(_day(1)) == []  # nothing was stored


async def test_suggestions_for_single_days_and_exclusions(hass, init_integration) -> None:
    c = init_integration.runtime_data
    a = await _recipe(c, "A", last_planned=_day(-9))
    b = await _recipe(c, "B", last_planned=_day(-8))
    d = await _recipe(c, "D", last_planned=_day(-7))

    one = c.suggest_meals([_day(3)])
    assert one["suggestions"] == [
        {
            "date": _day(3),
            "recipe_id": a["id"],
            "recipe_name": "A",
            "side_ids": [],
            "side_names": [],
        }
    ]
    assert one["unfilled"] == []

    again = c.suggest_meals([_day(3)], exclude_recipe_ids=[a["id"]])
    assert again["suggestions"][0]["recipe_id"] == b["id"]

    none_left = c.suggest_meals([_day(3)], exclude_recipe_ids=[a["id"], b["id"], d["id"]])
    assert none_left == {"suggestions": [], "unfilled": [_day(3)]}

    with pytest.raises(MealPlannerError) as err:
        c.suggest_meals([_day(-1)])
    assert err.value.code == "date_in_past"


async def test_apply_suggestions_adds_to_plan(hass, init_integration) -> None:
    c = init_integration.runtime_data
    a = await _recipe(c, "A")
    b = await _recipe(c, "B")
    await c.async_add_meal(_day(2), recipe_id=a["id"])

    result = await c.async_apply_suggestions(
        [
            {"date": _day(1), "recipe_id": a["id"]},
            {"date": _day(2), "recipe_id": a["id"]},  # already there -> skipped
            {"date": _day(2), "recipe_id": b["id"], "portions": 2},
        ]
    )
    assert result == {"added": 2, "skipped": 1}
    assert [e["recipe_id"] for e in c.data["plan"][_day(2)]] == [a["id"], b["id"]]

    # Validated up front: one bad item changes nothing.
    with pytest.raises(MealPlannerError):
        await c.async_apply_suggestions(
            [{"date": _day(5), "recipe_id": a["id"]}, {"date": _day(6), "recipe_id": "nope"}]
        )
    assert _day(5) not in c.data["plan"]


# --------------------------------------------------------------------- services


async def test_services_plan_cycle(hass, init_integration) -> None:
    c = init_integration.runtime_data
    created = await _call(
        hass,
        "create_recipe",
        {
            "name": "Gulasch",
            "ingredients": [
                {"name": "Zwiebel", "amount": 2, "unit": "Stück"},
                {"name": "zwiebeln", "amount": 1, "unit": "Stück"},  # same product
                {"name": "Paprika", "amount": 1, "unit": "Stück"},
            ],
        },
        response=True,
    )
    assert created["id"] == "gulasch"
    assert created["created_ingredients"] == ["Zwiebel", "Paprika"]
    assert c.recipes.data["gulasch"]["base_portions"] == 4  # household default

    await _call(hass, "add_meal", {"date": _day(1), "recipe": "gulasch"}, response=True)
    await _call(hass, "add_meal", {"date": _day(1), "text": "Nachtisch"})
    plan = await _call(hass, "get_plan", {"start_date": _day(1), "end_date": _day(2)}, response=True)
    assert [m["name"] for m in plan["days"][0]["meals"]] == ["Gulasch", "Nachtisch"]
    assert plan["days"][1]["meals"] == []

    await _call(hass, "remove_meal", {"date": _day(1), "text": "nachtisch"})
    assert [e["kind"] for e in c.data["plan"][_day(1)]] == ["recipe"]
    cleared = await _call(hass, "clear_day", {"date": _day(1)}, response=True)
    assert cleared == {"removed": 1}

    suggestion = await _call(hass, "suggest_meals", {"dates": [_day(3)]}, response=True)
    assert suggestion["suggestions"][0]["recipe_name"] == "Gulasch"
    applied = await _call(
        hass, "apply_suggestions", {"suggestions": [{"date": _day(3), "recipe": "Gulasch"}]}, response=True
    )
    assert applied == {"added": 1, "skipped": 0}

    with pytest.raises(ServiceValidationError):  # still planned
        await _call(hass, "delete_recipe", {"recipe": "Gulasch"})
    await _call(hass, "clear_day", {"date": _day(3)})
    await _call(hass, "delete_recipe", {"recipe": "Gulasch"})
    assert c.recipes.data == {}


async def test_service_errors(hass, init_integration) -> None:
    with pytest.raises(ServiceValidationError):
        await _call(hass, "add_meal", {"date": _day(1), "recipe": "gibt-es-nicht"})
    with pytest.raises(ServiceValidationError):
        await _call(hass, "add_meal", {"date": _day(-1), "text": "gestern"})
    with pytest.raises(Exception):  # recipe and text together are rejected by the schema
        await _call(hass, "add_meal", {"date": _day(1), "recipe": "x", "text": "y"})
