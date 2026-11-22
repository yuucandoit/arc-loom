# DESIGN.md: ARC-Loom FinOps & Routing Ledger

## Design Read

Reading this as: FinOps telemetry and CPU-ARC routing ledger console for backend and AI infrastructure engineers, in an Industrial Ledger / Precision Telemetry visual language, dial `ENERGY 1 / RHYTHM 2 / MOTION 1`.

## Three Dials

- `Dial: ENERGY 1 / RHYTHM 2 / MOTION 1`
- **ENERGY 1 (Calm)**: High-density financial and token telemetry with zero decorative gradients, zero glowing orbs, and zero marketing slogans.
- **RHYTHM 2 (Balanced with Asymmetric Breaks)**:
  1. Top band: Full-width Counterfactual vs. Actual Net Savings Equation Bar (the single focal point on entry).
  2. Middle band: Asymmetric 2-column split (7:5 on wide screens) pairing the Break-Even Semantic Function Registry Table on the left with the Interactive Routing & $O(1)$ Static Compiler Inspector on the right.
  3. Bottom band: Role-by-Role Token Movement Ledger (`source`, `builder`, `target`, `extractor`, `converter`) with live filtering and narration trace drawer.
- **MOTION 1 (Hover & Focus States Only)**: Instant state transitions on hover and keyboard `:focus-visible`. No pulsing status dots, no scroll-reveal choreography, and no looping animations.

## Brand Identity & Motif

- **Identity Motif**: Double-ruled archival ledger header (`2px` top rule + `1px` bottom hairline) paired with fixed 8-decimal USD accounting notation (`$0.04139886`) matching the PostgreSQL `NUMERIC(12, 8)` FinOps ledger schema.
- **Logo Placeholder (R-23)**: Text mark `ARC-LOOM // FINOPS LEDGER` in tabular uppercase tracking without an invented graphical mascot.

## Color System (Verified WCAG AA / AAA via `contrast-check.py`)

### Light Theme (Warm Ledger Paper & Carbon Ink)
- Surface Canvas: `#F6F4EE`
- Surface Ruled Panel: `#ECE8DD`
- Text Primary (Carbon Ink): `#141413` (`16.92:1` on `#F6F4EE`, `15.46:1` on `#ECE8DD`)
- Text Secondary (Graphite): `#4A4842` (`8.13:1` on `#F6F4EE`, `7.43:1` on `#ECE8DD`)
- Structural Border & Input Boundary: `#737067` (`4.38:1` on `#F6F4EE`, `4.00:1` on `#ECE8DD`, passing 3:1 non-text contrast)
- Deliberate Accent (Copper Shuttle): `#9A3412` with `#FFFFFF` label (`7.53:1` normal text PASS)
- Semantic Status Pairings (always paired with explicit text tag `[ARC_HIT]`, `[MISS_BUILD]`, `[ERROR]`):
  - Profitable / Cache Hit: `#14532D` text on `#F6F4EE` (`8.59:1`)
  - Builder Investment / Pending Break-Even: `#78350F` text on `#F6F4EE` (`8.18:1`)
  - Error / Rejected AST: `#7F1D1D` text on `#F6F4EE` (`8.73:1`)

### Dark Theme (Carbon Slate & Archival Bone)
- Surface Canvas: `#121316`
- Surface Ruled Panel: `#1B1D22`
- Text Primary (Archival Bone): `#F3F1EA` (`16.43:1` on `#121316`, `14.54:1` on `#1B1D22`)
- Text Secondary (Warm Ash): `#B8B4A9` (`9.05:1` on `#121316`, `8.01:1` on `#1B1D22`)
- Structural Border & Input Boundary: `#6E727D` (`3.84:1` on `#121316`, `3.40:1` on `#1B1D22`, passing 3:1 non-text contrast)
- Deliberate Accent (Amber Shuttle): `#F59E0B` with `#121316` label (`8.72:1` normal text PASS)
- Semantic Status Pairings:
  - Profitable / Cache Hit: `#6EE7B7` text on `#1B1D22` (`10.51:1`)
  - Builder Investment / Pending Break-Even: `#FCD34D` text on `#1B1D22` (`11.49:1`)
  - Error / Rejected AST: `#FCA5A5` text on `#1B1D22` (`8.21:1`)

## Written One-Line Reasons for Major Decisions (R-31)

1. **Why this color palette?** Warm archival paper (`#F6F4EE`) and carbon ink (`#141413`) with one burnt-copper accent (`#9A3412`) reflect accounting ledger legibility while guaranteeing >7:1 text contrast and >3:1 border contrast across light and dark modes.
2. **Why this layout?** The layout is organized around the FinOps operator's core task (verifying whether cached semantic functions have crossed their Break-Even call threshold and inspecting compiled $O(1)$ Python code), avoiding the generic 4-stat-card + sidebar template.
3. **Why this typography?** Humanist sans (`"IBM Plex Sans", -apple-system, sans-serif`) keeps prose labels scannable, while tabular monospace (`"IBM Plex Mono", ui-monospace, monospace` with `font-variant-numeric: tabular-nums`) aligns 8-decimal USD columns, token counts, and Python AST output.
4. **Why this spacing?** Fluid `clamp()` spacing across three responsive layout states (`<680px` single-column stack, `680px-1080px` two-column ledger, `>1080px` asymmetric workbench) prevents horizontal overflow and keeps every button and filter >= `44x44px`.
5. **Why ruled flat panels instead of floating cards?** Flat `1px` solid borders with zero drop shadows and `2px`/`4px` corners establish hierarchy through ruled table boundaries rather than artificial floating elevation.
6. **Why no decorative icons?** Explicit bracketed text badges (`[ARC_HIT]`, `[COMPILED O(1)]`, `[SHADOW]`) communicate exact system state without generic AI sparkle or robot glyphs.
