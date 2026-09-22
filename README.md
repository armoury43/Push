# PocketPush

Push a whole project folder to a GitHub repository in one clean commit — from your phone's browser or from a terminal. No dependencies to install, nothing runs on a server in between, and every request goes straight to `api.github.com`.

Two independent, interchangeable tools live in this repo:

| Tool | Where it runs | Requirements |
|---|---|---|
| `web/pocketpush.html` | Any modern mobile or desktop browser | None — open the file directly, no build step, no install |
| `cli/pocketpush.py` | Any machine with Python 3.8+ | None — standard library only |

Both speak the same [GitHub Git Data API](https://docs.github.com/en/rest/git) directly: create blobs → build a tree → create a commit → move the branch ref. That's a real Git commit, not a series of individual file edits, so history stays clean even for a folder with hundreds of files.

## Why this exists

The original version of this tool worked for a while and then quietly broke. The most common ways that happens with hand-rolled GitHub API scripts:

- **No pinned API version.** GitHub's REST API accepts requests without an explicit version and applies whatever the current default is. When that default changes, undeclared behavior can shift under you. This rebuild sends `X-GitHub-Api-Version: 2022-11-28` on every request, so it keeps behaving the same way regardless of what GitHub's default becomes later.
- **No retry/backoff.** A single rate-limited or momentarily-failed request would kill the whole run. This version retries rate limits (respecting `Retry-After`) and transient server errors with exponential backoff, instead of failing outright.
- **No handling for an empty repository.** A brand-new repo has no branches and no commits yet, so `git/ref/heads/...` returns 404 and a naive script crashes. Both tools detect this and bootstrap the repository with a single-file commit via the Contents API first, then proceed normally.
- **No path/content safety.** A script that blindly uploads whatever it's pointed at can leak `.git`, `node_modules`, build artifacts, or a stray `.env` file with real secrets in it straight into a public repo. Both tools exclude those by default (see below) and refuse `..` path segments.

## Quick start — web version

1. Open `web/pocketpush.html` in your phone's browser (or double-click it on desktop — it's a single self-contained file, nothing to host).
2. Switch the language to Persian or English with the toggle in the top right — this only changes the interface, not what gets pushed.
3. Paste a GitHub token, the repo owner and name, and pick a folder or a `.zip` file.
4. Tap **Push to GitHub**.

Nothing about the page is fetched from anywhere except `api.github.com`: there's no analytics, no CDN script, no external font, and a strict `Content-Security-Policy` tag blocks the page from loading anything else even if it tried to. Your token is sent only in the `Authorization` header of requests to `api.github.com`.

### About the token

Create one at **github.com → Settings → Developer settings → Personal access tokens**:

- **Fine-grained token (recommended):** scope it to the one repository you're pushing to, with **Contents: Read and write** permission.
- **Classic token:** the `repo` scope is enough.

By default the token lives only in the page's memory and is cleared the moment you close or reload the tab. Check **"Keep this token in this browser tab until I close it"** if you want it to survive a reload — it then sits in `sessionStorage`, which still disappears when the tab is closed and is never written to disk or `localStorage`.

## Quick start — CLI version

```bash
python3 cli/pocketpush.py --token ghp_xxx --owner your-username --repo your-repo --path ./my-project
```

Or just run it with no arguments and answer the prompts (add `--lang fa` for Persian prompts):

```bash
python3 cli/pocketpush.py --lang fa
```

Useful flags:

```
--branch main              # target branch (created automatically if it doesn't exist)
--target-folder apps/web   # push into a subfolder of the repo instead of the root
--message "..."            # commit message
--include-heavy            # also upload node_modules/venv/build-type folders (off by default)
--dry-run                  # list exactly what would be pushed, without touching GitHub
```

## What gets excluded automatically

Both tools skip the same things by default, so a push never accidentally includes noise or secrets:

- `.git` (always, even with `--include-heavy` / "include heavy folders" checked)
- `node_modules`, `venv`, `.venv`, `__pycache__`, `dist`, `build`, `.next`
- `.DS_Store`, `Thumbs.db`
- `.env` and any `.env.*` file, to avoid uploading local secrets by mistake

Files larger than the Git Data API's practical blob size are skipped with a warning rather than failing the whole push.

## How a push actually works

1. **Auth check** — `GET /user` confirms the token is valid before anything else happens.
2. **Resolve the branch** — if the branch exists, its current commit and tree become the starting point. If the branch doesn't exist but the repo has other commits, the push branches off the default branch. If the repo has *no* commits at all, the first file is uploaded through the Contents API to bootstrap it, then the rest proceeds normally.
3. **Upload blobs** — each file's raw bytes are base64-encoded and sent to `git/blobs`, five at a time in parallel (web) or sequentially with retry (CLI), so a big folder doesn't stall on one slow request.
4. **Build a tree** — one `git/trees` call lists every blob's path and SHA, layered on top of the branch's existing tree (`base_tree`) so files you didn't touch are left alone.
5. **Create a commit** — `git/commits` with your message, the new tree, and the previous commit as its parent.
6. **Move the branch** — `PATCH git/refs/heads/<branch>` (or `POST` to create it, if it's new) points the branch at the new commit.

If anything fails partway through, nothing has been pointed at yet — the branch ref is the last thing touched, so a failed run never leaves the branch pointing at a half-finished tree.

## Security notes

- No external scripts, fonts, or stylesheets are loaded by the web page — it works offline once opened and has nothing for a compromised CDN to tamper with.
- The page's `Content-Security-Policy` only allows network requests to `api.github.com`.
- The token is never written anywhere except the `Authorization` header of requests to `api.github.com`, and never to `localStorage`.
- All user-supplied text is rendered with `textContent`, not `innerHTML`, so a file or folder name can't inject markup into the page.
- Owner and repo names are validated against a strict character set before any request is made.
- Path entries containing `..` are rejected outright.

## License

MIT — see `LICENSE`.
