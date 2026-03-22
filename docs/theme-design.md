# SciPlotter Theme Design

Source logo: `assets/SciPlotter_logo.png`

## Core Brand Colors

- Navy blue background: `#021535`
- Dominant neon cyan: `#1B76B3`
- Dominant neon pink: `#AD47B7`
- Text white: `#FFFFFF`

## Suggested Theme Tokens

```css
:root {
  --brand-bg: #021535;
  --brand-cyan: #1B76B3;
  --brand-cyan-strong: #145A88;
  --brand-pink: #AD47B7;
  --brand-pink-soft: rgba(173, 71, 183, 0.14);
  --brand-pink-ring: rgba(173, 71, 183, 0.28);
  --brand-text: #FFFFFF;
}
```

## Common Button Theme

Use the export-toolbar button treatment as the default button system across the app.

```css
button,
.btn-primary,
.btn-secondary {
  color: #FFFFFF;
  background: linear-gradient(180deg, #1B76B3 0%, #145A88 100%);
  border: 1px solid #145A88;
  border-radius: 4px;
  box-shadow: 0 8px 18px rgba(27, 118, 179, 0.2);
  transition: transform 120ms ease, box-shadow 120ms ease, background-color 120ms ease, border-color 120ms ease;
}

button:hover,
.btn-primary:hover,
.btn-secondary:hover {
  transform: translateY(-1px);
  background: linear-gradient(180deg, #2285CA 0%, #166391 100%);
}

button:active,
.btn-primary:active,
.btn-secondary:active {
  transform: translateY(0);
}

button:disabled,
.btn-primary:disabled,
.btn-secondary:disabled {
  background: #D7E6F0;
  border-color: #BFD0DB;
  color: #6B7280;
  box-shadow: none;
}
```

Apply neon pink only to focus, selection, outline, and active-highlight states.

## Textboxtextbox Pattern

Use `textboxtextbox` as the standard two-column control pattern for histogram side-panel rows that follow:

- `text [box] | text [box]`
- same input box dimensions on both sides
- one vertical divider separating the two logical columns
- left and right label/input spacing kept visually balanced

This pattern is the standard for:

- Binning
- Axes & Scale numeric min/max rows
- Colors (2D) Z scale min/max row

For numeric `textboxtextbox` fields:

- use native `type="number"` inputs
- keep the same hover modifier arrows as the Plot header `X` and `Y` inputs
- do not replace those modifiers with custom arrow buttons unless browser behavior forces a fallback

The visual goal is one reusable control language across the histogram left panel, not separate one-off row designs.

## Notes

- `#021535` is the dominant exact background pixel from the logo.
- `#1B76B3` is the representative cyan derived from the neon waveform region.
- `#145A88` is the darker cyan stop used for button gradients and borders.
- `#AD47B7` is the representative pink derived from the neon waveform region.
- Use white text on the navy background for the strongest match to the logo.
- Standard button look: cyan neon gradient with a soft cyan glow shadow.
- Standard focus and selection look: pink ring, pink active fill, or pink inset highlight.