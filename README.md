# Meal Planner

Home-Assistant-Integration zur Planung des Mittagessens: Rezepte mit strukturierten Zutaten, ein fortlaufender Speiseplan und (ab Etappe 2) eine Einkaufsliste mit Übergabe an Bring.

## Stand: Etappe 1 (v0.1.2)

- Rezepte (Name, Basisportionen, Zutaten, Dauer, Tags, Zubereitung, Notiz/Link, „zuletzt geplant“)
- Zutatenkatalog mit Autovervollständigung und fester Einheitenliste (g, kg, ml, l, Stück, EL, TL, Bund, Packung, Dose, Zehe, Prise)
- Speiseplan als fortlaufender Kalender (Standard: 10 Tage ab heute); pro Tag ein Rezept oder ein Freitext-Eintrag („Reste“, „Auswärts“, …), Personenzahl pro Tag überschreibbar
- Eine Lovelace-Karte mit den Tabs **Plan** und **Rezepte**

Folgt: Etappe 2 (Einkaufsvorschau und Bring-Übertragung), Etappe 3 (Kalender, Sensoren, Vorschlag pro Tag).

## Installation

Ordner `custom_components/meal_planner` nach `<config>/custom_components/` kopieren (oder per HACS als benutzerdefiniertes Repository), Home Assistant neu starten, dann *Einstellungen → Geräte & Dienste → Integration hinzufügen → Meal Planner*.

Die Karte wird automatisch in jedes Dashboard eingebunden. Karte hinzufügen: `type: custom:meal-planner-card` (optional `title: Mittagessen`).

## Entwicklung

```
pip install -r requirements_test.txt
pytest
```
