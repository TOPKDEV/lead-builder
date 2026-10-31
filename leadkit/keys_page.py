"""A small page in the browser for pasting API keys, so nobody has to find
and edit a hidden .env file.

`python leads.py keys` starts it on this computer only (127.0.0.1), opens
the browser, and stops when the person clicks Done. Keys are written to
.env next to the code and tested on the spot; the page never shows a saved
key back in full.
"""
from __future__ import annotations

import html
import json
import os
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from leadkit import doctor, settings

# One card per service. Monid runs every tool the lead builder uses; Twilio
# is the recommended extra, and nothing needs it.
SERVICES = [
    {"check": "monid", "title": "Monid: the one key",
     "for": "Everything: building a list from active listings (with agents' emails), phone checks and "
            "finding real cells, email checks, top producers, and the searching.",
     "price": "Pay as you go, from one balance. About $0.01 per agent to check a list you have; "
              "building one is a few dollars per 1,000 listings.",
     "link": "https://monid.ai", "link_text": "Open Monid (sign up, then create an API key)",
     "keys": [("MONID_API_KEY", "API key (starts with monid_)")]},
    {"check": "twilio", "title": "Twilio (recommended)",
     "for": "When no record knows a number, a carrier's answer on whether it's a cell. Those go in their "
            "own \"probably\" file. Everything else works without it.",
     "price": "About $0.008 per number it checks, roughly $1.20 per 1,000 agents, from your own Twilio balance.",
     "link": "https://console.twilio.com", "link_text": "Open Twilio (sign up, then see Account Info)",
     "hint": "On the Twilio Console front page, under Account Info. You don't need to buy a phone number.",
     "keys": [("TWILIO_ACCOUNT_SID", "Account SID (starts with AC)"), ("TWILIO_AUTH_TOKEN", "Auth Token")]},
]


def write_env(updates: dict[str, str]) -> None:
    """Set keys in .env, keeping everything else in the file as it was.
    A missing .env starts from .env.example, so its explanations come along."""
    path = settings.ENV_FILE
    example = settings.ROOT / ".env.example"
    lines = (path.read_text(encoding="utf-8") if path.exists() else
             example.read_text(encoding="utf-8") if example.exists() else "").splitlines()
    remaining = dict(updates)
    for i, line in enumerate(lines):
        name = line.split("=", 1)[0].strip()
        if "=" in line and name in remaining and not line.lstrip().startswith("#"):
            lines[i] = f"{name}={remaining.pop(name)}"
    lines += [f"{k}={v}" for k, v in remaining.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _masked(value: str) -> str:
    return f"saved, ends in {value[-4:]}" if len(value) > 8 else ("saved" if value else "")


def page(token: str, need: set[str]) -> str:
    cards, extra = [], []
    for svc in SERVICES:
        fields = "".join(
            f'<label>{html.escape(placeholder)}'
            f'<input type="password" name="{key}" autocomplete="off" spellcheck="false" '
            f'placeholder="{html.escape(_masked(getattr(settings, key)) or "paste here")}"></label>'
            for key, placeholder in svc["keys"])
        badge = '<span class="need">Needed for your job</span>' if svc["check"] in need else ""
        hint = f'<p class="hint">{html.escape(svc["hint"])}</p>' if svc.get("hint") else ""
        (cards if svc["check"] in need or not need else extra).append(f'''
<section class="card{' needed' if svc['check'] in need else ''}" data-check="{svc['check']}">
  <h2>{html.escape(svc['title'])} {badge}</h2>
  <p>{html.escape(svc['for'])}</p>
  <p class="price">{html.escape(svc['price'])}</p>
  <a href="{svc['link']}" target="_blank" rel="noopener">{html.escape(svc['link_text'])} &rarr;</a>
  {hint}{fields}
  <div class="status" id="status-{svc['check']}"></div>
</section>''')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Lead list keys</title>
<style>
:root {{ --bg:#f6f5f2; --card:#fff; --ink:#1c1c1c; --muted:#666; --line:#e2dfd8; --ok:#1f7a3a; --bad:#b3261e; --accent:#2f5bd3; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#151515; --card:#1f1f1f; --ink:#eee; --muted:#aaa; --line:#333; --ok:#5cc27a; --bad:#ff7b72; --accent:#7aa2ff; }} }}
body {{ background:var(--bg); color:var(--ink); font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif; margin:0; padding:24px 16px 120px; }}
main {{ max-width:720px; margin:0 auto; }}
h1 {{ font-size:24px; margin:0 0 4px; }} .lead {{ color:var(--muted); margin:0 0 20px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px 18px; margin:0 0 14px; }}
.card.needed {{ border-color:var(--accent); }}
h2 {{ font-size:17px; margin:0 0 6px; }} p {{ margin:4px 0; }} .price {{ color:var(--muted); font-size:14px; }}
.hint {{ font-size:14px; color:var(--muted); }}
.need {{ white-space:nowrap; font-size:12px; background:var(--accent); color:#fff; border-radius:99px; padding:2px 8px; margin-left:6px; vertical-align:2px; }}
a {{ color:var(--accent); font-size:14px; }}
label {{ display:block; font-size:13px; color:var(--muted); margin-top:10px; }}
input {{ display:block; width:100%; box-sizing:border-box; margin-top:4px; padding:10px; font:14px ui-monospace,monospace;
  border:1px solid var(--line); border-radius:8px; background:var(--bg); color:var(--ink); }}
.status {{ margin-top:8px; font-size:14px; min-height:1em; }} .ok {{ color:var(--ok); }} .bad {{ color:var(--bad); }}
.bar {{ position:fixed; left:0; right:0; bottom:0; background:var(--card); border-top:1px solid var(--line); padding:12px 16px; }}
.bar div {{ max-width:720px; margin:0 auto; display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
button {{ font:inherit; padding:10px 18px; border-radius:8px; border:0; cursor:pointer; background:var(--accent); color:#fff; }}
button.secondary {{ background:transparent; color:var(--ink); border:1px solid var(--line); }}
#msg {{ color:var(--muted); font-size:14px; }}
details {{ margin-top:20px; }} summary {{ cursor:pointer; color:var(--muted); margin-bottom:12px; }}
</style></head><body><main>
<h1>Your Monid key</h1>
<p class="lead">One key runs everything. Sign up at Monid, add some credit, create an API key, paste it
here, then Save &amp; test. It stays on this computer, in a file called .env in the tool's folder.</p>
{"".join(cards)}
{f'<details><summary>Other services (not needed for this job)</summary>{"".join(extra)}</details>' if extra else ""}
</main>
<div class="bar"><div><button id="save">Save &amp; test</button><button class="secondary" id="done">I'm done</button>
<span id="msg"></span></div></div>
<script>
const token = {json.dumps(token)};
function show(results) {{
  for (const [name, [state, msg]] of Object.entries(results)) {{
    const el = document.getElementById("status-" + name);
    if (!el) continue;
    el.className = "status " + (state === "ok" ? "ok" : state === "bad" ? "bad" : "");
    el.textContent = state === "ok" ? "\\u2713 " + msg : state === "bad" ? "Problem: " + msg : "";
  }}
}}
async function post(path, body) {{
  const r = await fetch(path + "?t=" + token, {{method: "POST", headers: {{"Content-Type": "application/json"}},
    body: JSON.stringify(body || {{}})}});
  return r.json();
}}
document.getElementById("save").onclick = async () => {{
  const values = {{}};
  document.querySelectorAll("input").forEach(i => {{ if (i.value.trim()) values[i.name] = i.value.trim(); }});
  document.getElementById("msg").textContent = "Testing your keys...";
  const res = await post("/save", values);
  document.querySelectorAll("input").forEach(i => {{ if (i.value.trim()) {{ i.value = ""; i.placeholder = "saved"; }} }});
  show(res.results);
  document.getElementById("msg").textContent = "Saved. Green means it works.";
}};
document.getElementById("done").onclick = async () => {{
  await post("/done");
  document.body.innerHTML = "<main><h1>All set</h1><p class='lead'>You can close this tab and go back to Claude.</p></main>";
}};
post("/status").then(res => show(res.results));
</script></body></html>'''


def serve(need: set[str] | None = None, timeout_minutes: int = 30, ports=(8765, 8766, 8767, 0),
          on_ready=None) -> dict[str, tuple[str, str]]:
    """Open the page and wait until Done (or the timeout). Returns the final
    key check results."""
    token = secrets.token_urlsafe(16)
    finished = threading.Event()
    state: dict = {"results": {}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code: int, body: str, kind: str = "application/json"):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", f"{kind}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.split("?")[0] != "/":
                return self._send(404, "{}")
            self._send(200, page(token, need or set()), "text/html")

        def do_POST(self):
            path, _, query = self.path.partition("?")
            if query != f"t={token}":   # only the page this run served may post
                return self._send(403, "{}")
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if path == "/save":
                allowed = {k: v.strip() for k, v in body.items()
                           if k in settings.KEY_NAMES and isinstance(v, str) and v.strip()}
                if allowed:
                    write_env(allowed)
                    settings.reload_keys()
            if path in ("/save", "/status"):
                state["results"] = doctor.check_all()
                return self._send(200, json.dumps({"results": state["results"]}))
            if path == "/done":
                finished.set()
                return self._send(200, "{}")
            self._send(404, "{}")

    server = None
    for port in ports:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Keys page open at {url}  (if no browser window appeared, open that link)", flush=True)
    if on_ready:
        on_ready(url)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    finished.wait(timeout_minutes * 60)
    server.shutdown()
    return state["results"] or doctor.check_all()
