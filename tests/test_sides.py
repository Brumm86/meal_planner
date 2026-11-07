"""Tests for recipe roles (main/side dish), deposited side dishes and sides in the plan."""

from __future__ import annotations

from datetime import timedelta

import pytest

from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.meal_planner.const import DOMAIN
from custom_components.meal_planner.coordinator import MealPlannerError


def _day(offset: int) -> str:
    return (dt_util.now().date() + timedelta(days=offset)).isoformat()


async def _recipe(
    c,
    name: str,
    *,
    roles=None,
    side_ids=None,
    last_planned: str | None = None,
    ingredients=None,
    base_portions: int = 4,
) -> dict:
    data = {
        "name": name,
        "base_portions": base_portions,
        "ingredients": ingredients or [{"name": "Nudeln", "amount": 400, "unit": "g"}],
        "last_planned": last_planned,
    }
    if roles is not None:
        data["roles"] = roles
    if side_ids is not None:
        data["side_ids"] = side_ids
    result = await c.async_create_recipe_by_names(data)
    return c.recipes.data[result["id"]]


async def _call(hass, service: str, data: dict, *, response: bool = False):
    return await hass.services.async_call(
        DOMAIN, service, data, blocking=True, return_response=response
    )


# ------------------------------------------------------------------------ roles


async def test_new_recipes_are_main_dishes_and_legacy_ones_too(hass, init_integration) -> None:
    c = init_integration.runtime_data
    new = await _recipe(c, "Neu")
    assert new["roles"] == ["main"] and new["side_ids"] == []

    legacy = await _recipe(c, "Alt")
    legacy.pop("roles")  # as stored by versions before 0.5.0
    legacy.pop("side_ids")
    snapshot = c.build_snapshot()
    alt = next(r for r in snapshot["recipes"] if r["name"] == "Alt")
    assert alt["roles"] == ["main"] and alt["side_ids"] == []
    assert c.suggest_meals([_day(1)])["suggestions"]  # still proposable as main dish


async def test_roles_are_validated_and_ordered(hass, init_integration) -> None:
    c = init_integration.runtime_data
    both = await _recipe(c, "Gratin", roles=["side", "main"])
    assert both["roles"] == ["main", "side"]
    with pytest.raises(Exception):
        await _recipe(c, "Leer", roles=[])
    with pytest.raises(Exception):
        await _recipe(c, "Falsch", roles=["dessert"])


async def test_side_ids_need_existing_side_dishes(hass, init_integration) -> None:
    c = init_integration.runtime_data
    reis = await _recipe(c, "Reis", roles=["side"])
    main_only = await _recipe(c, "Suppe")

    schnitzel = await _recipe(c, "Schnitzel", side_ids=[reis["id"], reis["id"]])
    assert schnitzel["side_ids"] == [reis["id"]]  # duplicates dropped

    with pytest.raises(MealPlannerError) as err:
        await _recipe(c, "Braten", side_ids=[main_only["id"]])
    assert err.value.code == "not_a_side"
    with pytest.raises(MealPlannerError) as err:
        await _recipe(c, "Braten", side_ids=["gibt-es-nicht"])
    assert err.value.code == "not_found"

    # A recipe cannot be its own side dish.
    gratin = await _recipe(c, "Gratin", roles=["main", "side"])
    with pytest.raises(MealPlannerError) as err:
        await c.async_save_recipe(gratin["id"], {"side_ids": [gratin["id"]]})
    assert err.value.code == "side_is_self"


async def test_pure_side_dish_keeps_no_deposited_sides(hass, init_integration) -> None:
    c = init_integration.runtime_data
    reis = await _recipe(c, "Reis", roles=["side"])
    schnitzel = await _recipe(c, "Schnitzel", side_ids=[reis["id"]])
    await c.async_save_recipe(schnitzel["id"], {"roles": ["side"]})
    assert c.recipes.data[schnitzel["id"]]["side_ids"] == []


async def test_roles_cannot_be_taken_away_while_in_use(hass, init_integration) -> None:
    c = init_integration.runtime_data
    reis = await _recipe(c, "Reis", roles=["side"])
    schnitzel = await _recipe(c, "Schnitzel", side_ids=[reis["id"]])

    # Listed at another recipe -> still a side dish.
    with pytest.raises(MealPlannerError) as err:
        await c.async_save_recipe(reis["id"], {"roles": ["main"]})
    assert err.value.code == "recipe_is_side"
    await c.async_save_recipe(schnitzel["id"], {"side_ids": []})
    await c.async_save_recipe(reis["id"], {"roles": ["main"]})  # free now

    # Planned as main dish -> still a main dish.
    await c.async_add_meal(_day(1), recipe_id=schnitzel["id"])
    with pytest.raises(MealPlannerError) as err:
        await c.async_save_recipe(schnitzel["id"], {"roles": ["side"]})
    assert err.value.code == "recipe_in_plan"

    # Planned as side dish -> still a side dish.
    await c.async_save_recipe(reis["id"], {"roles": ["side"]})
    sid = (await c.async_add_meal(_day(2), recipe_id=schnitzel["id"], side_ids=[reis["id"]]))["id"]
    with pytest.raises(MealPlannerError) as err:
        await c.async_save_recipe(reis["id"], {"roles": ["main"]})
    assert err.value.code == "recipe_is_side"
    await c.async_remove_meal(sid)


# ------------------------------------------------------------------- the plan


async def test_sides_hang_at_the_entry_and_follow_its_portions(hass, init_integration) -> None:
    c = init_integration.runtime_data  # household 4
    gratin = await _recipe(
        c,
        "Gratin",
        roles=["side"],
        base_portions=2,
        ingredients=[{"name": "Kartoffeln", "amount": 400, "unit": "g"}],
    )
    schnitzel = await _recipe(
        c, "Schnitzel", ingredients=[{"name": "Schweinefleisch", "amount": 600, "unit": "g"}]
    )
    entry = await c.async_add_meal(_day(1), recipe_id=schnitzel["id"], side_ids=[gratin["id"]])
    assert entry["side_ids"] == [gratin["id"]]

    def needs():
        return {
            p["name"]: p["amount"] for p in c.compute_shopping(_day(1), _day(1))["positions"]
        }

    # household 4: Schnitzel x1, Gratin (for 2) x2
    assert needs() == {"Kartoffeln": 800.0, "Schweinefleisch": 600.0}

    await c.async_set_portions(entry["id"], 2)  # sides follow: x0.5 and x1
    assert needs() == {"Kartoffeln": 400.0, "Schweinefleisch": 300.0}

    # The snapshot and get_plan carry the sides.
    assert c.build_snapshot()["plan"][_day(1)][0]["side_ids"] == [gratin["id"]]
    meal = c.get_plan(_day(1), _day(1))[0]["meals"][0]
    assert meal["sides"] == ["Gratin"]


async def test_sides_can_be_changed_afterwards(hass, init_integration) -> None:
    c = init_integration.runtime_data
    reis = await _recipe(c, "Reis", roles=["side"])
    salat = await _recipe(c, "Salat", roles=["side"])
    suppe = await _recipe(c, "Suppe")
    schnitzel = await _recipe(c, "Schnitzel")
    entry = await c.async_add_meal(_day(1), recipe_id=schnitzel["id"])

    await c.async_set_sides(entry["id"], [reis["id"], salat["id"]])
    assert c.plan.data[entry["id"]]["side_ids"] == [reis["id"], salat["id"]]
    await c.async_set_sides(entry["id"], [])
    assert c.plan.data[entry["id"]]["side_ids"] == []

    with pytest.raises(MealPlannerError) as err:
        await c.async_set_sides(entry["id"], [suppe["id"]])
    assert err.value.code == "not_a_side"
    with pytest.raises(MealPlannerError) as err:
        await c.async_set_sides("gibt-es-nicht", [])
    assert err.value.code == "not_found"

    text = await c.async_add_meal(_day(1), text="Reste")
    with pytest.raises(MealPlannerError) as err:
        await c.async_set_sides(text["id"], [reis["id"]])
    assert err.value.code == "sides_need_recipe"
    with pytest.raises(MealPlannerError) as err:
        await c.async_add_meal(_day(2), text="Auswärts", side_ids=[reis["id"]])
    assert err.value.code == "sides_need_recipe"

    # Past days stay as they were.
    past = await c.plan.async_create_item(
        {"date": _day(-1), "kind": "recipe", "recipe_id": schnitzel["id"]}
    )
    with pytest.raises(MealPlannerError) as err:
        await c.async_set_sides(past["id"], [reis["id"]])
    assert err.value.code == "date_in_past"


async def test_removing_the_main_dish_removes_its_sides_too(hass, init_integration) -> None:
    c = init_integration.runtime_data
    reis = await _recipe(c, "Reis", roles=["side"])
    schnitzel = await _recipe(c, "Schnitzel")
    entry = await c.async_add_meal(_day(1), recipe_id=schnitzel["id"], side_ids=[reis["id"]])
    await c.async_remove_meal(entry["id"])
    assert c.plan.get_by_date(_day(1)) == []
    assert c.compute_shopping(_day(1), _day(1))["positions"] == []


async def test_past_days_bump_last_planned_of_sides(hass, init_integration) -> None:
    c = init_integration.runtime_data
    reis = await _recipe(c, "Reis", roles=["side"])
    schnitzel = await _recipe(c, "Schnitzel")
    await c.plan.async_create_item(
        {"date": _day(-2), "kind": "recipe", "recipe_id": schnitzel["id"], "side_ids": [reis["id"]]}
    )
    await c.async_refresh()
    assert c.recipes.data[schnitzel["id"]]["last_planned"] == _day(-2)
    assert c.recipes.data[reis["id"]]["last_planned"] == _day(-2)


async def test_delete_recipe_respects_and_cleans_side_links(hass, init_integration) -> None:
    c = init_integration.runtime_data
    reis = await _recipe(c, "Reis", roles=["side"])
    schnitzel = await _recipe(c, "Schnitzel", side_ids=[reis["id"]])
    entry = await c.async_add_meal(_day(1), recipe_id=schnitzel["id"], side_ids=[reis["id"]])

    # Planned as side dish today or later -> refused.
    with pytest.raises(MealPlannerError) as err:
        await c.async_delete_recipe(reis["id"])
    assert err.value.code == "recipe_in_plan"
    await c.async_remove_meal(entry["id"])

    # A past entry loses the deleted side dish; deposited links are cleaned up.
    past = await c.plan.async_create_item(
        {"date": _day(-1), "kind": "recipe", "recipe_id": schnitzel["id"], "side_ids": [reis["id"]]}
    )
    await c.async_delete_recipe(reis["id"])
    assert c.plan.data[past["id"]]["side_ids"] == []
    assert c.recipes.data[schnitzel["id"]]["side_ids"] == []

    # Deleting a main dish in the past turns its entry into text without sides.
    salat = await _recipe(c, "Salat", roles=["side"])
    suppe = await _recipe(c, "Suppe")
    past2 = await c.plan.async_create_item(
        {"date": _day(-1), "kind": "recipe", "recipe_id": suppe["id"], "side_ids": [salat["id"]]}
    )
    await c.async_delete_recipe(suppe["id"])
    assert c.plan.data[past2["id"]]["kind"] == "text"
    assert c.plan.data[past2["id"]]["side_ids"] == []


# ------------------------------------------------------------------ suggestions


async def test_suggestions_propose_only_main_dishes_with_a_deposited_side(
    hass, init_integration
) -> None:
    c = init_integration.runtime_data
    reis = await _recipe(c, "Reis", roles=["side"], last_planned=_day(-3))
    kartoffeln = await _recipe(c, "Kartoffeln", roles=["side"], last_planned=_day(-20))
    solo = await _recipe(c, "Suppe", last_planned=_day(-40))
    schnitzel = await _recipe(
        c, "Schnitzel", side_ids=[reis["id"], kartoffeln["id"]], last_planned=_day(-30)
    )

    result = c.suggest_meals()
    by_recipe = {s["recipe_id"]: s for s in result["suggestions"]}
    # The side dishes themselves are never proposed as a day's main dish.
    assert set(by_recipe) == {solo["id"], schnitzel["id"]}
    assert by_recipe[solo["id"]]["side_ids"] == []
    # Of the deposited sides the one planned longest ago is taken.
    assert by_recipe[schnitzel["id"]]["side_ids"] == [kartoffeln["id"]]
    assert by_recipe[schnitzel["id"]]["side_names"] == ["Kartoffeln"]


async def test_suggestions_avoid_repeating_a_side_in_one_batch(hass, init_integration) -> None:
    c = init_integration.runtime_data
    reis = await _recipe(c, "Reis", roles=["side"], last_planned=_day(-10))
    nudeln = await _recipe(c, "Nudeln", roles=["side"], last_planned=_day(-9))
    a = await _recipe(c, "A", side_ids=[reis["id"], nudeln["id"]], last_planned=_day(-30))
    b = await _recipe(c, "B", side_ids=[reis["id"], nudeln["id"]], last_planned=_day(-29))

    suggestions = c.suggest_meals()["suggestions"]
    assert [s["recipe_id"] for s in suggestions] == [a["id"], b["id"]]
    assert [s["side_ids"] for s in suggestions] == [[reis["id"]], [nudeln["id"]]]


async def test_suggestions_do_not_propose_what_is_planned_as_side_in_the_window(
    hass, init_integration
) -> None:
    c = init_integration.runtime_data
    gratin = await _recipe(c, "Gratin", roles=["main", "side"], last_planned=_day(-50))
    schnitzel = await _recipe(c, "Schnitzel", side_ids=[gratin["id"]])
    await c.async_add_meal(_day(0), recipe_id=schnitzel["id"], side_ids=[gratin["id"]])
    assert c.suggest_meals()["suggestions"] == []


async def test_apply_suggestions_hangs_the_sides(hass, init_integration) -> None:
    c = init_integration.runtime_data
    reis = await _recipe(c, "Reis", roles=["side"])
    schnitzel = await _recipe(c, "Schnitzel", side_ids=[reis["id"]])
    result = await c.async_apply_suggestions(
        [{"date": _day(1), "recipe_id": schnitzel["id"], "side_ids": [reis["id"]]}]
    )
    assert result == {"added": 1, "skipped": 0}
    assert c.plan.get_by_date(_day(1))[0]["side_ids"] == [reis["id"]]

    suppe = await _recipe(c, "Suppe")
    with pytest.raises(MealPlannerError) as err:  # validated before anything changes
        await c.async_apply_suggestions(
            [
                {"date": _day(2), "recipe_id": schnitzel["id"]},
                {"date": _day(3), "recipe_id": schnitzel["id"], "side_ids": [suppe["id"]]},
            ]
        )
    assert err.value.code == "not_a_side"
    assert c.plan.get_by_date(_day(2)) == []


# ------------------------------------------------------------------- services


async def test_services_create_recipe_and_add_meal_with_sides(hass, init_integration) -> None:
    c = init_integration.runtime_data
    created = await _call(
        hass,
        "create_recipe",
        {
            "name": "Kartoffelgratin",
            "roles": ["side"],
            "ingredients": [{"name": "Kartoffeln", "amount": 800, "unit": "g"}],
        },
        response=True,
    )
    assert c.recipes.data[created["id"]]["roles"] == ["side"]

    await _call(
        hass,
        "create_recipe",
        {"name": "Schnitzel", "sides": ["kartoffelgratin"]},
        response=True,
    )
    assert c.recipes.data["schnitzel"]["side_ids"] == ["kartoffelgratin"]
    assert c.recipes.data["schnitzel"]["roles"] == ["main"]

    suggested = await _call(hass, "suggest_meals", {"dates": [_day(2)]}, response=True)
    assert suggested["suggestions"][0]["side_names"] == ["Kartoffelgratin"]

    await _call(
        hass,
        "add_meal",
        {"date": _day(1), "recipe": "Schnitzel", "sides": ["Kartoffelgratin"]},
        response=True,
    )
    entry = c.plan.get_by_date(_day(1))[0]
    assert entry["side_ids"] == ["kartoffelgratin"]

    plan = await _call(hass, "get_plan", {"start_date": _day(1), "end_date": _day(1)}, response=True)
    assert plan["days"][0]["meals"][0]["sides"] == ["Kartoffelgratin"]

    await _call(
        hass,
        "apply_suggestions",
        {"suggestions": [{"date": _day(2), "recipe": "Schnitzel", "sides": ["Kartoffelgratin"]}]},
    )
    assert c.plan.get_by_date(_day(2))[0]["side_ids"] == ["kartoffelgratin"]


async def test_services_reject_bad_sides(hass, init_integration) -> None:
    c = init_integration.runtime_data
    await _recipe(c, "Suppe")
    with pytest.raises(ServiceValidationError):
        await _call(hass, "create_recipe", {"name": "X", "sides": ["Suppe"]}, response=True)
    with pytest.raises(ServiceValidationError):
        await _call(hass, "create_recipe", {"name": "Y", "sides": ["Gibt es nicht"]}, response=True)
    with pytest.raises(ServiceValidationError):
        await _call(
            hass, "add_meal", {"date": _day(1), "text": "Reste", "sides": ["Suppe"]}, response=True
        )


# ------------------------------------------------------------------- websocket


async def test_websocket_plan_add_and_sides(hass, hass_ws_client, init_integration) -> None:
    c = init_integration.runtime_data
    reis = await _recipe(c, "Reis", roles=["side"])
    salat = await _recipe(c, "Salat", roles=["side"])
    schnitzel = await _recipe(c, "Schnitzel", side_ids=[reis["id"], salat["id"]])
    client = await hass_ws_client(hass)

    await client.send_json(
        {
            "id": 1,
            "type": "meal_planner/plan/add",
            "date": _day(1),
            "recipe_id": schnitzel["id"],
            "side_ids": [reis["id"]],
        }
    )
    reply = await client.receive_json()
    assert reply["success"]
    entry_id = reply["result"]["id"]
    assert c.plan.data[entry_id]["side_ids"] == [reis["id"]]

    await client.send_json(
        {
            "id": 2,
            "type": "meal_planner/plan/sides",
            "entry_id": entry_id,
            "side_ids": [reis["id"], salat["id"]],
        }
    )
    assert (await client.receive_json())["success"]
    assert c.plan.data[entry_id]["side_ids"] == [reis["id"], salat["id"]]

    await client.send_json(
        {"id": 3, "type": "meal_planner/plan/sides", "entry_id": entry_id, "side_ids": ["nope"]}
    )
    reply = await client.receive_json()
    assert not reply["success"] and reply["error"]["code"] == "not_found"

    await client.send_json(
        {
            "id": 4,
            "type": "meal_planner/recipe/save",
            "recipe_id": schnitzel["id"],
            "recipe": {"roles": ["main", "side"], "side_ids": [reis["id"]]},
        }
    )
    reply = await client.receive_json()
    assert reply["success"]
    assert c.recipes.data[schnitzel["id"]]["roles"] == ["main", "side"]
