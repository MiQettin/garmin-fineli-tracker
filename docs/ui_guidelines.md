# UI/UX Guidelines - HealthManager

Last updated: 2026-10-12

Shared frontend conventions. Every page extends `core/templates/core/base.html`, which loads Bootstrap 5, Bootstrap Icons and `core/static/core/base.css`, and defines the global JavaScript helpers below.

## Design system

The app has a dark theme. All colors are CSS custom properties on `:root` in `core/static/core/base.css` - use the variables, never hard-coded colors.

| Group | Variables (current values) |
| --- | --- |
| Primary | `--primary` #3b82f6, `--primary-dark` #2563eb, `--primary-light` #60a5fa |
| Accents | `--accent-violet` #a78bfa, `--accent-cyan` #22d3ee, `--accent-green` #34d399, `--accent-red` #f87171, `--accent-orange` #fb923c |
| Semantic | `--success` #34d399 / `--success-bg` #103428, `--warning` #fbbf24 / `--warning-bg` #3d2e0a, `--error` #f87171 / `--error-bg` #3d1616, `--info` #60a5fa / `--info-bg` #122e4d |
| Surfaces | `--bg-page` #0b1220, `--bg-card` #16202f, `--bg-hover` #1f2c40, `--border-light` #263349, `--border-medium` #37485f |
| Text | `--gray-50` … `--gray-900`: one monotonic scale from darkest (#1c283a) to lightest (#f8fafc); body text `--gray-700`/`--gray-800`, secondary `--gray-500` |
| Shadows | `--shadow-sm`, `--shadow-md`, `--shadow-lg` |

```css
/* Good */
.panel { background: var(--bg-card); border: 1px solid var(--border-light); }
.warning-text { color: var(--warning); }

/* Bad */
.panel { background: #ffffff; }
```

Status colors mean the same everywhere: green on track, amber/yellow warning, red alert, blue info/surplus. The dashboard's "Avg daily balance" row slides its color from green through yellow to red with the distance below the goal (`energy.status_hue` from the view).

Activity step type badges: warm-up violet, run primary blue, recovery green, cool-down cyan, rest gray.

## Core principles

### 1. No browser dialogs

Never use `alert()`, `prompt()` or `confirm()`. Use:

- **Toasts** for feedback (`showToast()`, below).
- **Two-click confirmation** for destructive buttons: add `data-confirm="Click again to delete"` to a submit button. The first click arms it for 3 seconds (the label changes), the second click submits. The handler is global in `base.html`.
- **Inline panels or modals** for input (portion picker, recipe amount panel).
- **Inline validation** for form errors (Django form errors next to the field).

Known exception still to fix: the Foods page's Delete button uses `confirm()`.

### 2. Toasts

`base.html` defines, for every page:

```javascript
showToast(message, type = 'info', duration = 5000)   // type: 'success' | 'error' | 'warning' | 'info'
```

`showToast` inserts `message` as HTML - pass plain text you control, or escape it first.

Server-side feedback uses Django messages: `messages.success(request, "Saved X.")` in a view (followed by a redirect) shows as a toast on the next page. `base.html` renders the messages HTML-escaped. Every save should confirm what happened, and say when something else changed as a result (e.g. "Also recalculated the recipes that use Rahka: Puuro.").

### 3. Async requests

POSTs need Django's CSRF token; read it from the cookie at request time:

```javascript
await fetch(url, {
  method: 'POST',
  headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken')},
  body: JSON.stringify(payload),
});
```

`getCookie()` is global (`base.html`). JSON endpoints answer 401 (not a redirect) when logged out, 404 for foods that don't exist or aren't yours, and `{error: "..."}` with a 4xx status for invalid input - show the error as a toast.

### 4. Never inline data in HTML attributes

Don't build markup like `onclick='select(${JSON.stringify(food)})'`: a single quote in a name (48 Fineli foods, e.g. *MCDONALD'S*) ends the attribute and breaks the page. Instead:

- keep the objects in an array and reference them by index (`onclick="selectFineliFood(shownFoods[3])"`), or attach listeners and read `data-*` attributes;
- escape every name inserted with `innerHTML` (`escapeHtml()` in `food_diary.html`), or build nodes with `textContent`;
- only put numbers (ids) directly into attribute JavaScript.

### 5. Server is the source of truth

The browser may show previews (grams of a portion, a serving's size), but the server recomputes everything that is saved: portion grams from stored unit sizes, nutrient values from the database, recipe values from ingredients. Never trust a value only because the page sent it.

## Patterns

### Pick from a list, then set the amount

Used for recipe ingredients (Fineli suggestions, Fineli favorites, My Foods):

1. A list of large tappable rows (`.pick-item`, at least 48 px high) with the food name only.
2. Tapping a row hides the list and opens an amount panel (`.pick-panel`): name, amount, unit, resulting grams, **Cancel** / **Add**. Cancel, × or Esc returns to the list.

Live search: debounce input (~250 ms), abort the previous request (`AbortController`), require 2+ characters, highlight matched words.

### Food diary portion picker

The Add Food dialog's unit list depends on the food: grams plus Fineli portion sizes, "pakkaus" for products with a package size, ml/dl/l for per-100 ml products, "annos" for recipes with servings. The units and their grams come from the server; nothing is guessed in the browser.

### Meal name field

`nutrition/includes/meal_picker.html` + `static/nutrition/meal_picker.{js,css}`: a free-text input (`role="combobox"`) with the 5 standard meals as suggestions in the app language. Tapping the field or ▾ opens the short list; typing filters it (starts-with first, then contains); ↑/↓ + Enter picks, Esc closes. Any other text is a meal name of its own. The browser sends the text as typed - the server (`nutrition/meals.py`) maps standard names in either language to their keys. Used on the diary page (adds a meal section) and on the entry edit page.

### Analytics filters

Running analytics: workout filters (All, VK, PK, Pitkä) and lap inclusion filters (Intervals, Warm-up, Cool-down) as `.filter-tab` links, active ones `.filter-tab.active`. Filter links keep unrelated query state; Warm-up and Cool-down are independent opt-ins; Intervals are on by default; Clear filter appears when anything differs from the default.

## Mobile

- Touch targets at least 44×44 px; primary actions full width on narrow screens.
- Inputs use `font-size: 16px` (smaller makes iOS zoom in) and `inputmode="decimal"` for numbers.
- Number inputs that can hold imported values use `step="any"` - `step="0.01"` makes the browser refuse values with more decimals (e.g. salt 0.004).
- Forms collapse to one column under 576 px; long names wrap (`overflow-wrap: anywhere`), never overflow; no horizontal scrolling.
- Header toolbars stay on one row on phones: secondary buttons drop their text and keep the icon (`.btn-text` hidden), with the text in `title` and `aria-label`.
- Long lists scroll inside their own box (`max-height`, `overscroll-behavior: contain`).
- Respect `prefers-reduced-motion` for animations.

## Translations

The UI is in English and Finnish (Settings → Language). Write every user-visible text in English and mark it for translation; the Finnish text goes in `backend/locale/fi/LC_MESSAGES/django.po`.

- Templates: `{% load i18n %}`, then `{% translate "Text" %}`, or `{% blocktranslate with n=value %}… {{ n }} …{% endblocktranslate %}` for texts with values. Keep whole sentences in one msgid - never glue translated fragments together.
- `{% translate %}` output is **not** HTML-escaped. For texts with `&`, `<` or `>` use `{% translate "Health & Recovery" as t %}{{ t|force_escape }}`.
- A literal `%` in a translated text is written `%%` (also in the Finnish msgstr).
- Template tag arguments: `label=_("Text")`.
- JavaScript: `{% load core_extras %}` and `{% jstrans "Text" %}`, which outputs a JSON string literal that is safe inside `<script>`. Static `.js` files get their texts from a small object defined in the template (e.g. `window.HEALTH_I18N`) and keep English defaults.
- Python: `gettext("… %(name)s") % {"name": value}` for messages and JSON errors. Model `choices` are translated at display time (form `__init__`, `activities.labels`, template filters) - changing a model's choices would need a migration.
- Numbers: the Finnish locale prints decimal commas. Values that go into `<input value>`, CSS (`width: …%`) or JavaScript need `|unlocalize`.
- Units are metric only: g, ml, dl, l, kcal, km, min/km, bpm.
- Workflow: `manage.py update_translations` → translate the new entries in the .po → `manage.py compile_translations`. `core.tests.TranslationCatalogTests` fails when a text has no Finnish translation or the .mo is stale.

## Accessibility

- Text colors meet WCAG AA contrast on the dark surfaces.
- Icon-only buttons have `aria-label`; toggles use `aria-pressed`/`aria-expanded`; live status lines use `aria-live="polite"`.
- Expandable details use `<details>`/`<summary>` where possible (keyboard and screen-reader friendly without JavaScript).

## Code standards

- JavaScript: `const`/`let`, arrow functions, template literals, explicit error handling; wrap page scripts in an IIFE to keep globals out.
- CSS: variables for colors, transitions around 0.15-0.2 s, border radius 8-16 px.
- Naming: CSS classes kebab-case (`pick-panel`), JS variables and functions camelCase, functions verb-first (`closeModal()`), element ids camelCase (`fineliSearchInput`).

## Testing checklist

Before committing UI changes:

- [ ] No `alert`/`confirm`/`prompt`
- [ ] Every save and async action gives toast feedback
- [ ] Names with `'`, `"`, `<`, `&` display correctly and stay clickable
- [ ] Works at 360-390 px width without horizontal scrolling
- [ ] Keyboard: Tab order, Enter submits, Esc closes panels
- [ ] Browser console has no errors (`node --check` on extracted page scripts catches syntax errors)
- [ ] POST requests carry the CSRF token
- [ ] Unknown values show as "unknown"/"–", never as 0
- [ ] New texts are translated and the page works in both English and Finnish (numbers in inputs and JS use `|unlocalize`)
