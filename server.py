# server.py
# Standard-library Scrum Poker with http.server + SSE (no external deps)

from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from mimetypes import guess_type
import json, os, random, string, threading, time

PORT = 8000
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# ----- In-memory state (not persistent) -----
rooms_lock = threading.Lock()
rooms = {}  # room_code -> { "host": str, "players": {name: {"vote": str|None}}, "revealed": bool, "clients": set() }

# SSE client wrapper so we can remove safely
class SSEClient:
    def __init__(self, handler):
        self.handler = handler
        self.alive = True
        self.lock = threading.Lock()

    def send(self, event_dict):
        if not self.alive:
            return False
        try:
            data = json.dumps(event_dict, separators=(",", ":"))
            msg = f"data: {data}\n\n"
            with self.lock:
                self.handler.wfile.write(msg.encode("utf-8"))
                self.handler.wfile.flush()
            return True
        except Exception:
            self.alive = False
            return False

def rand_code(n=6):
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=n))

def tally_votes(code):
    r = rooms.get(code)
    if not r: return {}
    counts = {}
    for p in r["players"].values():
        v = p["vote"]
        if v is None: continue
        counts[v] = counts.get(v, 0) + 1
    return counts

def room_snapshot(code):
    with rooms_lock:
        r = rooms.get(code)
        if not r: return None
        # For unrevealed rooms we want to indicate whether a player has voted
        # without exposing their vote. Represent "voted" with a non-null
        # placeholder (empty string). If revealed, include the actual vote.
        snapshot_players = []
        for name, info in r["players"].items():
            if r["revealed"]:
                v = info["vote"]
            else:
                # If the player has a vote recorded, return an empty string so
                # clients can detect "voted" (p.vote !== null). If they haven't
                # voted, return None.
                v = "" if info["vote"] is not None else None
            snapshot_players.append({"name": name, "vote": v})
        return {
            "room": code,
            "host": r["host"],
            "revealed": r["revealed"],
            "players": snapshot_players,
            "tally": (tally_votes(code) if r["revealed"] else {})
        }

def broadcast(code):
    snap = room_snapshot(code)
    if not snap: return
    dead = []
    with rooms_lock:
        clients = list(rooms[code]["clients"])
    for c in clients:
        ok = c.send({"type": "update", "data": snap})
        if not ok:
            dead.append(c)
    if dead:
        with rooms_lock:
            for c in dead:
                rooms[code]["clients"].discard(c)

class Handler(BaseHTTPRequestHandler):
    # Utilities
    def _send_json(self, obj, status=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","application/json")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length","0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _serve_static(self, rel_path):
        # Prevent path traversal
        safe_rel = rel_path.lstrip("/\\")
        fs_path = os.path.normpath(os.path.join(STATIC_DIR, safe_rel))
        if not fs_path.startswith(STATIC_DIR):
            self.send_error(403, "Forbidden")
            return
        if not os.path.isfile(fs_path):
            self.send_error(404, "Not Found")
            return
        try:
            with open(fs_path, "rb") as f:
                data = f.read()
            ctype = guess_type(fs_path)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            # Basic caching for static assets (optional, tweak as needed)
            if fs_path.endswith((".css", ".js")):
                self.send_header("Cache-Control", "max-age=300")
            else:
                self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            self.send_error(500, "Static file error")

    # Routing
    def do_GET(self):
        parsed = urlparse(self.path)

        # Serve index
        if parsed.path == "/":
            return self._serve_static("index.html")

        # Serve static assets
        if parsed.path.startswith("/static/"):
            rel = parsed.path[len("/static/"):]
            return self._serve_static(rel)

        # SSE endpoint
        if parsed.path == "/events":
            qs = parse_qs(parsed.query)
            code = (qs.get("room",[None])[0] or "").upper()
            with rooms_lock:
                if code not in rooms:
                    rooms[code] = {"host":"", "players":{}, "revealed":False, "clients": set()}
                client = SSEClient(self)
                rooms[code]["clients"].add(client)

            # SSE headers
            self.send_response(200)
            self.send_header("Content-Type","text/event-stream")
            self.send_header("Cache-Control","no-store")
            self.send_header("Connection","keep-alive")
            self.end_headers()

            # Initial snapshot
            snap = room_snapshot(code)
            if snap:
                client.send({"type":"update","data":snap})

            # Keep alive pings
            try:
                while client.alive:
                    time.sleep(15)
                    client.send({"type":"ping","ts": time.time()})
            except Exception:
                pass
            finally:
                client.alive = False
                with rooms_lock:
                    if code in rooms:
                        rooms[code]["clients"].discard(client)
            return

        # 404
        self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        body = self._read_json()

        if parsed.path == "/api/create_room":
            host = (body.get("host") or "").strip()
            if not host:
                return self._send_json({"error":"Host name required"}, 400)
            code = rand_code()
            with rooms_lock:
                rooms[code] = {"host": host, "players": {host: {"vote": None}}, "revealed": False, "clients": set()}
            broadcast(code)
            return self._send_json({"room": code})

        if parsed.path == "/api/join":
            code = (body.get("room") or "").upper()
            name = (body.get("name") or "").strip()
            if not code or not name:
                return self._send_json({"error":"Room and name required"}, 400)
            with rooms_lock:
                if code not in rooms:
                    return self._send_json({"error":"Room not found"}, 404)
                rooms[code]["players"].setdefault(name, {"vote": None})
            broadcast(code)
            return self._send_json({"ok": True})

        if parsed.path == "/api/vote":
            code = (body.get("room") or "").upper()
            name = (body.get("name") or "").strip()
            value = body.get("value")
            if not code or not name:
                return self._send_json({"error":"Room and name required"}, 400)
            with rooms_lock:
                if code not in rooms or name not in rooms[code]["players"]:
                    return self._send_json({"error":"Join the room first"}, 400)
                rooms[code]["players"][name]["vote"] = str(value)
            broadcast(code)
            return self._send_json({"ok": True})

        if parsed.path == "/api/reveal":
            code = (body.get("room") or "").upper()
            name = (body.get("name") or "").strip()
            with rooms_lock:
                if code not in rooms:
                    return self._send_json({"error":"Room not found"}, 404)
                if rooms[code]["host"] and name != rooms[code]["host"]:
                    return self._send_json({"error":"Only the host can reveal"}, 403)
                rooms[code]["revealed"] = True
            broadcast(code)
            return self._send_json({"ok": True})

        if parsed.path == "/api/reset":
            code = (body.get("room") or "").upper()
            name = (body.get("name") or "").strip()
            with rooms_lock:
                if code not in rooms:
                    return self._send_json({"error":"Room not found"}, 404)
                if rooms[code]["host"] and name != rooms[code]["host"]:
                    return self._send_json({"error":"Only the host can reset"}, 403)
                for p in rooms[code]["players"].values():
                    p["vote"] = None
                rooms[code]["revealed"] = False
            broadcast(code)
            return self._send_json({"ok": True})

        if parsed.path == "/api/state":
            code = (body.get("room") or "").upper()
            snap = room_snapshot(code)
            if not snap:
                return self._send_json({"error":"Room not found"}, 404)
            return self._send_json({"data": snap})

        self.send_error(404, "Not Found")

def main():
    random.seed(os.urandom(16))
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Scrum Poker running on http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == "__main__":
    main()
