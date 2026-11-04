"""Coordinator and domain logic for the Meal Planner integration.

The coordinator owns the three storage collections and builds the snapshot the
card consumes (and, from stage 3 on, the calendar/sensor entities). Every
mutation goes through a method here so cross-collection rules live in one
place and every change ends with ``async_refresh`` - listeners (the websocket
subscription of the card) then get the new snapshot immediately.
"""

from __future__ import annotations

from datetime import date as date_cls, timedelta
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_HORIZON_DAYS,
    CONF_HOUSEHOLD_SIZE,
    DEFAULT_HORIZON_DAYS,
    DEFAULT_HOUSEHOLD_SIZE,
    DOMAIN,
    PLAN_KIND_RECIPE,
    PLAN_KIND_TEXT,
    UNITS,
)
from .storage import IngredientCollection, PlanCollection, RecipeCollection, iso_date

_LOGGER = logging.getLogger(__name__)

# Plan entries older than this are dropped on refresh (they only exist so the
# card can show "what we ate recently" and so last_planned gets bumped once).
PLAN_RETENTION_DAYS = 60


class MealPlannerError(Exception):
    """A domain error the websocket layer reports with a stable ``code``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class MealPlannerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Holds all meal planner state and the operations on it."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        recipes: RecipeCollection,
        ingredients: IngredientCollection,
        plan: PlanCollection,
    ) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry, update_interval=None)
        self.recipes = recipes
        self.ingredients = ingredients
        self.plan = plan

    # ------------------------------------------------------------------ setup

    @callback
    def async_start(self) -> None:
        """Refresh shortly after midnight so 'today' and last_planned roll over."""
        self.config_entry.async_on_unload(
            async_track_time_change(
                self.hass, self._async_midnight, hour=0, minute=0, second=5
            )
        )

    async def _async_midnight(self, _now: Any) -> None:
        await self.async_refresh()

    # --------------------------------------------------------------- settings

    @property
    def household_size(self) -> int:
        return int(self.config_entry.options.get(CONF_HOUSEHOLD_SIZE, DEFAULT_HOUSEHOLD_SIZE))

    @property
    def horizon_days(self) -> int:
        return int(self.config_entry.options.get(CONF_HORIZON_DAYS, DEFAULT_HORIZON_DAYS))

    @staticmethod
    def today() -> date_cls:
        """Today in the Home Assistant time zone."""
        return dt_util.now().date()

    # --------------------------------------------------------------- snapshot

    async def _async_update_data(self) -> dict[str, Any]:
        await self._async_roll_over_past_days()
        return self.build_snapshot()

    def build_snapshot(self) -> dict[str, Any]:
        """Everything the card needs, JSON-serialisable."""
        recipes = sorted(self.recipes.data.values(), key=lambda r: r["name"].casefold())
        ingredients = sorted(self.ingredients.data.values(), key=lambda i: i["name"].casefold())
        tags = sorted({t for r in recipes for t in r.get("tags", [])}, key=str.casefold)
        plan = {
            entry["date"]: {
                "date": entry["date"],
                "kind": entry["kind"],
                "recipe_id": entry.get("recipe_id"),
                "text": entry.get("text"),
                "portions": entry.get("portions"),
            }
            for entry in self.plan.data.values()
        }
        return {
            "today": self.today().isoformat(),
            "settings": {
                "household_size": self.household_size,
                "horizon_days": self.horizon_days,
            },
            "units": list(UNITS),
            "recipes": recipes,
            "ingredients": ingredients,
            "tags": tags,
            "plan": plan,
        }

    async def _async_roll_over_past_days(self) -> None:
        """Bump ``last_planned`` for days that have passed and prune old entries.

        A past plan day counts once (``counted``); a later manual correction of
        a recipe's last_planned is therefore never overwritten again.
        """
        today = self.today()
        cutoff = (today - timedelta(days=PLAN_RETENTION_DAYS)).isoformat()
        for entry in list(self.plan.data.values()):
            day = entry["date"]
            if day >= today.isoformat():
                continue
            if entry["kind"] == PLAN_KIND_RECIPE and not entry.get("counted"):
                recipe = self.recipes.data.get(entry["recipe_id"])
                if recipe is not None:
                    last = recipe.get("last_planned")
                    if last is None or last < day:
                        await self.recipes.async_update_item(recipe["id"], {"last_planned": day})
                await self.plan.async_update_item(entry["id"], {"counted": True})
            if day < cutoff:
                await self.plan.async_delete_item(entry["id"])

    # ---------------------------------------------------------------- recipes

    async def async_save_recipe(self, recipe_id: str | None, data: dict[str, Any]) -> dict[str, Any]:
        """Create (``recipe_id`` None) or update a recipe.

        Ingredient lines arrive as ``{"name", "amount", "unit"}``; names are
        resolved against the catalog and unknown ones are created on the fly
        (that is the autocomplete flow of the card).
        """
        payload = dict(data)
        if "ingredients" in payload:
            payload["ingredients"] = await self._resolve_lines(payload["ingredients"])
        if recipe_id is None:
            item = await self.recipes.async_create_item(payload)
        else:
            if recipe_id not in self.recipes.data:
                raise MealPlannerError("not_found", "Rezept nicht gefunden")
            item = await self.recipes.async_update_item(recipe_id, payload)
        await self.async_refresh()
        return item

    async def _resolve_lines(self, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        resolved: list[dict[str, Any]] = []
        for raw in lines:
            name = str(raw.get("name", "")).strip()
            if not name:
                raise vol.Invalid("ingredient name is required")
            unit = raw.get("unit") or None
            entry = self.ingredients.find_by_name(name)
            if entry is None:
                entry = await self.ingredients.async_create_item(
                    {"name": name, "default_unit": unit}
                )
            elif entry.get("default_unit") is None and unit:
                entry = await self.ingredients.async_update_item(
                    entry["id"], {"default_unit": unit}
                )
            amount = raw.get("amount")
            resolved.append(
                {
                    "ingredient_id": entry["id"],
                    "amount": amount if amount not in ("", None) else None,
                    "unit": unit,
                }
            )
        return resolved

    async def async_delete_recipe(self, recipe_id: str) -> None:
        """Delete a recipe unless it is still planned today or later.

        Entries on past days are kept as free text under the recipe's name so
        the history does not show dangling ids.
        """
        recipe = self.recipes.data.get(recipe_id)
        if recipe is None:
            raise MealPlannerError("not_found", "Rezept nicht gefunden")
        today = self.today().isoformat()
        uses = sorted(
            e["date"]
            for e in self.plan.data.values()
            if e["kind"] == PLAN_KIND_RECIPE and e["recipe_id"] == recipe_id
        )
        upcoming = [d for d in uses if d >= today]
        if upcoming:
            raise MealPlannerError(
                "recipe_in_plan",
                "Das Rezept ist noch im Plan eingetragen: " + ", ".join(upcoming),
            )
        for entry in list(self.plan.data.values()):
            if entry["kind"] == PLAN_KIND_RECIPE and entry["recipe_id"] == recipe_id:
                await self.plan.async_update_item(
                    entry["id"],
                    {"kind": PLAN_KIND_TEXT, "recipe_id": None, "text": recipe["name"], "portions": None},
                )
        await self.recipes.async_delete_item(recipe_id)
        await self.async_refresh()

    # ------------------------------------------------------------ ingredients

    async def async_save_ingredient(
        self, ingredient_id: str | None, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Create or rename/update a catalog ingredient."""
        if ingredient_id is None:
            item = await self.ingredients.async_create_item(data)
        else:
            if ingredient_id not in self.ingredients.data:
                raise MealPlannerError("not_found", "Zutat nicht gefunden")
            item = await self.ingredients.async_update_item(ingredient_id, data)
        await self.async_refresh()
        return item

    async def async_delete_ingredient(self, ingredient_id: str) -> None:
        """Delete a catalog ingredient that no recipe uses any more."""
        if ingredient_id not in self.ingredients.data:
            raise MealPlannerError("not_found", "Zutat nicht gefunden")
        used_in = sorted(
            r["name"]
            for r in self.recipes.data.values()
            if any(line["ingredient_id"] == ingredient_id for line in r.get("ingredients", []))
        )
        if used_in:
            raise MealPlannerError(
                "ingredient_in_use",
                "Die Zutat wird noch verwendet in: " + ", ".join(used_in),
            )
        await self.ingredients.async_delete_item(ingredient_id)
        await self.async_refresh()

    # ------------------------------------------------------------------- plan

    async def async_set_plan(
        self,
        date: str,
        *,
        recipe_id: str | None = None,
        text: str | None = None,
        portions: int | None = None,
    ) -> None:
        """Set the entry of one day (replaces whatever was planned there)."""
        day = iso_date(date)
        if day < self.today().isoformat():
            raise MealPlannerError("date_in_past", "Vergangene Tage lassen sich nicht mehr planen")
        if (recipe_id is None) == (text is None):
            raise vol.Invalid("exactly one of recipe_id or text is required")
        if recipe_id is not None and recipe_id not in self.recipes.data:
            raise MealPlannerError("not_found", "Rezept nicht gefunden")
        fields = {
            "kind": PLAN_KIND_RECIPE if recipe_id is not None else PLAN_KIND_TEXT,
            "recipe_id": recipe_id,
            "text": text,
            "portions": portions,
            "counted": False,
        }
        existing = self.plan.get_by_date(day)
        if existing is None:
            await self.plan.async_create_item({"date": day, **fields})
        else:
            await self.plan.async_update_item(existing["id"], fields)
        await self.async_refresh()

    async def async_clear_plan(self, date: str) -> None:
        """Remove the entry of one day (no-op when nothing is planned)."""
        day = iso_date(date)
        existing = self.plan.get_by_date(day)
        if existing is not None:
            await self.plan.async_delete_item(existing["id"])
            await self.async_refresh()
