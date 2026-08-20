---
name: status
description: Přehled všech projektů a jejich git stavů
---
Show an overview of all projects and the current work status.

## Instructions

1. Run `{{PYTHON}} {{CLAUDE_DIR}}/hooks/save-session.py` to refresh the session state.
2. Read `{{STATE_FILE}}`.
3. Present the info in a clean format:
   - Which projects have uncommitted changes
   - Recently modified files
   - Current git branch per project
4. If the user asks about a specific project, cd into it and show a detailed `git status` and `git log --oneline -5`.

## Output format

Show a clean table with project status. Highlight projects with uncommitted changes.
If a project has been inactive (last commit > 2 months), mark it as stale.

## Notes

- Which folders get scanned comes from `project_dirs` in `{{CLAUDE_DIR}}/hub-config.json`.
