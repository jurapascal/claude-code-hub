---
name: audit
description: Vizuální a technický audit webu
---
Audit a website — visual check plus a basic performance/accessibility review.

## Instructions

1. Get the URL from `$ARGUMENTS` (or ask the user).
2. Take screenshots (desktop + mobile) with headless Chrome:
   ```
   google-chrome --headless --disable-gpu --screenshot=/tmp/audit-desktop.png --window-size=1400,2000 --run-all-compositor-stages-before-draw --virtual-time-budget=5000 '<URL>'
   google-chrome --headless --disable-gpu --screenshot=/tmp/audit-mobile.png  --window-size=390,844  --run-all-compositor-stages-before-draw --virtual-time-budget=5000 '<URL>'
   ```
3. Show both screenshots.
4. Fetch the page HTML via curl and check:
   - Meta tags (title, description, OG tags)
   - Viewport meta tag
   - Missing `alt` attributes on images
   - Large inline styles
   - Broken asset references
5. Report findings as a checklist (✅ / ❌ per check) with concrete fixes.

## Optional deeper checks (if the user asks)

- Response headers (security headers, caching)
- Mobile usability issues
- Page weight: sizes of the largest assets
