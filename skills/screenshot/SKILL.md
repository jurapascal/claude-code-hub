---
name: screenshot
description: Screenshot webu (desktop + mobil)
---
Take a screenshot of a website using headless Chrome.

## Instructions

1. Parse the URL from: `$ARGUMENTS`
2. If no URL given, ask the user for one.
3. Take screenshots with headless Chrome (`google-chrome`, `chromium` or `chromium-browser` — use whichever is installed):
   - Desktop: `--window-size=1400,900`
   - Mobile: `--window-size=390,844` (iPhone 14 size)
   - Always add `--headless --disable-gpu --screenshot=<file> --run-all-compositor-stages-before-draw --virtual-time-budget=5000`
4. Save to `/tmp/screenshot-desktop.png` and `/tmp/screenshot-mobile.png`.
5. Show both screenshots to the user with the Read tool.
6. If the site needs auth/cookies, ask for the cookie header, verify with `curl -H 'Cookie: ...'` first, then pass it to Chrome.

## Tips

- For full-page shots use a tall viewport: `--window-size=1400,3000`.
- If the user says "full page" / "celá stránka", use the tall viewport.
- A flatpak/snap browser may render a wrong (small) viewport — if the image looks tiny, say so instead of pretending it's the real layout.
