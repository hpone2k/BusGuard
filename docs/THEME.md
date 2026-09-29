# BusTech green theme

The passenger app, detection console and 3D bus viewer adapt RailGuard's translucent panels,
fine borders, soft shadows and restrained typography into a light-green palette.
Their styles live in `static/passenger/style.css`, `static/style.css` and
`static/bus/showcase.css`. The bus studio background, fog, floor and lighting
also use sage/green tones in `static/bus/src/app.js`.

| Role | Colour |
| --- | --- |
| Page / sage | `#d6ebcc` |
| Mint surface | `#e0f0d6` / `#cfe6ce` |
| Primary ink | `#173e30` |
| Secondary ink | `#405d48` |
| Forest action | `#24583f` |
| Leaf accent | `#a8d78b` |
| Subtle border | `#abc6a5` |

Use amber and terracotta for warning/error states so meaning does not depend on
green alone. Labels and icons accompany status colours. Camera media has a deep
green surround to keep frames and overlays legible. Passenger high-contrast mode
retains green surfaces while strengthening text and borders.

The viewer keeps the Singapore lime bus livery and coloured accessibility signs.
Its glass blur is limited to 12px on desktop and 8px on small screens. See
[viewer performance and a separate display device](VIEWER.md).
