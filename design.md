# Art Direction/Design — Style Reference
> High-contrast monochrome blueprint

**Theme:** light

Lundqvist & Dallyn presents as a disciplined, high-contrast monochrome canvas, where bold typography and intentional negative space command attention. Visual information is presented with stark clarity, often reversing between black text on white and white text on black. The system uses a single, vibrant teal accent color sparingly, injecting a precise jolt of energy into an otherwise composed and minimal aesthetic. Imagery is integrated as bold, contained blocks, maintaining the overall sense of deliberate, high-impact design.

## Tokens — Colors

| Name | Value | Token | Role |
|------|-------|-------|------|
| Canvas White | `#ffffff` | `--color-canvas-white` | Page and section backgrounds, primary surface for content |
| Ink Black | `#1d1d1d` | `--color-ink-black` | Dark borders and separators for elevated surfaces and inverted UI. Do not promote it to the primary CTA color |
| Accent Teal | `#75fbee` | `--color-accent-teal` | Teal wash for highlight backgrounds, decorative bands, and soft emphasis behind content. Do not promote it to the primary CTA color |

### Themes

**Theme:** light, with a derived dark theme

The style reference defines one light theme. Because the system is a monochrome
blueprint that already "reverses between black text on white and white text on
black" (Ink Black is specified for "inverted UI"), the dark theme is a pure
inversion of the canvas and the ink. The three brand colors above never
change — only the roles they play.

| Role | Token | Light | Dark |
|------|-------|-------|------|
| Canvas / page surface | `--white` | `#ffffff` | `#1d1d1d` |
| Text and borders | `--ink` | `#1d1d1d` | `#ffffff` |
| Accent | `--teal` | `#75fbee` | `#75fbee` |
| Text on the accent | `--on-teal` | `#1d1d1d` | `#1d1d1d` |
| Elevated surface | `--surface` | `#ffffff` | `#1d1d1d` |
| Subtle surface | `--surface-subtle` | `rgba(0, 0, 0, 0.02)` | `rgba(255, 255, 255, 0.04)` |
| Hairline | `--rule` | `1px solid var(--ink)` | `1px solid var(--ink)` |

The accent stays `#75fbee` in both themes, so text on the accent always uses
Ink Black (`--on-teal`). Anything rendered on the accent — the top bar, the
footer, hover states, banners, notice strips — must set `color: var(--on-teal)`
rather than `var(--ink)`, which flips to white in the dark theme.

The theme is selected with `data-theme="light" | "dark"` on the root element.
The first visit follows `prefers-color-scheme`; the choice is then stored and
restored on the next visit.

## Tokens — Typography

### Favorit — Headlines, section titles, prominent display text — its wider tracking (-0.035em) gives it a composed, editorial feel. · `--font-favorit`
- **Substitute:** Arial
- **Weights:** 400
- **Sizes:** 21px, 51px, 60px
- **Line height:** 1.10, 1.20
- **Letter spacing:** -0.0350em
- **Role:** Headlines, section titles, prominent display text — its wider tracking (-0.035em) gives it a composed, editorial feel.

### AkkuratMonoLLWeb — Navigation links, body text, meta information, and functional UI elements — the monospaced, slightly wider tracking (0.1em) provides a technical, precise voice. · `--font-akkuratmonollweb`
- **Substitute:** Menlo
- **Weights:** 400
- **Sizes:** 11px, 18px
- **Line height:** 1.00, 1.70
- **Letter spacing:** 0.1000em
- **Role:** Navigation links, body text, meta information, and functional UI elements — the monospaced, slightly wider tracking (0.1em) provides a technical, precise voice.

### Type Scale

| Role | Size | Line Height | Letter Spacing | Token |
|------|------|-------------|----------------|-------|
| caption | 11px | 1 | 1.1px | `--text-caption` |
| body | 18px | 1.7 | 1.8px | `--text-body` |
| subheading | 21px | 1.1 | -0.735px | `--text-subheading` |
| heading | 51px | 1.2 | -1.785px | `--text-heading` |
| display | 60px | 1.2 | -2.1px | `--text-display` |

## Tokens — Spacing & Shapes

**Density:** compact

### Spacing Scale

| Name | Value | Token |
|------|-------|-------|
| 5 | 5px | `--spacing-5` |
| 10 | 10px | `--spacing-10` |
| 19 | 19px | `--spacing-19` |
| 20 | 20px | `--spacing-20` |
| 30 | 30px | `--spacing-30` |
| 75 | 75px | `--spacing-75` |
| 113 | 113px | `--spacing-113` |

### Layout

- **Section gap:** 113px
- **Card padding:** 20px
- **Element gap:** 10px

## Components

### Primary Navigation Link
**Role:** Top-level navigation items

Text in AkkuratMonoLLWeb, weight 400, 11px, line-height 1.00, letter-spacing 0.1em, Ink Black (#1d1d1d). Receives a 10px padding-bottom on hover, and an Ink Black (#1d1d1d) border-bottom.

### Hero Headline
**Role:** Main page headline

Favorit, weight 400, 60px, line-height 1.20, letter-spacing -0.035em, Ink Black (#1d1d1d). Presented with generous vertical spacing (marginTop 75px).

### Content Grid Item
**Role:** Individual elements within a layout grid for work examples or features

Each item uses an image or graphic as its main visual. Headings within are Favorit, weight 400, 21px, line-height 1.10, letter-spacing -0.035em, Ink Black (#1d1d1d). Subtext in AkkuratMonoLLWeb, weight 400, 18px, line-height 1.70, letter-spacing 0.1em, Ink Black (#1d1d1d).

### Footer Canvas
**Role:** Page footer background

Background color of Accent Teal (#75fbee), with Ink Black (#1d1d1d) text.

## Do's and Don'ts

### Do
- Use Canvas White (#ffffff) as the dominant background color for all main content areas.
- Employ Ink Black (#1d1d1d) exclusively for primary text, headings, and interactive element borders.
- Apply AkkuratMonoLLWeb for all functional text, navigation, and small information at 11px or 18px, with 0.1em letter-spacing.
- Reserve Favorit for headlines and prominent textual statements, at sizes 21px, 51px, or 60px, with -0.035em letter-spacing.
- Introduce Accent Teal (#75fbee) only as a background color for distinct content blocks, like the footer or specific highlighted sections.
- Maintain generous negative space, with a section gap of 113px between major content blocks.
- Utilize a compact element gap of 10px, especially for horizontal spacing within navigation or grouped links.

### Don't
- Do not use Accent Teal (#75fbee) for text or borders; it is strictly a background color.
- Avoid using any other colors beyond Canvas White, Ink Black, and Accent Teal.
- Do not vary the letter-spacing of Favorit or AkkuratMonoLLWeb from their specified values.
- Do not add shadows or heavy borders to elements; surfaces are largely flat.
- Avoid mixing text styles; Favorit is for display, AkkuratMonoLLWeb for everything else.
- Do not use subtle gradients or overlays; maintain a stark, high-contrast aesthetic.
- Do not introduce rounded corners; elements should adhere to sharp, precise edges.

## Imagery

Imagery primarily consists of contained, high-impact product photography or abstract graphics, often presented as large, full-width blocks within the layout. The treatment leans towards stark presentation with minimal context, emphasizing the object or subject itself. Icons are minimal, monochromatic, and typically outlined (#1d1d1d on #ffffff). The density is high-impact visual, where imagery takes up significant screen real estate, acting as both decorative atmosphere and content showcase, with a text-light approach.

## Layout

The page adheres to a full-bleed structure without a fixed max-width, allowing content to stretch across the viewport, then often containing inner elements. The hero features a centered headline over a background that alternates between Canvas White and Ink Black. Sections maintain a consistent vertical rhythm with a 113px section gap. Content is arranged in alternating structures, including text-left/image-right and centered stacks for large headlines. There's an implicit grid for case studies or portfolio pieces, where visuals are presented as large, distinct blocks. The layout is spacious yet compact in information flow, guiding the eye through deliberate, large-scale compositions. Navigation is a minimal top-bar, fixed to the top right corner, while the brand logo 'L&D' is top-left.

## Agent Prompt Guide

Quick Color Reference:
text: #1d1d1d
background: #ffffff
border: #1d1d1d
accent: #75fbee
primary action: no distinct CTA color

Example Component Prompts:
Create a navigation bar: background #ffffff. "L&D" logo (imagine an SVG icon in Ink Black #1d1d1d). Links are AkkuratMonoLLWeb weight 400, 11px, Ink Black (#1d1d1d), letter-spacing 0.1em, with 10px left/right padding and 5px bottom padding on active state, all elements separated by 10px. The words are 'Work', 'About', 'Index', 'Shop', 'Contact'.
Create a hero section with two columns: left column is empty. Right column features a headline "We create elegant and engaging designs for both online and offline" in Favorit weight 400, 60px, line-height 1.20, letter-spacing -0.035em, Ink Black (#1d1d1d). Section should have 75px top margin.
Create a footer section: background Accent Teal (#75fbee). Text in AkkuratMonoLLWeb weight 400, 18px, Ink Black (#1d1d1d), letter-spacing 0.1em. Content 'Oxford 4:44 AM' and 'NY 11:44 PM / PDX 8:44 PM' separated by 10px.

## Similar Brands

- **Stripe** — Monochrome base palette with precise accent colors and strong, hierarchical typography.
- **Linear** — High-contrast UI, emphasis on clean typography and functional, restrained use of color.
- **AIGA Design Archives** — Editorial feel, strong focus on stark typography, and a deliberate use of negative space.
- **Hey.com** — Clear, direct visual language with high contrast and minimal adornment, letting content lead.

## Quick Start

### CSS Custom Properties

```css
:root {
  /* Colors */
  --color-canvas-white: #ffffff;
  --color-ink-black: #1d1d1d;
  --color-accent-teal: #75fbee;

  /* Typography — Font Families */
  --font-favorit: 'Favorit', ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --font-akkuratmonollweb: 'AkkuratMonoLLWeb', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;

  /* Typography — Scale */
  --text-caption: 11px;
  --leading-caption: 1;
  --tracking-caption: 1.1px;
  --text-body: 18px;
  --leading-body: 1.7;
  --tracking-body: 1.8px;
  --text-subheading: 21px;
  --leading-subheading: 1.1;
  --tracking-subheading: -0.735px;
  --text-heading: 51px;
  --leading-heading: 1.2;
  --tracking-heading: -1.785px;
  --text-display: 60px;
  --leading-display: 1.2;
  --tracking-display: -2.1px;

  /* Typography — Weights */
  --font-weight-regular: 400;

  /* Spacing */
  --spacing-5: 5px;
  --spacing-10: 10px;
  --spacing-19: 19px;
  --spacing-20: 20px;
  --spacing-30: 30px;
  --spacing-75: 75px;
  --spacing-113: 113px;

  /* Layout */
  --section-gap: 113px;
  --card-padding: 20px;
  --element-gap: 10px;

  /* Semantic layer — themed */
  --white: var(--color-canvas-white);
  --ink: var(--color-ink-black);
  --teal: var(--color-accent-teal);
  --on-teal: var(--color-ink-black);
  --rule: 1px solid var(--ink);
  --surface: var(--color-canvas-white);
  --surface-subtle: rgba(0, 0, 0, 0.02);
}

/* Dark theme — a pure inversion of canvas and ink */
[data-theme="dark"] {
  --white: var(--color-ink-black);
  --ink: var(--color-canvas-white);
  --on-teal: var(--color-ink-black);
  --surface: var(--color-ink-black);
  --surface-subtle: rgba(255, 255, 255, 0.04);
}
```

### Tailwind v4

```css
@theme {
  /* Colors */
  --color-canvas-white: #ffffff;
  --color-ink-black: #1d1d1d;
  --color-accent-teal: #75fbee;

  /* Typography */
  --font-favorit: 'Favorit', ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --font-akkuratmonollweb: 'AkkuratMonoLLWeb', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;

  /* Typography — Scale */
  --text-caption: 11px;
  --leading-caption: 1;
  --tracking-caption: 1.1px;
  --text-body: 18px;
  --leading-body: 1.7;
  --tracking-body: 1.8px;
  --text-subheading: 21px;
  --leading-subheading: 1.1;
  --tracking-subheading: -0.735px;
  --text-heading: 51px;
  --leading-heading: 1.2;
  --tracking-heading: -1.785px;
  --text-display: 60px;
  --leading-display: 1.2;
  --tracking-display: -2.1px;

  /* Spacing */
  --spacing-5: 5px;
  --spacing-10: 10px;
  --spacing-19: 19px;
  --spacing-20: 20px;
  --spacing-30: 30px;
  --spacing-75: 75px;
  --spacing-113: 113px;
}
```
