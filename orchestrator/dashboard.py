"""The dashboard — a dumb read model over the event streams, plus a command sender.

It renders task state (folded from the per-task event streams) and forwards human-gate
decisions to the engine. It holds no orchestration state of its own: if it dies, the
pipeline keeps running headless. Dependency-free (stdlib http.server) so there is nothing
extra to install and nothing extra to break.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .engine import Engine
from .store import TaskStore


def task_summaries(store: TaskStore) -> list[dict]:
    summaries = []
    for task_id in store.list_task_ids():
        state = store.state(task_id)
        if state is None:
            continue
        summary = asdict(state)
        summary["status"] = state.status.value
        summary["total_cost_usd"] = round(state.total_cost_usd, 6)
        summaries.append(summary)
    return summaries


def task_detail(store: TaskStore, task_id: str) -> dict:
    state = store.state(task_id)
    if state is None:
        return {}
    detail = asdict(state)
    detail["status"] = state.status.value
    detail["events"] = [
        {"seq": event.seq, "type": event.event_type.value, "node": event.node_id,
         "ts": event.timestamp, "data": event.data}
        for event in store.stream(task_id).read()
    ]
    return detail


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep the console quiet
        pass

    @property
    def engine(self) -> Engine:
        return self.server.engine  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, _PAGE, content_type="text/html")
        elif path == "/api/tasks":
            self._send_json(200, {"tasks": task_summaries(self.engine.store)})
        elif path.startswith("/api/tasks/"):
            self._send_json(200, task_detail(self.engine.store, path.split("/")[-1]))
        else:
            self._send(404, "not found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        parts = path.split("/")
        if not (path.startswith("/api/tasks/") and parts[-1] in ("decide", "resolve")):
            self._send(404, "not found")
            return
        task_id = parts[3]
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        try:
            if parts[-1] == "decide":
                self.engine.submit_decision(task_id, body["choice"])
            else:
                self.engine.resolve_escalation(
                    task_id, body["action"], agent=body.get("agent"),
                    model=body.get("model"), role=body.get("role"))
            self._send_json(200, task_detail(self.engine.store, task_id))
        except (KeyError, ValueError, AssertionError) as exc:
            self._send_json(400, {"error": str(exc)})

    def _send(self, code: int, body: str, content_type: str = "text/plain") -> None:
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, code: int, data: dict) -> None:
        self._send(code, json.dumps(data), content_type="application/json")


def serve(engine: Engine, port: int = 8787, host: str = "127.0.0.1") -> None:
    server = ThreadingHTTPServer((host, port), _Handler)
    server.engine = engine  # type: ignore[attr-defined]
    print(f"fleet-orchestrator dashboard on http://{host}:{port}")
    server.serve_forever()


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>fleet-orchestrator</title>
<style>
:root{color-scheme:light dark;--bg:#faf9f5;--card:#fff;--line:#e5e3da;--fg:#1a1a18;--muted:#6b6a63;--accent:#185fa5;--warn:#854f0b;--warnbg:#faeeda;--danger:#a32d2d;--dangerbg:#fcebeb;--ok:#0f6e56}
@media(prefers-color-scheme:dark){:root{--bg:#1a1a18;--card:#26261f;--line:#3a3a33;--fg:#ecebe4;--muted:#a3a29a;--accent:#85b7eb;--warn:#fac77e;--warnbg:#3a2f18;--danger:#f09595;--dangerbg:#3a1c1c;--ok:#5dcaa5}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;padding:24px}
h1{font-size:18px;font-weight:500;margin:0 0 4px}.sub{color:var(--muted);font-size:13px;margin:0 0 20px}
h2{font-size:13px;font-weight:500;color:var(--muted);margin:24px 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:10px}
.gate-warn{border-color:var(--warn)}.gate-danger{border-color:var(--danger)}
.row{display:flex;justify-content:space-between;align-items:center;gap:12px}
.pill{font-size:11px;padding:2px 8px;border-radius:6px}.pw{background:var(--warnbg);color:var(--warn)}.pd{background:var(--dangerbg);color:var(--danger)}
.muted{color:var(--muted);font-size:12px}
button{font:inherit;font-size:13px;background:transparent;color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:6px 12px;cursor:pointer;margin:8px 8px 0 0}
button:hover{border-color:var(--fg)}
table{width:100%;border-collapse:collapse;font-size:13px}th{text-align:left;color:var(--muted);font-weight:400;padding:8px 10px;font-size:12px}
td{padding:8px 10px;border-top:1px solid var(--line)}
.s-running{color:var(--accent)}.s-blocked_on_human{color:var(--warn)}.s-escalated{color:var(--danger)}.s-completed{color:var(--ok)}.s-aborted{color:var(--muted)}
.empty{color:var(--muted);font-size:13px;padding:8px 0}
</style></head><body>
<h1>fleet-orchestrator</h1><p class="sub" id="sub">loading…</p>
<h2>Gate inbox</h2><div id="inbox"></div>
<h2>Tasks</h2><div class="card" style="padding:0;overflow:hidden"><table><thead><tr>
<th>Task</th><th>Flow</th><th>Node</th><th>Status</th><th>Rework</th><th style="text-align:right">Cost</th>
</tr></thead><tbody id="rows"></tbody></table></div>
<script>
async function post(url,body){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});load();return r}
async function decide(id,choice){await post(`/api/tasks/${id}/decide`,{choice})}
async function resolve(id,action){
 let body={action};
 if(action==='reassign'){const role=prompt('Reassign which role?');const agent=prompt('To which agent?');if(!role||!agent)return;body={action,role,agent}}
 const r=await post(`/api/tasks/${id}/resolve`,body);if(!r.ok){const e=await r.json();alert(e.error||'failed')}}
function esc(s){return String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function load(){
 const r=await fetch('/api/tasks');const {tasks}=await r.json();
 const need=tasks.filter(t=>t.status==='blocked_on_human'||t.status==='escalated');
 document.getElementById('sub').textContent=`${tasks.length} tasks · ${need.length} need you`;
 document.getElementById('inbox').innerHTML=need.length?need.map(t=>{
   const g=t.open_gate||{};const danger=t.status==='escalated';
   let btns;
   if(danger){btns=['retry','reassign','take_over','abort'].map(a=>`<button onclick="resolve('${t.task_id}','${a}')">${esc(a.replace('_',' '))}</button>`).join('')}
   else{const opts=g.options?Object.keys(g.options):[];btns=opts.map(c=>`<button onclick="decide('${t.task_id}','${c}')">${esc(c)}</button>`).join('')}
   const msg=danger?esc(g.reason||'escalated — needs attention'):esc(g.prompt||'');
   return `<div class="card ${danger?'gate-danger':'gate-warn'}"><div class="row">
     <div><span class="pill ${danger?'pd':'pw'}">${danger?'escalation':'gate'}</span> <b>${esc(t.task_id)}</b> · ${esc(t.current_node||'')}</div>
     <span class="muted">${esc(t.flow_name)}</span></div>
     <div style="margin-top:6px">${msg}</div>${btns}</div>`;
 }).join(''):'<div class="empty">Nothing needs you right now.</div>';
 document.getElementById('rows').innerHTML=tasks.length?tasks.map(t=>`<tr>
   <td><b>${esc(t.task_id)}</b></td><td class="muted">${esc(t.flow_name)}</td>
   <td>${esc(t.current_node||'—')}</td><td class="s-${t.status}">${esc(t.status)}</td>
   <td class="muted">${esc(JSON.stringify(t.rework_counts||{}))}</td>
   <td style="text-align:right">$${(t.total_cost_usd||0).toFixed(4)}</td></tr>`).join(''):'<tr><td colspan=6 class="empty">No tasks yet.</td></tr>';
}
load();setInterval(load,3000);
</script></body></html>"""
