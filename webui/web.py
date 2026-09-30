"""Web UI server for VASP Agent.

Exposes a single WebUI class that:
  - Serves the chat page at GET /
  - Handles WebSocket connections at GET /ws
  - Provides an asyncio Queue for inbound user messages
  - Provides a send() coroutine to push messages to the browser
"""

import asyncio
import errno
import json
import os
from contextlib import suppress
from collections.abc import Awaitable, Callable
from typing import Any
from aiohttp import web
import aiohttp

WEB_PORT = 18688
# If the preferred port is taken, try base+1, base+2, ... in turn (same idea as LiteLLM scanning ports from 4000)
WEB_PORT_TRY_COUNT = 64


def _is_address_in_use(err: OSError) -> bool:
    if err.errno == errno.EADDRINUSE:
        return True
    w = getattr(errno, "WSAEADDRINUSE", None)
    return w is not None and err.errno == w

# ---------------------------------------------------------------------------
# HTML / CSS / JS
# ---------------------------------------------------------------------------

_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VASP Agent</title>
<link rel="stylesheet"
  href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.0/marked.min.js"></script>
<style>
  :root {
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #e6edf3; --muted: #8b949e; --accent: #58a6ff;
    --user-bg: #1f3557; --agent-bg: #161b22;
    --tool-bg: #1a1f2b; --tool-border: #388bfd;
    --success: #3fb950; --error: #f85149;
    --warning: #d29922;
    --radius: 10px; --font: 'Segoe UI', system-ui, sans-serif;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; }
  body {
    background: var(--bg); color: var(--text);
    font-family: var(--font); font-size: 15px;
    display: flex; flex-direction: column;
    overflow: hidden;
  }
  #main-row {
    flex: 1;
    display: flex;
    min-height: 0;
    align-items: stretch;
  }
  #main-row.sessions-collapsed #session-sidebar {
    width: 44px;
  }
  #main-row.sessions-collapsed #session-list,
  #main-row.sessions-collapsed #new-session-btn,
  #main-row.sessions-collapsed #session-title-text {
    display: none;
  }
  #main-row.sessions-collapsed #session-sidebar-header {
    justify-content: center;
    padding-left: 6px;
    padding-right: 6px;
  }
  #main-row.sessions-collapsed #toggle-sessions-btn {
    transform: rotate(180deg);
  }
  #chat-column {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    min-height: 0;
  }
  #header {
    background: var(--surface); border-bottom: 1px solid var(--border);
    padding: 12px 20px; display: flex; align-items: center; gap: 12px;
    flex-shrink: 0;
  }
  #header h1 { font-size: 17px; font-weight: 600; }
  #status-badge {
    font-size: 12px; padding: 3px 10px; border-radius: 20px;
    background: #21262d; color: var(--muted); border: 1px solid var(--border);
    transition: all .3s;
  }
  #status-badge.thinking {
    background: #1a2a4a; color: var(--accent); border-color: var(--accent);
    animation: pulse 1.5s infinite;
  }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.5} }
  #log-path { margin-left: auto; font-size: 11px; color: var(--muted); }

  #chat-container {
    flex: 1; overflow-y: auto; padding: 20px;
    display: flex; flex-direction: column; gap: 16px;
    min-height: 0;
  }
  #chat-container::-webkit-scrollbar { width: 6px; }
  #chat-container::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

  .msg { display: flex; gap: 10px; max-width: 860px; width: 100%; }
  .msg.user { margin-left: auto; flex-direction: row-reverse; }
  .msg-label {
    font-size: 11px; font-weight: 600; color: var(--muted);
    flex-shrink: 0; padding-top: 6px; min-width: 42px; text-align: center;
  }
  .msg-bubble {
    padding: 12px 16px; border-radius: var(--radius);
    line-height: 1.65; border: 1px solid var(--border);
    max-width: calc(100% - 60px);
  }
  .msg.user .msg-bubble { background: var(--user-bg); border-color: #2563a0; white-space: pre-wrap; }
  .msg.agent .msg-bubble { background: var(--agent-bg); }

  .md-content h1,.md-content h2,.md-content h3 { margin: .8em 0 .4em; font-weight: 600; }
  .md-content h1 { font-size: 1.3em; border-bottom: 1px solid var(--border); padding-bottom: .3em; }
  .md-content h2 { font-size: 1.15em; }
  .md-content h3 { font-size: 1.05em; }
  .md-content p { margin: .5em 0; }
  .md-content ul,.md-content ol { margin: .5em 0 .5em 1.4em; }
  .md-content li { margin: .2em 0; }
  .md-content code:not(pre code) {
    font-family: 'Consolas','JetBrains Mono',monospace; font-size: .88em;
    background: #2d333b; padding: 2px 6px; border-radius: 4px; color: #e3b341;
  }
  .md-content pre {
    margin: .7em 0; border-radius: 8px; overflow: hidden;
    border: 1px solid var(--border); font-size: 13px;
  }
  .md-content pre code { padding: 14px 16px; display: block; overflow-x: auto; }
  .md-content blockquote { border-left: 3px solid var(--accent); padding-left: 12px; color: var(--muted); margin: .5em 0; }
  .md-content table { border-collapse: collapse; width: 100%; margin: .5em 0; font-size: .93em; }
  .md-content th,.md-content td { border: 1px solid var(--border); padding: 6px 12px; text-align: left; }
  .md-content th { background: #21262d; }
  .md-content a { color: var(--accent); }
  .md-content hr { border-color: var(--border); margin: .8em 0; }
  .md-content strong { color: #f0f6fc; }

  .agent-tools-stack {
    display: flex; flex-direction: column;
    gap: 0;
  }
  .agent-text-stack {
    display: flex; flex-direction: column;
    gap: 10px;
  }
  .agent-text-stack:empty { display: none; }
  .agent-tools-stack:not(:empty) + .agent-text-stack:not(:empty) {
    margin-top: 10px; padding-top: 10px;
    border-top: 1px solid var(--border);
  }

  .tool-card {
    background: var(--tool-bg); border: 1px solid var(--tool-border);
    border-radius: var(--radius); overflow: hidden; margin: 12px 0;
  }
  .tool-header {
    display: flex; align-items: center; gap: 8px;
    padding: 8px 14px; cursor: pointer; user-select: none;
    font-size: 13px; color: var(--accent);
  }
  .tool-header:hover { background: #1f2d42; }
  .tool-name { font-weight: 600; font-family: monospace; }
  .tool-toggle { margin-left: auto; font-size: 11px; color: var(--muted); }
  .tool-group-body {
    display: none; padding: 10px 14px;
    border-top: 1px solid var(--border);
  }
  .tool-group-body.open { display: block; }
  .tool-section-label {
    font-size: 11px; color: var(--muted); margin: 8px 0 4px;
    font-family: var(--font);
  }
  .tool-section-label:first-child { margin-top: 0; }
  .tool-input-pre {
    margin: 0;
    font-family: 'Consolas','JetBrains Mono',monospace;
    font-size: 12px; color: var(--muted);
    white-space: pre-wrap; word-break: break-all;
  }
  .tool-result-slot .tool-result-body-inner {
    margin: 0; max-height: 480px; overflow-y: auto;
    font-family: 'Consolas','JetBrains Mono',monospace;
    font-size: 12px; color: var(--text);
    white-space: pre-wrap; word-break: break-all;
  }
  .tool-card.err {
    border-color: var(--error);
    box-shadow: 0 0 0 1px rgba(248, 81, 73, 0.2);
  }
  .skill-context-details {
    margin: 10px 0; border: 1px solid var(--border); border-radius: 8px;
    background: #12151c; overflow: hidden;
  }
  .skill-context-details > summary {
    cursor: pointer; color: var(--muted); user-select: none;
    padding: 8px 12px; font-size: 13px;
  }
  .skill-context-details[open] > summary {
    color: var(--accent); border-bottom: 1px solid var(--border);
  }
  .skill-context-details .md-content { padding: 10px 12px; }

  .tool-result-card {
    margin: 12px 0; border: 1px solid var(--border); border-radius: var(--radius);
    overflow: hidden; background: #141820;
  }
  .tool-result-card.err {
    border-color: var(--error);
    box-shadow: 0 0 0 1px rgba(248, 81, 73, 0.2);
  }
  .tool-result-header {
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
    padding: 6px 12px; font-size: 12px; color: var(--muted);
    border-bottom: 1px solid var(--border);
  }
  .tool-result-id { font-family: monospace; font-size: 11px; opacity: 0.9; word-break: break-all; }
  .tool-result-body {
    margin: 0; padding: 10px 12px;
    font-family: 'Consolas','JetBrains Mono',monospace;
    font-size: 12px; color: var(--text);
    white-space: pre-wrap; word-break: break-all;
    max-height: 480px; overflow-y: auto;
  }

  .result-bar {
    text-align: center; font-size: 12px; color: var(--muted);
    padding: 6px; border-top: 1px solid var(--border); margin-top: 6px;
  }
  .result-bar.ok { color: var(--success); }
  .result-bar.err { color: var(--error); }
  .result-bar.interrupted { color: var(--warning); }

  .result-sdk-summary {
    margin-top: 8px; padding: 8px 10px;
    border: 1px solid var(--border); border-radius: 8px;
    background: #12151c; font-size: 13px;
  }
  .result-sdk-summary > summary {
    cursor: pointer; color: var(--muted); user-select: none;
  }
  .result-sdk-summary[open] > summary { margin-bottom: 8px; color: var(--accent); }

  #input-area {
    flex-shrink: 0; background: var(--surface); border-top: 1px solid var(--border);
    padding: 14px 20px; display: flex; gap: 10px; align-items: flex-end;
  }
  #input {
    flex: 1; background: #0d1117; color: var(--text);
    border: 1px solid var(--border); border-radius: var(--radius);
    padding: 10px 14px; font-family: var(--font); font-size: 14px;
    resize: none; outline: none; min-height: 44px; max-height: 200px;
    line-height: 1.5; overflow-y: auto;
  }
  #input:focus { border-color: var(--accent); }
  #send-btn {
    background: var(--accent); color: #fff; border: none;
    border-radius: var(--radius); padding: 10px 20px;
    font-size: 14px; font-weight: 600; cursor: pointer; height: 44px; flex-shrink: 0;
  }
  #send-btn:hover { background: #79c0ff; }
  #send-btn.stop {
    background: var(--error); color: #fff;
  }
  #send-btn.stop:hover { background: #ff6b64; }
  #send-btn:disabled { background: #21262d; color: var(--muted); cursor: not-allowed; }

  /* --- Left session sidebar --- */
  #session-sidebar {
    width: min(300px, 34vw);
    flex-shrink: 0;
    border-right: 1px solid var(--border);
    background: var(--surface);
    display: flex;
    flex-direction: column;
    min-height: 0;
    transition: width .18s ease;
  }
  #session-sidebar-header {
    padding: 10px 12px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid var(--border);
    color: var(--muted);
    font-size: 13px;
    font-weight: 600;
  }
  #new-session-btn,
  #toggle-sessions-btn,
  .session-star,
  .session-rename,
  .session-delete,
  .task-stop-btn {
    border: 1px solid var(--border);
    background: #21262d;
    color: var(--text);
    border-radius: 6px;
    cursor: pointer;
    font-size: 12px;
    line-height: 1;
  }
  #new-session-btn { width: 28px; height: 28px; font-size: 18px; }
  #toggle-sessions-btn { width: 28px; height: 28px; }
  #new-session-btn:hover,
  #toggle-sessions-btn:hover,
  .session-star:hover,
  .session-rename:hover,
  .session-delete:hover,
  .task-stop-btn:hover { border-color: var(--accent); color: var(--accent); }
  #session-list {
    flex: 1;
    overflow-y: auto;
    padding: 8px;
  }
  .session-item {
    width: 100%;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 8px;
    margin-bottom: 6px;
    background: transparent;
    color: var(--text);
    display: grid;
    grid-template-columns: 26px 1fr 26px 26px;
    gap: 8px;
    align-items: center;
    cursor: pointer;
    text-align: left;
  }
  .session-item.active {
    background: #1f2937;
    border-color: var(--accent);
  }
  .session-item.unread .session-title::after {
    content: ' •';
    color: var(--accent);
  }
  .session-title {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 13px;
    font-weight: 600;
  }
  .session-meta {
    grid-column: 2 / 5;
    color: var(--muted);
    font-size: 11px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .session-star,
  .session-rename,
  .session-delete {
    width: 26px;
    height: 24px;
  }
  .session-star.on { color: var(--warning); border-color: rgba(210,153,34,.55); }
  .session-delete { color: var(--error); }

  /* --- Right task sidebar --- */
  #todo-panel {
    width: min(320px, 38vw);
    flex-shrink: 0;
    border-left: 1px solid var(--border);
    background: var(--surface);
    display: flex;
    flex-direction: column;
    min-height: 0;
  }
  #todo-panel-header {
    padding: 12px 14px;
    font-size: 13px;
    font-weight: 600;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  #runtime-task-header {
    padding: 12px 14px;
    font-size: 13px;
    font-weight: 600;
    color: var(--muted);
    border-top: 1px solid var(--border);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  #todo-list {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: 10px 12px 16px;
    font-size: 13px;
    line-height: 1.45;
  }
  #runtime-task-list {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: 10px 12px 16px;
    font-size: 12px;
    line-height: 1.4;
  }
  #todo-list::-webkit-scrollbar { width: 5px; }
  #todo-list::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
  .todo-empty {
    color: var(--muted);
    font-size: 12px;
    padding: 8px 4px;
  }
  .todo-item {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 8px 4px;
    border-bottom: 1px solid #21262d;
  }
  .todo-item:last-child { border-bottom: none; }
  .todo-ic {
    flex-shrink: 0;
    margin-top: 2px;
    width: 18px;
    height: 18px;
    box-sizing: border-box;
  }
  .todo-ic-done {
    border-radius: 50%;
    background: var(--success);
    color: #0d1117;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 11px;
    font-weight: 700;
    line-height: 1;
  }
  .todo-ic-done::after { content: '✓'; }
  .todo-ic-pend {
    border-radius: 50%;
    border: 2px solid var(--muted);
    background: transparent;
  }
  .todo-ic-run {
    border-radius: 50%;
    border: 2px solid var(--border);
    border-top-color: var(--accent);
    animation: todo-spin 0.75s linear infinite;
  }
  @keyframes todo-spin {
    to { transform: rotate(360deg); }
  }
  .todo-label {
    flex: 1;
    min-width: 0;
    color: var(--text);
    word-break: break-word;
  }
  .todo-item.todo-muted .todo-label { color: var(--muted); }
  .runtime-task-item {
    border-bottom: 1px solid #21262d;
    padding: 8px 4px;
  }
  .runtime-task-top {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .runtime-task-label {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .runtime-task-meta {
    margin-top: 4px;
    color: var(--muted);
    font-family: monospace;
    font-size: 11px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .task-stop-btn {
    padding: 4px 7px;
    color: var(--error);
  }
  #delete-dialog-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,.52);
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 20;
  }
  #delete-dialog-backdrop.open { display: flex; }
  #delete-dialog {
    width: min(420px, calc(100vw - 32px));
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface);
    box-shadow: 0 18px 60px rgba(0,0,0,.45);
    padding: 16px;
  }
  #delete-dialog-title {
    font-size: 15px;
    font-weight: 600;
    margin-bottom: 8px;
  }
  #delete-dialog-body {
    color: var(--muted);
    font-size: 13px;
    line-height: 1.55;
    margin-bottom: 14px;
  }
  #delete-dialog-actions {
    display: flex;
    gap: 8px;
    justify-content: flex-end;
  }
  .delete-dialog-btn {
    border: 1px solid var(--border);
    background: #21262d;
    color: var(--text);
    border-radius: 6px;
    padding: 8px 10px;
    cursor: pointer;
  }
  .delete-dialog-btn.danger {
    color: #fff;
    background: var(--error);
    border-color: var(--error);
  }
  @media (max-width: 900px) {
    #session-sidebar { width: 220px; }
    #main-row.sessions-collapsed #session-sidebar { width: 44px; }
    #todo-panel { display: none; }
  }
</style>
</head>
<body>
<div id="header">
  <h1>⚛ VASP Agent</h1>
  <span id="status-badge">Ready</span>
  <span id="log-path"></span>
</div>
<div id="main-row" class="sessions-collapsed">
  <aside id="session-sidebar">
    <div id="session-sidebar-header">
      <button id="toggle-sessions-btn" title="Expand/collapse sessions">‹</button>
      <span id="session-title-text">Sessions</span>
      <button id="new-session-btn" title="New session">+</button>
    </div>
    <div id="session-list"></div>
  </aside>
  <div id="chat-column">
    <div id="chat-container"></div>
    <div id="input-area">
      <textarea id="input" rows="1" placeholder="Type a question; Enter to send, Shift+Enter for a new line"></textarea>
      <button id="send-btn">Send</button>
    </div>
  </div>
  <aside id="todo-panel">
    <div id="todo-panel-header">Todo</div>
    <div id="todo-list"><div class="todo-empty">No tasks</div></div>
    <div id="runtime-task-header">Background tasks</div>
    <div id="runtime-task-list"><div class="todo-empty">No background tasks</div></div>
  </aside>
</div>
<div id="delete-dialog-backdrop">
  <div id="delete-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-dialog-title">
    <div id="delete-dialog-title">Delete session</div>
    <div id="delete-dialog-body"></div>
    <div id="delete-dialog-actions">
      <button class="delete-dialog-btn" id="delete-cancel-btn">Cancel</button>
      <button class="delete-dialog-btn" id="delete-history-btn">Remove only</button>
      <button class="delete-dialog-btn danger" id="delete-files-btn">Delete files</button>
    </div>
  </div>
</div>
<script>
marked.setOptions({ breaks: true, gfm: true });

const chat = document.getElementById('chat-container');
const inputEl = document.getElementById('input');
const sendBtn = document.getElementById('send-btn');
const statusBadge = document.getElementById('status-badge');
const logPathEl = document.getElementById('log-path');
const todoListEl = document.getElementById('todo-list');
const mainRowEl = document.getElementById('main-row');
const sessionListEl = document.getElementById('session-list');
const newSessionBtn = document.getElementById('new-session-btn');
const toggleSessionsBtn = document.getElementById('toggle-sessions-btn');
const runtimeTaskListEl = document.getElementById('runtime-task-list');
const deleteDialogBackdrop = document.getElementById('delete-dialog-backdrop');
const deleteDialogBody = document.getElementById('delete-dialog-body');
const deleteCancelBtn = document.getElementById('delete-cancel-btn');
const deleteHistoryBtn = document.getElementById('delete-history-btn');
const deleteFilesBtn = document.getElementById('delete-files-btn');

let curBubble = null, textStackEl = null, toolsStackEl = null;
let isThinking = false;
/** @type {Record<string, HTMLElement>} merge request + result into one card by tool_use_id */
let toolCardByUseId = {};
let activeSessionId = null;
let sessions = [];
let sessionStates = {};
let pendingDeleteSession = null;

const sessionsCollapsed = localStorage.getItem('vasp-agent.sessions-collapsed');
if (sessionsCollapsed === 'false') {
  mainRowEl.classList.remove('sessions-collapsed');
}

const EVENT_TYPES = new Set(['user_message', 'agent_text', 'tool_use', 'tool_result', 'result']);

function ensureSessionState(id) {
  const sid = id || activeSessionId || '__default__';
  if (!sessionStates[sid]) {
    sessionStates[sid] = {
      events: [],
      todos: [],
      tasks: [],
      statusText: 'Ready',
      thinking: false,
      logPath: '',
      unread: false,
    };
  }
  return sessionStates[sid];
}

// VASP sessions often run for hours; without reconnecting after a network blip, the page looks like it is still running
// but actually receives nothing. Reconnect with exponential backoff; after reconnecting the server resends the session history.
let ws = null;
let wsRetry = 0;
let wsTimer = null;

function connectWs() {
  if (wsTimer) { clearTimeout(wsTimer); wsTimer = null; }
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => {
    wsRetry = 0;
    setStatus('Connected', false);
    sendBtn.disabled = false;
  };
  ws.onclose = () => {
    sendBtn.disabled = false;
    scheduleReconnect();
  };
  ws.onerror = () => { setStatus('Connection error', false); };
  ws.onmessage = (ev) => {
    let d;
    try { d = JSON.parse(ev.data); } catch (e) { return; }
    dispatch(d, false);
  };
}

function scheduleReconnect() {
  if (wsTimer) return;
  const delay = Math.min(1000 * Math.pow(2, wsRetry), 30000);
  wsRetry += 1;
  setStatus(`Disconnected; reconnecting in ${Math.round(delay / 1000)} s...`, false);
  wsTimer = setTimeout(() => { wsTimer = null; connectWs(); }, delay);
}

connectWs();

function dispatch(d, replay) {
  if (d.type === 'session_list') {
    sessions = Array.isArray(d.sessions) ? d.sessions : [];
    if (d.active_session_id) activeSessionId = d.active_session_id;
    renderSessionList();
    return;
  }
  if (d.type === 'session_history') {
    const sid = d.agent_session_id;
    if (!activeSessionId) activeSessionId = sid;
    const st = ensureSessionState(sid);
    st.events = Array.isArray(d.events) ? d.events : [];
    st.tasks = Array.isArray(d.tasks) ? d.tasks : [];
    st.logPath = d.log_path || st.logPath || '';
    hydrateSessionState(st);
    st.unread = false;
    if (sid === activeSessionId) renderActiveSession();
    renderSessionList();
    return;
  }

  const sid = d.agent_session_id || activeSessionId;
  const st = ensureSessionState(sid);
  if (EVENT_TYPES.has(d.type)) st.events.push(d);
  if (d.type === 'log_path') st.logPath = d.path || '';
  if (d.type === 'status') {
    st.statusText = d.text || 'Ready';
    st.thinking = !!d.thinking;
  }
  if (d.type === 'todo_update') st.todos = Array.isArray(d.todos) ? d.todos : [];
  if (d.type === 'task_snapshot') st.tasks = Array.isArray(d.tasks) ? d.tasks : [];
  if (d.type === 'done') {
    st.thinking = false;
    sendBtn.disabled = false;
  }

  if (sid !== activeSessionId) {
    if (EVENT_TYPES.has(d.type) || d.type === 'status' || d.type === 'task_snapshot') {
      st.unread = true;
      renderSessionList();
    }
    return;
  }
  renderEvent(d, replay);
}

function hydrateSessionState(st) {
  st.todos = [];
  st.statusText = 'Ready';
  st.thinking = false;
  (st.events || []).forEach((e) => {
    if (e.type === 'todo_update') st.todos = Array.isArray(e.todos) ? e.todos : [];
    else if (e.type === 'status') {
      st.statusText = e.text || st.statusText;
      st.thinking = !!e.thinking;
    } else if (e.type === 'log_path') {
      st.logPath = e.path || st.logPath || '';
    } else if (e.type === 'result') {
      st.statusText = 'Ready';
      st.thinking = false;
    }
  });
}

function renderEvent(d, replay) {
  if      (d.type === 'user_message') appendUserMsg(d.text);
  else if (d.type === 'agent_text')   appendText(d.text, d);
  else if (d.type === 'tool_use')     appendTool(d.name, d.input_str || '', d.tool_use_id);
  else if (d.type === 'tool_result')  appendToolResult(d);
  else if (d.type === 'result')       appendResult(d);
  else if (d.type === 'log_path')     logPathEl.textContent = d.path || '';
  else if (d.type === 'status')       setStatus(d.text, d.thinking ?? false);
  else if (d.type === 'todo_update') renderTodoPanel(d.todos);
  else if (d.type === 'task_snapshot') renderRuntimeTasks(d.tasks);
  else if (!replay && d.type === 'done') sendBtn.disabled = false;
}

function renderActiveSession() {
  const st = ensureSessionState(activeSessionId);
  chat.innerHTML = '';
  curBubble = textStackEl = toolsStackEl = null;
  toolCardByUseId = {};
  st.events.forEach((e) => renderEvent(e, true));
  renderTodoPanel(st.todos);
  renderRuntimeTasks(st.tasks);
  logPathEl.textContent = st.logPath || '';
  setStatus(st.statusText || 'Ready', !!st.thinking);
  sendBtn.disabled = false;
  scrollBottom();
}

function formatSessionTime(value) {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function renderSessionList() {
  if (!sessions.length) {
    sessionListEl.innerHTML = '<div class="todo-empty">No sessions</div>';
    return;
  }
  sessionListEl.innerHTML = '';
  sessions.forEach((s) => {
    const sid = String(s.agent_session_id);
    const st = ensureSessionState(sid);
    const item = document.createElement('div');
    item.className = 'session-item' + (sid === activeSessionId ? ' active' : '') + (st.unread ? ' unread' : '');
    item.role = 'button';
    item.tabIndex = 0;
    item.onclick = () => selectSession(sid);
    item.onkeydown = (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') {
        ev.preventDefault();
        selectSession(sid);
      }
    };

    const star = document.createElement('button');
    star.className = 'session-star' + (s.starred ? ' on' : '');
    star.type = 'button';
    star.title = s.starred ? 'Unstar' : 'Star';
    star.textContent = s.starred ? '★' : '☆';
    star.onclick = (ev) => {
      ev.stopPropagation();
      wsSend({ type: 'star_session', agent_session_id: sid, starred: !s.starred });
    };

    const title = document.createElement('div');
    title.className = 'session-title';
    title.textContent = s.title || sid;

    const rename = document.createElement('button');
    rename.className = 'session-rename';
    rename.type = 'button';
    rename.title = 'Rename';
    rename.textContent = '✎';
    rename.onclick = (ev) => {
      ev.stopPropagation();
      const next = prompt('Rename session', s.title || sid);
      if (next && next.trim()) {
        wsSend({ type: 'rename_session', agent_session_id: sid, title: next.trim() });
      }
    };

    const del = document.createElement('button');
    del.className = 'session-delete';
    del.type = 'button';
    del.title = 'Delete';
    del.textContent = '×';
    del.onclick = (ev) => {
      ev.stopPropagation();
      openDeleteDialog(s);
    };

    const meta = document.createElement('div');
    meta.className = 'session-meta';
    const changed = formatSessionTime(s.workspace_modified_at);
    meta.textContent = `${s.status || 'idle'}${changed ? ` · changed ${changed}` : ''} · ${sid}`;

    item.appendChild(star);
    item.appendChild(title);
    item.appendChild(rename);
    item.appendChild(del);
    item.appendChild(meta);
    sessionListEl.appendChild(item);
  });
}

function renderTodoPanel(todos) {
  const list = Array.isArray(todos) ? todos : [];
  if (!list.length) {
    todoListEl.innerHTML = '<div class="todo-empty">No tasks</div>';
    return;
  }
  todoListEl.innerHTML = list.map((t) => {
    const st = t.status;
    let ic = '';
    if (st === 'completed') {
      ic = '<span class="todo-ic todo-ic-done" title="Completed"></span>';
    } else if (st === 'in_progress') {
      ic = '<span class="todo-ic todo-ic-run" title="In progress"></span>';
    } else {
      ic = '<span class="todo-ic todo-ic-pend" title="Pending"></span>';
    }
    const muted = (st === 'pending') ? ' todo-muted' : '';
    return `<div class="todo-item${muted}">${ic}<span class="todo-label">${esc(t.label || '')}</span></div>`;
  }).join('');
}

function renderRuntimeTasks(tasks) {
  const list = Array.isArray(tasks) ? tasks : [];
  if (!list.length) {
    runtimeTaskListEl.innerHTML = '<div class="todo-empty">No background tasks</div>';
    return;
  }
  runtimeTaskListEl.innerHTML = '';
  list.forEach((t) => {
    const row = document.createElement('div');
    row.className = 'runtime-task-item';
    const top = document.createElement('div');
    top.className = 'runtime-task-top';
    const label = document.createElement('div');
    label.className = 'runtime-task-label';
    label.textContent = t.label || t.task_id || 'task';
    top.appendChild(label);
    const terminal = ['completed', 'failed', 'stopped', 'cancelled', 'canceled', 'killed'].includes(String(t.status || '').toLowerCase());
    if (t.kind === 'claude' && !terminal) {
      const stop = document.createElement('button');
      stop.className = 'task-stop-btn';
      stop.type = 'button';
      stop.textContent = 'Stop';
      stop.onclick = () => wsSend({ type: 'stop_task', agent_session_id: activeSessionId, task_id: t.task_id });
      top.appendChild(stop);
    }
    const meta = document.createElement('div');
    meta.className = 'runtime-task-meta';
    meta.textContent = `${t.kind || '?'} · ${t.status || '?'} · ${t.task_id || ''}`;
    row.appendChild(top);
    row.appendChild(meta);
    runtimeTaskListEl.appendChild(row);
  });
}

function setStatus(text, thinking) {
  isThinking = !!thinking;
  statusBadge.textContent = text;
  statusBadge.className = isThinking ? 'thinking' : '';
  sendBtn.textContent = isThinking ? 'Stop' : 'Send';
  sendBtn.classList.toggle('stop', isThinking);
}
function scrollBottom() { chat.scrollTop = chat.scrollHeight; }

function renderMarkdownTo(el, md) {
  try {
    el.innerHTML = marked.parse(md);
    el.querySelectorAll('pre code').forEach((c) => {
      try { hljs.highlightElement(c); } catch (_e) {}
    });
  } catch (_e) {
    el.textContent = md;
  }
}

function ensureAgentBubble() {
  if (curBubble) return;
  toolCardByUseId = {};
  const row = document.createElement('div');
  row.className = 'msg agent';
  row.innerHTML = '<div class="msg-label">Agent</div>';
  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';
  toolsStackEl = document.createElement('div');
  toolsStackEl.className = 'agent-tools-stack';
  textStackEl = document.createElement('div');
  textStackEl.className = 'agent-text-stack';
  bubble.appendChild(toolsStackEl);
  bubble.appendChild(textStackEl);
  row.appendChild(bubble);
  chat.appendChild(row);
  curBubble = bubble;
}

function appendText(text, meta) {
  ensureAgentBubble();
  meta = meta || {};
  if (meta.collapsed) {
    const det = document.createElement('details');
    det.className = 'skill-context-details';
    det.open = false;
    const sm = document.createElement('summary');
    sm.textContent = meta.collapsed_label || 'Skill body (click to expand)';
    const body = document.createElement('div');
    body.className = 'md-content';
    renderMarkdownTo(body, text);
    det.appendChild(sm);
    det.appendChild(body);
    curBubble.insertBefore(det, textStackEl);
    scrollBottom();
    return;
  }
  const block = document.createElement('div');
  block.className = 'md-content';
  renderMarkdownTo(block, text);
  textStackEl.appendChild(block);
  scrollBottom();
}

function appendTool(name, inputStr, toolUseId) {
  ensureAgentBubble();
  const innerId = 'g-' + Math.random().toString(36).slice(2);
  const card = document.createElement('div');
  card.className = 'tool-card';
  if (toolUseId) {
    toolCardByUseId[String(toolUseId)] = card;
  }
  /* When the right-hand task list is present, TodoWrite in the main chat is collapsed by default */
  const todoWriteCollapsed = name === 'TodoWrite';
  const bodyClass = todoWriteCollapsed ? 'tool-group-body' : 'tool-group-body open';
  const toggleLabel = todoWriteCollapsed ? '▶ Expand' : '▼ Collapse';
  card.innerHTML = `
    <div class="tool-header" onclick="toggleTool('${innerId}')">
      <span>🔧</span>
      <span class="tool-name">${esc(name)}</span>
      <span class="tool-toggle" id="${innerId}-btn">${toggleLabel}</span>
    </div>
    <div class="${bodyClass}" id="${innerId}">
      <div class="tool-section-label">Request</div>
      <pre class="tool-input-pre">${esc(inputStr)}</pre>
      <div class="tool-result-slot" style="display:none">
        <div class="tool-section-label">Result</div>
        <pre class="tool-result-body-inner"></pre>
      </div>
    </div>`;
  toolsStackEl.appendChild(card);
  scrollBottom();
}

function appendToolResult(d) {
  ensureAgentBubble();
  const tid = d.tool_use_id != null ? String(d.tool_use_id) : '';
  if (tid && toolCardByUseId[tid]) {
    const card = toolCardByUseId[tid];
    const slot = card.querySelector('.tool-result-slot');
    const inner = card.querySelector('.tool-result-body-inner');
    if (slot && inner) {
      inner.textContent = d.content_str != null ? String(d.content_str) : '';
      slot.style.display = 'block';
      card.classList.toggle('err', !!d.is_error);
      scrollBottom();
      return;
    }
  }
  const card = document.createElement('div');
  card.className = 'tool-result-card' + (d.is_error ? ' err' : '');
  const head = document.createElement('div');
  head.className = 'tool-result-header';
  const idSpan = document.createElement('span');
  idSpan.className = 'tool-result-id';
  idSpan.textContent = d.tool_use_id ? `id: ${d.tool_use_id}` : '';
  head.innerHTML = '<span>' + (d.is_error ? '⚠️' : '✅') + ' Tool result</span>';
  head.appendChild(idSpan);
  const pre = document.createElement('pre');
  pre.className = 'tool-result-body';
  pre.textContent = d.content_str != null ? String(d.content_str) : '';
  card.appendChild(head);
  card.appendChild(pre);
  toolsStackEl.appendChild(card);
  scrollBottom();
}

function toggleTool(id) {
  const body = document.getElementById(id);
  const btn  = document.getElementById(id + '-btn');
  if (!body || !btn) return;
  btn.textContent = body.classList.toggle('open') ? '▼ Collapse' : '▶ Expand';
}

function appendResult(d) {
  ensureAgentBubble();
  const bar = document.createElement('div');
  if (d.interrupted) {
    bar.className = 'result-bar interrupted';
    const next = d.pending_interrupt ? '; switching to the new instruction' : '';
    bar.textContent = `Stopped${next}  turns: ${d.turns}`;
  } else if (d.error) {
    bar.className = 'result-bar err';
    const sub = (d.subtype && String(d.subtype).trim()) ? `  ${d.subtype}` : '';
    bar.textContent = `✗ Error  turns: ${d.turns}${sub}`;
  } else {
    bar.className = 'result-bar ok';
    bar.textContent = `✓ Done  turns: ${d.turns}`;
  }
  curBubble.appendChild(bar);
  const summ = (d.summary || '').trim();
  if (summ) {
    const det = document.createElement('details');
    det.className = 'result-sdk-summary';
    det.open = false;
    const sm = document.createElement('summary');
    sm.textContent = 'Full ResultMessage output (may duplicate the above)';
    det.appendChild(sm);
    const body = document.createElement('div');
    body.className = 'md-content';
    renderMarkdownTo(body, summ);
    det.appendChild(body);
    curBubble.appendChild(det);
  }
  curBubble = textStackEl = toolsStackEl = null;
  scrollBottom();
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
                  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function appendUserMsg(text) {
  const row = document.createElement('div');
  row.className = 'msg user';
  row.innerHTML = `<div class="msg-label">You</div><div class="msg-bubble">${esc(text)}</div>`;
  chat.appendChild(row);
  scrollBottom();
}

function send() {
  const text = inputEl.value.trim();
  if (!text && !isThinking) return;
  if (ws.readyState !== WebSocket.OPEN) {
    setStatus('Connection not ready; please try again later', false);
    sendBtn.disabled = false;
    return;
  }
  if (isThinking) {
    if (text) {
      wsSend({ type: 'interrupt', text });
      inputEl.value = '';
      inputEl.style.height = '';
      setStatus('Interrupting and sending the new instruction...', true);
    } else {
      wsSend({ type: 'interrupt', text: '' });
      setStatus('Stopping the current reply...', true);
    }
  } else {
    wsSend({ type: 'user_message', text });
    inputEl.value = '';
    inputEl.style.height = '';
    setStatus('Thinking...', true);
  }
  sendBtn.disabled = false;
  curBubble = textStackEl = toolsStackEl = null;
  /* Do not clear Todo here: the right-hand list is driven by todo_update sent from the server; clearing it would show "No tasks" for a long time before the first TodoWrite of the next turn. */
}

function wsSend(payload) {
  if (!payload.agent_session_id && activeSessionId) payload.agent_session_id = activeSessionId;
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    // Silently dropping the message while disconnected would make the user think it was sent. Tell them explicitly and trigger a reconnect.
    setStatus('Disconnected; reconnecting...', false);
    scheduleReconnect();
    return false;
  }
  ws.send(JSON.stringify(payload));
  return true;
}

function selectSession(id) {
  activeSessionId = id;
  const st = ensureSessionState(id);
  st.unread = false;
  renderActiveSession();
  renderSessionList();
  wsSend({ type: 'select_session', agent_session_id: id });
}

function createSession() {
  const title = prompt('New session name');
  if (title === null) return;
  wsSend({ type: 'create_session', title: title.trim() });
}

function openDeleteDialog(session) {
  pendingDeleteSession = session;
  const name = session.title || session.agent_session_id;
  deleteDialogBody.innerHTML = `
    <div>Session: <strong>${esc(name)}</strong></div>
    <div style="margin-top:8px">Choosing <strong>Remove only</strong> hides it from the session history but keeps the calculation files and logs in the runs directory.</div>
    <div style="margin-top:6px">Choosing <strong>Delete files</strong> also deletes the local workspace directory.</div>`;
  deleteDialogBackdrop.classList.add('open');
}

function closeDeleteDialog() {
  pendingDeleteSession = null;
  deleteDialogBackdrop.classList.remove('open');
}

function confirmDelete(deleteFiles) {
  if (!pendingDeleteSession) return;
  const sid = pendingDeleteSession.agent_session_id;
  closeDeleteDialog();
  wsSend({ type: 'delete_session', agent_session_id: sid, delete_files: !!deleteFiles });
}

function toggleSessionSidebar() {
  const collapsed = mainRowEl.classList.toggle('sessions-collapsed');
  localStorage.setItem('vasp-agent.sessions-collapsed', collapsed ? 'true' : 'false');
}

inputEl.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
inputEl.addEventListener('input',   ()  => { inputEl.style.height = ''; inputEl.style.height = Math.min(inputEl.scrollHeight, 200) + 'px'; });
sendBtn.addEventListener('click', send);
newSessionBtn.addEventListener('click', createSession);
toggleSessionsBtn.addEventListener('click', toggleSessionSidebar);
deleteCancelBtn.addEventListener('click', closeDeleteDialog);
deleteHistoryBtn.addEventListener('click', () => confirmDelete(false));
deleteFilesBtn.addEventListener('click', () => confirmDelete(true));
deleteDialogBackdrop.addEventListener('click', (ev) => {
  if (ev.target === deleteDialogBackdrop) closeDeleteDialog();
});
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# WebUI class
# ---------------------------------------------------------------------------

class WebUI:
    def __init__(
        self,
        port: int = WEB_PORT,
        *,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_connect: Callable[["WebUI", web.WebSocketResponse], Awaitable[None]] | None = None,
        api_handler: Callable[[str, web.Request], Awaitable[Any]] | None = None,
    ) -> None:
        self.port = port
        self.input_queue: asyncio.Queue = asyncio.Queue()
        self._clients: set[web.WebSocketResponse] = set()
        self._send_locks: dict[int, asyncio.Lock] = {}
        self._runner: web.AppRunner | None = None
        self._on_event = on_event
        self._on_connect = on_connect
        self._api_handler = api_handler

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/", self._html_handler)
        app.router.add_get("/ws", self._ws_handler)
        app.router.add_get("/api/sessions", self._api_sessions)
        app.router.add_post("/api/sessions", self._api_sessions)
        app.router.add_patch("/api/sessions/{agent_session_id}", self._api_session)
        app.router.add_delete("/api/sessions/{agent_session_id}", self._api_session)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        requested = int(self.port)
        last: OSError | None = None
        # The UI has no authentication and the agent runs with bypassPermissions: binding to 0.0.0.0 would expose
        # arbitrary command execution to the whole subnet. Listen on loopback only by default; for remote access use SSH port forwarding
        # (ssh -L 18688:localhost:18688 user@host). To expose it to the intranet deliberately, set
        # VASP_AGENT_WEB_HOST explicitly; a warning is then printed.
        host = (os.environ.get("VASP_AGENT_WEB_HOST") or "127.0.0.1").strip()
        if host not in ("127.0.0.1", "localhost", "::1"):
            print(
                f"[web] Warning: listening on {host}; anyone on that subnet can operate this agent without authentication "
                f"(its permission mode is bypassPermissions). Use only on a trusted intranet.",
                flush=True,
            )
        for p in range(requested, requested + WEB_PORT_TRY_COUNT):
            try:
                await web.TCPSite(self._runner, host, p).start()
                self.port = p
                return
            except OSError as e:
                if not _is_address_in_use(e):
                    raise
                last = e
        hi = requested + WEB_PORT_TRY_COUNT - 1
        raise OSError(
            errno.EADDRINUSE,
            f"Cannot bind the Web UI on {requested}-{hi} (all addresses in use)",
        ) from last

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()


    async def send(self, data: dict) -> None:
        """Broadcast to all clients.

        Send concurrently rather than serially: serially, one slow client would block the receive loop of **all** sessions
        (every WorkspaceRuntime._receive_loop awaits here).
        """
        targets = [ws for ws in list(self._clients) if not ws.closed]
        for ws in self._clients - set(targets):
            self._clients.discard(ws)
        if not targets:
            return
        results = await asyncio.gather(
            *(self._send_one(ws, data) for ws in targets), return_exceptions=True
        )
        for ws, result in zip(targets, results):
            if result is not True:
                self._clients.discard(ws)

    async def _send_one(self, ws: web.WebSocketResponse, data: dict) -> bool:
        # One lock per connection: the receive loops of multiple runtimes send to the same WebSocket concurrently,
        # and concurrent send_json without a lock risks interleaved frames.
        lock = self._send_locks.setdefault(id(ws), asyncio.Lock())
        try:
            async with lock:
                await ws.send_json(data)
            return True
        except Exception:
            self._send_locks.pop(id(ws), None)
            return False

    async def send_to(self, ws: web.WebSocketResponse, data: dict) -> None:
        if not ws.closed:
            try:
                await ws.send_json(data)
            except Exception:
                pass

    async def _html_handler(self, _request: web.Request) -> web.Response:
        return web.Response(text=_HTML, content_type="text/html")

    async def _api_sessions(self, request: web.Request) -> web.Response:
        if not self._api_handler:
            return web.json_response({"error": "session API is not configured"}, status=501)
        action = "list_sessions" if request.method == "GET" else "create_session"
        data = await self._api_handler(action, request)
        return web.json_response(data)

    async def _api_session(self, request: web.Request) -> web.Response:
        if not self._api_handler:
            return web.json_response({"error": "session API is not configured"}, status=501)
        action = "delete_session" if request.method == "DELETE" else "update_session"
        data = await self._api_handler(action, request)
        return web.json_response(data)

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)
        self._clients.add(ws)
        if self._on_connect:
            await self._on_connect(self, ws)
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                try:
                    if self._on_event:
                        await self._on_event(data)
                    elif data.get("type") == "user_message":
                        await self.input_queue.put({"type": "user_message", "text": data["text"]})
                    elif data.get("type") == "interrupt":
                        text = str(data.get("text") or "")
                        await self.input_queue.put({"type": "interrupt", "text": text})
                except Exception as exc:
                    # This used to be `except Exception: pass`: unknown session, rmtree failure,
                    # start failure etc. were all silently swallowed, the browser received no feedback, and the UI stayed
                    # on "Thinking..." forever. At least send the error back and keep it in the server log.
                    import traceback

                    traceback.print_exc()
                    with suppress(Exception):
                        await ws.send_json(
                            {
                                "type": "agent_text",
                                "text": f"[Error] Failed to handle request: {type(exc).__name__}: {exc}",
                            }
                        )
                    with suppress(Exception):
                        await ws.send_json(
                            {"type": "status", "text": "Ready - type below", "thinking": False}
                        )
                        await ws.send_json({"type": "done"})
            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                break
        self._clients.discard(ws)
        self._send_locks.pop(id(ws), None)
        return ws
