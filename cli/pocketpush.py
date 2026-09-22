#!/usr/bin/env python3
"""
PocketPush CLI
Push a whole project folder to a GitHub repository in a single clean commit,
using only the Python 3 standard library (no pip installs required).

Usage:
    python3 pocketpush.py --token ghp_xxx --owner you --repo your-repo --path ./project
    python3 pocketpush.py                      # interactive, asks for everything
    python3 pocketpush.py --lang fa             # Persian prompts

See `python3 pocketpush.py --help` for all options.
"""

import argparse
import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"  # pinned so GitHub changing its default API version can't silently break this script
USER_AGENT = "PocketPush-CLI/2.0"

EXCLUDED_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build", ".next"}
EXCLUDED_FILES = {".DS_Store", "Thumbs.db"}
MAX_FILE_BYTES = 90 * 1024 * 1024  # GitHub's Git Data API blob limit is 100 MB; stay safely under it
MAX_RETRIES = 6

STRINGS = {
    "en": {
        "ask_token": "GitHub personal access token: ",
        "ask_owner": "Repository owner (user or org): ",
        "ask_repo": "Repository name: ",
        "ask_path": "Path to the project folder [.]: ",
        "ask_branch": "Branch [main]: ",
        "ask_message": "Commit message [Upload project folder]: ",
        "ask_target": "Target subfolder inside the repo (optional): ",
        "scanning": "Scanning {path} …",
        "found": "Found {n} file(s), {size} total.",
        "skipping_large": "Skipping {name}: larger than the size limit ({size}).",
        "skipping_env": "Skipping {name}: looks like a secrets file (.env).",
        "no_files": "No files to upload after filtering — nothing to do.",
        "auth_check": "Checking token and repository access…",
        "auth_ok": "Authenticated as {who}.",
        "repo_empty": "Repository has no commits yet — creating the first file to initialize it…",
        "repo_init_done": "Repository initialized.",
        "branch_new": "Branch \"{branch}\" doesn't exist yet — it will be created from this push.",
        "uploading": "Uploading {n} file(s) as blobs…",
        "blob_progress": "  {done}/{total} blobs uploaded",
        "building_tree": "Building the tree…",
        "creating_commit": "Creating the commit…",
        "updating_ref": "Updating branch \"{branch}\"…",
        "done": "Done. View it at: {url}",
        "retry": "Rate limited (HTTP {code}) — retrying in {wait}s… (attempt {attempt}/{max})",
        "retry_server": "Server error (HTTP {code}) — retrying in {wait}s… (attempt {attempt}/{max})",
        "err_auth": "GitHub rejected the token (401). Check that it's valid and hasn't expired.",
        "err_forbidden": "Access denied (403). The token may lack permission for this repository.",
        "err_not_found": "Repository not found (404). Check the owner, repo name, and token access.",
        "err_generic": "Error: {msg}",
        "cancelled": "Cancelled by user.",
        "dry_run_header": "Dry run — no changes will be made. Files that would be pushed:",
    },
    "fa": {
        "ask_token": "توکن دسترسی شخصی گیت‌هاب: ",
        "ask_owner": "صاحب ریپو (یوزر یا سازمان): ",
        "ask_repo": "نام ریپو: ",
        "ask_path": "مسیر پوشه‌ی پروژه [.]: ",
        "ask_branch": "شاخه [main]: ",
        "ask_message": "پیام کامیت [Upload project folder]: ",
        "ask_target": "زیرپوشه‌ی مقصد داخل ریپو (اختیاری): ",
        "scanning": "در حال اسکن {path} …",
        "found": "{n} فایل پیدا شد، مجموع {size}.",
        "skipping_large": "رد شدن از {name}: بزرگ‌تر از سقف مجازه ({size}).",
        "skipping_env": "رد شدن از {name}: به‌نظر فایل رمز/env هست.",
        "no_files": "بعد از فیلتر کردن، فایلی برای آپلود نمونده.",
        "auth_check": "در حال بررسی توکن و دسترسی به ریپو…",
        "auth_ok": "با حساب {who} وارد شدی.",
        "repo_empty": "ریپو هنوز کامیتی نداره — اول یه فایل می‌سازیم تا فعال بشه…",
        "repo_init_done": "ریپو فعال شد.",
        "branch_new": "شاخه‌ی «{branch}» هنوز وجود نداره — با همین push ساخته می‌شه.",
        "uploading": "در حال آپلود {n} فایل به‌صورت blob…",
        "blob_progress": "  {done}/{total} blob آپلود شد",
        "building_tree": "در حال ساخت tree…",
        "creating_commit": "در حال ساخت commit…",
        "updating_ref": "در حال به‌روزرسانی شاخه‌ی «{branch}»…",
        "done": "تمام شد. اینجا ببین: {url}",
        "retry": "محدودیت نرخ (HTTP {code}) — تلاش دوباره تا {wait} ثانیه دیگه… (تلاش {attempt}/{max})",
        "retry_server": "خطای سرور (HTTP {code}) — تلاش دوباره تا {wait} ثانیه دیگه… (تلاش {attempt}/{max})",
        "err_auth": "توکن رد شد (۴۰۱). مطمئن شو معتبره و منقضی نشده.",
        "err_forbidden": "دسترسی رد شد (۴۰۳). شاید توکن دسترسی کافی به این ریپو نداره.",
        "err_not_found": "ریپو پیدا نشد (۴۰۴). صاحب ریپو، اسم ریپو و دسترسی توکن رو چک کن.",
        "err_generic": "خطا: {msg}",
        "cancelled": "توسط کاربر لغو شد.",
        "dry_run_header": "اجرای آزمایشی — هیچ تغییری اعمال نمی‌شه. فایل‌هایی که push می‌شدن:",
    },
}


class Lang:
    def __init__(self, code):
        self.code = code if code in STRINGS else "en"

    def __call__(self, key, **kwargs):
        s = STRINGS[self.code][key]
        return s.format(**kwargs) if kwargs else s


def human_size(n):
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n/1024:.1f} KB"
    return f"{n/1024/1024:.1f} MB"


def is_env_file(name):
    return name == ".env" or name.startswith(".env.")


class GitHubError(Exception):
    def __init__(self, status, message, body=None):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message
        self.body = body


class GitHubClient:
    def __init__(self, token, lang):
        self.token = token
        self.lang = lang
        self.ctx = ssl.create_default_context()

    def request(self, method, path, payload=None):
        url = API_ROOT + path
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
        }
        attempt = 0
        while True:
            attempt += 1
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, context=self.ctx, timeout=60) as resp:
                    body = resp.read()
                    return resp.status, (json.loads(body) if body else None)
            except urllib.error.HTTPError as e:
                body_raw = e.read()
                try:
                    body = json.loads(body_raw) if body_raw else None
                except json.JSONDecodeError:
                    body = None
                if e.code in (403, 429) and attempt <= MAX_RETRIES:
                    retry_after = e.headers.get("Retry-After")
                    msg_text = (body or {}).get("message", "") if isinstance(body, dict) else ""
                    # A 403 can mean "rate limited" or "genuinely not allowed" — only retry the
                    # former, otherwise a real permissions error would sit retrying for a while.
                    looks_rate_limited = e.code == 429 or bool(retry_after) or "rate limit" in msg_text.lower() or "abuse" in msg_text.lower()
                    if looks_rate_limited:
                        wait = int(retry_after) if retry_after else min(60, 2 ** attempt)
                        print(self.lang("retry", code=e.code, wait=wait, attempt=attempt, max=MAX_RETRIES))
                        time.sleep(wait)
                        continue
                if e.code >= 500 and attempt <= MAX_RETRIES:
                    wait = 2 * attempt
                    print(self.lang("retry_server", code=e.code, wait=wait, attempt=attempt, max=MAX_RETRIES))
                    time.sleep(wait)
                    continue
                msg = (body or {}).get("message", str(e)) if isinstance(body, dict) else str(e)
                raise GitHubError(e.code, msg, body)
            except urllib.error.URLError as e:
                if attempt <= MAX_RETRIES:
                    time.sleep(2 * attempt)
                    continue
                raise

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, payload):
        return self.request("POST", path, payload)

    def patch(self, path, payload):
        return self.request("PATCH", path, payload)

    def put(self, path, payload):
        return self.request("PUT", path, payload)


def collect_files(root, include_heavy, lang):
    print(lang("scanning", path=root))
    collected = []
    total_bytes = 0
    for dirpath, dirnames, filenames in os.walk(root):
        if not include_heavy:
            dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        else:
            dirnames[:] = [d for d in dirnames if d != ".git"]  # .git is never included
        for fname in filenames:
            if fname in EXCLUDED_FILES:
                continue
            if is_env_file(fname):
                print(lang("skipping_env", name=fname))
                continue
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                print(lang("skipping_large", name=rel, size=human_size(MAX_FILE_BYTES)))
                continue
            collected.append((rel, full, size))
            total_bytes += size
    print(lang("found", n=len(collected), size=human_size(total_bytes)))
    return collected


def join_path(target, p):
    return f"{target.rstrip('/')}/{p}" if target else p


def encode_path(p):
    # encode each segment on its own so literal "/" separators survive in the URL
    return "/".join(urllib.parse.quote(seg) for seg in p.split("/"))


def encode_ref(branch):
    # branch names may legitimately contain "/" (e.g. "feature/login") — encode
    # each segment individually so the slash stays a literal path separator
    return "/".join(urllib.parse.quote(seg) for seg in branch.split("/"))


def push(args, lang):
    client = GitHubClient(args.token, lang)

    files = collect_files(args.path, args.include_heavy, lang)
    if not files:
        print(lang("no_files"))
        return

    if args.dry_run:
        print(lang("dry_run_header"))
        for rel, _, size in files:
            print(f"  {rel}  ({human_size(size)})")
        return

    print(lang("auth_check"))
    _, me = client.get("/user")
    print(lang("auth_ok", who=me["login"]))

    owner, repo, branch = args.owner, args.repo, args.branch
    parent_commit_sha = None
    base_tree_sha = None
    branch_exists = True

    try:
        status, ref = client.get(f"/repos/{owner}/{repo}/git/ref/heads/{encode_ref(branch)}")
        parent_commit_sha = ref["object"]["sha"]
    except GitHubError as e:
        # GitHub reports a branch-less repo as 404 "Not Found" in most cases, but as
        # 409 "Git Repository is empty." in others — both mean the same thing here.
        if e.status not in (404, 409):
            raise
        branch_exists = False
        _, repo_info = client.get(f"/repos/{owner}/{repo}")
        repo_is_empty = e.status == 409 or repo_info.get("size", 0) == 0
        if repo_is_empty:
            print(lang("repo_empty"))
            first_rel, first_full, _ = files[0]
            with open(first_full, "rb") as fh:
                content_b64 = base64.b64encode(fh.read()).decode("ascii")
            client.put(
                f"/repos/{owner}/{repo}/contents/{encode_path(join_path(args.target, first_rel))}",
                {"message": args.message, "content": content_b64, "branch": branch},
            )
            print(lang("repo_init_done"))
            files = files[1:]
            _, ref2 = client.get(f"/repos/{owner}/{repo}/git/ref/heads/{encode_ref(branch)}")
            parent_commit_sha = ref2["object"]["sha"]
            branch_exists = True  # the bootstrap commit above just created this branch
        else:
            print(lang("branch_new", branch=branch))
            default_branch = repo_info["default_branch"]
            _, default_ref = client.get(f"/repos/{owner}/{repo}/git/ref/heads/{encode_ref(default_branch)}")
            parent_commit_sha = default_ref["object"]["sha"]

    if parent_commit_sha:
        _, parent_commit = client.get(f"/repos/{owner}/{repo}/git/commits/{parent_commit_sha}")
        base_tree_sha = parent_commit["tree"]["sha"]

    if not files:
        # either nothing to push, or a single-file repo init already did everything needed
        print(lang("done", url=f"https://github.com/{owner}/{repo}"))
        return

    if files:
        print(lang("uploading", n=len(files)))
        tree_entries = []
        for i, (rel, full, size) in enumerate(files, 1):
            with open(full, "rb") as fh:
                content_b64 = base64.b64encode(fh.read()).decode("ascii")
            _, blob = client.post(f"/repos/{owner}/{repo}/git/blobs", {"content": content_b64, "encoding": "base64"})
            tree_entries.append({
                "path": join_path(args.target, rel),
                "mode": "100644",
                "type": "blob",
                "sha": blob["sha"],
            })
            if i % 10 == 0 or i == len(files):
                print(lang("blob_progress", done=i, total=len(files)))

        print(lang("building_tree"))
        tree_body = {"tree": tree_entries}
        if base_tree_sha:
            tree_body["base_tree"] = base_tree_sha
        _, tree = client.post(f"/repos/{owner}/{repo}/git/trees", tree_body)

        print(lang("creating_commit"))
        commit_body = {"message": args.message, "tree": tree["sha"]}
        if parent_commit_sha:
            commit_body["parents"] = [parent_commit_sha]
        _, commit = client.post(f"/repos/{owner}/{repo}/git/commits", commit_body)

        print(lang("updating_ref", branch=branch))
        if branch_exists:
            client.patch(f"/repos/{owner}/{repo}/git/refs/heads/{encode_ref(branch)}", {"sha": commit["sha"], "force": False})
        else:
            client.post(f"/repos/{owner}/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})

        print(lang("done", url=f"https://github.com/{owner}/{repo}/commit/{commit['sha']}"))


def interactive_fill(args, lang, ask_target=True):
    if not args.token:
        args.token = input(lang("ask_token"))
    if not args.owner:
        args.owner = input(lang("ask_owner"))
    if not args.repo:
        args.repo = input(lang("ask_repo"))
    if args.path is None:
        p = input(lang("ask_path"))
        args.path = p.strip() or "."
    if not args.branch:
        b = input(lang("ask_branch"))
        args.branch = b.strip() or "main"
    if not args.message:
        m = input(lang("ask_message"))
        args.message = m.strip() or "Upload project folder"
    if ask_target and not args.target:
        args.target = input(lang("ask_target")).strip()
    return args


def main():
    parser = argparse.ArgumentParser(description="Push a project folder to GitHub in one clean commit.")
    parser.add_argument("--token", help="GitHub personal access token")
    parser.add_argument("--owner", help="Repository owner (user or org)")
    parser.add_argument("--repo", help="Repository name")
    parser.add_argument("--path", help="Local folder to push (default: current directory)")
    parser.add_argument("--branch", default="main", help="Target branch (default: main)")
    parser.add_argument("--message", default="Upload project folder", help="Commit message")
    parser.add_argument("--target-folder", dest="target", default="", help="Subfolder inside the repo to push into")
    parser.add_argument("--include-heavy", action="store_true",
                         help="Include node_modules/venv/build-type folders (excluded by default)")
    parser.add_argument("--dry-run", action="store_true", help="List what would be pushed and exit")
    parser.add_argument("--lang", choices=["en", "fa"], default="en", help="Interface language")
    args = parser.parse_args()

    lang = Lang(args.lang)

    # Only the fields genuinely needed to do anything (token/owner/repo/path) trigger a
    # prompt; the target folder is only asked about when running fully interactively
    # (no flags at all), so a partially-scripted invocation never blocks on stdin.
    fully_interactive = not any([args.token, args.owner, args.repo, args.path])
    needs_prompt = not all([args.token, args.owner, args.repo]) or args.path is None
    if needs_prompt:
        try:
            args = interactive_fill(args, lang, ask_target=fully_interactive)
        except EOFError:
            print(lang("err_generic", msg="Required arguments are missing and no interactive terminal is available. Pass --token, --owner, --repo and --path directly."))
            sys.exit(1)
    if args.path is None:
        args.path = "."

    # defense in depth: a target folder can never escape the repo tree with ".."
    args.target = "/".join(seg for seg in args.target.strip("/").split("/") if seg and seg != "..")

    args.path = os.path.abspath(os.path.expanduser(args.path))
    if not os.path.isdir(args.path):
        print(lang("err_generic", msg=f"'{args.path}' is not a directory."))
        sys.exit(1)

    try:
        push(args, lang)
    except KeyboardInterrupt:
        print("\n" + lang("cancelled"))
        sys.exit(130)
    except GitHubError as e:
        if e.status == 401:
            print(lang("err_auth") + (f" — {e.message}" if e.message else ""))
        elif e.status == 403:
            print(lang("err_forbidden") + (f" — {e.message}" if e.message else ""))
        elif e.status == 404:
            print(lang("err_not_found") + (f" — {e.message}" if e.message else ""))
        else:
            print(lang("err_generic", msg=e.message))
        sys.exit(1)


if __name__ == "__main__":
    main()
