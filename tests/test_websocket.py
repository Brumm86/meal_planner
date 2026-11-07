"""Tests for the websocket API the card talks to."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.util import dt as dt_util


def _day(offset: int) -> str:
    return (dt_util.now().date() + timedelta(days=offset)).isoformat()


async def test_subscribe_pushes_snapshot_on_every_change(hass, hass_ws_client, init_integration) -> None:
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "meal_planner/subscribe"})
    assert (await client.receive_json())["success"]
    first = await client.receive_json()
    assert first["type"] == "event"
    snapshot = first["event"]
    assert snapshot["settings"] == {"household_size": 4, "horizon_days": 10, "bring_configured": False}
    assert snapshot["recipes"] == [] and snapshot["plan"] == {}
    assert "g" in snapshot["units"] and "Stück" in snapshot["units"]

    # Ingredients are created in the catalog first ...
    await client.send_json(
        {"id": 2, "type": "meal_planner/ingredient/save", "ingredient": {"name": "Mehl"}}
    )
    msgs = [await client.receive_json(), await client.receive_json()]
    result = next(m for m in msgs if m["type"] == "result")
    assert result["success"] and result["result"]["id"] == "mehl"
    assert result["result"]["ingredient"]["name"] == "Mehl"

    # ... and recipes only reference them by id.
    await client.send_json(
        {
            "id": 3,
            "type": "meal_planner/recipe/save",
            "recipe": {
                "name": "Pfannkuchen",
                "base_portions": 4,
                "ingredients": [{"ingredient_id": "mehl", "amount": 250, "unit": "g"}],
            },
        }
    )
    # The result and the pushed snapshot may arrive in either order.
    msgs = [await client.receive_json(), await client.receive_json()]
    result = next(m for m in msgs if m["type"] == "result")
    event = next(m for m in msgs if m["type"] == "event")
    assert result["success"] and result["result"]["id"] == "pfannkuchen"
    assert [r["name"] for r in event["event"]["recipes"]] == ["Pfannkuchen"]
    assert [i["name"] for i in event["event"]["ingredients"]] == ["Mehl"]
    assert event["event"]["ingredients"][0]["recipe_count"] == 1

    # A free-text ingredient is refused (no silent catalog entry).
    await client.send_json(
        {
            "id": 4,
            "type": "meal_planner/recipe/save",
            "recipe": {
                "name": "Brot",
                "base_portions": 2,
                "ingredients": [{"name": "Hefe", "amount": 1, "unit": "Packung"}],
            },
        }
    )
    response = await client.receive_json()
    assert not response["success"] and response["error"]["code"] == "invalid_format"


async def test_ingredient_similar_and_merge(hass, hass_ws_client, init_integration) -> None:
    client = await hass_ws_client(hass)
    coordinator = init_integration.runtime_data
    onion = await coordinator.async_save_ingredient(None, {"name": "Zwiebel"})
    onions = await coordinator.async_save_ingredient(None, {"name": "Zwiebeln"})

    await client.send_json({"id": 1, "type": "meal_planner/ingredient/similar", "name": "zwiebeln"})
    response = await client.receive_json()
    assert response["success"]
    assert response["result"]["exact"] == {"id": onions["id"], "name": "Zwiebeln"}
    assert response["result"]["similar"] == [{"id": onion["id"], "name": "Zwiebel"}]

    await client.send_json(
        {"id": 2, "type": "meal_planner/ingredient/save", "ingredient": {"name": "ZWIEBEL"}}
    )
    response = await client.receive_json()
    assert not response["success"] and response["error"]["code"] == "ingredient_exists"

    await client.send_json(
        {
            "id": 3,
            "type": "meal_planner/ingredient/merge",
            "source_id": onions["id"],
            "target_id": onion["id"],
        }
    )
    assert (await client.receive_json())["success"]
    assert [i["name"] for i in coordinator.data["ingredients"]] == ["Zwiebel"]

    await client.send_json(
        {
            "id": 4,
            "type": "meal_planner/ingredient/merge",
            "source_id": onion["id"],
            "target_id": onion["id"],
        }
    )
    response = await client.receive_json()
    assert not response["success"] and response["error"]["code"] == "invalid_merge"


async def test_plan_commands_and_errors(hass, hass_ws_client, init_integration) -> None:
    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "meal_planner/recipe/save",
            "recipe": {"name": "Gulasch", "base_portions": 4},
        }
    )
    assert (await client.receive_json())["success"]

    await client.send_json(
        {"id": 2, "type": "meal_planner/plan/add", "date": _day(1), "recipe_id": "gulasch", "portions": 2}
    )
    assert (await client.receive_json())["success"]
    entry = init_integration.runtime_data.data["plan"][_day(1)][0]
    assert entry["recipe_id"] == "gulasch" and entry["portions"] == 2

    # Domain errors carry a stable code the card can show.
    await client.send_json({"id": 3, "type": "meal_planner/recipe/delete", "recipe_id": "gulasch"})
    response = await client.receive_json()
    assert not response["success"] and response["error"]["code"] == "recipe_in_plan"

    await client.send_json({"id": 4, "type": "meal_planner/plan/clear", "date": _day(1)})
    assert (await client.receive_json())["success"]
    await client.send_json({"id": 5, "type": "meal_planner/recipe/delete", "recipe_id": "gulasch"})
    assert (await client.receive_json())["success"]

    await client.send_json({"id": 6, "type": "meal_planner/plan/add", "date": _day(-1), "text": "x"})
    response = await client.receive_json()
    assert not response["success"] and response["error"]["code"] == "date_in_past"

    await client.send_json({"id": 7, "type": "meal_planner/plan/add", "date": _day(1)})
    response = await client.receive_json()
    assert not response["success"] and response["error"]["code"] == "invalid_format"


async def test_not_loaded_error(hass, hass_ws_client) -> None:
    hass.config.components.add("meal_planner")
    from custom_components.meal_planner.websocket_api import async_setup_websocket_api

    async_setup_websocket_api(hass)
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "meal_planner/plan/clear", "date": _day(1)})
    response = await client.receive_json()
    assert not response["success"] and response["error"]["code"] == "not_loaded"
