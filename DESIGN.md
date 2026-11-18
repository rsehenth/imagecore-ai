---
name: ImageCore AI
description: Mesa de Prova, a riso print-proofing table for generating, editing and finding images. Blue-grey paper, indigo ink, two spot inks.
colors:
  desk: "#e8e9ec"
  paper: "#f5f5f3"
  paper-2: "#efefed"
  field: "#fbfbfa"
  mat: "#dcdde1"
  ink: "#17192b"
  ink-2: "#474a60"
  ink-3: "#62657d"
  pink: "#d6246e"
  pink-hover: "#bd1b5f"
  pink-deep: "#a3124f"
  pink-soft: "#f8dde8"
  blue: "#2e49d1"
  blue-deep: "#2338a8"
  blue-soft: "#dde3fa"
  ok: "#1a7250"
  ok-soft: "#d8eee3"
  warn: "#8a5a08"
  warn-soft: "#f6e8c6"
  danger: "#b5331b"
  danger-soft: "#f6dcd5"
typography:
  display:
    fontFamily: "Bahnschrift, Segoe UI Variable Display, Segoe UI, system-ui, sans-serif"
    fontSize: "24px"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "0.005em"
  title:
    fontFamily: "Bahnschrift, Segoe UI Variable Display, Segoe UI, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 600
    lineHeight: 1.2
  section:
    fontFamily: "Bahnschrift, Segoe UI Variable Display, Segoe UI, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 600
    lineHeight: 1.2
  body:
    fontFamily: "Segoe UI Variable Text, Segoe UI, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.45
  composer:
    fontFamily: "Segoe UI Variable Text, Segoe UI, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Segoe UI Variable Text, Segoe UI, system-ui, sans-serif"
    fontSize: "12.5px"
    fontWeight: 600
    lineHeight: 1.45
  measure:
    fontFamily: "Cascadia Mono, Cascadia Code, Consolas, monospace"
    fontSize: "12.9px"
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: "-0.01em"
    fontFeature: "tnum"
rounded:
  xs: "3px"
  sm: "6px"
  md: "10px"
  lg: "14px"
  pill: "99px"
spacing:
  gap: "20px"
  pad: "24px"
  gap-compact: "14px"
  pad-compact: "18px"
  control-h: "36px"
components:
  button-default:
    backgroundColor: "{colors.field}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    height: "36px"
    padding: "0 14px"
  button-primary:
    backgroundColor: "{colors.pink}"
    textColor: "#ffffff"
    rounded: "{rounded.sm}"
    height: "36px"
    padding: "0 14px"
  button-primary-hover:
    backgroundColor: "{colors.pink-hover}"
  button-generate:
    backgroundColor: "{colors.pink}"
    textColor: "#ffffff"
    rounded: "{rounded.sm}"
    height: "46px"
    padding: "0 24px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.sm}"
    height: "36px"
  input:
    backgroundColor: "{colors.field}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    height: "36px"
    padding: "0 10px"
  nav-item:
    textColor: "{colors.ink-2}"
    rounded: "{rounded.sm}"
    height: "42px"
    padding: "0 12px"
  nav-item-active:
    backgroundColor: "{colors.ink}"
    textColor: "#ffffff"
  panel:
    backgroundColor: "{colors.paper}"
    rounded: "{rounded.lg}"
    padding: "16px 18px"
  stage:
    backgroundColor: "{colors.mat}"
    rounded: "{rounded.lg}"
    padding: "36px"
  chip:
    textColor: "{colors.ink-2}"
    rounded: "{rounded.pill}"
    height: "30px"
    padding: "0 12px"
  layer-selected:
    backgroundColor: "{colors.blue-soft}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    height: "38px"
---

# Design System: ImageCore AI

## Overview

**Creative North Star: "Mesa de Prova"**

The app is a riso print-proofing table. Every generated image is a proof lying on a grey-blue mat, framed by crop marks, and never a dashboard card. The surface is light, cool paper with indigo-black ink. Two spot inks carry all meaning: pink says "act / generate", blue says "selected / focused / informational". Nothing else is saturated at size.

Density is Operate: a persistent navigation rail, a large composer on top, the proof taking the biggest area, and a narrow inspector on the right. Controls are small-radius and quiet so the proof is the loudest object on screen. Depth is soft paper shadow, never glow.

**Key Characteristics:**
- Light theme only (`color-scheme: light`). No dark theme is implemented.
- Pink and blue are the only chromatic voices; status colors (green, amber, red) are reserved for state.
- Images have sharp corners; controls and panels have small-to-medium radii.
- Measurements (dimensions, seed, percent, zoom) are set in tabular mono.
- Compact density mode exists (`data-density="compact"`): control height 32px, gap 14px, pad 18px.

## Colors

Cool blue-grey paper, indigo-black ink, two riso spot inks.

### Primary
- **Riso Pink** (#d6246e): Generate and the primary action only. Also the caret, checkbox/range accent, progress fill, the proof-label marker square, and the hot state of a GPU meter. Hover is #bd1b5f, border/pressed edge #a3124f, tint #f8dde8 (text selection).

### Secondary
- **Riso Blue** (#2e49d1): Selection, focus and information. Focus ring and input focus border, selected layer, pressed icon-button, active segmented states (tinted #dde3fa with #2338a8 text), links, the progress track, the standard meter fill.

### Neutral
- **Desk** (#e8e9ec): App background, the table behind everything.
- **Paper** (#f5f5f3): Panels, sidebar, composer, cards, modals.
- **Paper Lowered** (#efefed): Provider card, modal action footer, disabled fields.
- **Field** (#fbfbfa): Inputs, default buttons, chips on hover.
- **Mat** (#dcdde1): The passe-partout behind every image (stage, thumbnails, lightbox, detail media).
- **Indigo Ink** (#17192b): Text, active nav, toasts, skip link. **Ink 2** (#474a60) secondary text and icons; **Ink 3** (#62657d) hints, metadata, crop marks.
- **Lines**: ink at 12% (hairline), 24% (button and chip border) and 55% (text-field border, rgba of #17192b; 3.5:1 against Field/Paper for WCAG 1.4.11).

### Status
- **Ok** #1a7250 / #d8eee3, **Warn** #8a5a08 / #f6e8c6, **Danger** #b5331b / #f6dcd5. Used in badges, dots, error text and destructive hover only.

### Named Rules
**The Two Inks Rule.** Pink is for doing, blue is for selecting. A control is never both. If something is saturated and large, it is Generate.
**The Mat Rule.** Images always sit on Mat (#dcdde1), never directly on Paper.

## Typography

**Display Font:** Bahnschrift (fallback Segoe UI Variable Display, Segoe UI)
**Body Font:** Segoe UI Variable Text (fallback Segoe UI, system-ui)
**Label/Mono Font:** Cascadia Mono (fallback Cascadia Code, Consolas)

**Character:** A DIN-style industrial headline over a neutral Windows-native body, with mono reserved for numbers a proofer would read off the sheet.

### Hierarchy
- **Display** (600, 24px, 1.1): View titles (h2). 18px in modal and settings section heads, 21px under 760px.
- **Title** (600, 17px, 1.2): Empty-state headings.
- **Section** (600, 14px, 1.2): Inspector, brief and editor panel headings; brand wordmark is 19px.
- **Body** (400, 14px, 1.45): Default text. Hints 12px, ink-3.
- **Composer** (400, 17px, 1.5): The prompt textarea, the largest running text.
- **Label** (600, 12.5-13.5px): Field labels, buttons, nav, segmented controls. Sentence case, no tracking, never uppercase.
- **Measure** (mono, .92em, tabular, -0.01em): Proof label, seed value, zoom, percentage, gallery metadata.

### Named Rules
**The Mono Is For Measures Rule.** Mono appears only on numbers and identifiers (dimensions, seed, pct, kbd, diagnostics), never as decoration on prose.

## Layout

App shell is a two-column grid: navigation rail (232px) and main. The Image Creator is a flex column (composer then stage) beside a 316px inspector. Thumbnail Studio adds a 328px brief on the left (three columns), collapsible. Gallery is CSS columns (240px min, 18px gutter) so images keep their own aspect ratios. Settings is a 200px section nav beside a column capped at 760px.

Spacing rhythm is 20px between panels, 24px view padding, 16-18px inside panels, 10-14px within control groups. Control height is 36px (32px compact, 28px for editor toolbar and xs buttons).

Responsive: at 1440px and below the rail collapses to 68px icons-only; at 1100-1279px the Studio brief becomes a fixed drawer; below 1100px Creator and Studio stack as a single column with the stage at clamp(340px, 62dvh, 640px); below 760px the rail becomes a 60px bottom tab bar.

## Elevation & Depth

Hybrid: hairline borders on panels with very low-contrast paper shadows, plus one stronger shadow reserved for proofs. All shadows are indigo-tinted at low alpha; none are hard offset.

### Shadow Vocabulary
- **xs** (`0 1px 2px rgba(23,25,43,.10)`): Selected segment, badges, gallery card rest.
- **sm** (`0 1px 2px rgba(23,25,43,.06), 0 4px 14px -6px rgba(23,25,43,.12)`): Composer, inspector, brief, settings sections.
- **md** (`0 2px 4px rgba(23,25,43,.08), 0 14px 34px -12px rgba(23,25,43,.28)`): Progress card, gallery card hover.
- **lg** (`0 4px 10px rgba(23,25,43,.10), 0 28px 64px -18px rgba(23,25,43,.42)`): Modals, toasts.
- **proof** (`0 1px 2px rgba(23,25,43,.22), 0 16px 34px -14px rgba(23,25,43,.45)`): Every image shown as a proof (stage, lightbox, detail).

### Named Rules
**The Paper Shadow Rule.** Shadows are soft, low-contrast and ink-tinted. The only colored shadow is the Generate button's pink halo (`0 6px 16px -8px rgba(214,36,110,.7)`).

## Shapes

Small radii on controls (6px buttons, inputs, nav items; 3px on thumbnails and reference previews), 10px on cards inside panels, 14px on panels and stage, pill (99px) only for chips and badges. Images and the editor canvas have square corners. Borders are 1px hairlines; drop zones use a 1.5px dashed line.

**Signature: crop marks.** `.proof::before` draws eight 10px hairline ticks in Ink 3 at the corners, offset 15px outside the image, as the frame of every result. The proof label above/below the result is a 9px pink square plus mono dimensions.

## Components

### Buttons
- **Shape:** 6px radius, 36px high, 1px border at 24% ink.
- **Default:** Field fill, ink text, semibold 13.5px; hover goes white with a darker border.
- **Primary:** Riso Pink fill, #a3124f border, white text; hover #bd1b5f.
- **Generate:** Primary at 46px, 15px text, 24px side padding, pink halo shadow; the only large saturated element on screen.
- **Ghost / Danger / xs:** transparent ink-2 text with tint on hover; danger is red text and 40% red border; xs is 28px high.
- **Focus:** 2px blue outline, 2px offset.

### Inputs / Fields
- **Style:** Field fill, 1px border at 55% ink (hover 72%), 6px radius, 36px high.
- **Focus:** border turns blue with a 3px blue 16% ring. The prompt composer shows the same ring around the whole panel via `:focus-within`.
- **Disabled:** 50% opacity on Paper Lowered.
- **Placeholder:** Ink 3 at full opacity (5.5:1 on Field).

### Navigation
- Rail items 42px high, 6px radius, ink-2 text; hover is a 6% ink tint; active is solid ink fill with white text (not pink, not blue). Icons-only under 1440px, bottom tab bar under 760px. Settings uses the same active style in its section nav.

### Segmented control
- Ink 6% track, 8px radius; selected segment lifts to Field with xs shadow. Used for format, mode and tabs.

### Proof stage (signature)
- Mat-filled 14px container with 36px padding, image centered with proof shadow and crop marks. While busy a pale diagonal sweep passes across it. Progress is a Pink bar over a Blue-soft track whose fill carries a 2px-offset Blue `multiply` copy: ink overprint.

### Editor layers and chips
- Layer rows are 38px, Field fill; selected is Blue-soft with Blue border plus 1px inset ring. Chips are 30px pill outlines.

### Gallery card
- Paper, 1px hairline, 6px radius, xs shadow; hover lifts 2px with md shadow and reveals an action cluster. Metadata is 11.5px mono.

## Do's and Don'ts

### Do:
- **Do** frame every result image with the crop marks and place it on Mat (#dcdde1).
- **Do** use Riso Pink only for Generate and primary actions; use Riso Blue for focus, selection and information.
- **Do** set dimensions, seed, percentages and zoom in tabular mono.
- **Do** keep image corners square and control radii at 3-6px; panels 14px.
- **Do** keep shadows ink-tinted and low-contrast; use `--shadow-proof` for images.
- **Do** honour `prefers-reduced-motion` and `data-motion="reduce"` (animations collapse to ~0ms).

### Don't:
- **Don't** return to a dark graphite surface or a cyan accent; that world was discarded.
- **Don't** present results as a grid of identical dashboard cards in the main stage.
- **Don't** make a second large saturated element next to Generate.
- **Don't** use hard offset shadows, gradient text, or glow.
- **Don't** put mono on prose or uppercase tracking on labels.
