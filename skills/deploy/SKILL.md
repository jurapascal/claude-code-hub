---
name: deploy
description: Nasaď projekt (FTP nebo git, auto-detekce)
---
Deploy the current project — auto-detects FTP vs git.

## Instructions

1. Project dir = current working directory.
2. **Detect the deploy method:**
   - `.ftp-deploy.json` exists → **FTP deploy**: run `bash {{FTP_DEPLOY}} <dir> --yes`. (First-time setup: see `/ftp`.)
   - else a git repo with a remote (`git remote -v` non-empty) → **git deploy** (below).
   - else → ask the user which they want; offer `/ftp` (set up FTP) or `git init` + remote.
3. If `$ARGUMENTS` mentions "dry"/"test" and it's FTP → add `--dry-run`.

## Git deploy

1. `git status` — show a short summary of what changed.
2. Suggest a commit message from the diff (or use `$ARGUMENTS` if given).
3. `git add -A && git commit -m "<message>" && git push`
4. If the branch has no upstream: `git push -u origin <branch>`.
5. Verify the push succeeded; if there's a remote URL, tell the user where it went.

## Notes

- Never force push without explicit permission.
- If there are merge conflicts, help resolve them first.
- End commit messages with the Co-Authored-By trailer if committing on the user's behalf.
