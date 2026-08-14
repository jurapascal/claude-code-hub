---
name: skill
description: Načti skill z Obsidian Brain
---
Load a skill from the Obsidian Brain vault.

## Instructions

1. Parse the skill name/keyword from: `$ARGUMENTS`
2. If no argument given, read `{{SKILLS_DIR}}/SKILLS-INDEX.md` and show the categories overview.
   If that index doesn't exist, list the category folders in `{{SKILLS_DIR}}/` instead.
3. If a keyword is given:
   - Search the index (or folder names) for matching skill names
   - If found, read the `SKILL.md` file from the matching skill directory
   - If multiple matches, show options and let the user pick
4. Once the skill is loaded, confirm: "Skill **[name]** loaded. Ready to use."
5. Follow the skill's instructions for the rest of the conversation.

## Search paths

- `{{SKILLS_DIR}}/<category>/<skill-name>/SKILL.md`
- Read `references/` inside a skill folder only when you need the detail.

## Examples

- `/skill react` → loads react-expert
- `/skill seo` → loads seo-audit or shows options
- `/skill` → shows all categories
