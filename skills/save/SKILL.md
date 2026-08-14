---
name: save
description: Rychle ulož co jsme právě dělali do Obsidian paměti
---
Instantly save what we just did into memory as a linked Obsidian note. Minimal typing — no arguments needed.

## Instructions

1. Look at what was accomplished/learned/fixed in the recent conversation. If `$ARGUMENTS` is given, use it as the focus/hint.
2. Classify the note `type`:
   - fixed bug / gotcha / trap → filename `error_<slug>.md`, `metadata.type: reference`
   - reusable technical insight → `learning_<slug>.md`, `type: reference`
   - shipped / worked well → `win_<slug>.md`, `type: reference`
   - fact about a project → `project_<slug>.md`, `type: project`
   - user preference / how to work → `user_*` or `feedback_*`, `type: user`/`feedback`
3. Write **ONE** file to `{{MEMORY_DIR}}/` with frontmatter:
   ```markdown
   ---
   name: <same as filename without .md>
   description: <one-line CZ summary, ~80-120 chars — used for recall relevance>
   metadata:
     type: reference | project | user | feedback
   ---

   <body: **Kontext / Detail / Poučení** in CZ, plus [[wikilinks]] to related notes>
   **Souvisí:** [[...]], [[...]]
   ```
4. Add a one-line pointer to `{{MEMORY_DIR}}/MEMORY.md` under the matching section (`## Poznatky` / `## Chyby` / `## Úspěchy` / `## Projekty a reference`): `- [Title](file.md) — hook`.
5. If a note already covers this, **update** it instead of creating a duplicate.
6. Confirm in ONE line: `Uloženo: <file> → <title>`.

## Notes

- The memory folder is the Obsidian vault itself, so the note appears in Obsidian's graph immediately.
- Link liberally with `[[name]]` — a link to a note that doesn't exist yet is fine.
- One fact, one file. Don't dump the whole conversation.
