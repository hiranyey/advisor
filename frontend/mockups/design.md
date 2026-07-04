# AdvisorOS — Design System

The visual language behind the three mockups (`01-newspaper-digest`, `02-terminal-radar`, `03-meeting-brief`) and the `index`. It is a **warm-gold ledger** aesthetic: paper-toned surfaces, Playfair Display headlines, Inter body, hard offset "stamp" shadows, and square corners. Colors and type are lifted directly from the app's Tailwind theme tokens so the mockups read as native pages of the product.

---

## 1. Color

### Core palettes

| Token | Hex | Use |
|---|---|---|
| `--primary-50` | `#fefdf4` | Paper / card surface |
| `--primary-100` | `#fef9e7` | Page background (top), subtle card tint |
| `--primary-200` | `#fdf0c4` | Page background (bottom), hairline rules |
| `--primary-300` | `#fbe497` | Soft borders, dotted separators |
| `--primary-600` | `#d4a017` | Faint text, "review" severity, mid meters |
| `--primary-700` | `#b8860b` | **Brand** — accents, active nav, labels, muted text |
| `--primary-800` | `#96690a` | **Ink border**, secondary text, strong brand |
| `--primary-900` | `#6b4a08` | Headlines & body ink, stamp-shadow color, double rules |

### Semantic accents

| Token | Hex | Meaning |
|---|---|---|
| `--inflow` | `#4a7c3a` | Positive / good / on-track / opportunity |
| `--inflow-soft` | `#c9deb0` | Positive tint (badges, chips) |
| `--outflow` / `--secondary-700` | `#a8224a` | Critical / risk / breach / debit |
| `--outflow-soft` | `#f4b8c6` | Critical tint (flags, callouts) |

### Semantic mapping (what the raw tokens mean in use)

```
--paper        = primary-50     bg-card / bg-paper
--card         = primary-50     card fill
--card-2       = primary-100    inner tint, hover rows, chips
--ink          = primary-900    headings + body text
--ink-2        = primary-800    secondary body text
--mut          = primary-700    muted text, labels, captions
--faint        = primary-600    least-emphasis text
--rule         = primary-300    soft borders / separators
--rule-2       = primary-200    faint table row lines
--brand        = primary-700    accent, active state
--brand-strong = primary-800    stronger accent, big metrics
--on-brand     = primary-50     text on brand fill
```

### Severity system (3-level, palette-native)

- **Critical / breach** → `--outflow` (rose) on `--outflow-soft`
- **Review / tight** → `--primary-600/700/800` (gold) on `rgba(184,134,11,.16)`
- **Good / act-soon / on-track** → `--inflow` (green) on `--inflow-soft`

> Semantic color is separate from the brand gold. Gold = identity & mid-severity; green/rose = the two directional states.

---

## 2. Typography

Two families only.

| Role | Family | Notes |
|---|---|---|
| Display / headlines / card titles / big numbers | **Playfair Display** (`--font-serif`) | High-contrast Didone. Weights 400–900. Card titles use *italic 500*. |
| Body / labels / nav / captions / table cells | **Inter** (`--font-sans`) | Also serves as mono role (tabular figures). |

```css
--font-serif: 'Playfair Display', Georgia, serif;
--font-sans:  'Inter', ui-sans-serif, system-ui, sans-serif;
```

### Type roles as used

| Element | Font | Size / weight | Spacing |
|---|---|---|---|
| Page title (`h1.title`) | Playfair 900 | `clamp(40–44px, 6–7vw, 74–88px)`, line-height .9 | tracking `-.01em` |
| Card title (`h2`) | Playfair italic 500 | ~23–24px | — |
| Feature name | Playfair 800 | ~34px | — |
| Big metric / stat value | Playfair 700–800 | 22–52px | `tabular-nums` |
| Deck / lede | Playfair 400 | `clamp(21px, 2.8vw, 30px)`, line-height 1.36 | `max-width: 44ch` |
| Body | Inter 400 | 15–16px / 1.6 | — |
| Eyebrow / label / nav / th | Inter 600–700 | 10–11px UPPERCASE | tracking `.16em`–`.3em` |

**Rules of thumb:** numbers always `font-variant-numeric: tabular-nums`; running text capped near `44ch`; labels are the only uppercase text and always tracked.

---

## 3. Shadows — "stamp" (hard offset, no blur)

The signature. A solid offset shadow in `primary-900` (`rgb(107 74 8)`), never blurred.

```css
--stamp-color: 107 74 8;
--shadow-stamp-sm: 3px 3px 0 0 rgb(107 74 8);   /* nav, pills, small cards */
--shadow-stamp:    4px 4px 0 0 rgb(107 74 8);   /* the "after" what-if state */
--shadow-stamp-lg: 6px 6px 0 0 rgb(107 74 8);   /* standard cards (default) */
--shadow-stamp-xl: 10px 10px 0 0 rgb(107 74 8); /* hover-lift, hero cards */
--shadow-stamp-rose: 4px 4px 0 0 rgb(109 26 54);/* rose variant when on outflow */
```

- Cards pair the stamp with a **`1.5px solid primary-800` ink border**.
- Hover lift: `transform: translate(-3px,-3px)` + step shadow up one size (`lg → xl`).
- The green "after" state uses a green stamp (`4px 4px 0 0 var(--inflow)`) to signal improvement.

---

## 4. Borders, rules & corners

- **Corners: square everywhere** (`border-radius: 0`). No rounding on cards, chips, badges, meters, or nav.
- **Ink border:** `1.5px solid var(--primary-800)` on cards, nav, KPI tiles.
- **Double rule** under page titles: `border-top: 3px double var(--primary-900)`.
- **Hairline separators:** `1px solid var(--primary-300)` (card internal), `1px solid var(--primary-200)` (table rows), `1px dotted var(--primary-300)` (stat lists).
- **KPI accent:** `5px` colored left border keyed to severity.
- **Callout / movebar accent:** `3–5px` colored left border.

---

## 5. Background

Two stacked layers on `fixed` attachment:

```css
background:
  repeating-linear-gradient(0deg, rgba(107,74,8,.04) 0 1px, transparent 1px 6px), /* ledger lines */
  linear-gradient(180deg, var(--primary-100) 0%, var(--primary-200) 100%);        /* butter wash */
```

The faint 6px horizontal ledger lines are the texture cue; cards sit on `primary-50` so they read one step lighter than the page.

> Open decision: the theme's `--bg-paper` is near-white `#fefdf4`. The mockups use the warmer `100→200` gradient to match the screenshots. Swap to flat paper if the real overview page is near-white.

---

## 6. Spacing (8-pt scale)

`4 · 8 · 12 · 16 · 24 · 32 · 48 · 64 · 96 · 128` (px).

In practice: card padding `22–24px`; grid gap `22–26px`; section gap `26px`; page padding `20–28px`; max content width `900px` (brief) / `1160–1180px` (dashboards).

---

## 7. Components

### Nav shell (shared across all pages)
Sticky top bar, centered pill + absolute-right user pill. Cream fill, ink border, `stamp-sm`. Tabs: Inter 11px uppercase tracked `.16em`, icon (14px stroked SVG) + label. Active tab = brand fill + `on-brand` text. User pill: name · ☾ (brand) · Sign out (`secondary-700` rose). Labels hide < 680px.

### Card
`background: primary-50; border: 1.5px solid primary-800; box-shadow: stamp-lg`. Title = Playfair italic, followed by a `1px primary-300` `.h2rule`.

### Deck sentence
Playfair, `44ch` max, with inline colored spans: `.big` (900 ink), `.hot` (outflow), `.goodw` (inflow), `i` (italic). Mirrors the app's multi-color editorial lede.

### KPI tile (radar)
Card + `5px` severity left-border; label (Inter caps) over a large Playfair `tabular-nums` value + faint footnote.

### Badge / pill / flag / chip
Square, Inter 10–11px 700 uppercase tracked. Colored by severity tint (soft bg + strong fg). Chips (sim status) use `inflow-soft`; "slow/CPU" chip is neutral card-2 with a `primary-300` border.

### Watchlist / data table
`th`: Inter 10px caps, `mut`. `td`: `1px primary-200` top border, row hover = `card-2`. Rank & client name in Playfair; numeric columns right-aligned `tabular-nums`; status as a pill.

### Goal meter (brief)
10px track (`primary-200`, `primary-300` border), fill in green/gold/rose by state, plus a `2px primary-900` threshold tick at the confidence line (80%).

### Suitability heatmap
Grid: profile rows × drawdown-bucket columns. Cells tinted `ok`/`low`/`bad` (green/gold/rose); breach cells get a `1.5px rose` inset ring. Legend below.

### Monte Carlo fan chart (SVG)
Grid lines `primary-300`; 5–95 band = `primary-700 @ .20→.04` gradient; 25–75 band = `primary-700 @ .24`; median = `primary-800` 2.5px + endpoint dot; target line = dashed `inflow`. Axis labels Inter `primary-600`.

### What-if compare (brief)
Two states side by side with a Playfair `→`. "Before" neutral (`primary-300` border, gold %); "after" emphasized (green border + green stamp shadow, green %).

---

## 8. Motion

- `--ease-out: cubic-bezier(0.2, 0.7, 0.2, 1)`; standard rise `500ms`.
- Card hover: `translate(-3px,-3px)` + shadow step, `~160ms`.
- Live-status dot: 2.4s pulse ring (green). Wrapped in `@media (prefers-reduced-motion: reduce)` → animation off.

---

## 9. Principles

1. **Ledger, not dashboard-generic.** Paper wash, ink borders, hard stamp shadows, square corners.
2. **Two fonts, strict roles.** Playfair for anything expressive or numeric-hero; Inter for everything operational.
3. **Gold is identity; green/rose are state.** Never use rose/green decoratively.
4. **Numbers are first-class.** Tabular figures, Playfair for hero metrics, right-aligned in tables.
5. **Summary before detail.** Deck → KPIs/featured → tables → charts.
6. **One accent per surface, quiet around it.** The brand gold carries; severity color appears only where it means something.
