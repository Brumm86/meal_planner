/*
 * meal-planner-card - Lovelace card of the Meal Planner integration.
 *
 * One card with tabs: "Plan" (rolling lunch plan), "Rezepte" (recipes),
 * "Zutaten" (the ingredient catalog) and "Einkauf" (editable shopping preview
 * that is handed over to Bring). Recipes only reference catalog
 * ingredients (searchable dropdown), so one product exists exactly once.
 * A recipe can be a main dish, a side dish or both; a main dish lists the
 * side dishes that go with it, which can be hung at its plan entry.
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

  var CARD_VERSION = "0.6.0";
  var QUICK_TEXTS = ["Reste", "Auswärts", "Bestellen"];

  function esc(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // Search/compare key: case-, umlaut- and ß-insensitive ("kase" finds "Käse",
  // "strasse" finds "Straße"). Only for matching - names are shown and stored as typed.
  function fold(value) {
    var t = String(value === null || value === undefined ? "" : value).toLowerCase().replace(/ß/g, "ss");
    if (t.normalize) t = t.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    return t;
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

  function hasRole(recipe, role) {
    return !!recipe && (recipe.roles || ["main"]).indexOf(role) >= 0;
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
    ".grip{cursor:grab;touch-action:none;user-select:none;-webkit-user-select:none;padding:2px 6px;margin-right:2px;color:var(--secondary-text-color);font-size:1.1em}" +
    ".dragging{opacity:.45}.dropover{outline:2px dashed var(--primary-color);outline-offset:-2px;border-radius:8px}" +
    ".day.today{background:var(--secondary-background-color);border-radius:8px}" +
    ".day .date{font-weight:500}" +
    ".day .meals{display:flex;flex-direction:column;gap:6px;min-width:0}" +
    ".day .mealrow{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}" +
    ".day .mealrow .meal{flex:1;min-width:120px}" +
    ".day .meal{min-width:0}" +
    ".srow2{display:flex;align-items:center;gap:8px;padding:8px 6px;border-bottom:1px solid var(--divider-color)}" +
    ".srow2 .info{flex:1;min-width:0}" +
    ".srow2 .sub{font-size:.85em;color:var(--secondary-text-color)}" +
    ".day .meal .name{font-weight:500;overflow:hidden;text-overflow:ellipsis}" +
    ".day .actions{display:flex;align-items:center;gap:6px;flex-wrap:wrap;justify-content:flex-end}" +
    ".portions{display:inline-flex;align-items:center;gap:2px;border:1px solid var(--divider-color);border-radius:16px;padding:0 4px}" +
    ".portions.custom{border-color:var(--primary-color);color:var(--primary-color)}" +
    ".portions button{background:none;border:none;color:inherit;padding:2px 6px;font-size:1.1em}" +
    ".day .sides{flex-basis:100%;display:flex;flex-wrap:wrap;align-items:center;gap:2px 4px;font-size:.9em}" +
    ".day .sides .chip{margin:0}" +
    "label.check{display:flex;align-items:center;gap:8px;margin:4px 0;cursor:pointer}" +
    "label.check input{width:18px;height:18px;margin:0}" +
    ".chip{display:inline-block;background:var(--secondary-background-color);border-radius:12px;padding:2px 10px;margin:0 4px 4px 0;font-size:.85em;border:none;color:var(--primary-text-color)}" +
    "button.chip{cursor:pointer}" +
    ".chip.on{background:var(--primary-color);color:var(--text-primary-color,#fff)}" +
    ".split{display:grid;grid-template-columns:minmax(240px,1fr) 2fr;gap:20px}" +
    "@media(max-width:800px){.split{grid-template-columns:1fr}.day{grid-template-columns:1fr auto}.day .date{grid-column:1/-1}}" +
    ".rlist{max-height:70vh;overflow:auto;margin-top:8px}" +
    ".ritem{display:block;width:100%;text-align:left;background:none;border:none;border-bottom:1px solid var(--divider-color);padding:9px 6px;color:var(--primary-text-color)}" +
    ".ritem:hover,.ritem.sel{background:var(--secondary-background-color)}" +
    ".ritem .sub{font-size:.85em;color:var(--secondary-text-color)}" +
    ":host(.narrow) .split{grid-template-columns:1fr}" +
    ":host(.narrow) .day{grid-template-columns:1fr auto}" +
    ":host(.narrow) .day .date{grid-column:1/-1}" +
    ".ing{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) auto;gap:6px;margin-bottom:10px}" +
    ".ing .combo{grid-column:1/-1;position:relative}" +
    ".ing input,.ing select{width:100%;min-width:0}" +
    ".combo-list{position:absolute;left:0;right:0;top:100%;z-index:20;margin-top:2px;max-height:240px;overflow:auto;background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:8px;box-shadow:0 4px 14px rgba(0,0,0,.25)}" +
    ".combo-list[hidden]{display:none}" +
    ".copt{display:block;width:100%;text-align:left;background:none;border:none;border-bottom:1px solid var(--divider-color);padding:9px 10px;color:var(--primary-text-color)}" +
    ".copt:hover{background:var(--secondary-background-color)}" +
    ".copt.new{color:var(--primary-color);font-weight:500}" +
    ".copt .sub{font-size:.85em;color:var(--secondary-text-color)}" +
    ".combo-hint{padding:10px;font-size:.95em}" +
    ".combo-hint .btn{margin:6px 6px 0 0}" +
    ".hint{background:var(--secondary-background-color);border-radius:8px;padding:10px;margin:8px 0}" +
    ".hint .btn{margin:6px 6px 0 0}" +
    ".irow{display:flex;align-items:center;gap:6px;border-bottom:1px solid var(--divider-color);padding:6px}" +
    ".irow .info{flex:1;min-width:0}" +
    ".irow .info .sub{font-size:.85em;color:var(--secondary-text-color)}" +
    ".split>*{min-width:0}" +
    ".row{display:flex;gap:12px;flex-wrap:wrap}" +
    ".row>label.field{flex:1;min-width:120px}" +
    ".toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px}" +
    ".overlay{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:10;display:flex;align-items:center;justify-content:center;padding:16px}" +
    ".dialog{background:var(--card-background-color);color:var(--primary-text-color);border-radius:12px;padding:16px;width:min(560px,100%);max-height:85vh;overflow:auto}" +
    ".dialog h3{margin:0 0 10px}" +
    "ul.lines{margin:6px 0 12px;padding-left:20px}" +
    "pre.text{white-space:pre-wrap;font:inherit;margin:4px 0 12px}" +
    ".srow{display:grid;grid-template-columns:auto minmax(0,1fr) 90px 100px;gap:6px 10px;align-items:center;padding:6px;border-bottom:1px solid var(--divider-color)}" +
    ".srow.off .sname{text-decoration:line-through;color:var(--secondary-text-color)}" +
    ".srow input[type=checkbox]{width:20px;height:20px}" +
    ".srow input:not([type=checkbox]),.srow select{width:100%;min-width:0}" +
    ".badge{display:inline-block;margin-left:6px;font-size:.8em;color:var(--secondary-text-color);border:1px solid var(--divider-color);border-radius:10px;padding:0 8px}" +
    ".ok{background:var(--secondary-background-color);border-left:4px solid var(--primary-color);border-radius:6px;padding:8px 12px;margin-bottom:10px}";

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
      this._sugg = null; // {items: [{date, recipe_id}], unfilled: [date], rejected: [recipe_id], loading, message}
      this._search = "";
      this._tagFilter = "";
      this._confirmDelete = false;
      this._ingSearch = "";
      this._ingDialog = null; // {mode: new|edit|merge, id, name, unit, similar, force, search, target}
      this._confirmIngDelete = null;
      this._shop = null; // {start, end, positions, surplus, extras, bring, dirty, loading, message}
      this._comboBlurTimer = null;
      this._unsub = null;
      this._subscribing = false;
      this._hass = null;
      this._config = {};
      this.shadowRoot.addEventListener("click", this._onClick.bind(this));
      this.shadowRoot.addEventListener("input", this._onInput.bind(this));
      this.shadowRoot.addEventListener("change", this._onChange.bind(this));
      this.shadowRoot.addEventListener("focusin", this._onFocusIn.bind(this));
      this.shadowRoot.addEventListener("focusout", this._onFocusOut.bind(this));
      // Keep the focus in the search field while an entry of its list is clicked.
      var self = this;
      this._pressTarget = null;
      this.shadowRoot.addEventListener("mousedown", function (ev) {
        self._pressTarget = ev.target;
        if (ev.target.closest && ev.target.closest(".combo-list")) ev.preventDefault();
      });
      this.shadowRoot.addEventListener("touchstart", function (ev) {
        self._pressTarget = ev.target;
      }, { passive: true });
      // Drag and drop of whole days (mouse and touch) via the grip handle.
      this._drag = null;
      this.shadowRoot.addEventListener("pointerdown", this._dragStart.bind(this));
      this.shadowRoot.addEventListener("pointermove", this._dragMove.bind(this));
      this.shadowRoot.addEventListener("pointerup", this._dragEnd.bind(this));
      this.shadowRoot.addEventListener("pointercancel", this._dragCancel.bind(this));
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
      // Layout follows the width of the CARD (dashboard columns), not the window.
      var self = this;
      var apply = function () {
        var w = self.getBoundingClientRect().width;
        if (w) self.classList.toggle("narrow", w < 720);
      };
      apply();
      if (typeof ResizeObserver !== "undefined" && !this._ro) {
        this._ro = new ResizeObserver(apply);
        this._ro.observe(this);
      }
    }

    disconnectedCallback() {
      if (this._ro) {
        this._ro.disconnect();
        this._ro = null;
      }
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
          if (self._tab === "shop") {
            // Never throw away edits of the preview; otherwise follow plan changes.
            if (!self._shop || !self._shop.dirty) self._loadShop();
            return;
          }
          if (!self._editor && !self._picker && !self._sugg && !self._ingDialog) self._render();
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

    // A freshly created ingredient is usable at once, even if the pushed
    // snapshot (which contains it) has not been rendered yet.
    _ensureIngredient(item) {
      var list = this._snap.ingredients;
      for (var i = 0; i < list.length; i++) if (list[i].id === item.id) return;
      list.push(item);
      list.sort(function (a, b) { return fold(a.name) < fold(b.name) ? -1 : 1; });
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
          (this._error && !this._editor && !this._ingDialog ? this._errorBanner() : "") +
          '<div class="tabs">' +
          this._tabButton("plan", "Plan") +
          this._tabButton("recipes", "Rezepte") +
          this._tabButton("ingredients", "Zutaten") +
          this._tabButton("shop", "Einkauf") +
          "</div>" +
          (this._tab === "plan" ? this._renderPlan() : this._tab === "recipes" ? this._renderRecipes() : this._tab === "shop" ? this._renderShop() : this._renderIngredients()) +
          (this._picker ? this._renderPicker() : "") +
          (this._sugg ? this._renderSuggestions() : "") +
          (this._ingDialog ? this._renderIngDialog() : "");
      }
      var title = this._config.title ? "<h2 style=\"margin:0 0 8px\">" + esc(this._config.title) + "</h2>" : "";
      root.innerHTML = "<style>" + STYLE + "</style><ha-card>" + title + body + "</ha-card>";
      var banner = root.getElementById("mp-error");
      if (banner && banner.scrollIntoView) banner.scrollIntoView({ block: "nearest" });
    }

    _errorBanner() {
      return '<div class="error" id="mp-error"><span>' + esc(this._error) + '</span><button class="iconbtn" data-action="dismiss-error" style="color:#fff">✕</button></div>';
    }

    _tabButton(id, label) {
      return '<button class="tab' + (this._tab === id ? " active" : "") + '" data-action="tab" data-tab="' + id + '">' + label + "</button>";
    }

    // ------------------------------------------------------------------ plan

    _renderPlan() {
      var snap = this._snap;
      var self = this;
      var html = '<div class="toolbar"><span class="muted" style="flex:1">Standard: ' + snap.settings.household_size + ' Personen</span>' +
        '<button class="btn small" data-action="suggest-open">💡 Vorschläge für freie Tage</button></div>';
      for (var i = 0; i < snap.settings.horizon_days; i++) {
        var iso = addDays(snap.today, i);
        var entries = snap.plan[iso] || [];
        html += '<div class="day' + (i === 0 ? " today" : "") + '" data-dropdate="' + iso + '" data-dropscope="plan">';
        html += '<div class="date">' + (entries.length ? '<span class="grip" data-drag="plan" data-date="' + iso + '" title="Tag per Drag and Drop verschieben">⠿</span>' : "") + esc(dayLabel(iso)) + (i === 0 ? ' <span class="muted">· heute</span>' : "") + "</div>";
        if (!entries.length) {
          html += '<div class="meals"><div class="meal muted">– nichts geplant –</div></div>';
        } else {
          html += '<div class="meals">';
          entries.forEach(function (entry) {
            html += '<div class="mealrow">';
            if (entry.kind === "text") {
              html += '<div class="meal"><span class="name">' + esc(entry.text) + "</span></div>";
              html += '<div class="actions">' + self._mealRemove(entry) + "</div>";
            } else {
              var recipe = self._recipe(entry.recipe_id);
              var portions = entry.portions || snap.settings.household_size;
              html += '<div class="meal"><span class="name">' + esc(recipe ? recipe.name : "(Rezept fehlt)") + "</span>";
              if (recipe && recipe.duration_min) html += ' <span class="muted">· ' + recipe.duration_min + " min</span>";
              html += "</div>";
              html +=
                '<div class="actions"><span class="portions' + (entry.portions ? " custom" : "") + '" title="Personen">' +
                '<button data-action="portions" data-entry="' + esc(entry.id) + '" data-delta="-1">−</button>' +
                "<span>" + portions + " 👤</span>" +
                '<button data-action="portions" data-entry="' + esc(entry.id) + '" data-delta="1">+</button></span>' +
                self._mealRemove(entry) + "</div>";
              html += self._renderSides(entry, recipe);
            }
            html += "</div>";
          });
          html += "</div>";
        }
        html += '<div class="actions"><button class="btn small' + (entries.length ? " secondary" : "") + '" data-action="pick" data-date="' + iso + '">' + (entries.length ? "+ Gericht" : "Auswählen") + "</button>";
        if (entries.length > 1) html += '<button class="iconbtn" title="Tag leeren" data-action="clear-day" data-date="' + iso + '">🗑</button>';
        html += "</div></div>";
      }
      return html;
    }

    // Side dishes of a recipe entry: toggle chips for the side dishes deposited at
    // the recipe (on = hangs at this entry); ones attached by other means stay visible.
    _renderSides(entry, recipe) {
      var self = this;
      var attached = entry.side_ids || [];
      var offered = ((recipe && recipe.side_ids) || []).filter(function (id) { return self._recipe(id); });
      var ids = offered.slice();
      attached.forEach(function (id) { if (ids.indexOf(id) < 0 && self._recipe(id)) ids.push(id); });
      if (!ids.length) return "";
      var html = '<div class="sides"><span class="muted">Beilagen:</span>';
      ids.forEach(function (id) {
        var on = attached.indexOf(id) >= 0;
        html += '<button class="chip' + (on ? " on" : "") + '" data-action="toggle-side" data-entry="' + esc(entry.id) + '" data-side="' + esc(id) + '" title="' + (on ? "Beilage entfernen" : "Beilage dazu") + '">' + (on ? "✓ " : "+ ") + esc(self._recipe(id).name) + "</button>";
      });
      return html + "</div>";
    }

    _mealRemove(entry) {
      return '<button class="iconbtn" title="Entfernen" data-action="remove-meal" data-entry="' + esc(entry.id) + '">✕</button>';
    }

    _findEntry(id) {
      var plan = this._snap.plan;
      for (var d in plan) {
        if (!Object.prototype.hasOwnProperty.call(plan, d)) continue;
        for (var i = 0; i < plan[d].length; i++) if (plan[d][i].id === id) return plan[d][i];
      }
      return null;
    }

    // ------------------------------------------------------------ drag and drop

    _dragStart(ev) {
      var grip = ev.target && ev.target.closest ? ev.target.closest("[data-drag]") : null;
      if (!grip || (ev.pointerType === "mouse" && ev.button !== 0)) return;
      var scope = grip.getAttribute("data-drag");
      var from = grip.getAttribute("data-date");
      var row = grip.closest("[data-dropdate]");
      this._drag = { scope: scope, from: from, row: row, over: null };
      if (row) row.classList.add("dragging");
      try { grip.setPointerCapture(ev.pointerId); } catch (e) { /* not essential */ }
      ev.preventDefault();
    }

    _dropTarget(ev) {
      var d = this._drag;
      var at = this.shadowRoot.elementFromPoint ? this.shadowRoot.elementFromPoint(ev.clientX, ev.clientY) : null;
      var row = at && at.closest ? at.closest("[data-dropdate]") : null;
      if (!row || row.getAttribute("data-dropscope") !== d.scope) return null;
      return row;
    }

    _dragMove(ev) {
      var d = this._drag;
      if (!d) return;
      var row = this._dropTarget(ev);
      if (row === d.row) row = null;
      if (d.over && d.over !== row) d.over.classList.remove("dropover");
      if (row) row.classList.add("dropover");
      d.over = row;
    }

    _dragClear() {
      var d = this._drag;
      this._drag = null;
      if (!d) return null;
      if (d.row) d.row.classList.remove("dragging");
      if (d.over) d.over.classList.remove("dropover");
      return d;
    }

    _dragCancel() {
      this._dragClear();
    }

    _dragEnd(ev) {
      if (!this._drag) return;
      var row = this._dropTarget(ev);
      var d = this._dragClear();
      if (!row) return;
      var to = row.getAttribute("data-dropdate");
      if (!to || to === d.from) return;
      if (d.scope === "plan") {
        this._call({ type: "meal_planner/plan/move_day", from_date: d.from, to_date: to }).catch(function () {});
      } else {
        this._suggestMove(d.from, to);
      }
    }

    // Planning window: swap the proposals of two days, or move one onto a day without proposal.
    _suggestMove(from, to) {
      var g = this._sugg;
      if (!g) return;
      var src = null;
      var dst = null;
      g.items.forEach(function (item) {
        if (item.date === from) src = item;
        if (item.date === to) dst = item;
      });
      if (!src) return;
      if (dst) {
        dst.date = from;
      } else {
        g.unfilled = g.unfilled.filter(function (d) { return d !== to; });
        g.unfilled.push(from);
        g.unfilled.sort();
      }
      src.date = to;
      g.items.sort(function (a, b) { return a.date < b.date ? -1 : 1; });
      g.message = "";
      this._render();
    }

    // ------------------------------------------------------------ suggestions

    _renderSuggestions() {
      var g = this._sugg;
      var self = this;
      var html = '<div class="overlay" data-action="suggest-close"><div class="dialog" data-stop="1"><h3>Vorschläge für freie Tage</h3>';
      if (g.loading) {
        html += '<div class="muted">Lade…</div>';
      } else {
        if (g.message) html += '<div class="hint">' + esc(g.message) + "</div>";
        if (!g.items.length && !g.unfilled.length) html += '<div class="muted" style="padding:8px">Alle Tage im Planfenster haben schon ein Gericht.</div>';
        g.items.forEach(function (item) {
          var r = self._recipe(item.recipe_id);
          html +=
            '<div class="srow2" data-dropdate="' + item.date + '" data-dropscope="sugg"><span class="grip" data-drag="sugg" data-date="' + item.date + '" title="Vorschlag auf einen anderen Tag ziehen">⠿</span><div class="info"><div><b>' + esc(dayLabel(item.date)) + "</b> · " + esc(r ? r.name : item.recipe_name) + ((item.side_names || []).length ? " mit " + esc(item.side_names.join(", ")) : "") + "</div>" +
            '<div class="sub">' + (r ? esc(self._lastPlannedText(r)) + (r.duration_min ? " · " + r.duration_min + " min" : "") : "") + "</div></div>" +
            '<button class="iconbtn" title="Neuer Vorschlag" data-action="suggest-reroll" data-date="' + item.date + '">🎲</button>' +
            '<button class="iconbtn" title="Ablehnen" data-action="suggest-reject" data-date="' + item.date + '">✕</button>' +
            '<button class="btn small" data-action="suggest-apply" data-date="' + item.date + '">Übernehmen</button></div>';
        });
        g.unfilled.forEach(function (day) {
          html +=
            '<div class="srow2" data-dropdate="' + day + '" data-dropscope="sugg"><div class="info"><b>' + esc(dayLabel(day)) + '</b> <span class="muted">· kein Vorschlag</span></div>' +
            '<button class="btn small secondary" data-action="suggest-reroll" data-date="' + day + '">🎲 Vorschlag holen</button></div>';
        });
      }
      html += '<div style="display:flex;justify-content:space-between;gap:8px;margin-top:12px"><button class="btn secondary" data-action="suggest-close">Schließen</button>';
      html += '<button class="btn"' + (g.loading || !g.items.length ? " disabled" : "") + ' data-action="suggest-apply-all">Alle übernehmen</button></div></div></div>';
      return html;
    }

    // Everything that must not be proposed again: the shown proposals plus
    // those the user rejected or re-rolled while the dialog was open.
    _suggestIds() {
      return this._sugg.items.map(function (i) { return i.recipe_id; }).concat(this._sugg.rejected);
    }

    _suggestOpen() {
      var self = this;
      this._sugg = { items: [], unfilled: [], rejected: [], loading: true, message: "" };
      this._render();
      this._call({ type: "meal_planner/plan/suggest" }).then(function (res) {
        self._sugg.items = res.suggestions;
        self._sugg.unfilled = res.unfilled;
        self._sugg.loading = false;
        if (res.unfilled.length && res.suggestions.length) self._sugg.message = "Für " + res.unfilled.length + " Tag(e) gibt es nicht genug verschiedene Rezepte.";
        self._render();
      }).catch(function () {
        self._sugg = null;
        self._render();
      });
    }

    _suggestReroll(date) {
      var self = this;
      var g = this._sugg;
      // The current proposal for this day is excluded as well as all others shown.
      this._call({ type: "meal_planner/plan/suggest", dates: [date], exclude_recipe_ids: this._suggestIds() }).then(function (res) {
        if (!self._sugg) return;
        if (!res.suggestions.length) {
          g.message = "Kein weiteres Rezept verfügbar.";
        } else {
          g.message = "";
          g.unfilled = g.unfilled.filter(function (d) { return d !== date; });
          var found = false;
          g.items = g.items.map(function (item) {
            if (item.date !== date) return item;
            found = true;
            g.rejected.push(item.recipe_id);
            return res.suggestions[0];
          });
          if (!found) {
            g.items.push(res.suggestions[0]);
            g.items.sort(function (a, b) { return a.date < b.date ? -1 : 1; });
          }
        }
        self._render();
      }).catch(function () {});
    }

    _suggestApply(dates) {
      var self = this;
      var g = this._sugg;
      var chosen = g.items.filter(function (item) { return dates.indexOf(item.date) >= 0; });
      if (!chosen.length) return;
      var payload = chosen.map(function (item) { return { date: item.date, recipe_id: item.recipe_id, side_ids: item.side_ids || [] }; });
      this._call({ type: "meal_planner/plan/apply_suggestions", suggestions: payload }).then(function () {
        g.items = g.items.filter(function (item) { return dates.indexOf(item.date) < 0; });
        g.message = "";
        if (!g.items.length && !g.unfilled.length) self._sugg = null;
        self._render();
      }).catch(function () {});
    }

    _renderPicker() {
      var p = this._picker;
      var snap = this._snap;
      var onDay = (snap.plan[p.date] || []).map(function (e) { return e.recipe_id; });
      var recipes = snap.recipes.filter(function (r) { return onDay.indexOf(r.id) < 0 && hasRole(r, "main"); });
      var q = fold(p.search || "");
      recipes = recipes.filter(function (r) {
        if (p.tag && r.tags.indexOf(p.tag) < 0) return false;
        return !q || fold(r.name).indexOf(q) >= 0;
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
        "<h3>" + esc(dayLabel(p.date)) + " – Gericht hinzufügen</h3>" +
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

    // -------------------------------------------------------------- shopping

    _loadShop(range) {
      var self = this;
      var s = this._shop || (this._shop = { start: null, end: null, positions: [], surplus: [], extras: [], bring: null, dirty: false, loading: false, message: "" });
      if (range) {
        s.start = range.start;
        s.end = range.end;
      }
      s.loading = true;
      var msg = { type: "meal_planner/shopping/preview" };
      if (s.start) msg.start = s.start;
      if (s.end) msg.end = s.end;
      this._render();
      this._call(msg).then(function (res) {
        s.start = res.start;
        s.end = res.end;
        s.bring = res.bring;
        s.surplus = res.surplus;
        // Positions Bring already has open start unchecked ("schon auf der Liste").
        s.positions = res.positions.map(function (p) {
          return { ingredient_id: p.ingredient_id, name: p.name, amount: formatAmount(p.amount), unit: p.unit || "", on_list: p.on_list, checked: !p.on_list };
        });
        s.dirty = s.extras.length > 0;
        s.loading = false;
        self._render();
      }).catch(function () {
        s.loading = false;
        self._render();
      });
    }

    _syncShop() {
      var s = this._shop;
      if (!s) return;
      var rows = this.shadowRoot.querySelectorAll(".srow");
      for (var i = 0; i < rows.length; i++) {
        var p = s.positions[Number(rows[i].getAttribute("data-pos"))];
        if (!p) continue;
        p.checked = rows[i].querySelector('[data-s="check"]').checked;
        p.amount = rows[i].querySelector('[data-s="amount"]').value;
        p.unit = rows[i].querySelector('[data-s="unit"]').value;
      }
    }

    _shopCount() {
      var s = this._shop;
      var n = s.extras.length;
      s.positions.forEach(function (p) { if (p.checked) n++; });
      return n;
    }

    _renderShop() {
      var self = this;
      var s = this._shop;
      if (!s) {
        this._loadShop();
        return '<div class="muted">Lade…</div>';
      }
      var html = '<div class="toolbar"><label>von <input type="date" id="mp-shop-start" value="' + esc(s.start || "") + '" min="' + esc(this._snap.today) + '" /></label>' +
        '<label>bis <input type="date" id="mp-shop-end" value="' + esc(s.end || "") + '" min="' + esc(this._snap.today) + '" /></label>' +
        '<button class="btn secondary small" data-action="shop-refresh">Neu berechnen</button></div>';
      if (s.message) html += '<div class="ok">' + esc(s.message) + "</div>";
      if (s.bring && !s.bring.configured) {
        html += '<div class="hint">Es ist noch keine Bring-Liste eingestellt (Einstellungen → Geräte &amp; Dienste → Meal Planner → Konfigurieren). Ohne sie ist die Übertragung deaktiviert.</div>';
      } else if (s.bring && s.bring.error) {
        html += '<div class="hint">' + esc(s.bring.error) + " – ob eine Zutat schon auf der Liste steht, lässt sich gerade nicht prüfen.</div>";
      }
      if (s.loading && !s.positions.length) html += '<div class="muted">Berechne…</div>';
      if (!s.loading && !s.positions.length && !s.extras.length) html += '<div class="muted" style="padding:8px">Für diesen Zeitraum ist nichts mehr zu kaufen.</div>';
      s.positions.forEach(function (p, i) {
        html +=
          '<div class="srow' + (p.checked ? "" : " off") + '" data-pos="' + i + '">' +
          '<input type="checkbox" data-s="check" title="Übertragen"' + (p.checked ? " checked" : "") + " />" +
          '<div class="sname">' + esc(p.name) + (p.on_list ? '<span class="badge">schon auf der Liste</span>' : "") + "</div>" +
          '<input data-s="amount" inputmode="decimal" placeholder="Menge" value="' + esc(p.amount) + '" />' +
          '<select data-s="unit">' + self._unitOptions(p.unit, true) + "</select></div>";
      });
      s.extras.forEach(function (t, i) {
        html += '<div class="irow"><div class="info">' + esc(t) + ' <span class="badge">ergänzt</span></div><button class="iconbtn" title="Entfernen" data-action="shop-remove-extra" data-i="' + i + '">✕</button></div>';
      });
      html += '<div class="toolbar" style="margin-top:10px"><input id="mp-shop-extra" placeholder="Weitere Position ergänzen…" style="flex:1" /><button class="btn secondary small" data-action="shop-add-extra">Hinzufügen</button></div>';
      if (s.surplus.length) {
        html += '<div class="hint"><b>Nach der Übertragung geändert</b> – bei Bring bleibt es unverändert, du musst ggf. selbst streichen:<ul class="lines">' +
          s.surplus.map(function (x) {
            return "<li>" + esc(dayLabel(x.date)) + ": " + esc(x.amount === null ? x.name + " (nach Bedarf)" : formatAmount(x.amount) + " " + x.unit + " " + x.name) + "</li>";
          }).join("") + "</ul></div>";
      }
      var count = this._shopCount();
      var canSend = s.bring && s.bring.configured && !s.loading;
      html += '<div class="toolbar" style="margin-top:12px"><button class="btn" data-action="shop-transfer"' + (canSend ? "" : " disabled") + ">" +
        (count ? count + (count === 1 ? " Position" : " Positionen") + " an Bring übertragen" : "Zeitraum als erledigt markieren") + "</button></div>";
      if (!count && s.positions.length) html += '<div class="muted" style="font-size:.85em">Nichts angehakt: der Zeitraum gilt danach trotzdem als übertragen.</div>';
      return html;
    }

    _shopTransfer() {
      var self = this;
      var s = this._shop;
      this._syncShop();
      var items = [];
      try {
        s.positions.forEach(function (p) {
          if (!p.checked) return;
          var amount = parseAmount(p.amount);
          var unit = p.unit || null;
          if (isNaN(amount)) throw new Error("„" + p.name + "“: Menge ist keine Zahl.");
          if (amount !== null && amount <= 0) throw new Error("„" + p.name + "“: Menge muss größer als 0 sein.");
          if (amount !== null && !unit) throw new Error("„" + p.name + "“: Einheit fehlt.");
          if (amount === null && unit) throw new Error("„" + p.name + "“: Menge fehlt.");
          items.push({ ingredient_id: p.ingredient_id, amount: amount, unit: unit });
        });
      } catch (e) {
        this._error = e.message;
        this._render();
        return;
      }
      this._call({ type: "meal_planner/shopping/transfer", start: s.start, end: s.end, items: items, extras: s.extras }).then(function (res) {
        s.extras = [];
        s.dirty = false;
        s.message = res.added + (res.added === 1 ? " Position" : " Positionen") + " an Bring übertragen.";
        self._loadShop();
      }).catch(function () {});
    }

    // ----------------------------------------------------------- ingredients

    _unitHint(ing) {
      var used = ing.recipe_count || 0;
      return (
        (ing.default_unit ? "Standardeinheit: " + esc(ing.default_unit) : "keine Standardeinheit") +
        " · " + (used ? "in " + used + (used === 1 ? " Rezept" : " Rezepten") : "nicht verwendet")
      );
    }

    _renderIngredients() {
      var self = this;
      var q = fold(this._ingSearch);
      var all = this._snap.ingredients;
      var list = all.filter(function (i) { return !q || fold(i.name).indexOf(q) >= 0; });
      var html =
        '<div class="toolbar"><input id="mp-ing-search" placeholder="Zutat suchen…" value="' + esc(this._ingSearch) + '" style="flex:1" />' +
        '<button class="btn" data-action="ing-new">+ Neu</button></div>' +
        '<div class="muted" style="margin-bottom:6px">' + all.length + " Zutaten im Katalog</div>";
      if (!list.length) html += '<div class="muted" style="padding:8px">' + (all.length ? "Keine Treffer." : "Noch keine Zutaten. Mit „+ Neu“ geht es los.") + "</div>";
      list.forEach(function (i) {
        var remove;
        if (i.recipe_count) remove = '<button class="iconbtn" disabled title="Wird noch in Rezepten verwendet" style="opacity:.3;cursor:default">🗑</button>';
        else if (self._confirmIngDelete === i.id) remove = '<button class="btn danger small" data-action="ing-delete" data-ing="' + esc(i.id) + '">Wirklich löschen?</button>';
        else remove = '<button class="iconbtn" title="Löschen" data-action="ing-ask-delete" data-ing="' + esc(i.id) + '">🗑</button>';
        html +=
          '<div class="irow"><div class="info"><div>' + esc(i.name) + '</div><div class="sub">' + self._unitHint(i) + "</div></div>" +
          '<button class="iconbtn" title="Umbenennen / Einheit ändern" data-action="ing-edit" data-ing="' + esc(i.id) + '">✎</button>' +
          '<button class="iconbtn" title="In andere Zutat zusammenführen" data-action="ing-merge" data-ing="' + esc(i.id) + '">⇄</button>' +
          remove + "</div>";
      });
      return html;
    }

    _renderIngDialog() {
      var d = this._ingDialog;
      var html = '<div class="overlay" data-action="close-ing"><div class="dialog" data-stop="1">';
      if (d.mode === "merge") {
        var src = this._ingredient(d.id);
        var tgt = d.target ? this._ingredient(d.target) : null;
        html += "<h3>„" + esc(src ? src.name : "?") + "“ zusammenführen</h3>";
        html += '<div class="muted" style="margin-bottom:8px">Alle Rezepte mit dieser Zutat verwenden danach die gewählte Zutat; „' + esc(src ? src.name : "?") + "“ wird gelöscht.</div>";
        if (tgt) {
          var n = src ? src.recipe_count : 0;
          html +=
            '<div class="hint">„' + esc(src.name) + "“ → „" + esc(tgt.name) + "“<br>" +
            (n ? n + (n === 1 ? " Rezept wird" : " Rezepte werden") + " angepasst." : "Kein Rezept betroffen.") +
            '<br><button class="btn small" data-action="ing-merge-do">Zusammenführen</button>' +
            '<button class="btn secondary small" data-action="ing-merge-reset">Andere wählen</button></div>';
        } else {
          var q = fold(d.search || "");
          var others = this._snap.ingredients.filter(function (i) {
            return i.id !== d.id && (!q || fold(i.name).indexOf(q) >= 0);
          });
          html += '<div class="toolbar"><input id="mp-merge-search" placeholder="Ziel-Zutat suchen…" value="' + esc(d.search || "") + '" style="flex:1" /></div><div class="rlist" style="max-height:40vh">';
          if (!others.length) html += '<div class="muted" style="padding:8px">Keine Treffer.</div>';
          others.forEach(function (i) {
            html += '<button class="ritem" data-action="ing-merge-pick" data-ing="' + esc(i.id) + '"><div>' + esc(i.name) + '</div><div class="sub">' + esc(i.default_unit || "keine Standardeinheit") + "</div></button>";
          });
          html += "</div>";
        }
      } else {
        html += "<h3>" + (d.mode === "new" ? "Neue Zutat" : "Zutat bearbeiten") + "</h3>";
        html += '<label class="field"><span>Name</span><input id="mp-ing-name" style="width:100%" value="' + esc(d.name) + '" /></label>';
        html += '<label class="field"><span>Standardeinheit</span><select id="mp-ing-unit">' + this._unitOptions(d.unit || "", true) + "</select></label>";
        if (d.similar && d.similar.length) {
          html +=
            '<div class="hint">Ähnlich vorhanden: <b>' + d.similar.map(function (s) { return esc(s.name); }).join(", ") + "</b>.<br>" +
            "Nur speichern, wenn es wirklich eine andere Zutat ist." +
            '<br><button class="btn small" data-action="ing-save-force">Trotzdem speichern</button></div>';
        }
      }
      if (this._error) html += this._errorBanner();
      html += '<div style="text-align:right;margin-top:10px">';
      if (d.mode !== "merge") html += '<button class="btn" data-action="ing-save">Speichern</button> ';
      html += '<button class="btn secondary" data-action="close-ing">Abbrechen</button></div></div></div>';
      return html;
    }

    // --------------------------------------------------------------- recipes

    _renderRecipes() {
      var snap = this._snap;
      var q = fold(this._search);
      var tag = this._tagFilter;
      var list = snap.recipes.filter(function (r) {
        if (tag && r.tags.indexOf(tag) < 0) return false;
        return !q || fold(r.name).indexOf(q) >= 0;
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
          '<div class="sub">' + r.ingredients.length + " Zutaten · " + esc(self._lastPlannedText(r)) + esc(self._roleHint(r)) + "</div></button>";
      });
      left += "</div></div>";
      var right = '<div class="muted">Rezept wählen oder neu anlegen.</div>';
      if (this._editor) right = this._renderEditor();
      else if (this._selectedId && this._recipe(this._selectedId)) right = this._renderRecipeView(this._recipe(this._selectedId));
      return '<div class="split">' + left + "<div>" + right + "</div></div>";
    }

    // Only the exception is named: plain main dishes need no label.
    _roleHint(r) {
      if (hasRole(r, "side")) return hasRole(r, "main") ? " · auch Beilage" : " · Beilage";
      return "";
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
      var roleChips = (hasRole(r, "main") ? '<span class="chip">Hauptgericht</span>' : "") + (hasRole(r, "side") ? '<span class="chip">Beilage</span>' : "");
      html += "<div>" + roleChips + r.tags.map(function (t) { return '<span class="chip">' + esc(t) + "</span>"; }).join("") + "</div>";
      var sideNames = (r.side_ids || []).map(function (id) { var o = self._recipe(id); return o ? o.name : null; }).filter(function (n) { return n; });
      if (sideNames.length) html += '<div class="muted" style="margin:4px 0 8px">Passende Beilagen: ' + esc(sideNames.join(", ")) + "</div>";
      html += "<h4>Zutaten</h4>";
      html += r.ingredients.length ? '<ul class="lines">' + r.ingredients.map(function (l) { return "<li>" + esc(self._lineText(l)) + "</li>"; }).join("") + "</ul>" : '<div class="muted">Keine Zutaten.</div>';
      if (r.instructions) html += '<h4>Zubereitung</h4><pre class="text">' + esc(r.instructions) + "</pre>";
      if (r.notes) html += '<h4>Notiz</h4><pre class="text">' + esc(r.notes) + "</pre>";
      return html;
    }

    _newDraft() {
      return { name: "", base_portions: this._snap.settings.household_size, duration_min: "", tags: "", notes: "", instructions: "", last_planned: "", roles: ["main"], side_ids: [], ingredients: [this._emptyLine()] };
    }

    _emptyLine() {
      return { ingredient_id: "", name: "", amount: "", unit: "g", touched: false };
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
        roles: (r.roles || ["main"]).slice(),
        side_ids: (r.side_ids || []).slice(),
        ingredients: r.ingredients.map(function (l) {
          var ing = self._ingredient(l.ingredient_id);
          return { ingredient_id: l.ingredient_id, name: ing ? ing.name : "", amount: formatAmount(l.amount), unit: l.unit || "", touched: true };
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
      html += '<div class="field" style="margin-bottom:10px"><span class="muted" style="display:block;font-size:.85em;margin-bottom:3px">Rolle</span>' +
        '<label class="check"><input type="checkbox" data-role="main" data-action="toggle-role"' + (d.roles.indexOf("main") >= 0 ? " checked" : "") + " /> Hauptgericht</label>" +
        '<label class="check"><input type="checkbox" data-role="side" data-action="toggle-role"' + (d.roles.indexOf("side") >= 0 ? " checked" : "") + " /> Beilage</label></div>";
      if (d.roles.indexOf("main") >= 0) {
        var sideOptions = this._snap.recipes.filter(function (r) { return hasRole(r, "side") && r.id !== self._editor.id; });
        html += '<div class="field sidelist" style="margin-bottom:10px"><span class="muted" style="display:block;font-size:.85em;margin-bottom:3px">Passende Beilagen</span>';
        if (!sideOptions.length) {
          html += '<div class="muted" style="font-size:.9em">Noch keine Beilagen – Rezepte mit der Rolle „Beilage“ erscheinen hier.</div>';
        }
        sideOptions.forEach(function (r) {
          html += '<label class="check"><input type="checkbox" data-side="' + esc(r.id) + '"' + (d.side_ids.indexOf(r.id) >= 0 ? " checked" : "") + " /> " + esc(r.name) + "</label>";
        });
        html += "</div>";
      }
      html += "<h4 style=\"margin:8px 0 6px\">Zutaten <span class=\"muted\" style=\"font-weight:400\">(für " + esc(d.base_portions || "?") + " Portionen)</span></h4>";
      d.ingredients.forEach(function (line, i) {
        var chosen = line.ingredient_id ? self._ingredient(line.ingredient_id) : null;
        html +=
          '<div class="ing" data-line="' + i + '" data-ing="' + esc(chosen ? chosen.id : "") + '"' + (line.touched ? ' data-unit-touched="1"' : "") + ">" +
          '<div class="combo"><input data-l="search" autocomplete="off" placeholder="Zutat suchen oder neu anlegen…" value="' + esc(chosen ? chosen.name : line.name) + '" /><div class="combo-list" hidden></div></div>' +
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
          ingredient_id: rows[j].getAttribute("data-ing") || "",
          name: rows[j].querySelector('[data-l="search"]').value,
          amount: rows[j].querySelector('[data-l="amount"]').value,
          unit: rows[j].querySelector('[data-l="unit"]').value,
          touched: rows[j].hasAttribute("data-unit-touched"),
        });
      }
      d.ingredients = lines;
      var roleBoxes = root.querySelectorAll("[data-role]");
      if (roleBoxes.length) {
        var roles = [];
        for (var k = 0; k < roleBoxes.length; k++) if (roleBoxes[k].checked) roles.push(roleBoxes[k].getAttribute("data-role"));
        d.roles = roles;
      }
      // The side list is only shown for main dishes; a hidden list keeps its old choice.
      if (root.querySelector(".sidelist")) {
        var sideBoxes = root.querySelectorAll("[data-side]");
        var sides = [];
        for (var m = 0; m < sideBoxes.length; m++) if (sideBoxes[m].checked) sides.push(sideBoxes[m].getAttribute("data-side"));
        d.side_ids = sides;
      }
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
        var typed = String(l.name || "").trim();
        var amount = parseAmount(l.amount);
        var unit = l.unit || null;
        if (!typed && !l.ingredient_id && amount === null) continue; // empty row
        var chosen = l.ingredient_id ? this._ingredient(l.ingredient_id) : null;
        if (!chosen) {
          throw new Error(
            typed
              ? "Zutat „" + typed + "“: bitte aus der Liste wählen oder über „+ Neue Zutat anlegen“ neu anlegen."
              : "Zutat " + (i + 1) + ": bitte eine Zutat auswählen."
          );
        }
        var lname = chosen.name;
        if (isNaN(amount)) throw new Error("Zutat „" + lname + "“: Menge ist keine Zahl.");
        if (amount !== null && amount <= 0) throw new Error("Zutat „" + lname + "“: Menge muss größer als 0 sein.");
        if (amount !== null && !unit) throw new Error("Zutat „" + lname + "“: Einheit fehlt.");
        if (amount === null && unit) throw new Error("Zutat „" + lname + "“: Menge fehlt.");
        lines.push({ ingredient_id: chosen.id, amount: amount, unit: unit });
      }
      if (!d.roles.length) throw new Error("Bitte mindestens eine Rolle wählen (Hauptgericht und/oder Beilage).");
      var isMain = d.roles.indexOf("main") >= 0;
      var self = this;
      var payload = {
        name: name,
        roles: d.roles,
        side_ids: isMain ? d.side_ids.filter(function (id) { return hasRole(self._recipe(id), "side"); }) : [],
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
      } else if (t.id === "mp-ing-search") {
        this._ingSearch = t.value;
        this._rerenderKeepingFocus("mp-ing-search");
      } else if (t.id === "mp-merge-search") {
        this._ingDialog.search = t.value;
        this._rerenderKeepingFocus("mp-merge-search");
      } else if (t.getAttribute && t.getAttribute("data-l") === "search") {
        // Typing changes the text, so the earlier choice no longer applies.
        var row = t.closest(".ing");
        if (row) row.setAttribute("data-ing", "");
        this._showCombo(t, t.value);
      }
    }

    // ------------------------------------------------- ingredient dropdown

    _comboOptions(query) {
      var q = fold(String(query || "").trim());
      var all = this._snap.ingredients;
      var starts = [];
      var rest = [];
      all.forEach(function (i) {
        var n = fold(i.name);
        // Also match when the typed word is longer ("Zwiebeln" finds "Zwiebel").
        var hit = !q || n.indexOf(q) >= 0 || (n.length >= 3 && q.indexOf(n) >= 0);
        if (!hit) return;
        (q && n.indexOf(q) === 0 ? starts : rest).push(i);
      });
      var html = starts.concat(rest).map(function (i) {
        return '<button type="button" class="copt" data-action="combo-pick" data-ing="' + esc(i.id) + '">' + esc(i.name) +
          (i.default_unit ? ' <span class="sub">· ' + esc(i.default_unit) + "</span>" : "") + "</button>";
      }).join("");
      var exact = all.some(function (i) { return fold(i.name) === q; });
      if (q && !exact) html += '<button type="button" class="copt new" data-action="combo-new">+ Neue Zutat „' + esc(String(query).trim()) + "“ anlegen</button>";
      if (!html) html = '<div class="combo-hint muted">Noch keine Zutaten – Namen tippen, um eine anzulegen.</div>';
      return html;
    }

    _comboHint(name, similar) {
      return (
        '<div class="combo-hint">Ähnlich vorhanden – meinst du eine davon?<br>' +
        similar.map(function (s) { return '<button type="button" class="btn small" data-action="combo-pick" data-ing="' + esc(s.id) + '">„' + esc(s.name) + "“ verwenden</button>"; }).join("") +
        '<br><button type="button" class="btn secondary small" data-action="combo-create" data-name="' + esc(name) + '">Trotzdem „' + esc(name) + "“ neu anlegen</button></div>"
      );
    }

    _showCombo(input, query) {
      var list = input.parentNode.querySelector(".combo-list");
      if (!list) return;
      list.innerHTML = this._comboOptions(query);
      list.hidden = false;
    }

    _rowFor(line) {
      return this.shadowRoot.querySelector('.ing[data-line="' + line + '"]');
    }

    _selectIngredient(row, id) {
      var ing = this._ingredient(id);
      if (!row || !ing) return;
      row.setAttribute("data-ing", ing.id);
      row.querySelector('[data-l="search"]').value = ing.name;
      // Pre-fill the usual unit unless the unit was chosen by hand.
      var unitSel = row.querySelector('[data-l="unit"]');
      if (ing.default_unit && unitSel && !row.hasAttribute("data-unit-touched")) unitSel.value = ing.default_unit;
      var list = row.querySelector(".combo-list");
      if (list) list.hidden = true;
    }

    _createIngredientFor(line, name) {
      var self = this;
      this._call({ type: "meal_planner/ingredient/save", ingredient: { name: name } })
        .then(function (res) {
          self._ensureIngredient(res.ingredient);
          self._selectIngredient(self._rowFor(line), res.id);
        })
        .catch(function () {});
    }

    _onFocusIn(ev) {
      var t = ev.target;
      if (!t.getAttribute || t.getAttribute("data-l") !== "search") return;
      clearTimeout(this._comboBlurTimer);
      if (t.select) t.select();
      // Show the whole catalog first; the list narrows once the user types.
      this._showCombo(t, "");
    }

    _onFocusOut(ev) {
      var t = ev.target;
      if (!t.getAttribute || t.getAttribute("data-l") !== "search") return;
      this._comboBlurTimer = setTimeout(function () {
        if (!t.isConnected) return;
        var list = t.parentNode.querySelector(".combo-list");
        if (list) list.hidden = true;
      }, 200);
    }

    // ------------------------------------------------- ingredient catalog tab

    _syncIngDialog() {
      var d = this._ingDialog;
      if (!d) return;
      var n = this.shadowRoot.getElementById("mp-ing-name");
      if (n) d.name = n.value;
      var u = this.shadowRoot.getElementById("mp-ing-unit");
      if (u) d.unit = u.value;
    }

    _saveIngredient(force) {
      var self = this;
      this._syncIngDialog();
      var d = this._ingDialog;
      var name = String(d.name || "").trim();
      if (!name) {
        this._error = "Bitte einen Namen eingeben.";
        this._render();
        return;
      }
      var proceed = function () {
        var msg = { type: "meal_planner/ingredient/save", ingredient: { name: name, default_unit: d.unit || null } };
        if (d.mode === "edit") msg.ingredient_id = d.id;
        self._call(msg).then(function () {
          self._ingDialog = null;
          self._render();
        }).catch(function () {});
      };
      // Only the name matters for duplicates; changing just the unit is fine.
      if (force || (d.mode === "edit" && name === d.origName)) {
        proceed();
        return;
      }
      var check = { type: "meal_planner/ingredient/similar", name: name };
      if (d.mode === "edit") check.exclude_id = d.id;
      this._call(check).then(function (res) {
        if (res.exact) {
          d.similar = null;
          self._error = "„" + res.exact.name + "“ gibt es bereits.";
          self._render();
        } else if (res.similar.length) {
          d.similar = res.similar;
          self._error = "";
          self._render();
        } else {
          proceed();
        }
      }).catch(function () {});
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
      if (t.id === "mp-shop-start" || t.id === "mp-shop-end") {
        var sa = this.shadowRoot.getElementById("mp-shop-start").value;
        var sb = this.shadowRoot.getElementById("mp-shop-end").value;
        if (sa && sb) {
          this._shop.message = "";
          this._loadShop({ start: sa, end: sb });
        }
        return;
      }
      if (t.getAttribute && t.getAttribute("data-s") && this._shop) {
        this._syncShop();
        this._shop.dirty = true;
        this._shop.message = "";
        this._render();
        return;
      }
      var kind = t.getAttribute ? t.getAttribute("data-l") : null;
      var row = kind && t.closest ? t.closest(".ing") : null;
      if (kind === "unit" && row) row.setAttribute("data-unit-touched", "1");
    }

    _onClick(ev) {
      var target = ev.target;
      if (target.closest && target.closest("[data-stop]") && !target.closest("[data-action]")) return;
      var el = target.closest ? target.closest("[data-action]") : null;
      if (!el) return;
      // Clicking the dark backdrop closes the picker; clicks inside the dialog must not.
      var act = el.getAttribute("data-action");
      if ((act === "close-picker" || act === "close-ing" || act === "suggest-close") && el.classList.contains("overlay")) {
        // Close only for a real click on the dark backdrop: press AND release on it.
        // Picking an option in a native <select> list (e.g. "Stück") that reaches
        // beyond the dialog can end with a "click" on the backdrop although the
        // press happened elsewhere - that must not close the dialog.
        var pressed = this._pressTarget;
        this._pressTarget = null;
        if (target !== el || pressed !== el) return;
      }
      var action = el.getAttribute("data-action");
      var self = this;
      var date = el.getAttribute("data-date");
      switch (action) {
        case "tab":
          this._tab = el.getAttribute("data-tab");
          if (this._tab === "shop") {
            if (this._shop) this._shop.message = "";
            this._loadShop();
          } else {
            this._render();
          }
          break;
        case "shop-refresh":
          if (this._shop) this._shop.message = "";
          this._loadShop();
          break;
        case "shop-add-extra": {
          var xin = this.shadowRoot.getElementById("mp-shop-extra");
          var xt = xin ? xin.value.trim() : "";
          if (!xt) break;
          this._syncShop();
          this._shop.extras.push(xt);
          this._shop.dirty = true;
          this._shop.message = "";
          this._render();
          break;
        }
        case "shop-remove-extra":
          this._syncShop();
          this._shop.extras.splice(Number(el.getAttribute("data-i")), 1);
          this._render();
          break;
        case "shop-transfer":
          this._shopTransfer();
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
        case "remove-meal":
          this._call({ type: "meal_planner/plan/remove", entry_id: el.getAttribute("data-entry") }).catch(function () {});
          break;
        case "portions": {
          var entry = this._findEntry(el.getAttribute("data-entry"));
          if (!entry || entry.kind !== "recipe") break;
          var current = entry.portions || this._snap.settings.household_size;
          var next = Math.min(50, Math.max(1, current + Number(el.getAttribute("data-delta"))));
          var override = next === this._snap.settings.household_size ? null : next;
          this._call({ type: "meal_planner/plan/portions", entry_id: entry.id, portions: override }).catch(function () {});
          break;
        }
        case "toggle-side": {
          var sentry = this._findEntry(el.getAttribute("data-entry"));
          if (!sentry) break;
          var sid = el.getAttribute("data-side");
          var sides = (sentry.side_ids || []).slice();
          var at = sides.indexOf(sid);
          if (at >= 0) sides.splice(at, 1);
          else sides.push(sid);
          this._call({ type: "meal_planner/plan/sides", entry_id: sentry.id, side_ids: sides }).catch(function () {});
          break;
        }
        case "toggle-role":
          this._syncDraft();
          this._render();
          break;
        case "suggest-open":
          this._suggestOpen();
          break;
        case "suggest-close":
          this._sugg = null;
          this._render();
          break;
        case "suggest-reroll":
          this._suggestReroll(date);
          break;
        case "suggest-reject":
          var rejected = this._sugg.items.filter(function (item) { return item.date === date; });
          if (rejected.length) this._sugg.rejected.push(rejected[0].recipe_id);
          this._sugg.items = this._sugg.items.filter(function (item) { return item.date !== date; });
          this._sugg.unfilled.push(date);
          this._sugg.unfilled.sort();
          this._sugg.message = "";
          this._render();
          break;
        case "suggest-apply":
          this._suggestApply([date]);
          break;
        case "suggest-apply-all":
          this._suggestApply(this._sugg.items.map(function (item) { return item.date; }));
          break;
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
          this._editor.draft.ingredients.push(this._emptyLine());
          this._render();
          break;
        case "remove-line":
          this._syncDraft();
          this._editor.draft.ingredients.splice(Number(el.getAttribute("data-line")), 1);
          if (!this._editor.draft.ingredients.length) this._editor.draft.ingredients.push(this._emptyLine());
          this._render();
          break;
        case "combo-pick":
          this._selectIngredient(el.closest(".ing"), el.getAttribute("data-ing"));
          break;
        case "combo-new": {
          var nrow = el.closest(".ing");
          var nname = nrow.querySelector('[data-l="search"]').value.trim();
          if (!nname) break;
          var nline = nrow.getAttribute("data-line");
          this._syncDraft();
          this._call({ type: "meal_planner/ingredient/similar", name: nname }).then(function (res) {
            var r = self._rowFor(nline);
            if (!r) return;
            if (res.exact) {
              self._selectIngredient(r, res.exact.id);
            } else if (res.similar.length) {
              var lst = r.querySelector(".combo-list");
              lst.innerHTML = self._comboHint(nname, res.similar);
              lst.hidden = false;
            } else {
              self._createIngredientFor(nline, nname);
            }
          }).catch(function () {});
          break;
        }
        case "combo-create": {
          var crow = el.closest(".ing");
          this._syncDraft();
          this._createIngredientFor(crow.getAttribute("data-line"), el.getAttribute("data-name"));
          break;
        }
        case "ing-new":
          this._error = "";
          this._ingDialog = { mode: "new", id: null, name: "", origName: "", unit: "", similar: null };
          this._render();
          break;
        case "ing-edit": {
          var eing = this._ingredient(el.getAttribute("data-ing"));
          if (!eing) break;
          this._error = "";
          this._ingDialog = { mode: "edit", id: eing.id, name: eing.name, origName: eing.name, unit: eing.default_unit || "", similar: null };
          this._render();
          break;
        }
        case "ing-save":
          this._saveIngredient(false);
          break;
        case "ing-save-force":
          this._saveIngredient(true);
          break;
        case "close-ing":
          this._ingDialog = null;
          this._error = "";
          this._render();
          break;
        case "ing-merge":
          this._error = "";
          this._ingDialog = { mode: "merge", id: el.getAttribute("data-ing"), search: "", target: null };
          this._render();
          break;
        case "ing-merge-pick":
          this._ingDialog.target = el.getAttribute("data-ing");
          this._render();
          break;
        case "ing-merge-reset":
          this._ingDialog.target = null;
          this._render();
          break;
        case "ing-merge-do": {
          var md = this._ingDialog;
          this._call({ type: "meal_planner/ingredient/merge", source_id: md.id, target_id: md.target }).then(function () {
            self._ingDialog = null;
            self._render();
          }).catch(function () {});
          break;
        }
        case "ing-ask-delete":
          this._confirmIngDelete = el.getAttribute("data-ing");
          this._render();
          break;
        case "ing-delete":
          this._call({ type: "meal_planner/ingredient/delete", ingredient_id: el.getAttribute("data-ing") }).then(function () {
            self._confirmIngDelete = null;
            self._render();
          }).catch(function () {
            self._confirmIngDelete = null;
          });
          break;
        case "add-tag": {
          this._syncDraft();
          var tags = String(this._editor.draft.tags || "").split(",").map(function (t) { return t.trim(); }).filter(function (t) { return t; });
          var tag = el.getAttribute("data-tag");
          if (tags.map(function (t) { return fold(t); }).indexOf(fold(tag)) < 0) tags.push(tag);
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
      msg.type = "meal_planner/plan/add";
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
  // Same recovery as in family_tasks / heat_pilot: some Android WebViews swap
  // out window.customElements after this script ran, so Lovelace would not
  // find the element. Re-define it once <home-assistant> is known to the
  // registry that is current at that point (the guard avoids a double define).
  if (typeof customElements !== "undefined" && customElements.whenDefined) {
    customElements
      .whenDefined("home-assistant")
      .then(function () {
        if (!customElements.get("meal-planner-card")) {
          customElements.define("meal-planner-card", MealPlannerCard);
        }
      })
      .catch(function () {
        /* not inside a normal HA frontend - nothing to recover */
      });
  }

  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "meal-planner-card",
    name: "Meal Planner",
    description: "Mittagessen planen: Rezepte, Wochenplan und Einkauf (v" + CARD_VERSION + ")",
  });
})();
