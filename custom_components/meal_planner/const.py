"""Constants for the Meal Planner integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "meal_planner"

# Frontend card (plain script, no ES-module syntax - see __init__.py).
CARD_URL_PATH: Final = "/meal_planner_static/meal-planner-card.js"
CARD_FILENAME: Final = "meal-planner-card.js"

# Config entry (data) / options
CONF_HOUSEHOLD_SIZE: Final = "household_size"
CONF_HORIZON_DAYS: Final = "horizon_days"
CONF_BRING_ENTITY: Final = "bring_entity"

DEFAULT_HOUSEHOLD_SIZE: Final = 4
MIN_HOUSEHOLD_SIZE: Final = 1
MAX_HOUSEHOLD_SIZE: Final = 20

DEFAULT_HORIZON_DAYS: Final = 10
MIN_HORIZON_DAYS: Final = 3
MAX_HORIZON_DAYS: Final = 28

# Persistent storage (one Store per collection, see storage.py).
STORAGE_VERSION: Final = 1
STORAGE_KEY_RECIPES: Final = f"{DOMAIN}.recipes"
STORAGE_KEY_INGREDIENTS: Final = f"{DOMAIN}.ingredients"
STORAGE_KEY_PLAN: Final = f"{DOMAIN}.plan"
STORAGE_KEY_TRANSFERS: Final = f"{DOMAIN}.transfers"

# Fixed unit list (Zusammenfassung: "Zutatenkatalog mit fester Einheitenliste").
# unit -> (dimension, factor to the dimension's base unit). Units within one
# dimension convert into each other (g/kg, ml/l); every other unit is its own
# dimension, so e.g. "Stück" and "g" of the same ingredient stay two separate
# shopping-list positions.
UNITS: Final[dict[str, tuple[str, float]]] = {
    "g": ("mass", 1.0),
    "kg": ("mass", 1000.0),
    "ml": ("volume", 1.0),
    "l": ("volume", 1000.0),
    "Stück": ("piece", 1.0),
    "EL": ("tablespoon", 1.0),
    "TL": ("teaspoon", 1.0),
    "Bund": ("bunch", 1.0),
    "Packung": ("pack", 1.0),
    "Dose": ("can", 1.0),
    "Zehe": ("clove", 1.0),
    "Prise": ("pinch", 1.0),
}

# Recipe roles: a recipe can be a main dish, a side dish, or both.
ROLE_MAIN: Final = "main"
ROLE_SIDE: Final = "side"
ROLES: Final = (ROLE_MAIN, ROLE_SIDE)

# Plan entry kinds
PLAN_KIND_RECIPE: Final = "recipe"
PLAN_KIND_TEXT: Final = "text"

# Websocket commands
WS_PREFIX: Final = f"{DOMAIN}/"

# Shopping list: longest range one preview / transfer may cover.
MAX_SHOPPING_DAYS: Final = 31

# Services
SERVICE_TRANSFER_TO_BRING: Final = "transfer_to_bring"
SERVICE_ADD_MEAL: Final = "add_meal"
SERVICE_REMOVE_MEAL: Final = "remove_meal"
SERVICE_SET_PRECOOKED: Final = "set_precooked"
SERVICE_CLEAR_DAY: Final = "clear_day"
SERVICE_GET_PLAN: Final = "get_plan"
SERVICE_SUGGEST_MEALS: Final = "suggest_meals"
SERVICE_APPLY_SUGGESTIONS: Final = "apply_suggestions"
SERVICE_CREATE_RECIPE: Final = "create_recipe"
SERVICE_DELETE_RECIPE: Final = "delete_recipe"
