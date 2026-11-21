"""Keeps Handbook Live up to date from the handbook's update page.

Once a day (and never while testing) the app reads <UPDATE_BASE>latest.json, a small public file listing the newest
version and the SHA-256 of every file the app is made of. Files whose fingerprint differs from the copy on this
computer are downloaded from the same page, checked against that list, and only then swapped in. Nothing about you
is sent: it is a plain download, like opening a web page. Turn it off with "update_check": false in settings.json.

latest.json:
  {"version": "11.1.0", "notes": ["..."], "page": {"download": "<zip url>"},
   "app": {"files": [{"path": "live_app.py", "src": "app/live_app.py", "sha256": "...", "size": 123}, ...]}}
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import urllib.parse
import urllib.request

UPDATE_BASE = "https://munk11op.github.io/apologetics-handbook/"   # the update page (GitHub Pages); empty = no update checks
SAFE_PATH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_ .-]*(/[A-Za-z0-9_][A-Za-z0-9_ .-]*)*$")
SAFE_EXT = (".html", ".js", ".py", ".txt", ".ico", ".png", ".icns", ".md")
NEVER = {"settings.json", "live.log"}
HEADERS = {"User-Agent": "HandbookLive", "Cache-Control": "no-cache"}


def newer(a, b):
    def parts(v):
        return ([int(x) for x in re.findall(r"\d+", str(v))[:3]] + [0, 0, 0])[:3]
    return parts(a) > parts(b)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def get(url, limit, timeout=20):
    if not url.startswith("https://"):
        raise ValueError("updates come only over https")
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("file is larger than expected")
    return data


def fetch_manifest(base=None):
    base = base or UPDATE_BASE
    if not base:
        return None
    return json.loads(get(urllib.parse.urljoin(base, "latest.json"), 2_000_000).decode("utf-8"))


def safe_entry(e):
    p, src = str(e.get("path", "")), str(e.get("src", ""))
    ok = (SAFE_PATH.match(p) and ".." not in p and p.lower().endswith(SAFE_EXT) and p.split("/")[-1] not in NEVER
          and SAFE_PATH.match(src) and ".." not in src and re.fullmatch(r"[0-9a-f]{64}", str(e.get("sha256", ""))))
    return bool(ok)


def plan(manifest, here):
    """The files that differ from this computer's copy: [(entry, local path)]."""
    out = []
    for e in (manifest.get("app") or {}).get("files") or []:
        if not safe_entry(e):
            continue
        local = os.path.join(here, *e["path"].split("/"))
        if not (os.path.exists(local) and sha256_file(local) == e["sha256"]):
            out.append((e, local))
    return out


def apply(manifest, here, base=None, progress=None):
    """Downloads every changed file into a holding folder, checks each fingerprint, then swaps them all in.
    Returns the list of paths changed. Raises (changing nothing) if any download fails or doesn't match."""
    base = base or UPDATE_BASE
    todo = plan(manifest, here)
    if not todo:
        return []
    hold = os.path.join(here, ".update")
    shutil.rmtree(hold, ignore_errors=True)
    os.makedirs(hold, exist_ok=True)
    staged = []
    try:
        for i, (e, local) in enumerate(todo):
            data = get(urllib.parse.urljoin(base, e["src"]), int(e.get("size") or 0) + 1024 if e.get("size") else 60_000_000, timeout=60)
            if hashlib.sha256(data).hexdigest() != e["sha256"]:
                raise ValueError(f"{e['path']} didn't match its fingerprint")
            tmp = os.path.join(hold, f"{i}.part")
            with open(tmp, "wb") as f:
                f.write(data)
            staged.append((tmp, local, e["path"]))
            if progress:
                progress(i + 1, len(todo))
        for tmp, local, _ in staged:
            os.makedirs(os.path.dirname(local), exist_ok=True)
            os.replace(tmp, local)
    finally:
        shutil.rmtree(hold, ignore_errors=True)
    return [p for _, _, p in staged]
