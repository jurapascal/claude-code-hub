---
name: learn
description: Ulož poznatek, chybu nebo úspěch do paměti
---
Save a learning, error, or win to memory as a linked Obsidian note. (Sibling of `/save` with explicit keyword classification.)

## Instructions

1. Parse the input from `$ARGUMENTS`. If empty, ask: "Co chceš zaznamenat? Popis situace a poučení."
2. Determine the type from keywords:
   - "error", "bug", "chyba", "problém", "past" → `error_<slug>.md`
   - "win", "success", "hotovo", "fungovalo", "nasazeno" → `win_<slug>.md`
   - otherwise → `learning_<slug>.md`
   - all use `metadata.type: reference`
3. Write ONE file to `{{MEMORY_DIR}}/` in the standard format:
   ```markdown
   ---
   name: <filename without .md>
   description: <one-line CZ summary for recall>
   metadata:
     type: reference
   ---

   **Kontext:** ...
   **Detail:** ...
   **Poučení:** ...
   **Souvisí:** [[...]]
   ```
4. Add a one-line pointer to `{{MEMORY_DIR}}/MEMORY.md` under the matching section.
5. Update an existing note instead of duplicating if one already covers it.
6. Confirm: `Uloženo: <file>`.

## Notes

- Same unified per-note memory as `/save`.
- Prefer `/save` when you just want to capture "what we just did" without typing.
