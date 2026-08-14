---
name: ftp
description: FTP/FTPS/SFTP deploy projektu
---
Deploy the current project via FTP/FTPS/SFTP. First time you paste credentials, after that just `/ftp`.

## Instructions

1. Project dir = current working directory.
2. Check if `.ftp-deploy.json` exists in the project root:
   - **YES** → just deploy: `bash {{FTP_DEPLOY}} <dir> --yes`
   - **NO (first time)** → do NOT rely on the script's interactive prompt (it would hang here). Instead:
     a. Ask the user for: host, protocol (ftp/ftps/sftp), port, username, password, remote directory (e.g. `/www`).
     b. Write `.ftp-deploy.json` yourself in the project root — JSON with keys: `host, port, protocol, user, password, remote_dir, local_dir` (default `.`), and `exclude` (default `[".git/","node_modules/",".ftp-deploy.json",".ftp-deploy-sync-state.json",".DS_Store","*.log",".vscode/"]`).
     c. `chmod 600 .ftp-deploy.json` and append `.ftp-deploy.json` to `.gitignore`.
     d. Then run `bash {{FTP_DEPLOY}} <dir> --yes`.
3. If `$ARGUMENTS` mentions "dry" / "test" → add `--dry-run` (preview, uploads nothing).
4. Report what was uploaded and any errors.

## Notes

- The deploy script `{{FTP_DEPLOY}}` is **not** part of this repo — it's a personal tool. If it's missing, tell the user and offer `lftp`/`curl` commands directly instead.
- Password is stored plaintext in the **gitignored** `.ftp-deploy.json` (per-project) — the chosen tradeoff. Never print the password back.
- `lftp` is preferred (uploads only changed files, fast). Without it the script falls back to `curl` (per-file). Suggest `sudo apt-get install -y lftp` once.
- Change credentials later: `bash {{FTP_DEPLOY}} <dir> --setup`.
