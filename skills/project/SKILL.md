---
name: project
description: Založ nebo aktualizuj poznámku k projektu v paměti
---
Create or refresh the Obsidian memory note for the current folder — so every project you work in ends up in memory, linked and listed.

## Instructions

1. Project dir = current working directory. `slug` = folder name in kebab-case.
2. Detect (don't guess — only record what you can verify):
   - **type**: PHP / Shopify / Node / static — check for `.php`, `sections/`+`templates/` (Shopify), `package.json`, `composer.json`
   - **git**: remote URL + branch (`git remote -v`, `git branch --show-current`)
   - **deploy method**: `.ftp-deploy.json` present → FTP; git remote present → git; else unknown
   - **hosting** hints from config/README only if explicit
   - key entry files
3. Write `{{MEMORY_DIR}}/project_<slug>.md` with:
   ```markdown
   ---
   name: project_<slug>
   description: <one-line CZ: what the project is + hosting/deploy>
   metadata:
     type: project
   ---

   **Stack:** ...
   **Hosting:** ...
   **Deploy:** git / FTP (`/deploy` or `/ftp`)
   **Git:** <remote>

   ## Cíl
   <what we're doing — from context, or ask ONE short question>

   ## TODO
   - [ ] ...

   **Souvisí:** [[...]]
   ```
4. If the note already exists → **update** it, preserving existing TODO items.
5. Ensure a pointer line exists in `{{MEMORY_DIR}}/MEMORY.md` under `## Projekty a reference`.
6. Confirm: `Projekt <name> zapsán do paměti.`

## Notes

- This is the note to run when you open a folder you haven't documented yet.
- The memory folder is the Obsidian vault → the note shows up in the graph right away.
