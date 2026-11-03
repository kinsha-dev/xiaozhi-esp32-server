"""Voice search of GitHub through the Mac's already signed-in `gh` CLI.

Tool: github_search(query, kind, owner, open_top). Read-only: it only runs `gh search ...` (repos, issues, pull
requests, code) as an argument list, never a shell, never a write command. Results speak as a count plus the top
three (name and a short description); with open_top the best match opens in the default browser, only if it is a
https://github.com address. Queries go to GitHub, which is the point; nothing else leaves the machine.
"""
import json
import os
import re
import subprocess
from urllib.parse import urlparse

from plugins_func.register import register_function, ToolType, ActionResponse, Action
from plugins_func.functions import mac_control as mc
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()

GH_BIN = "/opt/homebrew/bin/gh"
KINDS = {
    "repos": ("repos", "fullName,description,stargazersCount,url"),
    "issues": ("issues", "title,repository,number,state,url"),
    "prs": ("prs", "title,repository,number,state,url"),
    "code": ("code", "repository,path,url"),
}
MAX_QUERY_CHARS = 200
SPOKEN_RESULTS = 3
OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")


def _describe(kind, item):
    if kind == "repos":
        desc = mc._shorten(item.get("description") or "", 70)
        return f"{item.get('fullName')}" + (f", {desc}" if desc else "")
    repo = (item.get("repository") or {}).get("nameWithOwner", "")
    if kind == "code":
        return f"{item.get('path')} in {repo}"
    return f"{mc._shorten(item.get('title') or '', 70)} in {repo}, {item.get('state', '')}"


def _open(url):
    p = urlparse(url or "")
    if p.scheme == "https" and p.hostname == "github.com":
        mc._run(["open", "-a", os.environ.get("VIBI_BROWSER", "Google Chrome"), url], timeout=10)
        return True
    return False


github_search_desc = {
    "type": "function",
    "function": {
        "name": "github_search",
        "description": (
            "Search GitHub for repositories, issues, pull requests or code and read out the top results. Use when "
            "the user says 'search GitHub for ...', 'find a repo for ...', 'any issues about ...'. Set open_top "
            "true if they ask to open or show it. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for, in plain words."},
                "kind": {"type": "string", "enum": list(KINDS), "description": "repos (default), issues, prs or code."},
                "owner": {"type": "string", "description": "Limit to a GitHub user or organisation, only if the user names one."},
                "open_top": {"type": "boolean", "description": "Open the best match in the browser."},
            },
            "required": ["query"],
        },
    },
}


@register_function("github_search", github_search_desc, ToolType.SYSTEM_CTL)
def github_search(conn, query, kind="repos", owner=None, open_top=False):
    query = " ".join((query or "").split())
    if not query:
        return ActionResponse(Action.RESPONSE, "empty query", "What should I search GitHub for?")
    if len(query) > MAX_QUERY_CHARS:
        return ActionResponse(Action.RESPONSE, "query too long", "That search is too long. Please shorten it.")
    if kind not in KINDS:
        kind = "repos"
    sub, fields = KINDS[kind]
    cmd = [GH_BIN, "search", sub, "--limit", "10", "--json", fields]
    if owner:
        if not OWNER_RE.match(owner):
            return ActionResponse(Action.RESPONSE, "bad owner", "I didn't catch a valid GitHub name.")
        cmd += ["--owner", owner]
    cmd += ["--", query]  # '--' keeps a spoken query from being read as a flag
    try:
        r = mc._run(cmd, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.bind(tag=TAG).warning(f"gh search failed: {e}")
        return ActionResponse(Action.RESPONSE, f"gh error: {e}", "The GitHub search didn't work.")
    if r.returncode != 0:
        err = mc._shorten((r.stderr or "").strip(), 150)
        logger.bind(tag=TAG).warning(f"gh search exit {r.returncode}: {err}")
        return ActionResponse(Action.RESPONSE, f"gh exit {r.returncode}: {err}", "The GitHub search didn't work. Check that gh is signed in.")
    try:
        items = json.loads(r.stdout or "[]")
    except ValueError:
        items = []
    if not items:
        return ActionResponse(Action.RESPONSE, "no results", f"I found no GitHub {kind} for {query}.")
    top = items[:SPOKEN_RESULTS]
    spoken = f"I found {len(items)}{'+' if len(items) >= 10 else ''} {kind} for {query}. " + ". ".join(
        f"{i + 1}, {_describe(kind, it)}" for i, it in enumerate(top)) + "."
    if open_top:
        spoken += " Opening the first one." if _open(top[0].get("url")) else " I couldn't open that one."
    return ActionResponse(Action.RESPONSE, "\n".join(f"{it.get('url')}" for it in top), spoken)
