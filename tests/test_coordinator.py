"""Tests for the recipe / ingredient / plan logic in the coordinator."""

from __future__ import annotations

from datetime import timedelta

import pytest
import voluptuous as vol

from homeassistant.util import dt as dt_util

from custom_components.meal_planner.coordinator import MealPlannerError
from custom_components.meal_planner.storage import similarity_key


def _day(offset: int) -> str:
    return (dt_util.now().date() + timedelta(days=offset)).isoformat()


async def _ing(coordinator, name: str, unit: str | None = None) -> dict:
    """Return the catalog entry called ``name``, creating it if necessary."""
    existing = coordinator.ingredients.find_by_name(name)
    if existing is not None:
        return existing
    return await coordinator.async_save_ingredient(None, {"name": name, "default_unit": unit})


async def _recipe(coordinator, name="Spaghetti", **extra):
    spaghetti = await _ing(coordinator, "Spaghetti", "g")
    onion = await _ing(coordinator, "Zwiebel", "Stück")
    salt = await _ing(coordinator, "Salz")
    data = {
        "name": name,
        "base_portions": 2,
        "ingredients": [
            {"ingredient_id": spaghetti["id"], "amount": 500, "unit": "g"},
            {"ingredient_id": onion["id"], "amount": 2, "unit": "Stück"},
            {"ingredient_id": salt["id"], "amount": None, "unit": None},
        ],
        **extra,
    }
    return await coordinator.async_save_recipe(None, data)


async def test_save_recipe_references_catalog_ingredients(hass, init_integration) -> None:
    coordinator = init_integration.runtime_data
    recipe = await _recipe(coordinator)

    names = {i["name"] for i in coordinator.data["ingredients"]}
    assert names == {"Spaghetti", "Zwiebel", "Salz"}
    lines = recipe["ingredients"]
    assert [ln["unit"] for ln in lines] == ["g", "Stück", None]
    assert lines[2]["amount"] is None  # "nach Bedarf"
    assert recipe["last_planned"] is None
    counts = {i["name"]: i["recipe_count"] for i in coordinator.data["ingredients"]}
    assert counts == {"Spaghetti": 1, "Zwiebel": 1, "Salz": 1}


async def test_recipe_rejects_unknown_or_free_text_ingredient(hass, init_integration) -> None:
    coordinator = init_integration.runtime_data
    with pytest.raises(MealPlannerError) as err:
        await coordinator.async_save_recipe(
            None,
            {
                "name": "X",
                "base_portions": 2,
                "ingredients": [{"ingredient_id": "gibt-es-nicht", "amount": 1, "unit": "kg"}],
            },
        )
    assert err.value.code == "not_found"
    # Names are no longer accepted - that is what keeps Zwiebel/Zwiebeln apart.
    with pytest.raises(vol.Invalid):
        await coordinator.async_save_recipe(
            None,
            {
                "name": "X",
                "base_portions": 2,
                "ingredients": [{"name": "Zwiebeln", "amount": 1, "unit": "kg"}],
            },
        )
    assert coordinator.data["recipes"] == []
    assert coordinator.data["ingredients"] == []  # nothing was created on the side


async def test_first_used_unit_becomes_default_unit(hass, init_integration) -> None:
    coordinator = init_integration.runtime_data
    flour = await _ing(coordinator, "Mehl")  # no default unit yet
    assert flour["default_unit"] is None
    await coordinator.async_save_recipe(
        None,
        {
            "name": "Pfannkuchen",
            "base_portions": 4,
            "ingredients": [{"ingredient_id": flour["id"], "amount": 250, "unit": "g"}],
        },
    )
    assert coordinator.ingredients.find_by_name("Mehl")["default_unit"] == "g"


async def test_ingredient_names_are_unique_case_insensitively(hass, init_integration) -> None:
    coordinator = init_integration.runtime_data
    await _ing(coordinator, "Zwiebel")
    with pytest.raises(MealPlannerError) as err:
        await coordinator.async_save_ingredient(None, {"name": "  zwiebel "})
    assert err.value.code == "ingredient_exists"
    assert len(coordinator.ingredients.data) == 1


async def test_amount_and_unit_must_go_together(hass, init_integration) -> None:
    coordinator = init_integration.runtime_data
    flour = await _ing(coordinator, "Mehl")
    with pytest.raises(vol.Invalid):
        await coordinator.async_save_recipe(
            None,
            {"name": "X", "base_portions": 2, "ingredients": [{"ingredient_id": flour["id"], "amount": 200}]},
        )
    with pytest.raises(vol.Invalid):
        await coordinator.async_save_recipe(
            None,
            {
                "name": "X",
                "base_portions": 2,
                "ingredients": [{"ingredient_id": flour["id"], "amount": 1, "unit": "Eimer"}],
            },
        )


async def test_update_recipe_replaces_lines_and_dedupes_tags(hass, init_integration) -> None:
    coordinator = init_integration.runtime_data
    recipe = await _recipe(coordinator, tags=["Pasta", "pasta", "schnell"])
    assert recipe["tags"] == ["Pasta", "schnell"]
    spaghetti = coordinator.ingredients.find_by_name("Spaghetti")

    updated = await coordinator.async_save_recipe(
        recipe["id"],
        {"ingredients": [{"ingredient_id": spaghetti["id"], "amount": 1, "unit": "kg"}], "duration_min": 20},
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
    with pytest.raises(MealPlannerError) as err:
        await coordinator.async_save_ingredient(onion["id"], {"name": "salz"})
    assert err.value.code == "ingredient_exists"
    renamed = await coordinator.async_save_ingredient(onion["id"], {"name": "Gemüsezwiebel"})
    assert renamed["name"] == "Gemüsezwiebel"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Zwiebel", "Zwiebeln"),
        ("Tomate", "tomaten"),
        ("Kartoffel", "Kartoffeln"),
        ("Möhre", "Mohren"),
        ("Ei", "Eier"),
        ("Rote Bete", "rote-bete"),
    ],
)
def test_similarity_key_merges_spelling_variants(a: str, b: str) -> None:
    assert similarity_key(a) == similarity_key(b)


@pytest.mark.parametrize(("a", "b"), [("Mehl", "Milch"), ("Reis", "Reisnudeln"), ("Salz", "Zucker")])
def test_similarity_key_keeps_real_differences(a: str, b: str) -> None:
    assert similarity_key(a) != similarity_key(b)


async def test_find_similar_ingredients(hass, init_integration) -> None:
    coordinator = init_integration.runtime_data
    onion = await _ing(coordinator, "Zwiebel")
    await _ing(coordinator, "Salz")

    result = coordinator.find_similar_ingredients("Zwiebeln")
    assert result["exact"] is None
    assert result["similar"] == [{"id": onion["id"], "name": "Zwiebel"}]

    result = coordinator.find_similar_ingredients("zwiebel")
    assert result["exact"] == {"id": onion["id"], "name": "Zwiebel"}
    assert result["similar"] == []

    # Renaming the entry itself must not report itself.
    result = coordinator.find_similar_ingredients("Zwiebeln", exclude_id=onion["id"])
    assert result == {"exact": None, "similar": []}

    assert coordinator.find_similar_ingredients("Paprika") == {"exact": None, "similar": []}


async def test_merge_ingredients_rewrites_recipes(hass, init_integration) -> None:
    coordinator = init_integration.runtime_data
    onion = await _ing(coordinator, "Zwiebel", "Stück")
    onions = await _ing(coordinator, "Zwiebeln")  # the duplicate to get rid of
    salt = await _ing(coordinator, "Salz")

    soup = await coordinator.async_save_recipe(
        None,
        {
            "name": "Suppe",
            "base_portions": 4,
            "ingredients": [
                {"ingredient_id": onions["id"], "amount": 2, "unit": "Stück"},
                {"ingredient_id": salt["id"], "amount": None, "unit": None},
                {"ingredient_id": onion["id"], "amount": 1, "unit": "Stück"},
                {"ingredient_id": onions["id"], "amount": 200, "unit": "g"},
            ],
        },
    )
    sauce = await coordinator.async_save_recipe(
        None,
        {
            "name": "Soße",
            "base_portions": 2,
            "ingredients": [{"ingredient_id": onions["id"], "amount": None, "unit": None}],
        },
    )
    await coordinator.async_merge_ingredients(onions["id"], onion["id"])

    assert coordinator.ingredients.find_by_name("Zwiebeln") is None
    soup_lines = coordinator.recipes.data[soup["id"]]["ingredients"]
    assert soup_lines == [
        {"ingredient_id": onion["id"], "amount": 3, "unit": "Stück"},  # 2 + 1 combined
        {"ingredient_id": salt["id"], "amount": None, "unit": None},
        {"ingredient_id": onion["id"], "amount": 200, "unit": "g"},  # other unit stays separate
    ]
    assert coordinator.recipes.data[sauce["id"]]["ingredients"] == [
        {"ingredient_id": onion["id"], "amount": None, "unit": None}
    ]
    counts = {i["name"]: i["recipe_count"] for i in coordinator.data["ingredients"]}
    assert counts == {"Zwiebel": 2, "Salz": 1}


async def test_merge_adopts_default_unit_and_validates(hass, init_integration) -> None:
    coordinator = init_integration.runtime_data
    keep = await _ing(coordinator, "Karotte")
    drop = await _ing(coordinator, "Karotten", "Stück")

    await coordinator.async_merge_ingredients(drop["id"], keep["id"])
    assert coordinator.ingredients.find_by_name("Karotte")["default_unit"] == "Stück"

    with pytest.raises(MealPlannerError) as err:
        await coordinator.async_merge_ingredients(keep["id"], keep["id"])
    assert err.value.code == "invalid_merge"
    with pytest.raises(MealPlannerError) as err:
        await coordinator.async_merge_ingredients("gibt-es-nicht", keep["id"])
    assert err.value.code == "not_found"


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
