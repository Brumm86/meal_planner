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
    assert snapshot["settings"] == {"household_size": 4, "horizon_days": 10}
    assert snapshot["recipes"] == [] and snapshot["plan"] == {}
    assert "g" in snapshot["units"] and "Stück" in snapshot["units"]

    await client.send_json(
        {
            "id": 2,
            "type": "meal_planner/recipe/save",
            "recipe": {
                "name": "Pfannkuchen",
                "base_portions": 4,
                "ingredients": [{"name": "Mehl", "amount": 250, "unit": "g"}],
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
        {"id": 2, "type": "meal_planner/plan/set", "date": _day(1), "recipe_id": "gulasch", "portions": 2}
    )
    assert (await client.receive_json())["success"]
    entry = init_integration.runtime_data.data["plan"][_day(1)]
    assert entry["recipe_id"] == "gulasch" and entry["portions"] == 2

    # Domain errors carry a stable code the card can show.
    await client.send_json({"id": 3, "type": "meal_planner/recipe/delete", "recipe_id": "gulasch"})
    response = await client.receive_json()
    assert not response["success"] and response["error"]["code"] == "recipe_in_plan"

    await client.send_json({"id": 4, "type": "meal_planner/plan/clear", "date": _day(1)})
    assert (await client.receive_json())["success"]
    await client.send_json({"id": 5, "type": "meal_planner/recipe/delete", "recipe_id": "gulasch"})
    assert (await client.receive_json())["success"]

    await client.send_json({"id": 6, "type": "meal_planner/plan/set", "date": _day(-1), "text": "x"})
    response = await client.receive_json()
    assert not response["success"] and response["error"]["code"] == "date_in_past"

    await client.send_json({"id": 7, "type": "meal_planner/plan/set", "date": _day(1)})
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
