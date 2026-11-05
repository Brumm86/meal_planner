# Meal Planner

Home-Assistant-Integration zur Planung des Mittagessens: Rezepte mit strukturierten Zutaten, ein fortlaufender Speiseplan und eine Einkaufsliste mit Übergabe an Bring.

## Stand: Etappe 2 (v0.3.0)

- Rezepte (Name, Basisportionen, Zutaten, Dauer, Tags, Zubereitung, Notiz/Link, „zuletzt geplant“)
- Zutatenkatalog mit Autovervollständigung und fester Einheitenliste (g, kg, ml, l, Stück, EL, TL, Bund, Packung, Dose, Zehe, Prise)
- Speiseplan als fortlaufender Kalender (Standard: 10 Tage ab heute); pro Tag ein Rezept oder ein Freitext-Eintrag („Reste“, „Auswärts“, …), Personenzahl pro Tag überschreibbar
- **Einkauf:** Vorschau für einen wählbaren Zeitraum, Mengen skaliert auf die Portionen und zusammengerechnet (g/kg und ml/l werden umgerechnet). Positionen lassen sich abhaken, in der Menge ändern und um eigene Einträge ergänzen; diese Änderungen gelten nur für den einen Einkauf.
- **Übergabe an Bring** auf Knopfdruck (Menge als Beschreibung des Eintrags). Zutaten, die bei Bring schon offen sind, stehen in der Vorschau unabgehakt („schon auf der Liste“).
- Pro Tag wird gemerkt, was übertragen wurde. Spätere Planänderungen ergänzen nur den Mehrbedarf; Weniger-Bedarf wird in der Karte als Hinweis angezeigt, bei Bring wird nie etwas gelöscht.
- Eine Lovelace-Karte mit den Tabs **Plan**, **Rezepte**, **Zutaten** und **Einkauf**

Die Bring-Liste wird in den Optionen der Integration gewählt (Entitätsauswahl, nur `todo`-Entitäten der Bring-Integration). Ohne Auswahl ist die Übergabe deaktiviert.

### Aktion `meal_planner.transfer_to_bring`

Überträgt die unveränderte Einkaufsliste an Bring (für Automationen). Optional `start_date` / `end_date` (Standard: heute bis Ende des Planfensters). Zutaten, die bei Bring schon offen sind, werden übersprungen. Antwort: `{"added": <Anzahl>}`.

Folgt: Etappe 3 (Kalender, Sensoren „Mittagessen heute/morgen“, Vorschlag pro Tag).

## Installation

Ordner `custom_components/meal_planner` nach `<config>/custom_components/` kopieren (oder per HACS als benutzerdefiniertes Repository), Home Assistant neu starten, dann *Einstellungen → Geräte & Dienste → Integration hinzufügen → Meal Planner*.

Die Karte wird automatisch in jedes Dashboard eingebunden. Karte hinzufügen: `type: custom:meal-planner-card` (optional `title: Mittagessen`).

## Entwicklung

```
pip install -r requirements_test.txt
pytest
```
