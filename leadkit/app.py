"""The app: a page in the browser that does everything leads.py does.

`python leads.py app` (or double-clicking the launcher) starts a small server
on this computer only (127.0.0.1) and opens the browser. Runs are started as
separate processes, exactly like `leads.py run`, so closing the browser tab
doesn't stop one; closing this window does, and starting it again picks up
where it left off.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from leadkit import actions, doctor, emailverify, estimate, jobs, keys_page, realtor, settings
from leadkit.jobs import ADDONS

PAGE = Path(__file__).parent / "static" / "app.html"
UPLOADS = settings.JOBS_DIR / "_uploads"
LEADS_PY = settings.ROOT / "leads.py"

_PROGRESS = re.compile(r"^\s+([\d,]+)/([\d,]+)\s+\(([\d.]+)/s, ~(\d+) min left\)")
_STEP = re.compile(r"^== (\w+) ==")
STEP_WORDS = {
    "count": "Counting active listings", "listings": "Getting listings and their agents",
    "agents": "Merging listings into agents",
    "emails": "Finding emails", "deep_emails": "Finding emails in Chrome (slow)",
    "verify_emails": "Verifying emails", "volume": "Checking homes.com sales",
    "verify_phones": "Checking phone numbers", "fix_phones": "Finding real cell numbers",
}


# UTF-8 throughout: Windows would otherwise write the log in its old code
# page, and a "→" or "✓" in it would stop the run. LEADS_FEED: print a line
# per agent for the live view.
RUN_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "LEADS_FEED": "1"}


class Runs:
    """Run processes, one per job. The pid is also written to the job's
    folder so a restarted app still knows a run is going."""

    def __init__(self):
        self.procs: dict[str, subprocess.Popen] = {}
        self.lock = threading.Lock()

    def _pid_file(self, job) -> Path:
        return job.dir / "run.pid"

    def running(self, job) -> bool:
        with self.lock:
            proc = self.procs.get(job.name)
            if proc is not None:
                if proc.poll() is None:
                    return True
                self.procs.pop(job.name)
                (job.dir / "run.exit").write_text(str(proc.returncode), encoding="utf-8")
        pid_file = self._pid_file(job)
        if not pid_file.exists():
            return False
        if sys.platform.startswith("win"):
            # Signal 0 means Ctrl+C on Windows, so it can't be used to ask
            # "is it alive?"; a run this app didn't start is treated as over.
            pid_file.unlink(missing_ok=True)
            return False
        try:
            os.kill(int(pid_file.read_text(encoding="utf-8")), 0)
            return True
        except (OSError, ValueError):
            pid_file.unlink(missing_ok=True)
            return False

    def start(self, job, limit: int | None = None):
        if self.running(job):
            return
        cmd = [sys.executable, "-u", str(LEADS_PY), "run", job.name]
        if limit:
            cmd += ["--limit", str(limit)]
        if sys.platform == "darwin":
            cmd = ["caffeinate", "-i"] + cmd  # keep the Mac awake while it runs
        log = (job.dir / "run.log").open("w", encoding="utf-8")
        (job.dir / "run.exit").unlink(missing_ok=True)
        (job.dir / "run.limit").write_text(str(limit or ""), encoding="utf-8")
        proc = subprocess.Popen(cmd, cwd=settings.ROOT, stdout=log, stderr=subprocess.STDOUT, env=RUN_ENV)
        with self.lock:
            self.procs[job.name] = proc
        self._pid_file(job).write_text(str(proc.pid), encoding="utf-8")

    def stop(self, job):
        with self.lock:
            proc = self.procs.get(job.name)
        if proc and proc.poll() is None:
            proc.terminate()
        elif self._pid_file(job).exists() and not sys.platform.startswith("win"):
            try:
                os.kill(int(self._pid_file(job).read_text(encoding="utf-8")), 15)
            except (OSError, ValueError):
                pass
        self._pid_file(job).unlink(missing_ok=True)
        (job.dir / "run.exit").write_text("stopped", encoding="utf-8")


RUNS = Runs()
QUOTES: dict[str, dict] = {}   # job name -> {"state": "working"|"done"|"error", ...}


def read_log(job, name: str = "run.log") -> dict:
    path = job.dir / name
    if not path.exists():
        return {"step": None, "progress": None, "stopped": None, "tail": "", "feed": []}
    raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    # "~ 12:01:33 emails ok Jane Doe -> jane@x.com": the live view's lines.
    feed, lines = [], []
    for line in raw:
        parts = line.split(" ", 4) if line.startswith("~ ") else None
        if parts and len(parts) == 5:
            feed.append({"time": parts[1], "step": parts[2], "tone": parts[3], "text": parts[4]})
        else:
            lines.append(line)
    step = progress = stopped = None
    for line in lines:
        if m := _STEP.match(line):
            step, progress = m.group(1), None
        elif m := _PROGRESS.match(line):
            done, total = int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))
            progress = {"done": done, "total": total, "minutes_left": int(m.group(4))}
        elif line.startswith("STOPPED: "):
            stopped = line[len("STOPPED: "):]
    if not stopped and any(l.startswith("Traceback") for l in lines):
        stopped = "Something went wrong. The details are in the log below."
    return {"step": step, "step_words": STEP_WORDS.get(step or "", step), "progress": progress,
            "stopped": stopped, "tail": "\n".join(lines[-40:]), "feed": feed[-200:]}


def job_view(name: str) -> dict:
    job = jobs.load(name)
    running = RUNS.running(job)
    exit_file = job.dir / "run.exit"
    log = read_log(job)
    limit_file = job.dir / "run.limit"
    view = {
        "job": {k: getattr(job, k) for k in ("name", "mode", "markets", "addons", "min_listings",
                                              "phones_for", "producer_min",
                                              "approved_budget", "created_at", "source_file")},
        "quote": job.quote, "quote_job": QUOTES.get(name),
        "running": running,
        "finished": exit_file.read_text(encoding="utf-8") if exit_file.exists() and not running else None,
        "trial": bool(limit_file.exists() and limit_file.read_text(encoding="utf-8").strip()),
        "log": log, "status": actions.status(job), "files": actions.files(job),
        "missing": estimate.missing_keys(job), "needs": sorted(estimate.services_needed(job)),
        "budget_stop": round((job.approved_budget or 0) * settings.BUDGET_OVERRUN, 2),
    }
    if (QUOTES.get(name) or {}).get("state") == "working":
        view["log"]["feed"] = read_log(job, "quote.log")["feed"]  # the listing count, live
    # After a run, anything still to do means some agents failed this time.
    view["left"] = 0
    if view["finished"] and job.approved_budget:
        q = estimate.quote(job)
        view["left"] = sum(l["count"] for l in q["lines"] if l["step"] != "fix_phones" or not q["notes"])
    if view["status"]["fix"] and view["status"]["fix"]["bad"]:
        view["fix_text"] = estimate.format_fix_summary(job, view["status"]["fix"]).split("\nTo do it")[0]
    return view


def start_quote(name: str):
    QUOTES[name] = {"state": "working", "started": time.time()}

    def work():
        try:
            job = jobs.load(name)
            store = job.store()
            counting = job.mode == "build" and not all(store.market_done(m["label"]) for m in job.markets)
            store.close()
            if counting:
                # Counting a county takes minutes; done in its own process,
                # like a run, so the page can stream it from quote.log.
                with (job.dir / "quote.log").open("w", encoding="utf-8") as log:
                    code = subprocess.call([sys.executable, "-u", str(LEADS_PY), "quote", name], cwd=settings.ROOT,
                                           stdout=log, stderr=subprocess.STDOUT, env=RUN_ENV)
                if code:
                    tail = (job.dir / "quote.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()
                    raise actions.ActionError(next((l for l in reversed(tail) if l.strip() and not l.startswith("~ ")),
                                                   "Counting the listings failed."))
            else:
                # The page shows this log while pricing: an old count isn't news.
                (job.dir / "quote.log").unlink(missing_ok=True)
            QUOTES[name] = {"state": "done", "result": actions.quote(jobs.load(name))}
        except Exception as e:  # shown to the person as-is
            QUOTES[name] = {"state": "error", "error": str(e)}

    threading.Thread(target=work, daemon=True).start()


def preview(job, limit: int = 8) -> dict:
    files = actions.files(job)
    if not files:
        return {"columns": [], "rows": []}
    import csv
    path = job.export_dir / files[0]["file"]
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = [r for _, r in zip(range(limit), reader)]
    wanted = ["full_name", "email", "email_verified", "phone", "phone_type", "brokerage",
              "active_listings", "listings_sold_last_12mo"]
    cols = [c for c in wanted if rows and c in rows[0]]
    return {"columns": cols, "rows": [[r.get(c, "") for c in cols] for r in rows]}


def open_folder(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


def unit_prices() -> dict:
    """Per-agent prices for the checkboxes, from the same numbers the quote uses."""
    from leadkit import phones
    search = estimate._search_price()
    p = settings.PRICES
    return {
        "phone": phones.lookup_price(),
        "volume": round(estimate._volume_price(), 4),
        "emails": round(2 * search + settings.EMAIL_HIT_RATE * emailverify.price(), 4),
        "listing": round(p["realtor_call"] / realtor.TARGET + estimate.LISTING_OVERHEAD * p["realtor_listing"], 4),
    }


def keys_view(checked: dict | None = None) -> dict:
    present = doctor.present()
    services = []
    for svc in keys_page.SERVICES:
        state = (checked or {}).get(svc["check"])
        services.append({**{k: svc[k] for k in ("check", "title", "for", "price", "link", "link_text")},
                         "hint": svc.get("hint", ""),
                         "fields": [{"key": k, "label": label,
                                     "saved": keys_page._masked(getattr(settings, k))} for k, label in svc["keys"]],
                         "present": present[svc["check"]],
                         "state": state[0] if state else None, "message": state[1] if state else ""})
    return {"services": services}


class Handler(BaseHTTPRequestHandler):
    token = ""

    def log_message(self, *args):
        pass

    def _send(self, code: int, body, kind: str = "application/json", headers=None):
        data = body if isinstance(body, bytes) else (
            body.encode() if isinstance(body, str) else json.dumps(body).encode())
        self.send_response(code)
        self.send_header("Content-Type", f"{kind}; charset=utf-8" if kind.startswith(("text", "application/json")) else kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _error(self, message: str, code: int = 400):
        self._send(code, {"error": message})

    def do_GET(self):
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        q = parse_qs(url.query)
        try:
            if not parts:
                html = PAGE.read_text(encoding="utf-8").replace("__TOKEN__", self.token)
                return self._send(200, html, "text/html")
            if parts == ["api", "home"]:
                listing = []
                for name in reversed(jobs.all_names()):
                    try:
                        view = job_view(name)
                    except Exception:
                        continue
                    listing.append({k: view[k] for k in ("job", "running", "finished", "status", "quote")}
                                   | {"stopped": view["log"]["stopped"]})
                return self._send(200, {"jobs": listing, "keys": doctor.present(),
                                        "addons": ADDONS, "prices": unit_prices()})
            if parts == ["api", "keys"]:
                return self._send(200, keys_view())
            if parts == ["api", "places"]:
                # The built-in catalog of US counties, cities and ZIP codes: instant and free.
                from leadkit import places
                query = (q.get("q") or [""])[0].strip()
                found = places.search(query) if query else []
                return self._send(200, {"places": [{**p, "population": p["pop"]} for p in found]})
            if parts == ["api", "estimate"]:
                one = lambda k, d="": (q.get(k) or [d])[0]  # noqa: E731
                return self._send(200, estimate.rough(
                    one("mode", "build"), population=int(float(one("population", "0") or 0)),
                    rows=int(float(one("rows", "0") or 0)), min_listings=int(one("min_listings", "1") or 1),
                    addons=[a for a in one("addons").split(",") if a], phones_for=one("phones_for", "all")))
            if parts == ["api", "markets"]:
                query = (q.get("q") or [""])[0].strip()
                return self._send(200, {"markets": actions.find_markets(query) if query else []})
            if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
                return self._send(200, job_view(parts[2]))
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "preview":
                return self._send(200, preview(jobs.load(parts[2])))
            if len(parts) == 5 and parts[:2] == ["api", "jobs"] and parts[3] == "download":
                job = jobs.load(parts[2])
                path = (job.export_dir / parts[4]).resolve()
                if path.parent != job.export_dir.resolve() or not path.exists():
                    return self._error("No such file.", 404)
                return self._send(200, path.read_bytes(), "text/csv",
                                  {"Content-Disposition": f'attachment; filename="{path.name}"'})
            self._error("Not found.", 404)
        except SystemExit as e:
            self._error(str(e), 404)
        except actions.ActionError as e:
            self._error(str(e))
        except Exception as e:
            self._error(f"Something went wrong: {e}", 500)

    def do_POST(self):
        url = urlparse(self.path)
        if parse_qs(url.query).get("t", [""])[0] != self.token:  # only our own page may change things
            return self._error("Reload the page.", 403)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            if parts == ["api", "upload"]:
                name = re.sub(r"[^\w.\- ]", "_", unquote(self.headers.get("X-Filename") or "list.csv"))[-80:]
                if not name.lower().endswith((".csv", ".xlsx", ".xlsm")):
                    return self._error("Use a .csv or .xlsx file (in Excel or Numbers: File, Export, CSV).")
                UPLOADS.mkdir(parents=True, exist_ok=True)
                path = UPLOADS / f"{int(time.time())}-{name}"
                path.write_bytes(raw)
                return self._send(200, {"path": str(path), "filename": name, **actions.read_list(path)})
            body = json.loads(raw or b"{}")
            if parts == ["api", "keys"]:
                values = {k: str(v).strip() for k, v in body.items()
                          if k in settings.KEY_NAMES and str(v).strip()}
                if values:
                    keys_page.write_env(values)
                    settings.reload_keys()
                touched = {name for name, keys, _ in doctor.CHECKS if set(keys) & set(values)}
                return self._send(200, keys_view(doctor.check_all(only=touched)))
            if parts == ["api", "jobs"]:
                result = actions.create_job(
                    body.get("name") or "my-list", body.get("mode", ""), markets=body.get("markets") or [],
                    file=body.get("file"), mapping=body.get("mapping") or {}, addons=body.get("addons") or [],
                    min_listings=body.get("min_listings") or 1, phones_for=body.get("phones_for") or "all",
                    producer_min=body.get("producer_min") or 5)
                start_quote(result["name"])
                return self._send(200, result)
            if len(parts) == 4 and parts[:2] == ["api", "jobs"]:
                job, action = jobs.load(parts[2]), parts[3]
                if action == "quote":
                    start_quote(job.name)
                elif action == "approve":
                    actions.approve(job, float(body.get("budget") or 0))
                    RUNS.start(job, limit=body.get("limit"))
                elif action == "run":
                    RUNS.start(job, limit=body.get("limit"))
                elif action == "stop":
                    RUNS.stop(job)
                elif action == "add":
                    actions.add_steps(job, body.get("addons") or [])
                    start_quote(job.name)
                elif action == "open":
                    open_folder(job.export_dir)
                else:
                    return self._error("Not found.", 404)
                return self._send(200, job_view(job.name))
            self._error("Not found.", 404)
        except SystemExit as e:
            self._error(str(e), 404)
        except actions.ActionError as e:
            self._error(str(e))
        except Exception as e:
            self._error(f"Something went wrong: {e}", 500)


def serve(open_browser: bool = True, port: int = 0, on_ready=None):
    from leadkit import places
    threading.Thread(target=places.search, args=("a",), daemon=True).start()  # load the catalog now, not on the first keystroke
    Handler.token = secrets.token_urlsafe(16)
    server = None
    for p in ([port] if port else [8780, 8781, 8782, 0]):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            break
        except OSError:
            continue
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Lead List Builder is open at {url}\n"
          "Keep this window open while you use it. Close it (or press Ctrl+C) to quit.", flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    if on_ready:
        on_ready(url, server)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
