"""cloud_lab.start - scaffold a new GCP terraform lab into labs/<slug>/.

One invocation collects everything the lab needs and does the busywork:

  1. fetch     the lab page first - it pre-fills the README and answers
               most of the questions (title, task count, and - when
               the lab is running - the project, region and other
               lab-specific values)
  2. collect   whatever the page couldn't answer (every flag you pass
               is a question you skip)
  3. scaffold  providers.tf, backend.tf, variables.tf, task<N>.tf,
               README.md, terraform.tfvars
  4. init      terraform init
  5. auth      one gcloud login --update-adc (gcloud + ADC), then
               project/region/zone config

The reset half of a refresh (start on an existing lab) runs
cloud_lab.reset directly - the shared implementation, not an external
executable.

Stdlib only - the browser fetch is an optional escalation, installed with:
  python3 -m venv .venv
  .venv/bin/pip install playwright && .venv/bin/playwright install chromium

Labs are managed directories: every target resolves to a direct child
of labs/ in this repository, no matter which directory you invoke the
CLI from. Templates live under templates/ and are selected internally
(standard, by default) - there is no template flag or prompt.
"""

import argparse
import fnmatch
import html
import importlib
import logging
import logging.handlers
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Literal, overload
from urllib.parse import urlsplit

from cloud_lab import reset
from cloud_lab.common import (BLU, CYN, GRN, LABS_DIR, RED, REPO_ROOT, RST,
                              Spinner, YLW, banner, cache_dir, check_slug,
                              resolve_lab_target)

TEMPLATES_DIR = REPO_ROOT / "templates"

# Internal template registry. The CLI always uses the standard
# template today; identity and lookup stay behind this boundary so a
# future template needs no call-site changes - and so no public flag,
# prompt, or auto-detection grows around an unfinished workflow.
DEFAULT_TEMPLATE = "standard"
TEMPLATE_IDS = ("standard", "terraform")
_TEMPLATE_DIRS = {
    "standard": "new-standard-lab",
    "terraform": "new-terraform-lab",
}


def template_dir(name: str = DEFAULT_TEMPLATE) -> Path:
    """The directory template `name` lives in (raises on unknown names).

    Internal only: the CLI passes no name and uses the default.
    """
    try:
        return TEMPLATES_DIR / _TEMPLATE_DIRS[name]
    except KeyError:
        raise ValueError(
            f"unknown template {name!r} (known: {', '.join(TEMPLATE_IDS)})") from None

# Files copied verbatim from the selected template. Task files are
# generated instead, because how many there are is a per-lab detail.
# (The reset half lives in cloud_lab.reset - one implementation serves
# every lab and every refresh, and bugfixes propagate instead of
# fossilizing in each lab dir.)
TEMPLATE_FILES = ("backend.tf", "providers.tf", "variables.tf", "README.md")

# Placeholders the script fills in. Plain unique markers + str.replace,
# NOT string.Template: the README contains $(shell code) and "$" would
# need escaping in a Template.
README_PLACEHOLDERS = {
    "title": "__LAB_TITLE__",
    "url": "__LAB_URL__",
    "sections": "__LAB_SECTIONS__",
}

# What __LAB_SECTIONS__ becomes when nothing could be extracted
FALLBACK_SECTIONS = """\
## Overview

<!-- Add lab overview -->

<!-- Include any images if applicable -->

## Objectives

<!-- Add list of objectives -->
"""

TASK_FILE_TEMPLATE = """\
# -----------------------------------------------
# Task {n}.{title}
# -----------------------------------------------

"""


# --- interactive prompts ---------------------------------------------------
@overload
def ask(prompt: str, default: str | None = None, validate=None,
        optional: Literal[False] = False) -> str: ...


@overload
def ask(prompt: str, default: str | None = None, validate=None,
        optional: bool = False) -> str | None: ...


def ask(prompt: str, default: str | None = None, validate=None,
        optional: bool = False) -> str | None:
    """Ask a question until the answer is usable, then return it.

    default  - shown as [hint]; pressing enter accepts it
    validate - fn(value) -> error string or None; bad answers re-prompt
    optional - an empty answer returns None instead of re-prompting

    Defaults are validated exactly like typed answers: a default that
    fails the validator (e.g. a page-derived slug that collides with an
    existing lab) is refused, never silently accepted. An exhausted
    stdin refuses an invalid default or an unanswered required prompt
    instead of spinning forever.
    """
    stdin_done = False  # set on the first EOF: no answer is ever coming
    while True:
        hint = f" [{YLW}{default}{RST}]" if default is not None else ""
        if stdin_done:
            answer = ""  # behave like enter on every later iteration
        else:
            try:
                answer = input(f"{CYN}?{RST} {prompt}{hint}: ").strip()
            except EOFError:
                answer = ""  # stdin exhausted (piped input): behave like enter
                stdin_done = True
        if not answer:
            if default is not None:
                if validate:
                    problem = validate(default)
                    if problem:
                        if stdin_done:
                            sys.exit(f"{RED}  {problem}{RST}")
                        print(f"{RED}  {problem}{RST}")
                        continue
                return default
            if optional:
                return None
            if stdin_done:
                sys.exit(f"{RED}  this one is required (no answer on stdin){RST}")
            print(f"{YLW}  this one is required{RST}")
            continue
        if validate:
            problem = validate(answer)
            if problem:
                if stdin_done:
                    sys.exit(f"{RED}  {problem}{RST}")
                print(f"{RED}  {problem}{RST}")
                continue
        return answer


# validators: return an error string (re-prompt) or None (accept)
def check_project(value: str):
    if not re.fullmatch(r"[a-z][a-z0-9-]{5,29}", value):
        return "project ids are 6-30 chars of lowercase letters, digits, hyphens"


def check_zone(value: str):
    if not re.fullmatch(r"[a-z]+-[a-z]+[0-9]+-[a-z]", value):
        return "zone format is like us-central1-a"


def check_tasks(value: str):
    if not value.isdigit() or not 1 <= int(value) <= 20:
        return "enter a number from 1 to 20"


def check_use_new(value: str):
    if value.lower() not in ("u", "use", "n", "new"):
        return "u to use it (reset + re-scaffold) or n for a new name"


def check_fresh_slug(value: str):
    """check_slug + must not exist under labs/: the re-entry prompt after 'new'."""
    problem = check_slug(value)
    if problem:
        return problem
    target = LABS_DIR / value
    if target.exists() or target.is_symlink():
        return f"labs/{value}/ already exists"


def check_url(value: str):
    # file:// is allowed so saved lab pages can drive the whole CLI in
    # offline tests, exactly like a real URL would
    if not (re.match(r"^https?://", value) or value.startswith("file://")):
        return "must start with http://, https:// or file://"


# --- best-effort lab page fetch ---------------------------------------------
def fetch_lab_page(url: str) -> tuple[str, str | None]:
    """Return (page title, meta description) for a lab URL, or raise.

    Lab pages usually sit behind a sign-in wall, so the title may come
    back as "Sign in | ...". That is not an error - the caller decides
    whether anything usable came back.
    """
    request = urllib.request.Request(
        url,
        # plain urllib announces itself and some CDNs 403 it; a browser
        # User-Agent gets served
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            page = response.read(500_000).decode("utf-8", errors="replace")
    except Exception as err:  # DNS, HTTP status, timeout - all just "no page"
        raise RuntimeError(f"couldn't fetch {url}: {err}") from err

    title_match = re.search(r"<title[^>]*>(.*?)</title>", page, re.IGNORECASE | re.DOTALL)
    title = html.unescape(title_match.group(1)).strip() if title_match else ""

    # meta tags appear in either attribute order, so scan every <meta>
    # rather than betting on one regex
    description = None
    for tag in re.findall(r"<meta[^>]*>", page):
        name = re.search(r"(?:name|property)=[\"']([^\"']+)[\"']", tag)
        content = re.search(r"content=[\"']([^\"']*)[\"']", tag)
        if name and content and name.group(1) in ("description", "og:description"):
            description = html.unescape(content.group(1)).strip() or None
            break

    if title.lower().startswith("sign in"):
        # we got the login page, not the lab - nothing usable here
        LOG.debug("urllib fetch of %s hit the sign-in wall", url)
        return title, None
    LOG.debug("urllib fetch of %s: title=%r, description=%r", url, title, description)
    return title, description


# --- optional: browser session for sign-in-walled pages --------------------
# The lab pages ignore gcloud tokens entirely - only a Qwiklabs browser
# session cookie gets through (see the walkthrough). urllib stays the
# fast path; a real browser is the escalation. The Chromium profile
# persists under the cache dir, so you sign in once and later runs are
# headless and silent.
def browser_profile() -> Path:
    """The persistent Chromium profile (where the session cookie lives)."""
    return cache_dir() / "profile"


def auth_marker() -> Path:
    """The marker saying the stored browser session is still live."""
    return cache_dir() / ".authenticated"


LOG = logging.getLogger("lab-start")


def _ensure_log() -> None:
    """Attach the debug log file under the cache dir, once per process.

    The console keeps its curated output; this file is the forensic
    record - every URL the browser passes through, every decision, and
    full tracebacks when things fail. Rotated at 200KB so the file
    can't grow without bound.
    """
    if LOG.handlers:
        return
    cache_dir().mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        cache_dir() / "lab-start.log", maxBytes=200_000, backupCount=1
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(handler)
    LOG.setLevel(logging.DEBUG)

# Extraction runs inside the page (page.evaluate). Ground truth from a
# real skills.google lab page:
#   - the first <h1> in the DOM belongs to a modal panel ("Before you
#     begin"), not the lab; the real title is document.title, formatted
#     "Lab Name - Course Name | Site"
#   - content images are nested: <p><button><img></button></p>
#   - <ql-variable> tags hold lab-specific values: the key attribute
#     ("primary_project.project_id") names the value, and once the
#     lab is running the real value comes from the control panel's
#     labdetails JSON (or the element's shadow root)
#   - .js-lab-control-panel carries JSON attributes: labcontrolbutton
#     {"running": bool} is the "lab in progress" signal, and labdetails
#     holds the connection details (student credentials, project IDs)
#   - every section is plain sibling flow: <h2><p><ul>... until the
#     next heading - semantic structure, not CSS classes, which churn
_EXTRACT_JS = """
() => {
    const clean = (s) => (s || "").replace(/\\s+/g, " ").trim();

    // --- lab state: the control panel carries JSON attributes ------
    // labcontrolbutton {"running": bool} says whether Start Lab was
    // pressed; once running, labdetails holds the connection details
    // (username, password, project IDs) as label/property/resource/value
    const panel = document.querySelector(".js-lab-control-panel");
    const attrJson = (name, fallback) => {
        try { return JSON.parse(panel.getAttribute(name)) ?? fallback; }
        catch (e) { return fallback; }
    };
    const running = panel ? attrJson("labcontrolbutton", {}).running === true : false;
    const details = panel ? attrJson("labdetails", []) : [];

    // ql-variable key "primary_project.project_id" pairs with the
    // labdetails entry whose resource and property match - the
    // attribute JSON, not the element text, is where running values live
    const detailValue = (key) => {
        if (!key || !key.includes(".")) return null;
        const prop = key.slice(key.lastIndexOf(".") + 1);
        const resource = key.slice(0, key.lastIndexOf("."));
        for (const d of details) {
            if (d.value && d.property === prop && d.resource === resource) return d.value;
        }
        return null;
    };

    // a ql-variable's rendered text lives in its shadow root (absent
    // from serialized HTML); when the lab isn't running it renders the
    // placeholder, so only something else counts as a real value
    const varValue = (v) => {
        const rendered = clean(v.textContent) ||
            (v.shadowRoot ? clean(v.shadowRoot.textContent) : "");
        const placeholder = clean(v.getAttribute("placeholder"));
        return rendered && rendered !== placeholder ? rendered : "";
    };

    // ql-variable text is empty in serialized HTML; the value it
    // represents lives in the shadow root or labdetails. Substitute so
    // list items don't extract with holes. Clone first: never mutate
    // the page - and shadow roots don't survive a clone, so read
    // the originals and write into their clones by index.
    const nodeText = (n) => {
        const clone = n.cloneNode(true);
        const originals = [...n.querySelectorAll("ql-variable")];
        const clones = [...clone.querySelectorAll("ql-variable")];
        originals.forEach((orig, i) => {
            clones[i].textContent = varValue(orig) ||
                detailValue(orig.getAttribute("key")) ||
                orig.getAttribute("placeholder") || "«lab value»";
        });
        return clean(clone.textContent);
    };

    const imgBlock = (img) => {
        const src = img.currentSrc || img.getAttribute("src") || img.dataset.src || "";
        return src ? { type: "img", alt: clean(img.getAttribute("alt")), src } : null;
    };

    const out = { title: "", sections: [], tasks: [], objectives: [],
                  running: running, details: details, variables: [] };
    out.title = clean(document.title.split("|")[0].split(" - ")[0]);

    // harvest the lab-specific variables: key + human name + value
    // (value empty until the lab is running)
    const seen = new Set();
    for (const v of document.querySelectorAll("ql-variable")) {
        const key = v.getAttribute("key") || "";
        if (!key || seen.has(key)) continue;
        seen.add(key);
        out.variables.push({
            key: key,
            name: clean(v.getAttribute("placeholder")) || key,
            value: varValue(v) || detailValue(key) || "",
        });
    }

    // whitelist content sections; everything else (Setup and
    // requirements, Congratulations!, PP001, modal h1s) self-excludes
    const CONTENT = /^(challenge|objectives?|overview|scenario|your challenge|environment|architecture|about)/i;
    const TASK = /^task\\s*(\\d+)/i;

    for (const heading of document.querySelectorAll("h2, h3")) {
        const label = clean(heading.textContent);
        if (!label) continue;

        // collect this heading's content, in order, until the next heading
        const blocks = [];
        let node = heading.nextElementSibling;
        while (node && !/^H[1-4]$/.test(node.tagName)) {
            if (node.tagName === "UL" || node.tagName === "OL") {
                const items = [...node.querySelectorAll("li")].map(nodeText).filter(Boolean);
                if (items.length) blocks.push({ type: "list", items });
            } else if (node.tagName === "PRE") {
                const code = node.textContent.replace(/\\s+$/, "");
                if (code.trim()) blocks.push({ type: "code", text: code });
            } else {
                // images nest inside wrappers (<p><button><img>)
                for (const img of node.querySelectorAll("img")) {
                    const block = imgBlock(img);
                    if (block) blocks.push(block);
                }
                const text = nodeText(node);
                if (text) {
                    // asides are callouts - keep them, marked as quotes
                    blocks.push({ type: "p", text: node.tagName === "ASIDE" ? "> " + text : text });
                }
            }
            node = node.nextElementSibling;
        }

        const taskMatch = label.match(TASK);
        if (taskMatch) {
            out.tasks.push({
                n: Number(taskMatch[1]),
                title: clean(label.replace(/^task\\s*\\d+[.:—-]*\\s*/i, "")),
            });
        } else if (CONTENT.test(label)) {
            out.sections.push({
                level: heading.tagName === "H3" ? 3 : 2,
                heading: label,
                blocks,
            });
            if (/^objectives?/i.test(label)) {
                for (const block of blocks) {
                    if (block.type === "list") { out.objectives = block.items; break; }
                }
            }
        }
        // else: boilerplate - the blocks were walked but are discarded
    }
    return out;
}
"""


def load_playwright():
    """Import playwright lazily, falling back to the repo's .venv.

    Lazy because importing at the top would slow every invocation -
    even a --help - for a module most runs never touch.
    Returns the sync_playwright factory, or None if not installed.
    """
    try:
        api = importlib.import_module("playwright.sync_api")
    except ImportError:
        pass
    else:
        try:
            return api.sync_playwright
        except AttributeError:
            pass  # a missing export is unavailable, just like a failed import
    # try the repo venv's site-packages (first python* found)
    venv = REPO_ROOT / ".venv"
    for site in venv.glob("lib/python*/site-packages"):
        sys.path.insert(0, str(site))
        try:
            api = importlib.import_module("playwright.sync_api")
        except ImportError:
            pass
        else:
            try:
                return api.sync_playwright
            except AttributeError:
                pass
        sys.path.remove(str(site))
    return None


def _settle(page) -> None:
    """Wait for the SPA to finish rendering.

    domcontentloaded fires early; Qwiklabs pages hydrate after, so give
    the network up to 10s to go quiet. Some pages never idle (analytics
    beacons) - the DOM is up by then, which is all we read.
    """
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass


def _on_lab_site(page, url: str) -> bool:
    """True when the tab is positively back on the lab site, signed in.

    Mid-login the tab wanders: Qwiklabs' /users/sign_in, its
    /users/auth/google_oauth2 hop, then accounts.google.com for the
    password and MFA. "Not on the login page" is true during most of
    that journey (that bug shipped once); only "same host, and off
    every sign-in/auth path" means the session cookie is real. A www.
    prefix difference is tolerated so a redirect that adds it doesn't
    stall the wait.
    """
    lab, current = urlsplit(url), urlsplit(page.url)
    if current.netloc.removeprefix("www.") != lab.netloc.removeprefix("www."):
        return False  # accounts.google.com and friends - still mid-login
    path = current.path.lower()
    return "sign" not in path and "auth" not in path


def _await_sign_in(page, url: str, timeout_s: int = 300) -> bool:
    """Wait for a human to finish signing in inside the visible window.

    Every hop the tab makes is logged; the heartbeat lines make it
    obvious whether a hang is "user still typing" or something else.
    """
    print(f"\n{BLU}  Chromium is open at the lab's sign-in page.{RST}")
    print(f"  Sign in with your skills.google account. Google may take you")
    print(f"  through accounts.google.com first - that's expected. This")
    print(f"  script continues by itself once you're back on the lab site.")
    print(f"  (Waits up to {timeout_s // 60} minutes; Ctrl+C aborts without creating anything.)")

    deadline = time.monotonic() + timeout_s
    last_url, next_heartbeat = "", time.monotonic() + 30
    while time.monotonic() < deadline:
        current = page.url
        if current != last_url:  # log every navigation the user makes
            LOG.debug("sign-in: browser now at %s", current)
            last_url = current
        if _on_lab_site(page, url):
            print()
            LOG.debug("sign-in: back on the lab site - session cookie in place")
            return True
        if time.monotonic() >= next_heartbeat:
            LOG.debug("sign-in: still waiting (browser at %s)", current)
            next_heartbeat = time.monotonic() + 30
        try:
            page.wait_for_timeout(2000)  # playwright's sleep
        except Exception:
            LOG.debug("sign-in: browser window closed by hand")
            return False
    LOG.debug("sign-in: timed out after %ss, browser at %s", timeout_s, last_url)
    return False


def fetch_lab_page_browser(url: str) -> dict | None:
    """Fetch a sign-in-walled lab page with a persistent browser profile.

    First run (no stored session): a visible Chromium window opens on
    the lab page, you sign in once, and the profile keeps the cookie.
    Later runs launch headless. Returns the extraction dict {title,
    sections, tasks, objectives, running, details, variables}, or None
    - having already printed why - so the caller falls back to README
    placeholders.
    """
    sync_playwright = load_playwright()
    if sync_playwright is None:
        return None
    _ensure_log()
    LOG.debug("browser fetch starting for %s", url)

    with sync_playwright() as sync_playwright_ctx:
        def _launch(headless: bool):
            LOG.debug("launching Chromium headless=%s profile=%s",
                       headless, browser_profile())
            return sync_playwright_ctx.chromium.launch_persistent_context(
                str(browser_profile()), headless=headless
            )

        try:
            # stored marker says the profile has a live session -> headless
            context = _launch(headless=auth_marker().exists())
        except Exception as err:
            LOG.exception("Chromium launch failed")
            print(f"{YLW}  couldn't launch Chromium: {err}{RST}")
            print(f"{YLW}  fresh install? run: .venv/bin/playwright install chromium{RST}")
            return None

        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            _settle(page)
            LOG.debug("goto %s landed on %s", url, page.url)

            # Qwiklabs bounces anonymous visitors to /users/sign_in
            if "sign_in" in page.url:
                LOG.debug("sign-in wall detected at %s", page.url)
                if auth_marker().exists():
                    # the headless try found an expired cookie - go visible
                    LOG.debug("stored session has expired - relaunching visible")
                    auth_marker().unlink(missing_ok=True)
                    print(f"{YLW}  stored browser session expired - reopening visible window{RST}")
                    context.close()
                    context = _launch(headless=False)
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    _settle(page)
                    LOG.debug("after visible relaunch, landed on %s", page.url)

            if "sign_in" in page.url:
                if not _await_sign_in(page, url):
                    print(f"{YLW}  sign-in did not complete - README placeholders left for you{RST}")
                    print(f"{YLW}  details in {cache_dir() / 'lab-start.log'}{RST}")
                    return None
                # the post-login redirect usually returns to the lab
                # page, but re-navigate so we never extract the wrong one
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                _settle(page)
                LOG.debug("post-sign-in goto landed on %s", page.url)

            if "sign_in" in page.url:  # still walled after all that
                LOG.debug("still walled at %s - giving up", page.url)
                print(f"{YLW}  page still requires sign-in - README placeholders left for you{RST}")
                print(f"{YLW}  details in {cache_dir() / 'lab-start.log'}{RST}")
                return None

            auth_marker().touch()
            LOG.debug("session confirmed - marker written")
            with Spinner(f"{CYN}extracting lab details{RST}"):
                info = page.evaluate(_EXTRACT_JS)
            LOG.debug("extracted: title=%r, %d sections, %d tasks, %d objectives, "
                      "running=%s, %d variables",
                      info["title"], len(info["sections"]), len(info["tasks"]),
                      len(info["objectives"]), info["running"], len(info["variables"]))

            # keep the last rendered page for tuning the extraction
            try:
                cache_dir().mkdir(parents=True, exist_ok=True)
                (cache_dir() / "last-lab-page.html").write_text(page.content())
                page.screenshot(path=str(cache_dir() / "last-lab-page.png"), full_page=True)
                print(f"  {CYN}rendered page cached in {cache_dir()} (for selector debugging){RST}")
            except Exception:
                pass  # debug artifacts are a nicety, never a failure

            context.close()
            if (info["title"] or info["sections"] or info["tasks"]
                    or info["objectives"] or info["variables"]):
                return info
            print(f"{YLW}  page fetched but nothing extracted - README placeholders left for you{RST}")
            return None
        except Exception as err:
            LOG.exception("browser fetch failed")
            print(f"{YLW}  browser fetch failed: {err}{RST}")
            print(f"{YLW}  details in {cache_dir() / 'lab-start.log'}{RST}")
            try:
                context.close()
            except Exception:
                pass
            return None


# --- scaffolding -----------------------------------------------------------
def _tf_name(text: str) -> str:
    """snake_case any identifier-ish string (ql-variable keys, titles)."""
    return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")


def detail_project(details: list) -> str | None:
    """The project id from the connection-details JSON, primary first.

    Single-project labs often never inline the project into the
    instructions, so no ql-variable references it - the connection
    details are then the only source.
    """
    entries = [d for d in details
               if d.get("property") == "project_id" and d.get("value")]
    for entry in entries:
        if "primary" in (entry.get("resource") or ""):
            return entry["value"]
    return entries[0]["value"] if entries else None


def classify_page_variables(variables: list) -> dict:
    """Split page ql-variables into known slots and lab-specific extras.

    The first <resource>.project_id / *region* / *zone* key claims the
    scaffold's existing project/compute_region/compute_zone variables
    (value only when the lab is running); every other key becomes an
    extra for variables.tf, named after the key's last segment - and
    when that's taken, resource + property suffix
    (secondary_project.project_id -> secondary_project_id).
    """
    out = {"project": None, "region": None, "zone": None, "extras": []}
    claimed = set()  # slots already claimed by an earlier key
    used = {"project_id", "compute_region", "compute_zone"}
    for var in variables:
        segs = [s for s in var["key"].split(".") if s != "startup_script"]
        prop = _tf_name(segs[-1])
        value = var["value"] or None
        if prop == "project_id" and "project" not in claimed:
            claimed.add("project")
            out["project"] = value
        elif prop.endswith("region") and "region" not in claimed:
            claimed.add("region")
            out["region"] = value
        elif prop.endswith("zone") and "zone" not in claimed:
            claimed.add("zone")
            out["zone"] = value
        else:
            prefix = "_".join(_tf_name(s) for s in segs[:-1])
            name = prop if prop not in used else f"{prefix}_{prop.rsplit('_', 1)[-1]}"
            used.add(name)
            out["extras"].append({
                "name": name,
                "description": var["name"].replace('"', "'").strip(),
                "value": var["value"],
            })
    return out


def render_sections(sections: list) -> str | None:
    """Turn extracted page sections into README markdown, in page order.

    Headings keep their own level (h3 -> ###), lists become bullets,
    images become ![alt](src) - the README mirrors the lab page the
    way a hand-written one would.
    """
    parts = []
    for section in sections:
        prefix = "###" if section["level"] == 3 else "##"
        chunks = [f"{prefix} {section['heading']}"]
        for block in section["blocks"]:
            if block["type"] == "list":
                chunks.append("\n".join(f"- {item}" for item in block["items"]))
            elif block["type"] == "code":
                chunks.append(f"```\n{block['text']}\n```")
            elif block["type"] == "img":
                chunks.append(f"![{block['alt']}]({block['src']})")
            else:  # "p" ("> " callouts included)
                chunks.append(block["text"])
        parts.append("\n\n".join(chunks))
    return "\n\n".join(parts) if parts else None


def scaffold(lab_dir: Path, title: str, url: str | None, sections_md: str | None,
             tasks: int, task_titles: list, project: str, region: str, zone: str,
             extras: list | None = None, refresh: bool = False) -> list[Path]:
    """Write the whole lab into lab_dir; return the files created.

    Refresh-safe by construction: the dir is created only when
    missing, and a task stub is written only for a file that doesn't
    exist - task files are user work, never overwritten.
    """
    lab_dir.mkdir(parents=True, exist_ok=True)
    template = template_dir()
    copied = [lab_dir / name for name in TEMPLATE_FILES]
    for path in copied:
        # copyfile addresses the exact output filename: an existing
        # directory at the destination is an error, never a folder to
        # copy INTO (copy2's dest-directory semantics). copystat
        # keeps copy2's metadata preservation.
        shutil.copyfile(template / path.name, path)
        shutil.copystat(template / path.name, path)

    # render the README: swap each placeholder for a collected value
    readme = lab_dir / "README.md"
    text = readme.read_text()
    values = {
        README_PLACEHOLDERS["title"]: title,
        README_PLACEHOLDERS["url"]: url or "<!-- Add lab URL -->",
        # the page's own sections when they were extracted; otherwise
        # the familiar Overview/Objectives placeholder skeleton
        README_PLACEHOLDERS["sections"]: sections_md or FALLBACK_SECTIONS,
    }
    for placeholder, value in values.items():
        text = text.replace(placeholder, value)
    readme.write_text(text)

    # one stub per task, titled from the page when the page told us;
    # an existing task<N>.tf is user work - kept byte-identical.
    # is_symlink() covers DANGLING links too: exists() is False for
    # those, and a write would follow the link out of the lab. A task
    # link is a user entry - kept as-is, referent untouched.
    task_files = []
    for n in range(1, tasks + 1):
        stub_title = next((t["title"] for t in task_titles if t["n"] == n), "")
        task = lab_dir / f"task{n}.tf"
        if task.exists() or task.is_symlink():
            LOG.debug("%s already exists - kept as-is%s", task,
                      " (refresh)" if refresh else "")
            continue
        task.write_text(TASK_FILE_TEMPLATE.format(n=n, title=f" {stub_title}" if stub_title else ""))
        task_files.append(task)

    # lab-specific values; *.tfvars is gitignored, so this stays local
    tfvars_lines = [
        "# lab-specific values (*.tfvars is gitignored - stays local)",
        f'project_id     = "{project}"',
        f'compute_region = "{region}"',
        f'compute_zone   = "{zone}"',
    ]
    # extras: the ql-variables the lab page named (bucket, network,
    # instance names...). The declarations land in variables.tf; the
    # values are only known once the lab is running - until then they
    # stay commented placeholders in tfvars rather than silent blanks.
    if extras:
        blocks = []
        for extra in extras:
            blocks.append(
                f'variable "{extra["name"]}" {{\n'
                f'    description = "{extra["description"]}"\n'
                f"    type = string\n}}"
            )
        vars_tf = lab_dir / "variables.tf"
        vars_tf.write_text(vars_tf.read_text().rstrip("\n") + "\n\n" + "\n\n".join(blocks) + "\n")

        tfvars_lines.append("")
        for extra in extras:
            if extra["value"]:
                tfvars_lines.append(f'{extra["name"]} = "{extra["value"]}"')
            else:
                tfvars_lines.append(f'# {extra["name"]} = "?"  # {extra["description"]} - from the lab page')

    tfvars = lab_dir / "terraform.tfvars"
    tfvars.write_text("\n".join(tfvars_lines) + "\n")

    created = copied + task_files + [tfvars]
    created.sort()
    return created


# --- subprocess helpers ------------------------------------------------------
def run_quiet(*cmd: str, cwd: Path | None = None) -> None:
    """Run a command with its output captured (hidden behind a spinner)
    and STDIN CLOSED, so no child can ever sit waiting on a hidden
    prompt.

    Raises RuntimeError with the command's error output on failure, so
    callers can wrap the call in `with Spinner(...)` and let the spinner
    print "failed" for free.
    """
    result = subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip()[-2000:])


def terraform_init(lab_dir: Path) -> bool:
    try:
        with Spinner(f"{CYN}terraform init{RST}"):
            run_quiet("terraform", "init", cwd=lab_dir)
    except RuntimeError as err:
        print(f"{RED}terraform init failed:\n{err}{RST}")
        return False
    return True


def gcloud_setup(project: str, region: str, zone: str) -> bool:
    print(f"\n{CYN}A browser window will open - sign in with the lab credentials, then come back here.{RST}")
    print(f"{CYN}One sign-in covers both stores: gcloud itself and application-default "
          f"credentials (what terraform uses).{RST}\n")

    # No spinner and no capture: `gcloud auth login` is itself
    # interactive, so stdin/stdout must stay wired to the terminal.
    # --update-adc writes the same credentials to ADC, so one browser
    # flow covers both stores (the reset still revokes both).
    if subprocess.run(["gcloud", "auth", "login", "--update-adc"]).returncode != 0:
        print(f"{YLW}login did not complete - skipping gcloud setup{RST}")
        return False

    try:
        for message, setting in [
            ("setting gcloud project", ["config", "set", "project", project]),
            ("setting gcloud region", ["config", "set", "compute/region", region]),
            ("setting gcloud zone", ["config", "set", "compute/zone", zone]),
        ]:
            with Spinner(f"{CYN}{message}{RST}"):
                run_quiet("gcloud", *setting)
    except RuntimeError as err:
        print(f"{RED}gcloud config failed: {err}{RST}")
        return False
    return True


# --- refresh: reuse an existing lab dir -------------------------------------
# A restarted Qwiklabs lab gets new values (project ID, bucket names...),
# so a half-finished dir holds stale tfvars and a tfstate pointing at a
# dead project. *Use* = reset (cloud_lab.reset owns the semantics, and
# the refresh calls it directly) then re-scaffold the generated
# artifacts. Task files and anything else the scaffold didn't generate
# are never touched.
def looks_like_lab_dir(lab_dir: Path) -> bool:
    """True when the dir carries at least one .tf file.

    The guard against reset + rewrite on an arbitrary directory: a
    reset is only ever pointed at a lab.
    """
    return lab_dir.is_dir() and any(lab_dir.glob("*.tf"))


def require_existing_for_use(args, lab_dir: Path) -> None:
    """--use-existing names an existing lab: a missing target is a
    typo to fail on, never a replacement to scaffold.

    Enforced after ANY target selection - positional, typed at the
    prompt, or page-derived default - and before any side effect.
    """
    if args.use_existing and not lab_dir.exists():
        sys.exit(f"{RED}{lab_dir} does not exist, and --use-existing must not "
                 f"scaffold a replacement after a typo{RST}\n"
                 f"{YLW}hint: drop --use-existing to create it, or check the name{RST}")


def use_or_new(args, lab_dir: Path) -> tuple[Path | None, bool]:
    """The user named an existing dir: use it, or pick a new name.

    Returns (directory, refresh); (None, False) means "new name" -
    the caller re-enters the directory prompt with a not-exists
    validator. The directory comes back as the resolved lab path
    itself, not just its basename.
    """
    # an unattended run must never silently reset + rewrite
    if args.yes and not args.use_existing:
        sys.exit(f"{RED}{lab_dir} already exists, and --yes must not reset + rewrite "
                 f"an existing dir unattended{RST}\n"
                 f"{YLW}hint: re-run with --use-existing to allow the refresh{RST}")
    if not args.use_existing:
        choice = ask(f"{lab_dir.name}/ exists. [u]se it or [n]ew name",
                     default="u", validate=check_use_new).lower()
        if choice in ("n", "new"):
            return None, False
    if not looks_like_lab_dir(lab_dir):
        print(f"{RED}  {lab_dir.name}/ has no .tf files - doesn't look like a "
              f"lab dir, not resetting it{RST}")
        return None, False
    return lab_dir, True


def refresh_keeps(lab_dir: Path) -> list[str]:
    """What a refresh leaves untouched: task files + non-generated entries.

    Excluded: the files the scaffold rewrites (templates, tfvars) and
    the ones the reset deletes (tfstate, tfvars backups). Everything
    else - task files, user modules, a main.tf from older scaffolds -
    survives, and gets named in the preview.
    """
    keeps = []
    for entry in sorted(lab_dir.iterdir()):
        if entry.is_dir():
            keeps.append(entry.name + "/")
        elif (entry.name not in TEMPLATE_FILES
              and not fnmatch.fnmatch(entry.name, "*.tfvars*")
              and not fnmatch.fnmatch(entry.name, "*.tfstate*")):
            keeps.append(entry.name)
    return keeps


# --- output safety boundary ---------------------------------------------
# A refresh rewrites the generated artifacts in place. If one of those
# paths is a symlink, every write (copy, read_text, write_text)
# follows it: a link aimed outside the lab turns the refresh into an
# external overwrite, and a DANGLING link (exists() is False) turns a
# rewrite into creating the referent. A path that is not a regular
# file at all - a directory named backend.tf, say - is just as
# hostile: copy2 treats an existing destination directory as a folder
# to copy INTO, so the template lands at backend.tf/backend.tf, and a
# symlink nested there escapes the lab. Refuse every generated
# destination that exists and is not a regular file before the reset
# runs and before anything is written. Task files are different:
# they are user entries, so a task link - even a dangling one - is
# kept as-is and its referent never touched (scaffold treats
# is_symlink() as "exists").
def refuse_unsafe_generated_outputs(lab_dir: Path) -> None:
    """Exit before any reset or write if a generated output is not a
    regular file (a symlink - dangling included - or a directory)."""
    for name in (*TEMPLATE_FILES, "terraform.tfvars"):
        path = lab_dir / name
        if path.is_symlink():
            sys.exit(f"{RED}{path} is a symlink - refusing to write through it{RST}\n"
                     f"{YLW}hint: remove the link, then refresh again "
                     f"(task files are never rewritten; this one is generated){RST}")
        if os.path.lexists(path) and not stat.S_ISREG(os.lstat(path).st_mode):
            sys.exit(f"{RED}{path} exists and is not a regular file - refusing "
                     f"to treat it as a copy destination{RST}\n"
                     f"{YLW}hint: a directory here would be copied INTO, not "
                     f"replaced; remove it, then refresh again "
                     f"(task files are never rewritten; this one is generated){RST}")


# --- entry plumbing -----------------------------------------------------------
# The argument contract for `labkit start`.
SUMMARY = ("Scaffold a new GCP terraform lab into labs/<slug>/ - or refresh "
           "an existing one: reset it, then re-scaffold the generated files "
           "(task files untouched).")


def examples(command: str) -> str:
    """The help examples, named for the entry point that shows them."""
    return (
        "examples:\n"
        f"  {command} my-new-lab\n"
        f"  {command} my-new-lab --tasks 4 --skip-auth\n"
        f"  {command} my-new-lab --project qwiklabs-gcp-00-abc123 --zone us-central1-a\n"
        f"  {command} --url https://partner.skills.google/course_templates/1573/labs/620245\n"
        f"  {command} old-lab --use-existing   # refresh: reset + re-scaffold\n"
        "\n"
        "The target is always a managed lab under labs/, whatever your\n"
        "current directory is. Anything passed as a flag is one question\n"
        "you won't be asked."
    )


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The options for labkit start."""
    parser.add_argument("directory", nargs="?",
                        help="lab to create or refresh: a slug (labs/<slug>) or "
                             "an explicit path to a managed lab under labs/")
    parser.add_argument("--title", help="human-readable lab title (README H1)")
    parser.add_argument("--url", help="lab URL, used to pre-fill the README")
    parser.add_argument("--tasks", type=int, help="how many task<N>.tf files to create")
    parser.add_argument("--project", help="GCP project ID from the lab")
    parser.add_argument("--region", help="compute region, e.g. us-central1")
    parser.add_argument("--zone", help="compute zone, e.g. us-central1-a")
    parser.add_argument("--skip-init", action="store_true", help="don't run terraform init")
    parser.add_argument("--skip-auth", action="store_true", help="don't run gcloud login/config")
    parser.add_argument("--use-existing", action="store_true",
                        help="an existing lab dir gets refreshed: reset it, then "
                             "re-scaffold the generated files (task files untouched)")
    parser.add_argument("-y", "--yes", action="store_true", help="answer yes to all prompts")


# --- main -----------------------------------------------------------------
def run(args) -> int:
    """The start workflow, after argument parsing."""
    banner("LAB START")
    _ensure_log()
    LOG.debug("invoked with %s", args)

    # --- resolve the target before any fetch work --------------------
    # The fetch is expensive (a full Chromium run on sign-in-walled
    # pages), so the target is resolved and the use-or-new decision
    # settles FIRST: *use* = reset + re-scaffold (refresh),
    # *new* = the directory prompt again, after the fetch. A missing
    # target with --use-existing dies here - it must not scaffold a
    # replacement after a typo.
    lab_dir = None
    refresh = False
    pending_new = False
    if args.directory:
        lab_dir = resolve_lab_target(args.directory, cwd=Path.cwd())
        if lab_dir.exists():
            if use_or_new(args, lab_dir)[0] is None:
                # the user chose a new name (or was refused the use path)
                pending_new = True
                lab_dir = None
            else:
                refresh = True
                # output safety before the fetch: a symlinked or
                # non-file generated output would turn the refresh
                # into an external write
                refuse_unsafe_generated_outputs(lab_dir)
        else:
            require_existing_for_use(args, lab_dir)

    # --- fetch phase: the page answers most of the questions --------
    # URL comes first so a successful fetch can derive the title, the
    # directory name, the task count, and - when the lab is running -
    # the project/region and other lab-specific values.
    url = args.url or ask("Lab URL (enter to skip)", optional=True, validate=check_url)

    description = None    # meta description, for the urllib fallback
    sections_md = None    # README sections rendered from the page
    page_title = None     # the lab's own title, when extracted
    page_tasks = []      # [{n, title}] harvested from Task N. headings
    running = False       # Start Lab pressed? the values are live
    fetched = {"project": None, "region": None, "zone": None, "extras": []}
    student = None        # student account, shown at the gcloud step
    task_default = 1

    if url:
        try:
            with Spinner(f"{CYN}fetching lab page{RST}"):
                doc_title, description = fetch_lab_page(url)
        except RuntimeError as err:
            print(f"{YLW}{err}{RST}")
            doc_title, description = "", None

        if "sign in" in doc_title.lower():
            # urllib got the login page; escalate to a real browser
            # session. fetch_lab_page_browser prints its own progress
            # and failure details - here we only act on the outcome.
            info = fetch_lab_page_browser(url)
            if info:
                # precedence: --title flag > page > guessed default
                if not args.title and info["title"]:
                    page_title = info["title"]
                sections_md = render_sections(info["sections"]) or None
                page_tasks = info["tasks"]
                if page_tasks:
                    task_default = len(page_tasks)
                elif info["objectives"]:
                    task_default = len(info["objectives"])
                running = info["running"]
                fetched = classify_page_variables(info["variables"])
                # the connection details are the authoritative source
                # for the project id, primary project first
                fetched["project"] = fetched["project"] or detail_project(info["details"])
                student = next((d.get("value") for d in info["details"]
                                if d.get("property") == "username"), None)

                if page_title:
                    print(f"  {GRN}from lab page:{RST} {BLU}{page_title}{RST}")
                if sections_md:
                    print(f"  {GRN}{len(info['sections'])} content sections extracted (images included){RST}")
                if page_tasks:
                    print(f"  {GRN}{len(page_tasks)} task headings -> real titles in task<N>.tf{RST}")
                elif info["objectives"]:
                    print(f"  {GRN}{len(info['objectives'])} objectives found on the page{RST}")
                if running:
                    print(f"  {GRN}lab is running - live values extracted:{RST}")
                    for label, value in (("project", fetched["project"]),
                                         ("region", fetched["region"]),
                                         ("zone", fetched["zone"])):
                        if value:
                            print(f"    {CYN}{label}{RST} {value}")
                elif info["variables"]:
                    print(f"  {GRN}lab not started - {len(info['variables'])} lab-specific variables found"
                          f" (values once the lab runs){RST}")
            else:
                print(f"{YLW}  README placeholders left for you{RST}")
                if load_playwright() is None:
                    print(f"{YLW}  to enable auto-fill: python3 -m venv .venv && "
                          f".venv/bin/pip install playwright && .venv/bin/playwright install chromium{RST}")
        elif doc_title:
            # "Lab Name - Course Name | Site" -> "Lab Name" (the same
            # dedup the browser extractor does)
            page_title = doc_title.split("|")[0].split(" - ")[0].strip()
            print(f"  {GRN}from lab page:{RST} {BLU}{page_title}{RST}")
            if description:
                print(f"  {GRN}overview pre-filled from the page's meta description{RST}")

    # --- collect phase: ask what the page couldn't answer ------------
    # the directory derives from the fetched title when there is one:
    # "Some Long Lab Title" -> "some-long-lab-title" (capped at 6 words)
    slug = None
    if page_title:
        slug = "-".join(_tf_name(page_title).replace("_", " ").split()[:6]) or None

    if pending_new:
        # the user saw the existing lab and chose "new" (or was
        # refused the use path) - this prompt refuses existing names,
        # including its own default (ask validates defaults like
        # typed answers)
        name = ask("New lab directory (kebab-case)",
                   default=slug, validate=check_fresh_slug)
        lab_dir = resolve_lab_target(name, cwd=Path.cwd())
    elif lab_dir is None:
        name = ask("New lab directory (kebab-case)",
                   default=slug, validate=check_slug)
        lab_dir = resolve_lab_target(name, cwd=Path.cwd())
        if lab_dir.exists():
            if use_or_new(args, lab_dir)[0] is None:
                name = ask("New lab directory (kebab-case)",
                           default=slug, validate=check_fresh_slug)
                lab_dir = resolve_lab_target(name, cwd=Path.cwd())
            else:
                refresh = True
                refuse_unsafe_generated_outputs(lab_dir)
        else:
            # a prompted or page-derived default is as much a target
            # selection as a positional: --use-existing on a missing
            # one must fail here, before any side effect
            require_existing_for_use(args, lab_dir)

    # every path below agrees on this one resolved target: preview,
    # reset, scaffold, and the next-step cd all name labs/<slug>
    display = lab_dir.relative_to(REPO_ROOT)

    # precedence: --title flag > the page's own title > name-based guess
    title = args.title or page_title or \
        ask("Lab title", default=lab_dir.name.replace("-", " ").title())

    tasks = args.tasks or int(ask(
        "How many tasks (one task<N>.tf each)",
        default=str(task_default),
        validate=check_tasks,
    ))
    # urllib-only fetch: no sections, but synthesize the same README
    # shape from the meta description so output stays consistent
    if sections_md is None and description:
        objectives = "\n".join(f"{i}. " for i in range(1, tasks + 1))
        sections_md = f"## Overview\n\n{description}\n\n## Objectives\n\n{objectives}"

    # precedence for each: flag > running lab's value > ask
    if args.project:
        project = args.project
    elif fetched["project"]:
        project = fetched["project"]
        print(f"  {GRN}project from the running lab:{RST} {BLU}{project}{RST}")
    else:
        project = ask("GCP project ID", validate=check_project)

    if args.zone:
        zone = args.zone
    elif fetched["zone"]:
        zone = fetched["zone"]
        print(f"  {GRN}zone from the running lab:{RST} {BLU}{zone}{RST}")
    else:
        # region known from the page? its -a zone makes a good default
        zone = ask("Compute zone (like us-central1-a)",
                   default=f"{fetched['region']}-a" if fetched["region"] else None,
                   validate=check_zone)
    region = args.region or fetched["region"] or zone.rsplit("-", 1)[0]

    # --- confirm: preview before side effects ----------------------------
    # the selected template is validated here - before any reset or
    # rewrite, so a missing template file can neither destroy an
    # existing lab nor leave partial generated output behind
    template = template_dir()
    missing = [n for n in TEMPLATE_FILES if not (template / n).is_file()]
    if missing:
        sys.exit(f"{RED}templates missing from {template}: {', '.join(missing)}{RST}")

    task_files = "task1.tf" if tasks == 1 else f"task1.tf..task{tasks}.tf"
    print()
    print(f"{BLU}Here's the plan:{RST}")
    # the resolved absolute target, not a repo-relative summary: the
    # caller may be outside the repository, where a relative path
    # names nothing (spec: previews show the resolved target)
    print(f"  {CYN}directory{RST}  {lab_dir}/ "
          f"({'existing - refresh' if refresh else 'new'})")
    if refresh:
        print(f"  {CYN}reset{RST}     clears: terraform.tfstate*, *.tfvars*, "
              f"gcloud config + credentials")
        print(f"  {CYN}rewrites{RST}  {', '.join(TEMPLATE_FILES)}, terraform.tfvars")
        keeps = refresh_keeps(lab_dir)
        print(f"  {CYN}keeps{RST}     {', '.join(keeps) if keeps else '(nothing else)'}")
    print(f"  {CYN}title{RST}      {title}")
    print(f"  {CYN}url{RST}        {url or '(none)'}")
    task_note = " (titled from the page)" if page_tasks else ""
    if refresh:
        missing = [f"task{n}.tf" for n in range(1, tasks + 1)
                   if not ((lab_dir / f"task{n}.tf").exists()
                           or (lab_dir / f"task{n}.tf").is_symlink())]
        task_note = (f" (+ creates {', '.join(missing)})" if missing
                     else " (existing files kept)")
    print(f"  {CYN}tasks{RST}      {tasks} file(s): {task_files}{task_note}")
    print(f"  {CYN}project{RST}    {project}")
    print(f"  {CYN}region{RST}     {region} / {zone}")
    if running:
        print(f"  {CYN}lab{RST}        {GRN}running{RST} - extracted values are live")
    if fetched["extras"]:
        print(f"  {CYN}variables{RST}  +{len(fetched['extras'])} lab-specific (variables.tf + tfvars)")
    print(f"  {CYN}readme{RST}     {'content sections from the lab page' if sections_md else 'placeholder sections (fill manually)'}")
    if not refresh:
        print(f"  {CYN}creates{RST}    {' '.join(TEMPLATE_FILES)}, task files, terraform.tfvars")

    if not args.yes and ask("Proceed?", default="y").lower() not in ("y", "yes"):
        print(f"{YLW}aborted - nothing was created{RST}")
        return 1

    # --- execution phase: no more questions, just work ---------------------
    if refresh:
        # reset first, then re-scaffold - the shared reset prints its
        # banners and spinners inline, so exactly what got cleared is
        # visible. A DIRECT call, never the legacy executable: a
        # refresh depends on the reset implementation, not on a file
        # that happens to sit next to the entry point. reset_lab
        # revalidates the target immediately before its cleanup and
        # returns 1 if the local cleanup fails, so nothing below runs
        # unless the lab really was reset.
        # flush first: piped runs are block-buffered, and the reset's
        # output must not overtake the preview the user just approved.
        sys.stdout.flush()
        rc = reset.reset_lab(str(lab_dir))
        if rc != 0:
            sys.exit(f"{RED}reset failed (exit {rc}) - nothing was rewritten{RST}")

    with Spinner(f"{CYN}{'refreshing' if refresh else 'scaffolding'} {display}{RST}"):
        created = scaffold(lab_dir, title, url, sections_md, tasks, page_tasks,
                           project, region, zone, fetched["extras"], refresh=refresh)
    LOG.debug("scaffolded %d files into %s", len(created), lab_dir)

    init_status = "skipped"
    if not args.skip_init:
        if shutil.which("terraform"):
            init_status = "initialized" if terraform_init(lab_dir) else "failed"
        else:
            print(f"{YLW}terraform not found on PATH - skipped init{RST}")

    auth_status = "skipped"
    if not args.skip_auth:
        wants_auth = args.yes or ask("Authenticate gcloud now?", default="y").lower() in ("y", "yes")
        if wants_auth:
            if shutil.which("gcloud"):
                if student:
                    # the lab page's connection details name the
                    # account the gcloud browser login should use
                    print(f"\n{CYN}Student account for this lab:{RST} {BLU}{student}{RST}")
                auth_status = "ok" if gcloud_setup(project, region, zone) else "failed"
            else:
                print(f"{YLW}gcloud not found on PATH - skipped auth{RST}")

    # --- summary ------------------------------------------------------------
    banner("LAB READY")

    print(f"{GRN}{'Refreshed' if refresh else 'Created'} {len(created)} files in {display}/:{RST}")
    for path in created:
        print(f"  {path.name}")
    if refresh:
        kept = refresh_keeps(lab_dir)
        print(f"\n{GRN}Left untouched ({len(kept)}):{RST}")
        for name in kept:
            print(f"  {name}")

    print()
    print(f"  terraform: {init_status}")
    print(f"  gcloud:    {auth_status}")
    LOG.debug("run complete: init=%s auth=%s", init_status, auth_status)
    if auth_status == "ok":
        account = subprocess.run(
            ["gcloud", "config", "get-value", "account"], capture_output=True, text=True
        ).stdout.strip()
        if account:
            print(f"  authenticated as {BLU}{account}{RST}")

    if "<!-- Add" in (lab_dir / "README.md").read_text():
        print(f"\n{YLW}README still has placeholders - fill in the overview/objectives from the lab page{RST}")

    print(f"\n{CYN}Next steps:{RST}")
    # the resolved absolute target, quoted when it needs quoting: the
    # printed cd works pasted into the caller's shell, wherever that
    # shell happens to be - including outside the repository
    print(f"  cd {shlex.quote(str(lab_dir))}")
    if init_status != "initialized":
        print(f"  terraform init")
    print(f"  # write resources in task<N>.tf as you work through the lab")
    print(f"  terraform plan")
    return 0


def main(argv=None, prog="labkit start") -> int:
    """Parse and run the start workflow."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description=SUMMARY,
        epilog=examples(prog),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_arguments(parser)
    return run(parser.parse_args(argv))
