# UI/UX Guidelines — HealthManager

Last updated: 2026-07-22

## Design System (CSS Variables)

All colors are defined as CSS custom properties in `base.html`. Use these variables instead of hardcoded colors:

### Color Palette

#### Primary Colors
```css
--primary: #2563eb;        /* Main blue - buttons, links, active states */
--primary-dark: #1d4ed8;   /* Hover states */
--primary-light: #3b82f6;  /* Lighter variant */
```

#### Accent Colors
```css
--accent-violet: #7c3aed;  /* Warmup badges, special highlights */
--accent-cyan: #0891b2;    /* Cooldown badges, info elements */
--accent-green: #059669;   /* Success, recovery badges, positive actions */
```

#### Semantic Colors
```css
--success: #16a34a;        /* Success states */
--success-bg: #dcfce7;     /* Success backgrounds */
--warning: #d97706;        /* Warning states */
--warning-bg: #fef3c7;     /* Warning backgrounds */
--error: #dc2626;          /* Error states */
--error-bg: #fee2e2;       /* Error backgrounds */
--info: #0284c7;           /* Info states */
--info-bg: #e0f2fe;        /* Info backgrounds */
```

#### Gray Scale
```css
--gray-50: #f9fafb;        /* Lightest - backgrounds */
--gray-100: #f3f4f6;       /* Light backgrounds */
--gray-200: #e5e7eb;       /* Borders, dividers */
--gray-300: #d1d5db;       /* Disabled states */
--gray-400: #9ca3af;       /* Placeholder text */
--gray-500: #6b7280;       /* Secondary text */
--gray-600: #4b5563;       /* Body text */
--gray-700: #374151;       /* Headings */
--gray-800: #1f2937;       /* Primary text */
--gray-900: #111827;       /* Darkest */
```

#### Utility
```css
--bg-card: #ffffff;        /* Card backgrounds */
--border-light: #e5e7eb;   /* Light borders */
--shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
--shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
--shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
```

### Usage Examples

```css
/* Good - uses variables */
.button { background: var(--primary); }
.button:hover { background: var(--primary-dark); }
.card { box-shadow: var(--shadow-md); }
.label { color: var(--gray-500); }
.error-message { color: var(--error); background: var(--error-bg); }

/* Bad - hardcoded colors */
.button { background: #667eea; }  /* Don't do this */
```

### Step Type Badge Colors
Activity intervals use these consistent colors:
- **Warmup**: `var(--accent-violet)` - #7c3aed
- **Run**: `var(--primary)` - #2563eb  
- **Recovery**: `var(--accent-green)` - #059669
- **Cooldown**: `var(--accent-cyan)` - #0891b2
- **Rest**: `var(--gray-500)` - #6b7280

### Analytics Filter Controls
Running analytics uses two related filter groups:
- **Workout filters**: All, VK, PK, Pitka
- **Lap inclusion filters**: Intervals, Warm-up, Cool-down

Use `.filter-tab` links for both groups. Active filters use `.filter-tab.active`.

Rules:
- Filter links should preserve unrelated query state where useful.
- Warm-up and Cool-down are independent opt-in filters.
- Intervals are enabled by default.
- Clear filter should appear when any filter differs from the default state.
- Avoid checkbox filters for analytics pages unless the rest of the page already uses form controls for the same interaction.

### WCAG 2.0 Compliance
All text colors meet WCAG AA contrast ratios (≥4.5:1):
- White text on colored badges
- Dark text (gray-700/800) on light backgrounds
- Semantic colors tested for accessibility

---

## Core UI Principles

### 1. No Intrusive Dialogs
**Rule**: NEVER use `alert()`, `prompt()`, or `confirm()` for user feedback.

**Why**: 
- Blocks the entire UI
- Cannot be styled or integrated with design
- Poor user experience on mobile
- Interrupts workflow unnecessarily

**Instead Use**:
- **Toast notifications** (base.html `showToast()`) for success/error messages
- **Custom modals** for complex inputs (like portion selection)
- **Inline validation** for form errors
- **confirm()** is acceptable ONLY for destructive actions (delete) where blocking is intentional

### 2. Toast Notification System
Located in `base.html`, available globally:

```javascript
showToast(message, type, duration)
```

**Parameters**:
- `message`: String to display
- `type`: 'success', 'error', 'warning', 'info'
- `duration`: milliseconds (default 5000)

**Usage Examples**:
```javascript
// Success
showToast('Food added successfully', 'success');

// Error
showToast('Failed to add food. Please try again.', 'error');

// Warning
showToast('Database sync in progress...', 'warning', 10000);

// Info
showToast('Loading data...', 'info', 3000);
```

**Best Practices**:
- Use brief, actionable messages
- Success: 1-1.5 seconds before page reload
- Errors: Include what went wrong and hint at solution
- Always provide feedback for async operations

### 3. Async POST Requests
Async POST requests must include Django's CSRF token. Prefer reading the token from the `csrftoken` cookie so cached/rendered templates do not bake stale tokens into JavaScript.

```javascript
function getCookie(name) {
  const value = `; ${document.cookie}`;
  const parts = value.split(`; ${name}=`);
  if (parts.length === 2) return parts.pop().split(';').shift();
  return null;
}

await fetch('/sync-garmin/', {
  method: 'POST',
  headers: {
    'X-CSRFToken': getCookie('csrftoken'),
  },
});
```

When handling the result, call the global toast API as `showToast(message, type)`.

## Food Diary Specific

### Portion Selection Modal
**Feature**: Custom modal for selecting portion size and unit

**Supported Units**:
- **g (grams)**: Default, most accurate (1:1 conversion)
- **dl (deciliter)**: For liquids and flour (~100g/dl approximation)
- **kpl (pieces)**: For countable items (~50g/piece default)
- **rkl (tablespoon)**: For spoons (~15g/tablespoon)
- **tl (teaspoon)**: For spoons (~5g/teaspoon)

**Conversion Logic** (food_diary.html):
```javascript
switch(unit) {
    case 'dl': portionGrams = amount * 100; break;
    case 'kpl': portionGrams = amount * 50; break;
    case 'rkl': portionGrams = amount * 15; break;
    case 'tl': portionGrams = amount * 5; break;
    // 'g' = no conversion
}
```

**User Flow**:
1. User searches and clicks food
2. Modal opens with food name displayed
3. User enters amount (default: 100)
4. User selects unit (default: g)
5. User clicks OK
6. System converts to grams, adds to database
7. Toast confirms success, page reloads

**Why Modal Instead of Prompt**:
- Can show food name as context
- Styled to match application design
- Supports multiple input types (number + dropdown)
- Non-blocking during network request
- Can add more fields later (e.g., meal name)

### Error Handling Patterns

#### Network Errors
```javascript
fetch('/api/endpoint/')
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showToast('Operation successful', 'success');
            setTimeout(() => location.reload(), 1000);
        } else {
            showToast('Operation failed: ' + (data.error || 'Unknown error'), 'error');
        }
    })
    .catch(error => {
        console.error('Error:', error);
        showToast('Operation failed. Please try again.', 'error');
    });
```

#### Form Validation
- Use browser built-in validation (required, min, max, step)
- Add custom validation with toast feedback
- Show specific field errors inline when possible

#### Delete Confirmation
- `confirm()` is acceptable for delete actions
- Follow up with toast for feedback
- Brief delay before reload to show toast

## Accessibility Considerations

### Current Implementation (WCAG 2.0 AA)
- All color combinations tested for ≥4.5:1 contrast ratio
- White text on colored badges (primary, accent colors)
- Dark text (gray-700/800) on light backgrounds
- Semantic colors (success/warning/error) with accessible contrast

### Future Improvements
- Add ARIA labels to modals
- Keyboard navigation for modal (ESC to close, TAB to move)
- Focus management (return focus after modal close)
- Screen reader announcements for toast messages

## Responsive Design

### Modal Sizing
- Max-width: 400-600px depending on content
- Max-height: 80vh with scroll
- Centered on screen
- Dark overlay (rgba(0,0,0,0.5))
- Higher z-index for nested modals (e.g., portion over food search: 2100 vs 2000)

### Mobile Considerations
- Touch-friendly button sizes (min 44x44px)
- Adequate spacing between interactive elements
- Readable font sizes (min 16px for inputs)
- Avoid hover-only interactions

## Code Standards

### JavaScript
- Use const/let, not var
- Arrow functions for callbacks
- Template literals for strings
- Async/await for complex promise chains (optional)
- Always handle errors explicitly

### CSS
- Use CSS variables from base.html for all colors
- Use consistent spacing units (px for borders, rem/em for text)
- Transitions for all interactive states (0.2s default)
- Box-shadow using --shadow-sm/md/lg variables
- Border-radius for friendliness (6-12px range)
- Never hardcode colors - always use var(--color-name)

### Naming Conventions
- CSS classes: kebab-case (e.g., `modal-content`)
- JavaScript variables: camelCase (e.g., `currentMealType`)
- Functions: camelCase, verb-first (e.g., `closeModal()`)
- IDs: camelCase (e.g., `fineliSearch`)

## Testing Checklist

Before committing UI changes:
- [ ] No alert/prompt calls (except delete confirm)
- [ ] All async operations have toast feedback
- [ ] Success operations reload with brief delay
- [ ] Errors display helpful messages
- [ ] Modal closes properly (X button and outside click)
- [ ] Keyboard navigation works (TAB, ENTER, ESC)
- [ ] Mobile viewport renders correctly
- [ ] Network errors handled gracefully
- [ ] Console has no JavaScript errors
- [ ] CSRF tokens included in POST requests
