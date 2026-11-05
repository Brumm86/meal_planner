"""Persistent storage collections for recipes, the ingredient catalog and the plan.

Built on Home Assistant's ``helpers.collection`` so loading, saving (delayed
writes) and change notification follow the same pattern core helpers use.
Each collection owns one ``Store`` file:

* ``meal_planner.recipes``     - recipes (id = generated slug of the name)
* ``meal_planner.ingredients`` - ingredient catalog (the only place ingredient
  names live; recipes reference it by id, so one product = one entry)
* ``meal_planner.plan``        - one entry per day (id = ISO date)

Only schema validation and uniqueness rules live here; the cross-collection
logic (resolving ingredient names, refusing deletes of items still in use,
marking past plan days) is in ``coordinator.py``.
"""

from __future__ import annotations

import unicodedata
from datetime import date as date_cls
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import collection
from homeassistant.helpers.storage import Store
from homeassistant.util import slugify

from .const import (
    PLAN_KIND_RECIPE,
    PLAN_KIND_TEXT,
    STORAGE_KEY_INGREDIENTS,
    STORAGE_KEY_PLAN,
    STORAGE_KEY_RECIPES,
    STORAGE_VERSION,
    UNITS,
)


_UMLAUTS = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss"})
# Longest first: "Tomaten" -> "tomat", "Eier" -> "ei", "Zwiebeln" -> "zwiebel".
_PLURAL_SUFFIXES = ("en", "er", "n", "e", "s")


def nfc(text: str) -> str:
    """Compose umlauts: macOS/iOS keyboards may send "u" + U+0308 instead of "ü".

    Stored and compared names are always the composed form, so "Käse" typed on
    different devices is one and the same string.
    """
    return unicodedata.normalize("NFC", text)


def sort_key(name: str) -> str:
    """Alphabetical key that sorts umlauts like their base letter ("Äpfel" under A)."""
    return nfc(name).casefold().translate(_UMLAUTS)


def similarity_key(name: str) -> str:
    """Spelling-insensitive key used to spot near-duplicate ingredient names.

    Case, umlauts, punctuation/spaces and one common German plural ending are
    ignored, so "Zwiebel" / "Zwiebeln" and "Tomate" / "Tomaten" share a key.
    It is only a heuristic for *warning* the user - never for blocking.
    """
    key = "".join(ch for ch in sort_key(name) if ch.isalnum())
    for suffix in _PLURAL_SUFFIXES:
        if key.endswith(suffix) and len(key) - len(suffix) >= 2:
            return key[: -len(suffix)]
    return key


def _clean_str(max_len: int, *, min_len: int = 0) -> vol.All:
    """A stripped string with length limits."""
    return vol.All(str, nfc, vol.Strip, vol.Length(min=min_len, max=max_len))


def iso_date(value: Any) -> str:
    """Validate an ISO calendar date (YYYY-MM-DD) and return it normalised."""
    if not isinstance(value, str):
        raise vol.Invalid("date must be a string YYYY-MM-DD")
    try:
        return date_cls.fromisoformat(value).isoformat()
    except ValueError as err:
        raise vol.Invalid("date must be a string YYYY-MM-DD") from err


def _positive_amount(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as err:
        raise vol.Invalid("amount must be a number") from err
    if number <= 0:
        raise vol.Invalid("amount must be greater than 0")
    return round(number, 3)


def _validate_line(line: dict[str, Any]) -> dict[str, Any]:
    """An amount needs a unit; both empty means 'nach Bedarf' (e.g. salt)."""
    amount = line.get("amount")
    unit = line.get("unit")
    if amount is not None and unit is None:
        raise vol.Invalid("a unit is required when an amount is given")
    if amount is None and unit is not None:
        raise vol.Invalid("an amount is required when a unit is given")
    return line


INGREDIENT_LINE_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required("ingredient_id"): str,
            vol.Optional("amount", default=None): vol.Any(None, _positive_amount),
            vol.Optional("unit", default=None): vol.Any(None, vol.In(list(UNITS))),
        }
    ),
    _validate_line,
)


def _normalise_tags(tags: list[str]) -> list[str]:
    """Drop empty/duplicate tags (case-insensitive), keep the first spelling."""
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        key = tag.casefold()
        if key and key not in seen:
            seen.add(key)
            result.append(tag)
    return result


_RECIPE_FIELDS: dict[str, Any] = {
    "name": _clean_str(120, min_len=1),
    "base_portions": vol.All(vol.Coerce(int), vol.Range(min=1, max=50)),
    "ingredients": [INGREDIENT_LINE_SCHEMA],
    "notes": _clean_str(2000),
    "instructions": _clean_str(10000),
    "duration_min": vol.Any(None, vol.All(vol.Coerce(int), vol.Range(min=1, max=1440))),
    "tags": vol.All([_clean_str(40)], _normalise_tags),
    "last_planned": vol.Any(None, iso_date),
}

RECIPE_CREATE_SCHEMA = vol.Schema(
    {
        vol.Required("name"): _RECIPE_FIELDS["name"],
        vol.Required("base_portions"): _RECIPE_FIELDS["base_portions"],
        vol.Optional("ingredients", default=list): _RECIPE_FIELDS["ingredients"],
        vol.Optional("notes", default=""): _RECIPE_FIELDS["notes"],
        vol.Optional("instructions", default=""): _RECIPE_FIELDS["instructions"],
        vol.Optional("duration_min", default=None): _RECIPE_FIELDS["duration_min"],
        vol.Optional("tags", default=list): _RECIPE_FIELDS["tags"],
        vol.Optional("last_planned", default=None): _RECIPE_FIELDS["last_planned"],
    }
)
RECIPE_UPDATE_SCHEMA = vol.Schema({vol.Optional(k): v for k, v in _RECIPE_FIELDS.items()})

INGREDIENT_CREATE_SCHEMA = vol.Schema(
    {
        vol.Required("name"): _clean_str(80, min_len=1),
        vol.Optional("default_unit", default=None): vol.Any(None, vol.In(list(UNITS))),
    }
)
INGREDIENT_UPDATE_SCHEMA = vol.Schema(
    {
        vol.Optional("name"): _clean_str(80, min_len=1),
        vol.Optional("default_unit"): vol.Any(None, vol.In(list(UNITS))),
    }
)


def _validate_plan_item(item: dict[str, Any]) -> dict[str, Any]:
    """A day is either a recipe or a free-text entry, never both."""
    if item["kind"] == PLAN_KIND_RECIPE:
        if not item.get("recipe_id") or item.get("text"):
            raise vol.Invalid("a recipe entry needs recipe_id and no text")
    else:
        if not item.get("text") or item.get("recipe_id"):
            raise vol.Invalid("a text entry needs text and no recipe_id")
        item["portions"] = None  # free text has no ingredients to scale
    return item


_PLAN_FIELDS: dict[str, Any] = {
    "kind": vol.In([PLAN_KIND_RECIPE, PLAN_KIND_TEXT]),
    "recipe_id": vol.Any(None, str),
    "text": vol.Any(None, _clean_str(120)),
    # None = use the household default; an int overrides it for this day.
    "portions": vol.Any(None, vol.All(vol.Coerce(int), vol.Range(min=1, max=50))),
    # Set by the coordinator once the day has passed and the recipe's
    # "last_planned" was bumped; guards against re-applying after a manual fix.
    "counted": bool,
}

PLAN_CREATE_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required("date"): iso_date,
            vol.Required("kind"): _PLAN_FIELDS["kind"],
            vol.Optional("recipe_id", default=None): _PLAN_FIELDS["recipe_id"],
            vol.Optional("text", default=None): _PLAN_FIELDS["text"],
            vol.Optional("portions", default=None): _PLAN_FIELDS["portions"],
            vol.Optional("counted", default=False): _PLAN_FIELDS["counted"],
        }
    ),
    _validate_plan_item,
)
PLAN_UPDATE_SCHEMA = vol.Schema({vol.Optional(k): v for k, v in _PLAN_FIELDS.items()})


class _MealPlannerCollection(collection.DictStorageCollection):
    """Shared plumbing: validate with the class' schemas, merge updates."""

    CREATE_SCHEMA: vol.Schema
    UPDATE_SCHEMA: vol.Schema

    async def _process_create_data(self, data: dict) -> dict:
        return self.CREATE_SCHEMA(data)

    async def _update_data(self, item: dict, update_data: dict) -> dict:
        return {**item, **self.UPDATE_SCHEMA(update_data)}


class RecipeCollection(_MealPlannerCollection):
    """Recipes."""

    CREATE_SCHEMA = RECIPE_CREATE_SCHEMA
    UPDATE_SCHEMA = RECIPE_UPDATE_SCHEMA

    def _get_suggested_id(self, info: dict) -> str:
        return slugify(info["name"]) or "rezept"


class IngredientCollection(_MealPlannerCollection):
    """Ingredient catalog; names are unique case-insensitively."""

    CREATE_SCHEMA = INGREDIENT_CREATE_SCHEMA
    UPDATE_SCHEMA = INGREDIENT_UPDATE_SCHEMA

    def find_by_name(self, name: str) -> dict[str, Any] | None:
        """Return the catalog entry whose name matches ``name`` (case-insensitive)."""
        wanted = nfc(name).strip().casefold()
        for item in self.data.values():
            if nfc(item["name"]).casefold() == wanted:
                return item
        return None

    def find_similar(self, name: str, exclude_id: str | None = None) -> list[dict[str, Any]]:
        """Catalog entries that look like ``name`` (see ``similarity_key``).

        Exact (case-insensitive) matches are returned too; callers that treat
        those separately filter them out. ``exclude_id`` skips the entry being
        renamed.
        """
        wanted = similarity_key(name)
        if not wanted:
            return []
        return sorted(
            (
                item
                for item in self.data.values()
                if item["id"] != exclude_id and similarity_key(item["name"]) == wanted
            ),
            key=lambda item: sort_key(item["name"]),
        )

    def _get_suggested_id(self, info: dict) -> str:
        return slugify(info["name"]) or "zutat"

    async def _process_create_data(self, data: dict) -> dict:
        validated = await super()._process_create_data(data)
        if self.find_by_name(validated["name"]):
            raise vol.Invalid("ingredient_exists")
        return validated

    async def _update_data(self, item: dict, update_data: dict) -> dict:
        updated = await super()._update_data(item, update_data)
        other = self.find_by_name(updated["name"])
        if other is not None and other["id"] != item["id"]:
            raise vol.Invalid("ingredient_exists")
        return updated


class PlanCollection(_MealPlannerCollection):
    """One entry per day.

    The collection id is the slugified ISO date ("2026-11-07" -> "2026_11_07",
    the IDManager always slugifies suggestions). Callers go through
    ``id_for`` / ``get_by_date`` instead of building ids by hand.
    """

    CREATE_SCHEMA = PLAN_CREATE_SCHEMA
    UPDATE_SCHEMA = PLAN_UPDATE_SCHEMA

    @staticmethod
    def id_for(date_iso: str) -> str:
        """Collection id of the entry for ``date_iso``."""
        return slugify(date_iso)

    def get_by_date(self, date_iso: str) -> dict[str, Any] | None:
        """Return the entry planned for ``date_iso``, if any."""
        return self.data.get(self.id_for(date_iso))

    def _get_suggested_id(self, info: dict) -> str:
        return self.id_for(info["date"])

    async def _update_data(self, item: dict, update_data: dict) -> dict:
        updated = await super()._update_data(item, update_data)
        # Re-run the cross-field rule on the merged result (also normalises
        # the portions of text entries); the id/date never change.
        return _validate_plan_item(updated)


async def async_load_collections(
    hass: HomeAssistant,
) -> tuple[RecipeCollection, IngredientCollection, PlanCollection]:
    """Create and load the three collections."""
    recipes = RecipeCollection(Store(hass, STORAGE_VERSION, STORAGE_KEY_RECIPES))
    ingredients = IngredientCollection(Store(hass, STORAGE_VERSION, STORAGE_KEY_INGREDIENTS))
    plan = PlanCollection(Store(hass, STORAGE_VERSION, STORAGE_KEY_PLAN))
    for coll in (recipes, ingredients, plan):
        await coll.async_load()
    return recipes, ingredients, plan
