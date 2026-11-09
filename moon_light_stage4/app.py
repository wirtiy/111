"""Flask entry point for Moon Light. Existing SQLite/API logic is preserved in server.py."""
from pathlib import Path
from threading import Thread
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import importlib.util
from flask import Flask, Response, request, send_from_directory

ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location("moonlight_legacy", ROOT / "server.py")
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)

app = Flask(__name__, static_folder="static", static_url_path="/static")
BACKEND_HOST, BACKEND_PORT = "127.0.0.1", 8001
_backend_started = False

def start_legacy_backend():
    global _backend_started
    if _backend_started:
        return
    legacy.init_db()
    httpd = ThreadingHTTPServer((BACKEND_HOST, BACKEND_PORT), legacy.Handler)
    Thread(target=httpd.serve_forever, daemon=True, name="moonlight-api").start()
    _backend_started = True

@app.before_request
def ensure_backend():
    start_legacy_backend()

@app.get("/")
def home():
    return send_from_directory(ROOT / "static", "index.html")

@app.get("/admin")
def admin():
    return send_from_directory(ROOT / "static", "admin.html")

@app.route("/api/<path:api_path>", methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"])
@app.route("/api", methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"])
def api_proxy(api_path=""):
    target = f"http://{BACKEND_HOST}:{BACKEND_PORT}/api/{api_path}"
    if request.query_string:
        target += "?" + request.query_string.decode("latin1")
    headers = {}
    for key in ("Content-Type", "Authorization", "Accept"):
        if request.headers.get(key):
            headers[key] = request.headers[key]
    req = Request(target, data=request.get_data() or None, headers=headers, method=request.method)
    try:
        with urlopen(req, timeout=15) as upstream:
            body = upstream.read()
            return Response(body, status=upstream.status,
                            content_type=upstream.headers.get("Content-Type"))
    except HTTPError as err:
        return Response(err.read(), status=err.code,
                        content_type=err.headers.get("Content-Type", "application/json"))
    except (URLError, TimeoutError) as err:
        return {"error": "API временно недоступен", "details": str(err)}, 502

@app.get("/health")
def flask_health():
    return {"status": "ok", "framework": "Flask", "stage": 3}

if __name__ == "__main__":
    start_legacy_backend()
    print("Moon Light Stage 3 — Flask: http://127.0.0.1:8000")
    print("Админ-панель: http://127.0.0.1:8000/admin")
    app.run(host="127.0.0.1", port=8000, debug=False, threaded=True)
