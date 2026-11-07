# Meal Planner

Home-Assistant-Integration zur Planung des Mittagessens: Rezepte mit strukturierten Zutaten, ein fortlaufender Speiseplan und eine Einkaufsliste mit Übergabe an Bring.

## Stand: v0.6.0

- Rezepte (Name, Basisportionen, Zutaten, Dauer, Tags, Zubereitung, Notiz/Link, „zuletzt geplant“)
- **Rolle und Beilagen:** Jedes Rezept ist Hauptgericht, Beilage oder beides. An einem Hauptgericht werden die **passenden Beilagen** hinterlegt (z. B. beim Schnitzel Kartoffelgratin, Spätzle, Gemüse). Im Plan hängen sie als Beilage am Eintrag des Hauptgerichts: gemeinsam angezeigt, gemeinsam gelöscht, die Personenzahl folgt dem Hauptgericht; ihre Zutaten laufen in die Einkaufsliste. Im Plan werden nur die hinterlegten Beilagen angeboten. Es gibt keine verschachtelten Rezepte – Haupt- und Beilage bleiben eigene, flache Rezepte. Bestehende Rezepte gelten als Hauptgericht.
- **Tage verschieben (Drag and Drop):** Am Griff ⠿ vor dem Datum lässt sich ein ganzer Tag auf einen anderen ziehen (Maus und Touch). Die Tage werden komplett getauscht – alle Gerichte samt Beilagen und Portionen wechseln; ein leerer Zieltag übernimmt sie einfach. Das geht auch im Planungsfenster mit den Vorschlägen. Vergangene Tage bleiben unverändert. Websocket: `meal_planner/plan/move_day` (`from_date`, `to_date`).
- Zutatenkatalog mit Autovervollständigung und fester Einheitenliste (g, kg, ml, l, Stück, EL, TL, Bund, Packung, Dose, Zehe, Prise)
- Speiseplan als fortlaufender Kalender (Standard: 10 Tage ab heute); **pro Tag beliebig viele Gerichte** (Rezept oder Freitext wie „Reste“, „Auswärts“), Personenzahl pro Gericht überschreibbar; ein Rezept steht pro Tag höchstens einmal
- **Einkauf:** Vorschau für einen wählbaren Zeitraum, Mengen skaliert auf die Portionen und zusammengerechnet (g/kg und ml/l werden umgerechnet). Positionen lassen sich abhaken, in der Menge ändern und um eigene Einträge ergänzen; diese Änderungen gelten nur für den einen Einkauf.
- **Übergabe an Bring** auf Knopfdruck (Menge als Beschreibung des Eintrags). Zutaten, die bei Bring schon offen sind, stehen in der Vorschau unabgehakt („schon auf der Liste“).
- Pro Tag wird gemerkt, was übertragen wurde. Spätere Planänderungen ergänzen nur den Mehrbedarf; Weniger-Bedarf wird in der Karte als Hinweis angezeigt, bei Bring wird nie etwas gelöscht.
- **Vorschläge:** Die Integration schlägt aus den vorhandenen Rezepten Gerichte für die Tage des Planfensters vor, die noch leer sind (am längsten nicht geplante Rezepte zuerst, kein Rezept doppelt). Vorgeschlagen werden nur Hauptgerichte; dazu kommt eine der am Rezept hinterlegten Beilagen (die am längsten nicht geplante, innerhalb eines Vorschlags möglichst ohne Wiederholung). Ein Vorschlag wird erst nach Bestätigung in den Plan geschrieben; einzelne Tage lassen sich neu vorschlagen oder ablehnen.
- Eine Lovelace-Karte mit den Tabs **Plan**, **Rezepte**, **Zutaten** und **Einkauf**

Die Bring-Liste wird in den Optionen der Integration gewählt (Entitätsauswahl, nur `todo`-Entitäten der Bring-Integration). Ohne Auswahl ist die Übergabe deaktiviert.

### Aktionen (Services)

Rezepte werden in den Aktionen über **Name oder ID** angegeben. Aktionen mit Antwort liefern ihr Ergebnis über `response_variable`.

| Aktion | Zweck | Felder (Pflicht fett) | Antwort |
|---|---|---|---|
| `meal_planner.add_meal` | Gericht zu einem Tag hinzufügen | **`date`**, `recipe` *oder* `text`, `portions`, `sides` (Beilagen, nur mit `recipe`) | `{"id": …}` |
| `meal_planner.remove_meal` | ein Gericht eines Tages entfernen | **`date`**, `recipe` *oder* `text` | – |
| `meal_planner.clear_day` | alle Gerichte eines Tages entfernen | **`date`** | `{"removed": n}` |
| `meal_planner.get_plan` | Plan abfragen | `start_date`, `end_date` (Standard: Planfenster) | `{"days": [{"date", "meals": […]}]}` (je Gericht auch `side_ids`, `sides`) |
| `meal_planner.suggest_meals` | Vorschläge machen (speichert nichts) | `dates` (nur diese Tage), `exclude_recipes` | `{"suggestions": […], "unfilled": […]}` (je Vorschlag auch `side_ids`, `side_names`) |
| `meal_planner.apply_suggestions` | Vorschläge in den Plan übernehmen | **`suggestions`**: Liste aus `date`, `recipe`, optional `portions`, `sides` | `{"added": n, "skipped": n}` |
| `meal_planner.create_recipe` | Rezept anlegen | **`name`**, `base_portions`, `duration_min`, `tags`, `roles` (`main`, `side`; Standard `main`), `sides` (passende Beilagen), `notes`, `instructions`, `ingredients` (`name`, `amount`, `unit`) | `{"id", "name", "created_ingredients"}` |
| `meal_planner.delete_recipe` | Rezept löschen (nicht, solange es heute oder später geplant ist) | **`recipe`** | – |
| `meal_planner.transfer_to_bring` | Einkaufsliste an Bring übergeben | `start_date`, `end_date` | `{"added": n}` |

`suggest_meals` ohne `dates` schlägt für alle leeren Tage des Planfensters vor. Mit `dates` wird für genau diese Tage vorgeschlagen, auch wenn dort schon etwas steht; mit `exclude_recipes` lässt sich das bisherige Rezept beim erneuten Vorschlagen ausschließen. `sides` (bei `add_meal`, `apply_suggestions`, `create_recipe`) nimmt Namen oder IDs; jede Beilage muss das Rezept mit der Rolle `side` sein, `create_recipe` mit `sides` nur für Hauptgerichte. Zutaten in `create_recipe` werden über den Namen dem Zutatenkatalog zugeordnet (gleicher Name oder eindeutige Schreibvariante wie Zwiebel/Zwiebeln), fehlende werden angelegt und in `created_ingredients` genannt.

Beispiel (Automation):

```yaml
- action: meal_planner.suggest_meals
  response_variable: vorschlag
- repeat:
    for_each: "{{ vorschlag.suggestions }}"
    sequence:
      - action: meal_planner.add_meal
        data:
          date: "{{ repeat.item.date }}"
          recipe: "{{ repeat.item.recipe_id }}"
          sides: "{{ repeat.item.side_ids }}"
```

Folgt: Kalender und Sensoren „Mittagessen heute/morgen“.

## Installation

Ordner `custom_components/meal_planner` nach `<config>/custom_components/` kopieren (oder per HACS als benutzerdefiniertes Repository), Home Assistant neu starten, dann *Einstellungen → Geräte & Dienste → Integration hinzufügen → Meal Planner*.

Die Karte wird automatisch in jedes Dashboard eingebunden. Karte hinzufügen: `type: custom:meal-planner-card` (optional `title: Mittagessen`).

## Entwicklung

```
pip install -r requirements_test.txt
pytest
```
