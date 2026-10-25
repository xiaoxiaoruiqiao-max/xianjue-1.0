"""Loopback-only website; the market updater runs in a separate process."""
import argparse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import subprocess
import sys
import threading
from urllib.parse import urlparse

from update import ROOT, DATA, build_dashboard, now_cn, read_json, phase_for

PORT = 18789
VERSION = "1.0.0"
PUBLIC = ROOT / "public"
worker = None
worker_guard = threading.Lock()


def start_update(force=False):
    global worker
    with worker_guard:
        if worker is not None and worker.poll() is None:
            return False
        now = now_cn()
        status = read_json(DATA / "update-status.json", {})
        last = status.get("attempted_at") or status.get("started_at")
        if last and not force and now - datetime.fromisoformat(last) < timedelta(minutes=15):
            return False
        if not force:
            dashboard = read_json(DATA / "dashboard.json", {})
            current = now.date().isoformat()
            phase = phase_for(now)
            records = dashboard.get("records", [])
            if any(r["date"] == current and r["phase"] == phase for r in records):
                return False
            # On a known holiday, already current cached prices need no repeat fetch.
            if dashboard and current <= (dashboard.get("calendar_end") or "") and current not in dashboard.get("trade_days", []):
                expected = [d for d in dashboard.get("trade_days", []) if d < current]
                if expected and (dashboard.get("latest_date") or "") >= max(expected):
                    return False
        DATA.mkdir(exist_ok=True)
        python = Path(sys.executable)
        if python.name.lower() == "pythonw.exe":
            python = python.with_name("python.exe")
        with (DATA / "update.log").open("ab") as log:
            worker = subprocess.Popen([str(python), str(ROOT / "update.py")], cwd=ROOT,
                                      stdout=log, stderr=log,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_bytes(self, status, body, kind="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def json(self, status, obj):
        self.send_bytes(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def valid_host(self):
        return self.headers.get("Host") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def do_GET(self):
        if not self.valid_host():
            return self.json(403, {"error": "仅允许本机访问"})
        path = urlparse(self.path).path
        if path == "/api/health":
            return self.json(200, {"app": "position-guide", "name": "先觉1.0", "version": VERSION,
                                   "port": PORT, "project_root": str(ROOT)})
        if path == "/api/data":
            dashboard = read_json(DATA / "dashboard.json")
            if not dashboard:
                return self.json(503, {"error": "正在初始化行情，请稍后刷新"})
            dashboard["server_now"] = now_cn().isoformat()
            dashboard["today"] = now_cn().date().isoformat()
            dashboard["update"] = read_json(DATA / "update-status.json", {"state": "never"})
            dashboard["schedule"] = read_json(DATA / "schedule.json", {"enabled": False})
            return self.json(200, dashboard)
        files = {"/": "index.html", "/index.html": "index.html", "/style.css": "style.css", "/app.js": "app.js", "/favicon.svg": "favicon.svg"}
        if path not in files or not (PUBLIC / files[path]).exists():
            return self.json(404, {"error": "页面不存在"})
        file = PUBLIC / files[path]
        self.send_bytes(200, file.read_bytes(), (mimetypes.guess_type(file.name)[0] or "text/plain") + "; charset=utf-8")

    def do_POST(self):
        origin = self.headers.get("Origin")
        if not self.valid_host() or origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}") or self.headers.get("X-Local-Request") != "position-guide":
            return self.json(403, {"error": "仅接受本机页面的更新请求"})
        if self.path != "/api/update":
            return self.json(404, {"error": "接口不存在"})
        # Page refreshes catch up once; a deliberate user click can retry immediately.
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.json(400, {"error": "请求长度无效"})
        if length < 0:
            return self.json(400, {"error": "请求长度无效"})
        if length > 1024:
            return self.json(413, {"error": "请求过大"})
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, UnicodeDecodeError):
            return self.json(400, {"error": "请求无效"})
        if not isinstance(request, dict) or ("force" in request and type(request["force"]) is not bool):
            return self.json(400, {"error": "请求必须是JSON对象，force必须为布尔值"})
        started = start_update(force=request.get("force") is True)
        self.json(202, {"started": started, "message": "正在更新行情" if started else "已有数据或更新正在运行"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="先觉1.0：本机市场观察网页")
    parser.add_argument("--port", type=int, default=PORT, help="本机端口，默认18789")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    PORT = args.port
    if not (DATA / "dashboard.json").exists():
        build_dashboard()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
