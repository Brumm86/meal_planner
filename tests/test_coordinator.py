"""Tests for the recipe / ingredient / plan logic in the coordinator."""

from __future__ import annotations

from datetime import timedelta

import pytest
import voluptuous as vol

from homeassistant.util import dt as dt_util

from custom_components.meal_planner.coordinator import MealPlannerError


def _day(offset: int) -> str:
    return (dt_util.now().date() + timedelta(days=offset)).isoformat()


async def _recipe(coordinator, name="Spaghetti", **extra):
    data = {
        "name": name,
        "base_portions": 2,
        "ingredients": [
            {"name": "Spaghetti", "amount": 500, "unit": "g"},
            {"name": "Zwiebel", "amount": 2, "unit": "Stück"},
            {"name": "Salz", "amount": None, "unit": None},
        ],
        **extra,
    }
    return await coordinator.async_save_recipe(None, data)


async def test_save_recipe_creates_catalog_ingredients(hass, init_integration) -> None:
    coordinator = init_integration.runtime_data
    recipe = await _recipe(coordinator)

    names = {i["name"] for i in coordinator.data["ingredients"]}
    assert names == {"Spaghetti", "Zwiebel", "Salz"}
    lines = recipe["ingredients"]
    assert [ln["unit"] for ln in lines] == ["g", "Stück", None]
    assert lines[2]["amount"] is None  # "nach Bedarf"
    assert coordinator.ingredients.find_by_name("zwiebel")["default_unit"] == "Stück"
    assert recipe["last_planned"] is None


async def test_ingredient_names_are_matched_case_insensitively(hass, init_integration) -> None:
    coordinator = init_integration.runtime_data
    await _recipe(coordinator)
    await coordinator.async_save_recipe(
        None,
        {
            "name": "Zwiebelsuppe",
            "base_portions": 4,
            "ingredients": [{"name": "  zwiebel ", "amount": 1, "unit": "kg"}],
        },
    )
    assert len(coordinator.ingredients.data) == 3  # no second "Zwiebel"


async def test_amount_and_unit_must_go_together(hass, init_integration) -> None:
    coordinator = init_integration.runtime_data
    with pytest.raises(vol.Invalid):
        await coordinator.async_save_recipe(
            None,
            {"name": "X", "base_portions": 2, "ingredients": [{"name": "Mehl", "amount": 200}]},
        )
    with pytest.raises(vol.Invalid):
        await coordinator.async_save_recipe(
            None,
            {"name": "X", "base_portions": 2, "ingredients": [{"name": "Mehl", "amount": 1, "unit": "Eimer"}]},
        )


async def test_update_recipe_replaces_lines_and_dedupes_tags(hass, init_integration) -> None:
    coordinator = init_integration.runtime_data
    recipe = await _recipe(coordinator, tags=["Pasta", "pasta", "schnell"])
    assert recipe["tags"] == ["Pasta", "schnell"]

    updated = await coordinator.async_save_recipe(
        recipe["id"],
        {"ingredients": [{"name": "Spaghetti", "amount": 1, "unit": "kg"}], "duration_min": 20},
    )
    assert len(updated["ingredients"]) == 1
    assert updated["duration_min"] == 20
    assert updated["name"] == "Spaghetti"  # untouched fields survive


async def test_plan_set_replace_and_clear(hass, init_integration) -> None:
    coordinator = init_integration.runtime_data
    recipe = await _recipe(coordinator)
    day = _day(1)

    await coordinator.async_set_plan(day, recipe_id=recipe["id"], portions=6)
    entry = coordinator.data["plan"][day]
    assert entry["kind"] == "recipe" and entry["portions"] == 6

    await coordinator.async_set_plan(day, text="Reste")
    entry = coordinator.data["plan"][day]
    assert entry["kind"] == "text" and entry["text"] == "Reste"
    assert entry["recipe_id"] is None and entry["portions"] is None
    assert len(coordinator.data["plan"]) == 1

    await coordinator.async_clear_plan(day)
    assert day not in coordinator.data["plan"]
    await coordinator.async_clear_plan(day)  # idempotent


async def test_plan_rejects_bad_input(hass, init_integration) -> None:
    coordinator = init_integration.runtime_data
    recipe = await _recipe(coordinator)
    with pytest.raises(MealPlannerError) as err:
        await coordinator.async_set_plan(_day(-1), text="gestern")
    assert err.value.code == "date_in_past"
    with pytest.raises(MealPlannerError) as err:
        await coordinator.async_set_plan(_day(1), recipe_id="gibt-es-nicht")
    assert err.value.code == "not_found"
    with pytest.raises(vol.Invalid):
        await coordinator.async_set_plan(_day(1))
    with pytest.raises(vol.Invalid):
        await coordinator.async_set_plan(_day(1), recipe_id=recipe["id"], text="beides")
    with pytest.raises(vol.Invalid):
        await coordinator.async_set_plan("morgen", text="x")


async def test_delete_recipe_blocked_while_planned(hass, init_integration) -> None:
    coordinator = init_integration.runtime_data
    recipe = await _recipe(coordinator)
    await coordinator.async_set_plan(_day(2), recipe_id=recipe["id"])

    with pytest.raises(MealPlannerError) as err:
        await coordinator.async_delete_recipe(recipe["id"])
    assert err.value.code == "recipe_in_plan"

    await coordinator.async_clear_plan(_day(2))
    await coordinator.async_delete_recipe(recipe["id"])
    assert coordinator.data["recipes"] == []


async def test_delete_ingredient_blocked_while_used(hass, init_integration) -> None:
    coordinator = init_integration.runtime_data
    recipe = await _recipe(coordinator)
    onion = coordinator.ingredients.find_by_name("Zwiebel")

    with pytest.raises(MealPlannerError) as err:
        await coordinator.async_delete_ingredient(onion["id"])
    assert err.value.code == "ingredient_in_use"

    await coordinator.async_delete_recipe(recipe["id"])
    await coordinator.async_delete_ingredient(onion["id"])
    assert coordinator.ingredients.find_by_name("Zwiebel") is None


async def test_rename_ingredient_must_stay_unique(hass, init_integration) -> None:
    coordinator = init_integration.runtime_data
    await _recipe(coordinator)
    onion = coordinator.ingredients.find_by_name("Zwiebel")
    with pytest.raises(vol.Invalid):
        await coordinator.async_save_ingredient(onion["id"], {"name": "salz"})
    renamed = await coordinator.async_save_ingredient(onion["id"], {"name": "Gemüsezwiebel"})
    assert renamed["name"] == "Gemüsezwiebel"


async def test_past_days_bump_last_planned_once(hass, init_integration) -> None:
    """A passed plan day sets last_planned; a manual correction is not overwritten."""
    coordinator = init_integration.runtime_data
    recipe = await _recipe(coordinator)
    past = _day(-3)
    await coordinator.plan.async_create_item(
        {"date": past, "kind": "recipe", "recipe_id": recipe["id"]}
    )

    await coordinator.async_refresh()
    assert coordinator.recipes.data[recipe["id"]]["last_planned"] == past

    # The user corrects the date by hand ("we ordered pizza instead").
    await coordinator.async_save_recipe(recipe["id"], {"last_planned": None})
    await coordinator.async_refresh()
    assert coordinator.recipes.data[recipe["id"]]["last_planned"] is None


async def test_old_entries_are_pruned_and_deleted_recipes_become_text(hass, init_integration) -> None:
    coordinator = init_integration.runtime_data
    recipe = await _recipe(coordinator, name="Linsensuppe")
    await coordinator.plan.async_create_item(
        {"date": _day(-100), "kind": "text", "text": "uralt"}
    )
    await coordinator.plan.async_create_item(
        {"date": _day(-2), "kind": "recipe", "recipe_id": recipe["id"]}
    )
    await coordinator.async_refresh()
    assert _day(-100) not in coordinator.data["plan"]

    await coordinator.async_delete_recipe(recipe["id"])  # only a past use -> allowed
    entry = coordinator.data["plan"][_day(-2)]
    assert entry["kind"] == "text" and entry["text"] == "Linsensuppe"
