/*
 * meal-planner-card - Lovelace card of the Meal Planner integration.
 *
 * One card with tabs: "Plan" (rolling lunch plan) and "Rezepte" (recipes with
 * structured ingredients). Further tabs (Einkauf) follow in later stages.
 *
 * Deliberately a plain script: NO import/export statements and no syntax newer
 * than what older WebViews accept (no optional chaining, class fields, ...).
 * Home Assistant loads this file as a module on modern browsers and as a plain
 * <script> on the ones it classifies as "not latest" (es5 registration).
 *
 * Data flow: the card subscribes to "meal_planner/subscribe" and renders the
 * full snapshot it receives; every edit is a websocket command whose effect
 * arrives back through that subscription.
 */
(function () {
  "use strict";

  var CARD_VERSION = "0.1.1";
  var QUICK_TEXTS = ["Reste", "Auswärts", "Bestellen"];

  function esc(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function parseISO(iso) {
    var p = iso.split("-");
    return new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]));
  }

  function toISO(date) {
    var m = String(date.getMonth() + 1);
    var d = String(date.getDate());
    return date.getFullYear() + "-" + (m.length < 2 ? "0" + m : m) + "-" + (d.length < 2 ? "0" + d : d);
  }

  function addDays(iso, n) {
    var d = parseISO(iso);
    d.setDate(d.getDate() + n);
    return toISO(d);
  }

  function dayLabel(iso) {
    return parseISO(iso).toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit" });
  }

  function daysBetween(fromIso, toIso) {
    return Math.round((parseISO(toIso) - parseISO(fromIso)) / 86400000);
  }

  function parseAmount(text) {
    var t = String(text || "").trim().replace(",", ".");
    if (t === "") return null;
    var n = Number(t);
    return isNaN(n) ? NaN : n;
  }

  function formatAmount(n) {
    if (n === null || n === undefined) return "";
    return String(Math.round(n * 1000) / 1000).replace(".", ",");
  }

  var STYLE =
    ":host{display:block}" +
    "ha-card{padding:16px}" +
    ".tabs{display:flex;gap:4px;border-bottom:1px solid var(--divider-color);margin:-4px 0 12px}" +
    ".tab{background:none;border:none;border-bottom:3px solid transparent;padding:10px 16px;font:inherit;font-weight:500;color:var(--secondary-text-color);cursor:pointer}" +
    ".tab.active{color:var(--primary-color);border-bottom-color:var(--primary-color)}" +
    "button{font:inherit;cursor:pointer}" +
    ".btn{background:var(--primary-color);color:var(--text-primary-color,#fff);border:none;border-radius:8px;padding:8px 14px;font-weight:500}" +
    ".btn.secondary{background:none;color:var(--primary-color);border:1px solid var(--divider-color)}" +
    ".btn.danger{background:none;color:var(--error-color,#db4437);border:1px solid var(--error-color,#db4437)}" +
    ".btn.small{padding:4px 10px;font-size:.9em}" +
    ".btn[disabled]{opacity:.5;cursor:default}" +
    ".iconbtn{background:none;border:none;color:var(--secondary-text-color);font-size:1.2em;padding:4px 8px;border-radius:6px}" +
    ".iconbtn:hover{background:var(--secondary-background-color)}" +
    ".error{background:var(--error-color,#db4437);color:#fff;border-radius:8px;padding:8px 12px;margin-bottom:12px;display:flex;justify-content:space-between;gap:8px}" +
    ".muted{color:var(--secondary-text-color)}" +
    "input,select,textarea{font:inherit;color:var(--primary-text-color);background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:6px;padding:7px 9px;box-sizing:border-box;max-width:100%}" +
    "input:focus,select:focus,textarea:focus{outline:2px solid var(--primary-color);outline-offset:-1px}" +
    "textarea{width:100%;min-height:70px;resize:vertical}" +
    "label.field{display:block;margin-bottom:10px}" +
    "label.field>span{display:block;font-size:.85em;color:var(--secondary-text-color);margin-bottom:3px}" +
    ".day{display:grid;grid-template-columns:140px 1fr auto;gap:8px 12px;align-items:center;padding:8px 6px;border-bottom:1px solid var(--divider-color)}" +
    ".day.today{background:var(--secondary-background-color);border-radius:8px}" +
    ".day .date{font-weight:500}" +
    ".day .meal{min-width:0}" +
    ".day .meal .name{font-weight:500;overflow:hidden;text-overflow:ellipsis}" +
    ".day .actions{display:flex;align-items:center;gap:6px;flex-wrap:wrap;justify-content:flex-end}" +
    ".portions{display:inline-flex;align-items:center;gap:2px;border:1px solid var(--divider-color);border-radius:16px;padding:0 4px}" +
    ".portions.custom{border-color:var(--primary-color);color:var(--primary-color)}" +
    ".portions button{background:none;border:none;color:inherit;padding:2px 6px;font-size:1.1em}" +
    ".chip{display:inline-block;background:var(--secondary-background-color);border-radius:12px;padding:2px 10px;margin:0 4px 4px 0;font-size:.85em;border:none;color:var(--primary-text-color)}" +
    "button.chip{cursor:pointer}" +
    ".chip.on{background:var(--primary-color);color:var(--text-primary-color,#fff)}" +
    ".split{display:grid;grid-template-columns:minmax(240px,1fr) 2fr;gap:20px}" +
    "@media(max-width:800px){.split{grid-template-columns:1fr}.day{grid-template-columns:1fr auto}.day .date{grid-column:1/-1}}" +
    ".rlist{max-height:70vh;overflow:auto;margin-top:8px}" +
    ".ritem{display:block;width:100%;text-align:left;background:none;border:none;border-bottom:1px solid var(--divider-color);padding:9px 6px;color:var(--primary-text-color)}" +
    ".ritem:hover,.ritem.sel{background:var(--secondary-background-color)}" +
    ".ritem .sub{font-size:.85em;color:var(--secondary-text-color)}" +
    ".ing{display:grid;grid-template-columns:1fr 80px 100px auto;gap:6px;margin-bottom:6px}" +
    ".ing input,.ing select{width:100%}" +
    ".row{display:flex;gap:12px;flex-wrap:wrap}" +
    ".row>label.field{flex:1;min-width:120px}" +
    ".toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px}" +
    ".overlay{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:10;display:flex;align-items:center;justify-content:center;padding:16px}" +
    ".dialog{background:var(--card-background-color);color:var(--primary-text-color);border-radius:12px;padding:16px;width:min(560px,100%);max-height:85vh;overflow:auto}" +
    ".dialog h3{margin:0 0 10px}" +
    "ul.lines{margin:6px 0 12px;padding-left:20px}" +
    "pre.text{white-space:pre-wrap;font:inherit;margin:4px 0 12px}";

  class MealPlannerCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._snap = null;
      this._tab = "plan";
      this._error = "";
      this._editor = null; // {id, draft}
      this._selectedId = null;
      this._picker = null; // {date, search, tag, sort}
      this._search = "";
      this._tagFilter = "";
      this._confirmDelete = false;
      this._unsub = null;
      this._subscribing = false;
      this._hass = null;
      this._config = {};
      this.shadowRoot.addEventListener("click", this._onClick.bind(this));
      this.shadowRoot.addEventListener("input", this._onInput.bind(this));
      this.shadowRoot.addEventListener("change", this._onChange.bind(this));
    }

    static getStubConfig() {
      return {};
    }

    setConfig(config) {
      this._config = config || {};
      this._render();
    }

    getCardSize() {
      return 8;
    }

    set hass(hass) {
      this._hass = hass;
      if (!this._unsub && !this._subscribing && this.isConnected) this._subscribe();
    }

    connectedCallback() {
      if (this._hass && !this._unsub && !this._subscribing) this._subscribe();
      this._render();
    }

    disconnectedCallback() {
      if (this._unsub) {
        try {
          this._unsub();
        } catch (e) {
          /* connection already gone */
        }
        this._unsub = null;
      }
    }

    // ------------------------------------------------------------ transport

    _subscribe() {
      var self = this;
      this._subscribing = true;
      this._hass.connection
        .subscribeMessage(function (snapshot) {
          self._snap = snapshot;
          self._error = self._error === "not_loaded" ? "" : self._error;
          if (!self._editor && !self._picker) self._render();
        }, { type: "meal_planner/subscribe" })
        .then(function (unsub) {
          self._unsub = unsub;
          self._subscribing = false;
        })
        .catch(function () {
          self._subscribing = false;
          self._error = "not_loaded";
          self._render();
        });
    }

    _call(msg) {
      var self = this;
      return this._hass.callWS(msg).then(
        function (result) {
          self._error = "";
          return result;
        },
        function (err) {
          self._error = (err && err.message) || "Unbekannter Fehler";
          self._render();
          throw err;
        }
      );
    }

    // --------------------------------------------------------------- helpers

    _recipe(id) {
      var list = (this._snap && this._snap.recipes) || [];
      for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
      return null;
    }

    _ingredient(id) {
      var list = (this._snap && this._snap.ingredients) || [];
      for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
      return null;
    }

    _ingredientByName(name) {
      var wanted = String(name || "").trim().toLowerCase();
      var list = (this._snap && this._snap.ingredients) || [];
      for (var i = 0; i < list.length; i++) if (list[i].name.toLowerCase() === wanted) return list[i];
      return null;
    }

    _lastPlannedText(recipe) {
      if (!recipe.last_planned) return "noch nie geplant";
      var days = daysBetween(recipe.last_planned, this._snap.today);
      if (days <= 0) return "zuletzt heute";
      if (days === 1) return "zuletzt gestern";
      return "zuletzt vor " + days + " Tagen";
    }

    // ---------------------------------------------------------------- render

    _render() {
      var root = this.shadowRoot;
      var body;
      if (this._error === "not_loaded") {
        body = '<div class="error">Meal Planner ist nicht geladen. Ist die Integration eingerichtet?</div>';
      } else if (!this._snap) {
        body = '<div class="muted">Lade…</div>';
      } else {
        body =
          (this._error && !this._editor ? this._errorBanner() : "") +
          '<div class="tabs">' +
          this._tabButton("plan", "Plan") +
          this._tabButton("recipes", "Rezepte") +
          "</div>" +
          (this._tab === "plan" ? this._renderPlan() : this._renderRecipes()) +
          (this._picker ? this._renderPicker() : "");
      }
      var title = this._config.title ? "<h2 style=\"margin:0 0 8px\">" + esc(this._config.title) + "</h2>" : "";
      root.innerHTML = "<style>" + STYLE + "</style><ha-card>" + title + body + "</ha-card>" + this._datalists();
      var banner = root.getElementById("mp-error");
      if (banner && banner.scrollIntoView) banner.scrollIntoView({ block: "nearest" });
    }

    _errorBanner() {
      return '<div class="error" id="mp-error"><span>' + esc(this._error) + '</span><button class="iconbtn" data-action="dismiss-error" style="color:#fff">✕</button></div>';
    }

    _tabButton(id, label) {
      return '<button class="tab' + (this._tab === id ? " active" : "") + '" data-action="tab" data-tab="' + id + '">' + label + "</button>";
    }

    _datalists() {
      if (!this._snap) return "";
      var ing = this._snap.ingredients.map(function (i) { return '<option value="' + esc(i.name) + '"></option>'; }).join("");
      return '<datalist id="mp-ingredients">' + ing + "</datalist>";
    }

    // ------------------------------------------------------------------ plan

    _renderPlan() {
      var snap = this._snap;
      var html = '<div class="muted" style="margin-bottom:8px">Standard: ' + snap.settings.household_size + " Personen</div>";
      for (var i = 0; i < snap.settings.horizon_days; i++) {
        var iso = addDays(snap.today, i);
        var entry = snap.plan[iso];
        html += '<div class="day' + (i === 0 ? " today" : "") + '">';
        html += '<div class="date">' + esc(dayLabel(iso)) + (i === 0 ? ' <span class="muted">· heute</span>' : "") + "</div>";
        if (!entry) {
          html += '<div class="meal muted">– nichts geplant –</div>';
          html += '<div class="actions"><button class="btn small" data-action="pick" data-date="' + iso + '">Auswählen</button></div>';
        } else if (entry.kind === "text") {
          html += '<div class="meal"><span class="name">' + esc(entry.text) + "</span></div>";
          html += '<div class="actions">' + this._dayButtons(iso) + "</div>";
        } else {
          var recipe = this._recipe(entry.recipe_id);
          var portions = entry.portions || snap.settings.household_size;
          html += '<div class="meal"><span class="name">' + esc(recipe ? recipe.name : "(Rezept fehlt)") + "</span>";
          if (recipe && recipe.duration_min) html += ' <span class="muted">· ' + recipe.duration_min + " min</span>";
          html += "</div>";
          html +=
            '<div class="actions"><span class="portions' + (entry.portions ? " custom" : "") + '" title="Personen">' +
            '<button data-action="portions" data-date="' + iso + '" data-delta="-1">−</button>' +
            "<span>" + portions + " 👤</span>" +
            '<button data-action="portions" data-date="' + iso + '" data-delta="1">+</button></span>' +
            this._dayButtons(iso) + "</div>";
        }
        html += "</div>";
      }
      return html;
    }

    _dayButtons(iso) {
      return (
        '<button class="iconbtn" title="Ändern" data-action="pick" data-date="' + iso + '">✎</button>' +
        '<button class="iconbtn" title="Leeren" data-action="clear-day" data-date="' + iso + '">🗑</button>'
      );
    }

    _renderPicker() {
      var p = this._picker;
      var snap = this._snap;
      var recipes = snap.recipes.slice();
      var q = (p.search || "").toLowerCase();
      recipes = recipes.filter(function (r) {
        if (p.tag && r.tags.indexOf(p.tag) < 0) return false;
        return !q || r.name.toLowerCase().indexOf(q) >= 0;
      });
      if (p.sort === "oldest") {
        recipes.sort(function (a, b) {
          var la = a.last_planned || "0000-00-00";
          var lb = b.last_planned || "0000-00-00";
          return la < lb ? -1 : la > lb ? 1 : a.name.localeCompare(b.name, "de");
        });
      }
      var self = this;
      var html =
        '<div class="overlay" data-action="close-picker"><div class="dialog" data-stop="1">' +
        "<h3>" + esc(dayLabel(p.date)) + " – was gibt es?</h3>" +
        '<div style="margin-bottom:10px">' +
        QUICK_TEXTS.map(function (t) { return '<button class="chip" data-action="set-text" data-text="' + esc(t) + '">' + esc(t) + "</button>"; }).join("") +
        '<div class="toolbar" style="margin-top:6px"><input id="mp-free-text" placeholder="Eigener Eintrag (z. B. Kantine)" style="flex:1" />' +
        '<button class="btn small" data-action="set-free-text">Übernehmen</button></div></div>' +
        '<div class="toolbar"><input id="mp-picker-search" placeholder="Rezept suchen…" value="' + esc(p.search || "") + '" style="flex:1" />' +
        '<select id="mp-picker-sort"><option value="name"' + (p.sort === "name" ? " selected" : "") + '>Nach Name</option><option value="oldest"' + (p.sort === "oldest" ? " selected" : "") + ">Lange nicht geplant zuerst</option></select></div>";
      if (snap.tags.length) {
        html += "<div>" + snap.tags.map(function (t) { return '<button class="chip' + (p.tag === t ? " on" : "") + '" data-action="picker-tag" data-tag="' + esc(t) + '">' + esc(t) + "</button>"; }).join("") + "</div>";
      }
      html += '<div class="rlist">';
      if (!recipes.length) html += '<div class="muted" style="padding:8px">Keine Rezepte gefunden.</div>';
      recipes.forEach(function (r) {
        html +=
          '<button class="ritem" data-action="set-recipe" data-recipe="' + esc(r.id) + '"><div>' + esc(r.name) + "</div>" +
          '<div class="sub">' + esc(self._lastPlannedText(r)) + (r.duration_min ? " · " + r.duration_min + " min" : "") + (r.tags.length ? " · " + esc(r.tags.join(", ")) : "") + "</div></button>";
      });
      html += '</div><div style="text-align:right;margin-top:10px"><button class="btn secondary" data-action="close-picker">Abbrechen</button></div></div></div>';
      return html;
    }

    // --------------------------------------------------------------- recipes

    _renderRecipes() {
      var snap = this._snap;
      var q = this._search.toLowerCase();
      var tag = this._tagFilter;
      var list = snap.recipes.filter(function (r) {
        if (tag && r.tags.indexOf(tag) < 0) return false;
        return !q || r.name.toLowerCase().indexOf(q) >= 0;
      });
      var self = this;
      var left =
        '<div><div class="toolbar"><input id="mp-search" placeholder="Rezept suchen…" value="' + esc(this._search) + '" style="flex:1" />' +
        '<button class="btn" data-action="new-recipe">+ Neu</button></div>';
      if (snap.tags.length) {
        left += "<div>" + snap.tags.map(function (t) { return '<button class="chip' + (tag === t ? " on" : "") + '" data-action="filter-tag" data-tag="' + esc(t) + '">' + esc(t) + "</button>"; }).join("") + "</div>";
      }
      left += '<div class="rlist">';
      if (!list.length) left += '<div class="muted" style="padding:8px">' + (snap.recipes.length ? "Keine Treffer." : "Noch keine Rezepte. Mit „+ Neu“ geht es los.") + "</div>";
      list.forEach(function (r) {
        left +=
          '<button class="ritem' + (r.id === self._selectedId && !self._editor ? " sel" : "") + '" data-action="select-recipe" data-recipe="' + esc(r.id) + '"><div>' + esc(r.name) + "</div>" +
          '<div class="sub">' + r.ingredients.length + " Zutaten · " + esc(self._lastPlannedText(r)) + "</div></button>";
      });
      left += "</div></div>";
      var right = '<div class="muted">Rezept wählen oder neu anlegen.</div>';
      if (this._editor) right = this._renderEditor();
      else if (this._selectedId && this._recipe(this._selectedId)) right = this._renderRecipeView(this._recipe(this._selectedId));
      return '<div class="split">' + left + "<div>" + right + "</div></div>";
    }

    _lineText(line) {
      var ing = this._ingredient(line.ingredient_id);
      var name = ing ? ing.name : "?";
      if (line.amount === null || line.amount === undefined) return name + " (nach Bedarf)";
      return formatAmount(line.amount) + " " + line.unit + " " + name;
    }

    _renderRecipeView(r) {
      var self = this;
      var html = '<div class="toolbar"><h3 style="margin:0;flex:1">' + esc(r.name) + '</h3><button class="btn secondary" data-action="edit-recipe">Bearbeiten</button></div>';
      html += '<div class="muted" style="margin-bottom:8px">' + r.base_portions + " Portionen" + (r.duration_min ? " · " + r.duration_min + " min" : "") + " · " + esc(this._lastPlannedText(r)) + "</div>";
      if (r.tags.length) html += "<div>" + r.tags.map(function (t) { return '<span class="chip">' + esc(t) + "</span>"; }).join("") + "</div>";
      html += "<h4>Zutaten</h4>";
      html += r.ingredients.length ? '<ul class="lines">' + r.ingredients.map(function (l) { return "<li>" + esc(self._lineText(l)) + "</li>"; }).join("") + "</ul>" : '<div class="muted">Keine Zutaten.</div>';
      if (r.instructions) html += '<h4>Zubereitung</h4><pre class="text">' + esc(r.instructions) + "</pre>";
      if (r.notes) html += '<h4>Notiz</h4><pre class="text">' + esc(r.notes) + "</pre>";
      return html;
    }

    _newDraft() {
      return { name: "", base_portions: this._snap.settings.household_size, duration_min: "", tags: "", notes: "", instructions: "", last_planned: "", ingredients: [{ name: "", amount: "", unit: "g" }] };
    }

    _draftFrom(r) {
      var self = this;
      return {
        name: r.name,
        base_portions: r.base_portions,
        duration_min: r.duration_min === null ? "" : r.duration_min,
        tags: r.tags.join(", "),
        notes: r.notes,
        instructions: r.instructions,
        last_planned: r.last_planned || "",
        ingredients: r.ingredients.map(function (l) {
          var ing = self._ingredient(l.ingredient_id);
          return { name: ing ? ing.name : "", amount: formatAmount(l.amount), unit: l.unit || "" };
        }),
      };
    }

    _unitOptions(selected, allowEmpty) {
      var opts = allowEmpty ? '<option value=""' + (selected === "" ? " selected" : "") + ">–</option>" : "";
      return opts + this._snap.units.map(function (u) { return '<option value="' + esc(u) + '"' + (u === selected ? " selected" : "") + ">" + esc(u) + "</option>"; }).join("");
    }

    _renderEditor() {
      var d = this._editor.draft;
      var self = this;
      var isNew = !this._editor.id;
      var html = "<h3 style=\"margin:0 0 10px\">" + (isNew ? "Neues Rezept" : "Rezept bearbeiten") + "</h3>";
      html += '<label class="field"><span>Name</span><input data-f="name" style="width:100%" value="' + esc(d.name) + '" /></label>';
      html += '<div class="row">' +
        '<label class="field"><span>Basisportionen</span><input data-f="base_portions" inputmode="numeric" value="' + esc(d.base_portions) + '" /></label>' +
        '<label class="field"><span>Dauer (Minuten)</span><input data-f="duration_min" inputmode="numeric" value="' + esc(d.duration_min) + '" /></label></div>';
      html += '<label class="field"><span>Tags (kommagetrennt)</span><input data-f="tags" style="width:100%" value="' + esc(d.tags) + '" /></label>';
      if (this._snap.tags.length) html += "<div style=\"margin:-4px 0 10px\">" + this._snap.tags.map(function (t) { return '<button class="chip" data-action="add-tag" data-tag="' + esc(t) + '">+ ' + esc(t) + "</button>"; }).join("") + "</div>";
      html += "<h4 style=\"margin:8px 0 6px\">Zutaten <span class=\"muted\" style=\"font-weight:400\">(für " + esc(d.base_portions || "?") + " Portionen)</span></h4>";
      d.ingredients.forEach(function (line, i) {
        html +=
          '<div class="ing" data-line="' + i + '">' +
          '<input data-l="name" list="mp-ingredients" placeholder="Zutat" value="' + esc(line.name) + '" />' +
          '<input data-l="amount" inputmode="decimal" placeholder="Menge" value="' + esc(line.amount) + '" />' +
          '<select data-l="unit">' + self._unitOptions(line.unit, true) + "</select>" +
          '<button class="iconbtn" title="Entfernen" data-action="remove-line" data-line="' + i + '">✕</button></div>';
      });
      html += '<button class="btn secondary small" data-action="add-line">+ Zutat</button>';
      html += '<div class="muted" style="font-size:.85em;margin:4px 0 12px">Menge und Einheit leer lassen = „nach Bedarf“ (z. B. Salz).</div>';
      html += '<label class="field"><span>Zubereitung</span><textarea data-f="instructions">' + esc(d.instructions) + "</textarea></label>";
      html += '<label class="field"><span>Notiz / Link</span><textarea data-f="notes">' + esc(d.notes) + "</textarea></label>";
      if (!isNew) html += '<label class="field"><span>Zuletzt geplant (JJJJ-MM-TT, leer = noch nie)</span><input data-f="last_planned" value="' + esc(d.last_planned) + '" /></label>';
      if (this._error) html += '<div style="margin-top:12px">' + this._errorBanner() + "</div>";
      html += '<div class="toolbar" style="margin-top:12px"><button class="btn" data-action="save-recipe">Speichern</button><button class="btn secondary" data-action="cancel-edit">Abbrechen</button>';
      if (!isNew) html += '<span style="flex:1"></span>' + (this._confirmDelete ? '<button class="btn danger" data-action="delete-recipe">Wirklich löschen?</button>' : '<button class="btn danger" data-action="ask-delete">Löschen</button>');
      html += "</div>";
      return html;
    }

    // ----------------------------------------------------------- DOM → draft

    _syncDraft() {
      if (!this._editor) return;
      var d = this._editor.draft;
      var root = this.shadowRoot;
      var fields = root.querySelectorAll("[data-f]");
      for (var i = 0; i < fields.length; i++) d[fields[i].getAttribute("data-f")] = fields[i].value;
      var rows = root.querySelectorAll(".ing");
      var lines = [];
      for (var j = 0; j < rows.length; j++) {
        lines.push({
          name: rows[j].querySelector('[data-l="name"]').value,
          amount: rows[j].querySelector('[data-l="amount"]').value,
          unit: rows[j].querySelector('[data-l="unit"]').value,
        });
      }
      d.ingredients = lines;
    }

    _buildRecipePayload() {
      var d = this._editor.draft;
      var name = String(d.name || "").trim();
      if (!name) throw new Error("Bitte einen Namen eingeben.");
      var portions = parseInt(d.base_portions, 10);
      if (!(portions >= 1)) throw new Error("Basisportionen müssen mindestens 1 sein.");
      var duration = String(d.duration_min).trim() === "" ? null : parseInt(d.duration_min, 10);
      if (duration !== null && !(duration >= 1)) throw new Error("Die Dauer muss eine Zahl in Minuten sein.");
      var lines = [];
      for (var i = 0; i < d.ingredients.length; i++) {
        var l = d.ingredients[i];
        var lname = String(l.name || "").trim();
        var amount = parseAmount(l.amount);
        var unit = l.unit || null;
        if (!lname && amount === null && !unit) continue; // empty row
        if (!lname) throw new Error("Zutat " + (i + 1) + ": Name fehlt.");
        if (isNaN(amount)) throw new Error("Zutat „" + lname + "“: Menge ist keine Zahl.");
        if (amount !== null && amount <= 0) throw new Error("Zutat „" + lname + "“: Menge muss größer als 0 sein.");
        if (amount !== null && !unit) throw new Error("Zutat „" + lname + "“: Einheit fehlt.");
        if (amount === null && unit) throw new Error("Zutat „" + lname + "“: Menge fehlt.");
        lines.push({ name: lname, amount: amount, unit: unit });
      }
      var payload = {
        name: name,
        base_portions: portions,
        duration_min: duration,
        tags: String(d.tags || "").split(",").map(function (t) { return t.trim(); }).filter(function (t) { return t; }),
        notes: d.notes || "",
        instructions: d.instructions || "",
        ingredients: lines,
      };
      if (this._editor.id) {
        var lp = String(d.last_planned || "").trim();
        if (lp !== "" && !/^\d{4}-\d{2}-\d{2}$/.test(lp)) throw new Error("„Zuletzt geplant“ bitte als JJJJ-MM-TT eingeben.");
        payload.last_planned = lp === "" ? null : lp;
      }
      return payload;
    }

    // ---------------------------------------------------------------- events

    _onInput(ev) {
      var t = ev.target;
      if (t.id === "mp-search") {
        this._search = t.value;
        this._rerenderKeepingFocus("mp-search");
      } else if (t.id === "mp-picker-search") {
        this._picker.search = t.value;
        this._rerenderKeepingFocus("mp-picker-search");
      }
    }

    _rerenderKeepingFocus(id) {
      var el = this.shadowRoot.getElementById(id);
      var pos = el ? el.selectionStart : null;
      this._render();
      var again = this.shadowRoot.getElementById(id);
      if (again) {
        again.focus();
        if (pos !== null) again.setSelectionRange(pos, pos);
      }
    }

    _onChange(ev) {
      var t = ev.target;
      if (t.id === "mp-picker-sort") {
        this._picker.sort = t.value;
        this._render();
        return;
      }
      var kind = t.getAttribute ? t.getAttribute("data-l") : null;
      var row = kind && t.closest ? t.closest(".ing") : null;
      if (kind === "unit" && row) row.setAttribute("data-unit-touched", "1");
      // Picking a known ingredient pre-fills its usual unit (unless the unit was chosen by hand).
      if (kind === "name" && row && !row.getAttribute("data-unit-touched")) {
        var known = this._ingredientByName(t.value);
        var unitSel = row.querySelector('[data-l="unit"]');
        if (known && known.default_unit && unitSel) unitSel.value = known.default_unit;
      }
    }

    _onClick(ev) {
      var target = ev.target;
      if (target.closest && target.closest("[data-stop]") && !target.closest("[data-action]")) return;
      var el = target.closest ? target.closest("[data-action]") : null;
      if (!el) return;
      // Clicking the dark backdrop closes the picker; clicks inside the dialog must not.
      if (el.getAttribute("data-action") === "close-picker" && el.classList.contains("overlay") && target !== el) return;
      var action = el.getAttribute("data-action");
      var self = this;
      var date = el.getAttribute("data-date");
      switch (action) {
        case "tab":
          this._tab = el.getAttribute("data-tab");
          this._render();
          break;
        case "dismiss-error":
          this._error = "";
          this._render();
          break;
        case "pick":
          this._picker = { date: date, search: "", tag: "", sort: "name" };
          this._render();
          break;
        case "close-picker":
          this._picker = null;
          this._render();
          break;
        case "picker-tag":
          this._picker.tag = this._picker.tag === el.getAttribute("data-tag") ? "" : el.getAttribute("data-tag");
          this._render();
          break;
        case "set-recipe":
          this._setPlan({ date: this._picker.date, recipe_id: el.getAttribute("data-recipe") });
          break;
        case "set-text":
          this._setPlan({ date: this._picker.date, text: el.getAttribute("data-text") });
          break;
        case "set-free-text": {
          var input = this.shadowRoot.getElementById("mp-free-text");
          var text = input ? input.value.trim() : "";
          if (text) this._setPlan({ date: this._picker.date, text: text });
          break;
        }
        case "clear-day":
          this._call({ type: "meal_planner/plan/clear", date: date }).catch(function () {});
          break;
        case "portions": {
          var entry = this._snap.plan[date];
          if (!entry || entry.kind !== "recipe") break;
          var current = entry.portions || this._snap.settings.household_size;
          var next = Math.min(50, Math.max(1, current + Number(el.getAttribute("data-delta"))));
          var override = next === this._snap.settings.household_size ? null : next;
          this._call({ type: "meal_planner/plan/set", date: date, recipe_id: entry.recipe_id, portions: override }).catch(function () {});
          break;
        }
        case "filter-tag":
          this._tagFilter = this._tagFilter === el.getAttribute("data-tag") ? "" : el.getAttribute("data-tag");
          this._render();
          break;
        case "select-recipe":
          this._selectedId = el.getAttribute("data-recipe");
          this._editor = null;
          this._confirmDelete = false;
          this._render();
          break;
        case "new-recipe":
          this._editor = { id: null, draft: this._newDraft() };
          this._selectedId = null;
          this._confirmDelete = false;
          this._render();
          break;
        case "edit-recipe":
          this._editor = { id: this._selectedId, draft: this._draftFrom(this._recipe(this._selectedId)) };
          this._confirmDelete = false;
          this._render();
          break;
        case "cancel-edit":
          this._editor = null;
          this._confirmDelete = false;
          this._render();
          break;
        case "add-line":
          this._syncDraft();
          this._editor.draft.ingredients.push({ name: "", amount: "", unit: "g" });
          this._render();
          break;
        case "remove-line":
          this._syncDraft();
          this._editor.draft.ingredients.splice(Number(el.getAttribute("data-line")), 1);
          if (!this._editor.draft.ingredients.length) this._editor.draft.ingredients.push({ name: "", amount: "", unit: "g" });
          this._render();
          break;
        case "add-tag": {
          this._syncDraft();
          var tags = String(this._editor.draft.tags || "").split(",").map(function (t) { return t.trim(); }).filter(function (t) { return t; });
          var tag = el.getAttribute("data-tag");
          if (tags.map(function (t) { return t.toLowerCase(); }).indexOf(tag.toLowerCase()) < 0) tags.push(tag);
          this._editor.draft.tags = tags.join(", ");
          this._render();
          break;
        }
        case "save-recipe":
          this._saveRecipe();
          break;
        case "ask-delete":
          this._syncDraft();
          this._confirmDelete = true;
          this._render();
          break;
        case "delete-recipe": {
          var id = this._editor.id;
          this._call({ type: "meal_planner/recipe/delete", recipe_id: id }).then(function () {
            self._editor = null;
            self._selectedId = null;
            self._confirmDelete = false;
            self._render();
          }).catch(function () {
            self._confirmDelete = false;
          });
          break;
        }
        default:
          break;
      }
    }

    _setPlan(msg) {
      var self = this;
      msg.type = "meal_planner/plan/set";
      this._call(msg).then(function () {
        self._picker = null;
        self._render();
      }).catch(function () {});
    }

    _saveRecipe() {
      var self = this;
      this._syncDraft();
      var payload;
      try {
        payload = this._buildRecipePayload();
      } catch (e) {
        this._error = e.message;
        this._render();
        return;
      }
      var msg = { type: "meal_planner/recipe/save", recipe: payload };
      if (this._editor.id) msg.recipe_id = this._editor.id;
      this._call(msg).then(function (result) {
        self._selectedId = result.id;
        self._editor = null;
        self._render();
      }).catch(function () {});
    }
  }

  if (!customElements.get("meal-planner-card")) {
    customElements.define("meal-planner-card", MealPlannerCard);
  }
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "meal-planner-card",
    name: "Meal Planner",
    description: "Mittagessen planen: Rezepte und Wochenplan (v" + CARD_VERSION + ")",
  });
})();
