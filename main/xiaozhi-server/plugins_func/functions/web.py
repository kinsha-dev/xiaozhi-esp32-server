"""One tool for everything web: `web(mode, ...)` routes to the existing implementations.

Modes: answer (search_web: look something up and speak the answer), read (read_page: read a given page),
open (open_in_browser: show a page or a search on screen), github (github_search), browser (browser_task: an agent
clicks through a public site in Chrome, no confirmation). The model picks one tool and a mode instead of choosing
between five similar tools. The underlying tools keep their own safeguards; enable only `web` (and `confirm_action`)
in Intent.function_call.functions, not the individual tools.
"""
import importlib
import re

from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()

MODES = ("answer", "read", "open", "github", "browser")


def _fn(module, name):
    try:
        return getattr(importlib.import_module(f"plugins_func.functions.{module}"), name)
    except (ImportError, AttributeError) as e:
        logger.bind(tag=TAG).warning(f"web mode needs {module}.{name}: {e}")
        return None


web_desc = {
    "type": "function",
    "function": {
        "name": "web",
        "description": (
            "Everything on the internet. Pick a mode: "
            "browser = USE THIS (except for flights: use answer) whenever the user names a website to work in or wants to browse, filter, click or "
            "fill something on a site ('on LinkedIn', 'go to <site> and', 'use the browser', 'filter the results', "
            "'find me a job on <site>'). An agent drives Chrome; url = the site's start page (https://...), "
            "query = the goal in plain words. It runs straight away, no confirmation. "
            "answer = flights ('find flights from X to Y on <date>': put the whole request in query) and plain factual lookups spoken aloud with no site named ('what is the latest', news, "
            "weather, prices; query = the question); "
            "read = read a specific page aloud (url, optional query = what to find in it); "
            "open = just show a page on screen, no interaction (query = address or search words); "
            "github = search GitHub repos, issues, prs or code (query, optional kind, owner, open_top)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": list(MODES), "description": "What to do."},
                "query": {"type": "string", "description": "Question, search words, address or goal, depending on the mode."},
                "url": {"type": "string", "description": "Web address, for read and browser."},
                "kind": {"type": "string", "enum": ["repos", "issues", "prs", "code"], "description": "github only."},
                "owner": {"type": "string", "description": "github only: a user or organisation the user named."},
                "open_top": {"type": "boolean", "description": "github only: also open the best match on screen."},
            },
            "required": ["mode"],
        },
    },
}


@register_function("web", web_desc, ToolType.SYSTEM_CTL)
def web(conn, mode, query=None, url=None, kind="repos", owner=None, open_top=False):
    mode = (mode or "").strip().lower()
    if mode not in MODES:
        return ActionResponse(Action.RESPONSE, "unknown mode", "I can look things up, read a page, open a page, search GitHub or use the browser. Which one?")
    q = (query or "").strip()
    # Flight forms can't be driven reliably by the browser agent; search_web reads Google Flights results instead.
    if mode == "browser" and re.search(r"\bflights?\b", f"{q} {url or ''}", re.I):
        mode = "answer"
    if mode == "answer":
        fn, args = _fn("search_web", "search_web"), (q,)
    elif mode == "read":
        fn, args = _fn("browser", "read_page"), (url or q, q if url else None)
    elif mode == "open":
        fn, args = _fn("browser", "open_in_browser"), (url or q,)
    elif mode == "github":
        fn, args = _fn("github_search", "github_search"), (q, kind, owner, bool(open_top))
    else:
        fn, args = _fn("browser_task", "browser_task"), (url, q)
    if fn is None:
        return ActionResponse(Action.RESPONSE, f"{mode} unavailable", "That web feature isn't installed.")
    if mode != "browser" and not (q or url):
        return ActionResponse(Action.RESPONSE, "empty request", "What should I look for?")
    return fn(conn, *args)
