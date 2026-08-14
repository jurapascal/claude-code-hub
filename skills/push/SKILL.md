---
name: push
description: Zacommituj a pushni tenhle projekt na GitHub
---
Commit and push the current project to its git remote.

## Instructions

1. Show a short `git status` — what changed, how many files.
2. `git add -A`, then commit with a message derived from the diff (Czech, format `typ(rozsah): popis`).
   Use `$ARGUMENTS` as the message if the user gave one.
3. `git push`. If the branch has no upstream: `git push -u origin <branch>`.
4. Report the commit hash and where it went (remote URL + branch).

## Notes

- **Never** force push. On a conflict, stop and ask first.
- If the repo has no remote, offer to create one (`gh repo create`) instead of guessing.
- "nothing to commit" is not "nothing to push" — check `git log origin/<branch>..HEAD` too.
- End commit messages with the Co-Authored-By trailer when committing on the user's behalf.
