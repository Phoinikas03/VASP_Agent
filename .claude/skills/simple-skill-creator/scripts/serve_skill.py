#!/usr/bin/env python3
"""
Render SKILL.md as a web interface that can be edited directly in the browser (editor on the left + live preview on the right).

Usage:
  Start in background: nohup python scripts/serve_skill.py <skill_dir> > /tmp/skill_view.log 2>&1 &
  Read URL: grep '^URL=' /tmp/skill_view.log
  Stop service: kill $(grep '^PID=' /tmp/skill_view.log | cut -d= -f2)
"""
import argparse
import json
import os
import re
import socket
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

SKILL_FILE: Path = None  # set by main() at runtime


def find_free_port(start=8700):
    for p in range(start, start + 100):
        try:
            with socket.socket() as s:
                s.bind(("", p))
                return p
        except OSError:
            continue
    raise RuntimeError("No free port available")


def get_name() -> str:
    content = SKILL_FILE.read_text("utf-8")
    m = re.search(r'^name:\s*["\']?(.+?)["\']?\s*$', content, re.MULTILINE)
    return m.group(1).strip() if m else SKILL_FILE.parent.name


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Edit SKILL - {name}</title>
<script src="https://cdn.jsdelivr.net/npm/marked@9/marked.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f6f8fa;color:#24292f}
.topbar{background:#24292f;color:#fff;padding:10px 20px;display:flex;align-items:center;justify-content:space-between;height:46px}
.topbar-left{display:flex;align-items:center;gap:10px;font-size:14px;font-weight:600}
.badge{background:#1a7f37;font-size:11px;padding:2px 8px;border-radius:20px;font-weight:500}
.topbar-right{display:flex;align-items:center;gap:12px}
#status{font-size:12px;color:#8c959f;transition:color .3s}
#status.dirty{color:#f0883e}#status.saved{color:#3fb950}
#save-btn{background:#238636;color:#fff;border:1px solid #2ea043;padding:5px 14px;border-radius:6px;cursor:pointer;font-size:13px;font-weight:500}
#save-btn:hover{background:#2ea043}#save-btn:disabled{opacity:.5;cursor:default}
.panes{display:flex;height:calc(100vh - 46px)}
.pane{flex:1;display:flex;flex-direction:column;border-right:1px solid #d0d7de}
.pane:last-child{border-right:none}
.pane-header{background:#f6f8fa;border-bottom:1px solid #d0d7de;padding:6px 16px;font-size:12px;color:#57606a;font-weight:600;letter-spacing:.5px;display:flex;justify-content:space-between;align-items:center}
#editor{flex:1;width:100%;border:none;outline:none;resize:none;padding:20px;font-family:"SFMono-Regular",Consolas,monospace;font-size:13px;line-height:1.6;background:#fff;color:#24292f;tab-size:2}
.preview{flex:1;overflow-y:auto;padding:24px 32px;background:#fff}
/* Markdown preview styles */
.preview h1,.preview h2{border-bottom:1px solid #d0d7de;padding-bottom:6px;margin:24px 0 12px;font-weight:600}
.preview h1{font-size:1.8em}.preview h2{font-size:1.4em}.preview h3{font-size:1.15em;margin:20px 0 8px}
.preview p{line-height:1.75;margin:10px 0}
.preview ul,.preview ol{padding-left:26px;margin:8px 0}.preview li{line-height:1.75;margin:3px 0}
.preview code{background:#f6f8fa;border:1px solid #d0d7de;border-radius:3px;padding:2px 6px;font-family:monospace;font-size:88%}
.preview pre{background:#f6f8fa;border:1px solid #d0d7de;border-radius:6px;padding:16px;overflow-x:auto;margin:14px 0}
.preview pre code{background:none;border:none;padding:0;font-size:13px;line-height:1.6}
.preview table{border-collapse:collapse;width:100%;margin:14px 0}
.preview th,.preview td{border:1px solid #d0d7de;padding:7px 13px}
.preview th{background:#f6f8fa;font-weight:600}
.preview blockquote{border-left:4px solid #d0d7de;padding:4px 16px;color:#57606a;margin:14px 0}
.preview hr{border:none;border-top:1px solid #d0d7de;margin:24px 0}
.preview a{color:#0969da}.preview strong{font-weight:600}
.line-count{font-size:11px;color:#8c959f}
</style></head><body>
<div class="topbar">
  <div class="topbar-left">✏️ {name} <span class="badge">SKILL</span></div>
  <div class="topbar-right">
    <span id="status">Loaded</span>
    <button id="save-btn" onclick="save()">Save</button>
  </div>
</div>
<div class="panes">
  <div class="pane">
    <div class="pane-header">
      <span>SKILL.md - Edit</span>
      <span class="line-count" id="line-count"></span>
    </div>
    <textarea id="editor" spellcheck="false" oninput="onEdit()"></textarea>
  </div>
  <div class="pane">
    <div class="pane-header">Preview</div>
    <div class="preview" id="preview"></div>
  </div>
</div>
<script>
const editor = document.getElementById('editor');
const preview = document.getElementById('preview');
const status = document.getElementById('status');
const saveBtn = document.getElementById('save-btn');
let dirty = false, saveTimer = null;

// Load content
fetch('/content').then(r=>r.json()).then(d=>{
  editor.value = d.content;
  updatePreview();
  updateLineCount();
  setStatus('Loaded', '');
});

function onEdit() {
  dirty = true;
  saveBtn.disabled = false;
  setStatus('Unsaved', 'dirty');
  updateLineCount();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(updatePreview, 300);
}

function updatePreview() {
  preview.innerHTML = marked.parse(editor.value);
}

function updateLineCount() {
  const lines = editor.value.split('\n').length;
  document.getElementById('line-count').textContent = lines + ' lines';
}

function setStatus(text, cls) {
  status.textContent = text;
  status.className = cls;
}

function save() {
  if (!dirty) return;
  saveBtn.disabled = true;
  setStatus('Saving…', '');
  fetch('/save', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({content: editor.value})
  }).then(r=>r.json()).then(d=>{
    if (d.ok) { dirty=false; setStatus('Saved ✓', 'saved'); }
    else       { setStatus('Save failed', 'dirty'); saveBtn.disabled=false; }
  }).catch(()=>{ setStatus('Network error', 'dirty'); saveBtn.disabled=false; });
}

// Ctrl+S / Cmd+S to save
document.addEventListener('keydown', e=>{
  if ((e.ctrlKey||e.metaKey) && e.key==='s') { e.preventDefault(); save(); }
});

// Tab key inserts spaces
editor.addEventListener('keydown', e=>{
  if (e.key==='Tab') {
    e.preventDefault();
    const s=editor.selectionStart, end=editor.selectionEnd;
    editor.value = editor.value.substring(0,s)+'  '+editor.value.substring(end);
    editor.selectionStart = editor.selectionEnd = s+2;
    onEdit();
  }
});
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "":
            html = HTML_TEMPLATE.replace("{name}", get_name()).encode("utf-8")
            self._respond(200, "text/html;charset=utf-8", html)
        elif self.path == "/content":
            data = json.dumps({"content": SKILL_FILE.read_text("utf-8")}).encode("utf-8")
            self._respond(200, "application/json", data)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/save":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            SKILL_FILE.write_text(body["content"], "utf-8")
            data = json.dumps({"ok": True}).encode("utf-8")
            self._respond(200, "application/json", data)
        else:
            self.send_error(404)

    def _respond(self, code, ctype, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    global SKILL_FILE
    ap = argparse.ArgumentParser(description="Online SKILL.md editor")
    ap.add_argument("skill_path", help="Skill directory path")
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()

    skill_dir = Path(args.skill_path).resolve()
    SKILL_FILE = skill_dir / "SKILL.md"
    if not SKILL_FILE.exists():
        print(f"ERROR: SKILL.md not found in {skill_dir}", flush=True)
        raise SystemExit(1)

    port = args.port or find_free_port()
    print(f"URL=http://localhost:{port}", flush=True)
    print(f"PID={os.getpid()}", flush=True)
    print(f"Editing {SKILL_FILE} on port {port} ...", flush=True)

    # This service has no authentication and can write SKILL.md, so it listens only on loopback; use SSH port forwarding for remote access.
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
