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
import random
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.components.todo import TodoListEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BRING_ENTITY,
    CONF_HORIZON_DAYS,
    CONF_HOUSEHOLD_SIZE,
    DEFAULT_HORIZON_DAYS,
    DEFAULT_HOUSEHOLD_SIZE,
    DOMAIN,
    MAX_SHOPPING_DAYS,
    PLAN_KIND_RECIPE,
    PLAN_KIND_TEXT,
    ROLE_MAIN,
    ROLE_SIDE,
    ROLES,
    UNITS,
)
from . import shopping
from .storage import (
    INGREDIENT_LINE_SCHEMA,
    IngredientCollection,
    PlanCollection,
    RecipeCollection,
    TransferCollection,
    iso_date,
    nfc,
    similarity_key,
    sort_key,
)

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


def recipe_roles(recipe: dict[str, Any]) -> list[str]:
    """Roles of a recipe; recipes stored before 0.5.0 have none and are main dishes."""
    return list(recipe.get("roles") or [ROLE_MAIN])


def _merge_lines(
    lines: list[dict[str, Any]], source_id: str, target_id: str
) -> list[dict[str, Any]]:
    """Point ``source_id`` lines at ``target_id`` and combine same-unit duplicates."""
    result: list[dict[str, Any]] = []
    for original in lines:
        line = dict(original)
        if line["ingredient_id"] == source_id:
            line["ingredient_id"] = target_id
        if line["ingredient_id"] == target_id:
            for existing in result:
                if (
                    existing["ingredient_id"] == target_id
                    and existing["unit"] == line["unit"]
                    and (existing["amount"] is None) == (line["amount"] is None)
                ):
                    if line["amount"] is not None:
                        existing["amount"] = round(existing["amount"] + line["amount"], 3)
                    break
            else:
                result.append(line)
        else:
            result.append(line)
    return result


def _merge_transfer_lines(
    lines: list[dict[str, Any]], source_id: str, target_id: str
) -> list[dict[str, Any]]:
    """Repoint remembered transfer lines of a merged ingredient (summing duplicates)."""
    merged: shopping.Amounts = {}
    for line in lines:
        ingredient_id = target_id if line["ingredient_id"] == source_id else line["ingredient_id"]
        shopping.add_into(merged, {(ingredient_id, line["dimension"]): line["amount"]})
    return shopping.amounts_to_lines(merged)


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
        transfers: TransferCollection,
    ) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry, update_interval=None)
        self.recipes = recipes
        self.ingredients = ingredients
        self.plan = plan
        self.transfers = transfers

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

    @property
    def bring_entity(self) -> str | None:
        """Entity id of the Bring shopping list (todo entity), if configured."""
        return self.config_entry.options.get(CONF_BRING_ENTITY) or None

    @staticmethod
    def today() -> date_cls:
        """Today in the Home Assistant time zone."""
        return dt_util.now().date()

    @staticmethod
    def _entry_view(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": entry["id"],
            "date": entry["date"],
            "kind": entry["kind"],
            "recipe_id": entry.get("recipe_id"),
            "text": entry.get("text"),
            "portions": entry.get("portions"),
            "side_ids": list(entry.get("side_ids", [])),
        }

    # --------------------------------------------------------------- snapshot

    async def _async_update_data(self) -> dict[str, Any]:
        await self._async_roll_over_past_days()
        return self.build_snapshot()

    def build_snapshot(self) -> dict[str, Any]:
        """Everything the card needs, JSON-serialisable."""
        recipes = [
            {**r, "roles": recipe_roles(r), "side_ids": list(r.get("side_ids", []))}
            for r in sorted(self.recipes.data.values(), key=lambda r: sort_key(r["name"]))
        ]
        used_in: dict[str, int] = {}
        for recipe in recipes:
            for ingredient_id in {line["ingredient_id"] for line in recipe.get("ingredients", [])}:
                used_in[ingredient_id] = used_in.get(ingredient_id, 0) + 1
        ingredients = [
            {**item, "recipe_count": used_in.get(item["id"], 0)}
            for item in sorted(self.ingredients.data.values(), key=lambda i: sort_key(i["name"]))
        ]
        tags = sorted({t for r in recipes for t in r.get("tags", [])}, key=sort_key)
        plan: dict[str, list[dict[str, Any]]] = {}
        for entry in self.plan.data.values():
            plan.setdefault(entry["date"], []).append(self._entry_view(entry))
        return {
            "today": self.today().isoformat(),
            "settings": {
                "household_size": self.household_size,
                "horizon_days": self.horizon_days,
                "bring_configured": self.bring_entity is not None,
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
                for recipe_id in (entry["recipe_id"], *entry.get("side_ids", [])):
                    recipe = self.recipes.data.get(recipe_id)
                    if recipe is not None:
                        last = recipe.get("last_planned")
                        if last is None or last < day:
                            await self.recipes.async_update_item(
                                recipe["id"], {"last_planned": day}
                            )
                await self.plan.async_update_item(entry["id"], {"counted": True})
            if day < cutoff:
                await self.plan.async_delete_item(entry["id"])
        for record in list(self.transfers.data.values()):
            if record["date"] < cutoff:
                await self.transfers.async_delete_item(record["id"])

    # ---------------------------------------------------------------- recipes

    async def async_save_recipe(self, recipe_id: str | None, data: dict[str, Any]) -> dict[str, Any]:
        """Create (``recipe_id`` None) or update a recipe.

        Ingredient lines arrive as ``{"ingredient_id", "amount", "unit"}`` and
        must reference an existing catalog entry. Free-text names are not
        accepted on purpose: that is what keeps "Zwiebel" and "Zwiebeln" from
        ending up as two shopping-list items.
        """
        payload = dict(data)
        if recipe_id is not None and recipe_id not in self.recipes.data:
            raise MealPlannerError("not_found", "Rezept nicht gefunden")
        self._prepare_roles_and_sides(recipe_id, payload)
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

    def _prepare_roles_and_sides(self, recipe_id: str | None, payload: dict[str, Any]) -> None:
        """Validate ``roles``/``side_ids`` of a recipe payload and keep them consistent.

        * ``roles`` is a non-empty subset of main/side; ``side_ids`` (the side
          dishes that go with the recipe) only make sense for main dishes and
          are emptied otherwise.
        * Every listed side dish must exist and be marked as side dish.
        * A role cannot be taken away while the recipe is still used in it:
          as a side dish (listed at another recipe or planned today or later)
          or as a main dish (planned today or later).
        """
        if "roles" not in payload and "side_ids" not in payload:
            return
        existing = self.recipes.data.get(recipe_id) if recipe_id else None
        if "roles" in payload:
            raw = payload["roles"]
            if not isinstance(raw, list) or not raw or any(r not in ROLES for r in raw):
                raise vol.Invalid("roles must be a non-empty list of main/side")
            roles = [role for role in ROLES if role in raw]
        else:
            roles = recipe_roles(existing) if existing else [ROLE_MAIN]
        if "side_ids" in payload:
            raw_ids = payload["side_ids"]
            if not isinstance(raw_ids, list) or any(not isinstance(i, str) for i in raw_ids):
                raise vol.Invalid("side_ids must be a list of recipe ids")
            side_ids = list(dict.fromkeys(raw_ids))
        else:
            side_ids = list(existing.get("side_ids", [])) if existing else []
        if ROLE_MAIN not in roles:
            side_ids = []
        self._check_sides(recipe_id, side_ids)
        if existing is not None:
            today = self.today().isoformat()
            lost = set(recipe_roles(existing)) - set(roles)
            if ROLE_SIDE in lost:
                users = sorted(
                    {r["name"] for r in self.recipes.data.values() if recipe_id in r.get("side_ids", [])}
                    | {
                        e["date"]
                        for e in self.plan.data.values()
                        if recipe_id in e.get("side_ids", []) and e["date"] >= today
                    }
                )
                if users:
                    raise MealPlannerError(
                        "recipe_is_side",
                        "Das Rezept ist noch als Beilage hinterlegt oder eingeplant: "
                        + ", ".join(users),
                    )
            if ROLE_MAIN in lost:
                upcoming = sorted(
                    e["date"]
                    for e in self.plan.data.values()
                    if e["kind"] == PLAN_KIND_RECIPE
                    and e["recipe_id"] == recipe_id
                    and e["date"] >= today
                )
                if upcoming:
                    raise MealPlannerError(
                        "recipe_in_plan",
                        "Das Rezept ist noch im Plan eingetragen: " + ", ".join(upcoming),
                    )
        payload["roles"] = roles
        payload["side_ids"] = side_ids

    def _check_sides(self, recipe_id: str | None, side_ids: list[str]) -> None:
        """Every id must be an existing recipe that is marked as side dish."""
        for side_id in side_ids:
            if side_id == recipe_id:
                raise MealPlannerError(
                    "side_is_self", "Ein Rezept kann nicht seine eigene Beilage sein"
                )
            side = self.recipes.data.get(side_id)
            if side is None:
                raise MealPlannerError("not_found", "Beilage nicht gefunden")
            if ROLE_SIDE not in recipe_roles(side):
                raise MealPlannerError(
                    "not_a_side", f"„{side['name']}“ ist nicht als Beilage gekennzeichnet"
                )

    async def _resolve_lines(self, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        resolved: list[dict[str, Any]] = []
        for raw in lines:
            ingredient_id = raw.get("ingredient_id")
            if not ingredient_id:
                raise vol.Invalid("ingredient_id is required")
            entry = self.ingredients.data.get(ingredient_id)
            if entry is None:
                raise MealPlannerError("not_found", "Zutat nicht gefunden")
            unit = raw.get("unit") or None
            # The first unit a recipe uses becomes the ingredient's usual unit.
            if entry.get("default_unit") is None and unit:
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
            if e["kind"] == PLAN_KIND_RECIPE
            and (e["recipe_id"] == recipe_id or recipe_id in e.get("side_ids", []))
        )
        upcoming = [d for d in uses if d >= today]
        if upcoming:
            raise MealPlannerError(
                "recipe_in_plan",
                "Das Rezept ist noch im Plan eingetragen: " + ", ".join(upcoming),
            )
        for entry in list(self.plan.data.values()):
            if entry["kind"] != PLAN_KIND_RECIPE:
                continue
            if entry["recipe_id"] == recipe_id:
                await self.plan.async_update_item(
                    entry["id"],
                    {
                        "kind": PLAN_KIND_TEXT,
                        "recipe_id": None,
                        "text": recipe["name"],
                        "portions": None,
                        "side_ids": [],
                    },
                )
            elif recipe_id in entry.get("side_ids", []):
                await self.plan.async_update_item(
                    entry["id"],
                    {"side_ids": [i for i in entry["side_ids"] if i != recipe_id]},
                )
        for other in list(self.recipes.data.values()):
            if recipe_id in other.get("side_ids", []):
                await self.recipes.async_update_item(
                    other["id"], {"side_ids": [i for i in other["side_ids"] if i != recipe_id]}
                )
        await self.recipes.async_delete_item(recipe_id)
        await self.async_refresh()

    # ------------------------------------------------------------ ingredients

    async def async_save_ingredient(
        self, ingredient_id: str | None, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Create or rename/update a catalog ingredient."""
        try:
            if ingredient_id is None:
                item = await self.ingredients.async_create_item(data)
            else:
                if ingredient_id not in self.ingredients.data:
                    raise MealPlannerError("not_found", "Zutat nicht gefunden")
                item = await self.ingredients.async_update_item(ingredient_id, data)
        except vol.Invalid as err:
            if str(err) == "ingredient_exists":
                raise MealPlannerError(
                    "ingredient_exists", "Diese Zutat gibt es bereits."
                ) from err
            raise
        await self.async_refresh()
        return item

    def find_similar_ingredients(
        self, name: str, exclude_id: str | None = None
    ) -> dict[str, Any]:
        """Duplicate check the card runs before creating/renaming an ingredient.

        ``exact`` is a catalog entry with the same name (ignoring case);
        ``similar`` are other entries that differ only in spelling/plural.
        """
        exact = self.ingredients.find_by_name(name)
        if exact is not None and exact["id"] == exclude_id:
            exact = None
        similar = [
            item
            for item in self.ingredients.find_similar(name, exclude_id)
            if exact is None or item["id"] != exact["id"]
        ]
        brief = lambda item: {"id": item["id"], "name": item["name"]}  # noqa: E731
        return {
            "exact": brief(exact) if exact else None,
            "similar": [brief(item) for item in similar],
        }

    async def async_merge_ingredients(self, source_id: str, target_id: str) -> None:
        """Fold ``source`` into ``target``: recipes switch over, source is deleted.

        Lines of the target that end up with the same unit within one recipe are
        added together ("nach Bedarf" lines are merged among themselves).
        """
        if source_id == target_id:
            raise MealPlannerError("invalid_merge", "Quelle und Ziel müssen verschieden sein")
        source = self.ingredients.data.get(source_id)
        target = self.ingredients.data.get(target_id)
        if source is None or target is None:
            raise MealPlannerError("not_found", "Zutat nicht gefunden")
        for recipe in list(self.recipes.data.values()):
            lines = recipe.get("ingredients", [])
            if any(line["ingredient_id"] == source_id for line in lines):
                await self.recipes.async_update_item(
                    recipe["id"],
                    {"ingredients": _merge_lines(lines, source_id, target_id)},
                )
        for record in list(self.transfers.data.values()):
            if any(line["ingredient_id"] == source_id for line in record["lines"]):
                await self.transfers.async_update_item(
                    record["id"], {"lines": _merge_transfer_lines(record["lines"], source_id, target_id)}
                )
        if target.get("default_unit") is None and source.get("default_unit"):
            await self.ingredients.async_update_item(
                target_id, {"default_unit": source["default_unit"]}
            )
        await self.ingredients.async_delete_item(source_id)
        await self.async_refresh()

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

    def find_recipe(self, reference: str) -> dict[str, Any]:
        """Resolve a recipe by id or (case-insensitive) name; services use names."""
        ref = reference.strip()
        recipe = self.recipes.data.get(ref)
        if recipe is None:
            wanted = nfc(ref).casefold()
            recipe = next(
                (r for r in self.recipes.data.values() if nfc(r["name"]).casefold() == wanted),
                None,
            )
        if recipe is None:
            raise MealPlannerError("not_found", "Rezept nicht gefunden")
        return recipe

    async def _async_add_meal(
        self,
        date: str,
        *,
        recipe_id: str | None = None,
        text: str | None = None,
        portions: int | None = None,
        side_ids: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Add one entry to a day without refreshing; ``None`` if the recipe is already there."""
        day = iso_date(date)
        if day < self.today().isoformat():
            raise MealPlannerError("date_in_past", "Vergangene Tage lassen sich nicht mehr planen")
        if (recipe_id is None) == (text is None):
            raise vol.Invalid("exactly one of recipe_id or text is required")
        sides = list(dict.fromkeys(side_ids or []))
        if sides and recipe_id is None:
            raise MealPlannerError(
                "sides_need_recipe", "Beilagen lassen sich nur an ein Rezept hängen"
            )
        if recipe_id is not None:
            if recipe_id not in self.recipes.data:
                raise MealPlannerError("not_found", "Rezept nicht gefunden")
            self._check_sides(recipe_id, sides)
            if any(e.get("recipe_id") == recipe_id for e in self.plan.get_by_date(day)):
                return None
        return await self.plan.async_create_item(
            {
                "date": day,
                "kind": PLAN_KIND_RECIPE if recipe_id is not None else PLAN_KIND_TEXT,
                "recipe_id": recipe_id,
                "text": text,
                "portions": portions,
                "side_ids": sides,
            }
        )

    async def async_add_meal(
        self,
        date: str,
        *,
        recipe_id: str | None = None,
        text: str | None = None,
        portions: int | None = None,
        side_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add a dish (recipe or free text) to a day; a day may hold several.

        ``side_ids`` hang side dishes at a recipe dish; they follow its portions.
        """
        entry = await self._async_add_meal(
            date, recipe_id=recipe_id, text=text, portions=portions, side_ids=side_ids
        )
        if entry is None:
            raise MealPlannerError("recipe_already_planned", "Das Rezept steht an diesem Tag bereits im Plan")
        await self.async_refresh()
        return self._entry_view(entry)

    async def async_remove_meal(self, entry_id: str) -> None:
        """Remove one dish; past days stay untouched (history, last_planned)."""
        entry = self.plan.data.get(entry_id)
        if entry is None:
            raise MealPlannerError("not_found", "Eintrag nicht gefunden")
        if entry["date"] < self.today().isoformat():
            raise MealPlannerError("date_in_past", "Vergangene Tage lassen sich nicht mehr ändern")
        await self.plan.async_delete_item(entry_id)
        await self.async_refresh()

    async def async_set_portions(self, entry_id: str, portions: int | None) -> None:
        """Change the portions of a recipe dish (``None`` = household default)."""
        entry = self.plan.data.get(entry_id)
        if entry is None:
            raise MealPlannerError("not_found", "Eintrag nicht gefunden")
        if entry["kind"] != PLAN_KIND_RECIPE:
            raise MealPlannerError("not_a_recipe", "Nur Rezepte haben Portionen")
        if entry["date"] < self.today().isoformat():
            raise MealPlannerError("date_in_past", "Vergangene Tage lassen sich nicht mehr ändern")
        await self.plan.async_update_item(entry_id, {"portions": portions})
        await self.async_refresh()

    async def async_set_sides(self, entry_id: str, side_ids: list[str]) -> None:
        """Replace the side dishes of a recipe dish (``[]`` removes them all)."""
        entry = self.plan.data.get(entry_id)
        if entry is None:
            raise MealPlannerError("not_found", "Eintrag nicht gefunden")
        if entry["kind"] != PLAN_KIND_RECIPE:
            raise MealPlannerError(
                "sides_need_recipe", "Beilagen lassen sich nur an ein Rezept hängen"
            )
        if entry["date"] < self.today().isoformat():
            raise MealPlannerError("date_in_past", "Vergangene Tage lassen sich nicht mehr ändern")
        sides = list(dict.fromkeys(side_ids))
        self._check_sides(entry["recipe_id"], sides)
        await self.plan.async_update_item(entry_id, {"side_ids": sides})
        await self.async_refresh()

    async def async_remove_recipe_from_day(self, date: str, recipe_id: str) -> None:
        """Remove a recipe from a day by recipe (service convenience)."""
        day = iso_date(date)
        entry = next((e for e in self.plan.get_by_date(day) if e.get("recipe_id") == recipe_id), None)
        if entry is None:
            raise MealPlannerError("not_found", "Das Rezept steht an diesem Tag nicht im Plan")
        await self.async_remove_meal(entry["id"])

    async def async_remove_text_from_day(self, date: str, text: str) -> None:
        """Remove a free-text dish from a day by its text (service convenience)."""
        day = iso_date(date)
        wanted = nfc(text).strip().casefold()
        entry = next(
            (
                e
                for e in self.plan.get_by_date(day)
                if e["kind"] == PLAN_KIND_TEXT and nfc(e["text"]).casefold() == wanted
            ),
            None,
        )
        if entry is None:
            raise MealPlannerError("not_found", "Der Eintrag steht an diesem Tag nicht im Plan")
        await self.async_remove_meal(entry["id"])

    async def async_move_day(self, from_date: str, to_date: str) -> int:
        """Swap two days completely; an empty target simply takes the dishes over.

        All dishes (with their sides and portions) of both days change places.
        Returns how many entries were moved.
        """
        source = iso_date(from_date)
        target = iso_date(to_date)
        if source == target:
            return 0
        today = self.today().isoformat()
        if source < today or target < today:
            raise MealPlannerError("date_in_past", "Vergangene Tage lassen sich nicht mehr ändern")
        first = list(self.plan.get_by_date(source))
        second = list(self.plan.get_by_date(target))
        if not first and not second:
            return 0
        for entry in first:
            await self.plan.async_update_item(entry["id"], {"date": target})
        for entry in second:
            await self.plan.async_update_item(entry["id"], {"date": source})
        await self.async_refresh()
        return len(first) + len(second)

    async def async_clear_plan(self, date: str) -> int:
        """Remove every dish of one day; returns how many were removed."""
        day = iso_date(date)
        entries = self.plan.get_by_date(day)
        if entries and day < self.today().isoformat():
            raise MealPlannerError("date_in_past", "Vergangene Tage lassen sich nicht mehr ändern")
        for entry in entries:
            await self.plan.async_delete_item(entry["id"])
        if entries:
            await self.async_refresh()
        return len(entries)

    def get_plan(self, start: str | None, end: str | None) -> list[dict[str, Any]]:
        """The plan per day for a range (default: today + the plan window)."""
        today = self.today()
        first = date_cls.fromisoformat(iso_date(start)) if start else today
        last = (
            date_cls.fromisoformat(iso_date(end))
            if end
            else first + timedelta(days=self.horizon_days - 1)
        )
        if last < first:
            raise MealPlannerError("invalid_range", "Das Ende liegt vor dem Beginn")
        if (last - first).days + 1 > MAX_SHOPPING_DAYS:
            raise MealPlannerError(
                "range_too_long", f"Höchstens {MAX_SHOPPING_DAYS} Tage auf einmal"
            )
        days = []
        for day in self._days(first, last):
            meals = []
            for entry in self.plan.get_by_date(day):
                view = self._entry_view(entry)
                recipe = self.recipes.data.get(entry.get("recipe_id") or "")
                view["name"] = recipe["name"] if recipe else entry.get("text")
                view["sides"] = [
                    self.recipes.data[sid]["name"]
                    for sid in view["side_ids"]
                    if sid in self.recipes.data
                ]
                meals.append(view)
            days.append({"date": day, "meals": meals})
        return days

    # ------------------------------------------------------------ suggestions

    def suggest_meals(
        self,
        dates: list[str] | None = None,
        exclude_recipe_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Propose recipes for days without a dish (nothing is stored).

        Without ``dates`` the days of the plan window that have no entry yet are
        filled; with ``dates`` exactly those days are proposed for (also when
        they already hold something - the user asked for them, e.g. to re-roll
        one proposal). Recipes are taken from the collection, longest-not-planned
        first (never planned = first); ties are broken randomly so a re-roll can
        differ. A recipe is proposed at most once, and never when it is already
        planned within the window or listed in ``exclude_recipe_ids`` (the card
        passes the proposals it currently shows). Days for which no recipe is
        left are returned as ``unfilled``.

        Only main dishes are proposed. To each proposal one of the side dishes
        deposited at the recipe is added (the one planned longest ago; a side
        dish already proposed for another day is only reused when no other is
        left); a recipe without deposited side dishes is proposed alone.
        """
        today = self.today()
        window_end = today + timedelta(days=self.horizon_days - 1)
        window = self._days(today, window_end)
        if dates is None:
            targets = [day for day in window if not self.plan.get_by_date(day)]
        else:
            targets = sorted({iso_date(day) for day in dates})
            if len(targets) > MAX_SHOPPING_DAYS:
                raise MealPlannerError(
                    "range_too_long", f"Höchstens {MAX_SHOPPING_DAYS} Tage auf einmal"
                )
            if targets and targets[0] < today.isoformat():
                raise MealPlannerError(
                    "date_in_past", "Für vergangene Tage gibt es keine Vorschläge"
                )
        blocked: set[str] = set()
        for e in self.plan.data.values():
            if e["kind"] == PLAN_KIND_RECIPE and window[0] <= e["date"] <= window[-1]:
                blocked.add(e["recipe_id"])
                blocked.update(e.get("side_ids", []))
        blocked.update(exclude_recipe_ids or [])
        candidates = [
            r
            for r in self.recipes.data.values()
            if r["id"] not in blocked and ROLE_MAIN in recipe_roles(r)
        ]
        random.shuffle(candidates)  # random tie-break; sort() below is stable
        candidates.sort(key=lambda r: r.get("last_planned") or "")
        suggestions = []
        unfilled = []
        used_sides: set[str] = set()
        for day in targets:
            if candidates:
                recipe = candidates.pop(0)
                sides = self._pick_side(recipe, used_sides)
                used_sides.update(side["id"] for side in sides)
                suggestions.append(
                    {
                        "date": day,
                        "recipe_id": recipe["id"],
                        "recipe_name": recipe["name"],
                        "side_ids": [side["id"] for side in sides],
                        "side_names": [side["name"] for side in sides],
                    }
                )
            else:
                unfilled.append(day)
        return {"suggestions": suggestions, "unfilled": unfilled}

    def _pick_side(self, recipe: dict[str, Any], used: set[str]) -> list[dict[str, Any]]:
        """One of the side dishes deposited at ``recipe`` (longest not planned first)."""
        options = [
            side
            for side_id in recipe.get("side_ids", [])
            if (side := self.recipes.data.get(side_id)) is not None
            and ROLE_SIDE in recipe_roles(side)
        ]
        if not options:
            return []
        fresh = [side for side in options if side["id"] not in used]
        pool = fresh or options
        random.shuffle(pool)  # random tie-break; sort() below is stable
        pool.sort(key=lambda side: side.get("last_planned") or "")
        return [pool[0]]

    async def async_apply_suggestions(self, suggestions: list[dict[str, Any]]) -> dict[str, int]:
        """Write confirmed suggestions into the plan (a recipe already on its day is skipped)."""
        for item in suggestions:  # validate everything first, then change
            day = iso_date(item["date"])
            if day < self.today().isoformat():
                raise MealPlannerError("date_in_past", "Vergangene Tage lassen sich nicht mehr planen")
            if item["recipe_id"] not in self.recipes.data:
                raise MealPlannerError("not_found", "Rezept nicht gefunden")
            self._check_sides(item["recipe_id"], list(item.get("side_ids") or []))
        added = skipped = 0
        for item in suggestions:
            entry = await self._async_add_meal(
                item["date"],
                recipe_id=item["recipe_id"],
                portions=item.get("portions"),
                side_ids=item.get("side_ids"),
            )
            if entry is None:
                skipped += 1
            else:
                added += 1
        if added:
            await self.async_refresh()
        return {"added": added, "skipped": skipped}

    # ------------------------------------------------- recipes by name (services)

    async def async_create_recipe_by_names(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create a recipe whose ingredient lines name catalog ingredients.

        Services cannot offer an id picker, so lines carry a ``name``: an
        existing catalog entry (same name, or - if exactly one - the same
        spelling variant such as Zwiebel/Zwiebeln) is reused, otherwise the
        ingredient is created. The names of newly created ones are returned.
        """
        lines = []
        created: list[str] = []
        for raw in data.get("ingredients", []):
            name = nfc(raw["name"]).strip()
            entry = self.ingredients.find_by_name(name)
            if entry is None:
                similar = self.ingredients.find_similar(name)
                entry = similar[0] if len(similar) == 1 else None
            if entry is None:
                entry = await self.ingredients.async_create_item({"name": name})
                created.append(entry["name"])
            lines.append(
                {"ingredient_id": entry["id"], "amount": raw.get("amount"), "unit": raw.get("unit")}
            )
        payload = {k: v for k, v in data.items() if k != "ingredients"}
        payload["ingredients"] = lines
        recipe = await self.async_save_recipe(None, payload)
        return {"id": recipe["id"], "name": recipe["name"], "created_ingredients": created}

    # --------------------------------------------------------------- shopping

    def _resolve_range(self, start: str | None, end: str | None) -> tuple[date_cls, date_cls]:
        """Validate the day range of a preview/transfer (default: today + horizon)."""
        today = self.today()
        first = date_cls.fromisoformat(iso_date(start)) if start else today
        first = max(first, today)  # days that have passed need no shopping
        last = (
            date_cls.fromisoformat(iso_date(end))
            if end
            else today + timedelta(days=self.horizon_days - 1)
        )
        if last < first:
            raise MealPlannerError("invalid_range", "Das Ende liegt vor dem Beginn")
        if (last - first).days + 1 > MAX_SHOPPING_DAYS:
            raise MealPlannerError(
                "range_too_long", f"Höchstens {MAX_SHOPPING_DAYS} Tage auf einmal"
            )
        return first, last

    def _days(self, first: date_cls, last: date_cls) -> list[str]:
        return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]

    def _needed_on(self, day: str) -> shopping.Amounts:
        total: shopping.Amounts = {}
        for entry in self.plan.get_by_date(day):
            recipe = self.recipes.data.get(entry["recipe_id"]) if entry.get("recipe_id") else None
            shopping.add_into(total, shopping.needed_for_entry(entry, recipe, self.household_size))
            # Side dishes follow the portions of the entry they hang at.
            for side_id in entry.get("side_ids", []):
                side = self.recipes.data.get(side_id)
                shopping.add_into(
                    total, shopping.needed_for_entry(entry, side, self.household_size)
                )
        return total

    def _transferred_on(self, day: str) -> shopping.Amounts:
        record = self.transfers.get_by_date(day)
        return shopping.lines_to_amounts(record["lines"]) if record else {}

    def _position(self, key: shopping.Key, amount: float | None) -> dict[str, Any] | None:
        ingredient = self.ingredients.data.get(key[0])
        if ingredient is None:
            return None
        unit, shown = shopping.from_base(key[1], amount)
        return {
            "key": f"{key[0]}|{key[1]}",
            "ingredient_id": key[0],
            "name": ingredient["name"],
            "amount": shown,
            "unit": unit,
        }

    def compute_shopping(self, start: str | None, end: str | None) -> dict[str, Any]:
        """What still has to be bought for the range, and what became surplus.

        ``positions`` is the summed difference between what the plan needs and
        what was already handed to Bring; ``surplus`` lists days whose plan now
        needs *less* than was transferred (Bring is never changed for those).
        """
        first, last = self._resolve_range(start, end)
        total: shopping.Amounts = {}
        surplus: list[dict[str, Any]] = []
        for day in self._days(first, last):
            to_add, extra = shopping.diff(self._needed_on(day), self._transferred_on(day))
            shopping.add_into(total, to_add)
            for key, value in extra.items():
                position = self._position(key, value)
                if position is not None:
                    surplus.append({"date": day, **position})
        positions = [p for key, value in total.items() if (p := self._position(key, value))]
        positions.sort(key=lambda p: (sort_key(p["name"]), p["unit"] or ""))
        return {
            "start": first.isoformat(),
            "end": last.isoformat(),
            "positions": positions,
            "surplus": surplus,
        }

    async def _async_bring_open_items(self) -> list[dict[str, Any]]:
        """Open (not yet bought) items of the configured Bring list."""
        entity_id = self.bring_entity
        if entity_id is None:
            raise MealPlannerError("bring_not_configured", "Keine Bring-Liste eingestellt")
        if self.hass.states.get(entity_id) is None:
            raise MealPlannerError("bring_unavailable", f"{entity_id} ist nicht verfügbar")
        try:
            response = await self.hass.services.async_call(
                "todo",
                "get_items",
                {"entity_id": entity_id, "status": "needs_action"},
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as err:
            raise MealPlannerError("bring_unavailable", f"Bring nicht erreichbar: {err}") from err
        return list((response or {}).get(entity_id, {}).get("items", []))

    async def async_shopping_preview(self, start: str | None, end: str | None) -> dict[str, Any]:
        """The editable shopping preview, each position flagged if Bring has it open."""
        result = self.compute_shopping(start, end)
        bring: dict[str, Any] = {"configured": self.bring_entity is not None, "error": None}
        on_list: set[str] = set()
        if bring["configured"]:
            try:
                on_list = {
                    similarity_key(item.get("summary", ""))
                    for item in await self._async_bring_open_items()
                }
            except MealPlannerError as err:
                bring["error"] = err.message
        for position in result["positions"]:
            position["on_list"] = similarity_key(position["name"]) in on_list
        result["bring"] = bring
        return result

    async def async_transfer_to_bring(
        self,
        start: str | None,
        end: str | None,
        items: list[dict[str, Any]] | None = None,
        extras: list[str] | None = None,
    ) -> dict[str, int]:
        """Hand the shopping list for the range over to Bring.

        ``items`` is the list the user confirmed in the preview (edited
        amounts, struck positions already removed). Without it (service call)
        the unedited positions are used, skipping those Bring already has open.
        Afterwards every day of the range counts as transferred - also what the
        user struck or skipped - so a later plan change only adds the difference.
        """
        entity_id = self.bring_entity
        if entity_id is None:
            raise MealPlannerError("bring_not_configured", "Keine Bring-Liste eingestellt")
        first, last = self._resolve_range(start, end)

        if items is None:
            preview = await self.async_shopping_preview(start, end)
            if preview["bring"]["error"]:
                raise MealPlannerError("bring_unavailable", preview["bring"]["error"])
            lines = [
                {"ingredient_id": p["ingredient_id"], "amount": p["amount"], "unit": p["unit"]}
                for p in preview["positions"]
                if not p["on_list"]
            ]
        else:
            lines = [INGREDIENT_LINE_SCHEMA(item) for item in items]
        extra_texts = [text.strip() for text in (extras or []) if text and text.strip()]

        state = self.hass.states.get(entity_id)
        if state is None:
            raise MealPlannerError("bring_unavailable", f"{entity_id} ist nicht verfügbar")
        has_description = bool(
            int(state.attributes.get("supported_features", 0))
            & TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
        )

        requests: list[dict[str, Any]] = []
        for line in lines:
            ingredient = self.ingredients.data.get(line["ingredient_id"])
            if ingredient is None:
                raise MealPlannerError("not_found", "Zutat nicht gefunden")
            quantity = shopping.format_quantity(line["amount"], line["unit"])
            request: dict[str, Any] = {"entity_id": entity_id, "item": ingredient["name"]}
            if quantity and has_description:
                request["description"] = quantity
            elif quantity:
                request["item"] = f"{ingredient['name']} ({quantity})"
            requests.append(request)
        requests.extend({"entity_id": entity_id, "item": text} for text in extra_texts)

        try:
            for request in requests:
                await self.hass.services.async_call("todo", "add_item", request, blocking=True)
        except HomeAssistantError as err:
            raise MealPlannerError(
                "bring_unavailable", f"Übertragung an Bring fehlgeschlagen: {err}"
            ) from err

        for day in self._days(first, last):
            new = shopping.raised(self._needed_on(day), self._transferred_on(day))
            record = self.transfers.get_by_date(day)
            if record is None:
                await self.transfers.async_create_item(
                    {"date": day, "lines": shopping.amounts_to_lines(new)}
                )
            else:
                await self.transfers.async_update_item(
                    record["id"], {"lines": shopping.amounts_to_lines(new)}
                )
        await self.async_refresh()
        return {"added": len(requests)}
