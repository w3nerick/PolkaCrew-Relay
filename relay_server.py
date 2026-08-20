#!/usr/bin/env python3
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import json, queue, threading, time, os, re

ROOT = Path(__file__).resolve().parent
clients = {}       # (room, client_id) -> Queue
client_auth = {}   # (room, client_id) -> unbroadcast client secret
room_hosts = {}    # room -> authoritative client_id
room_phase = {}    # room -> last host-declared phase
host_disconnect_generation = {}  # room -> monotonic grace token
lock = threading.Lock()
MAX_POST_BYTES = 96 * 1024
MAX_CLIENTS_PER_ROOM = 16
MAX_POSTS_PER_SECOND = int(os.environ.get('POLKACREW_MAX_POSTS_PER_SECOND', '120'))
rate_windows = {}  # (room, client_id) -> [unix_second, count]
ROOM_RE = re.compile(r'^[A-Z0-9]{3,12}$')
HOST_RECONNECT_GRACE_SECONDS = 8


def _allowed_origins():
    raw = os.environ.get('POLKACREW_ALLOWED_ORIGINS', '*').strip()
    if not raw or raw == '*':
        return None
    return {item.strip().rstrip('/') for item in raw.split(',') if item.strip()}


def _origin_allowed(origin):
    allowed = _allowed_origins()
    return allowed is None or not origin or origin.rstrip('/') in allowed


def _cors_origin(origin):
    allowed = _allowed_origins()
    return '*' if allowed is None else (origin if origin and origin.rstrip('/') in allowed else 'null')


def _rate_allowed_locked(room, client_id):
    now = int(time.time())
    key = (room, client_id)
    window, count = rate_windows.get(key, (now, 0))
    if window != now:
        window, count = now, 0
    count += 1
    rate_windows[key] = (window, count)
    return count <= MAX_POSTS_PER_SECOND

HOST_ONLY_TYPES = {
    'host', 'lobby', 'start-secret', 'snapshot', 'match-ended', 'settlement', 'error'
}
SERVER_ONLY_TYPES = {'presence', 'host-migrated', 'host-lost'}


def _put_system_locked(room, message, exclude=None):
    envelope = {'sender': 'relay', 'message': message, 'ts': int(time.time() * 1000)}
    for (r, cid), q in list(clients.items()):
        if r == room and cid != exclude:
            q.put(envelope)


def _schedule_host_grace(room, host_id):
    with lock:
        generation = host_disconnect_generation.get(room, 0) + 1
        host_disconnect_generation[room] = generation

    def resolve():
        time.sleep(HOST_RECONNECT_GRACE_SECONDS)
        with lock:
            if host_disconnect_generation.get(room) != generation:
                return
            if (room, host_id) in clients:
                return
            if room_hosts.get(room) != host_id:
                return

            connected_ids = sorted(cid for (r, cid) in clients if r == room)
            phase = room_phase.get(room, 'lobby')
            if phase == 'lobby' and connected_ids:
                next_host = connected_ids[0]
                room_hosts[room] = next_host
                _put_system_locked(room, {'type': 'host-migrated', 'hostId': next_host})
            else:
                room_hosts.pop(room, None)
                _put_system_locked(room, {'type': 'host-lost', 'hostId': host_id})

            if not connected_ids:
                room_phase.pop(room, None)
                room_hosts.pop(room, None)
                for key in [key for key in client_auth if key[0] == room]:
                    client_auth.pop(key, None)
                    rate_windows.pop(key, None)

    threading.Thread(target=resolve, daemon=True).start()


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        clean = urlparse(path).path.lstrip('/')
        if not clean:
            clean = 'index.html'
        return str(ROOT / clean)

    def do_OPTIONS(self):
        origin = self.headers.get('Origin')
        if not _origin_allowed(origin):
            self.send_error(403, 'origin not allowed')
            return
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', _cors_origin(origin))
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        origin = self.headers.get('Origin')
        if parsed.path in ('/events', '/health') and not _origin_allowed(origin):
            self.send_error(403, 'origin not allowed')
            return
        if parsed.path == '/health':
            with lock:
                rooms = len({room for room, _ in clients})
                connected = len(clients)
            payload = json.dumps({
                'ok': True,
                'service': 'polkacrew-relay',
                'version': '0.5',
                'rooms': rooms,
                'clients': connected,
                'allowedOrigins': '*' if _allowed_origins() is None else sorted(_allowed_origins()),
                'maxPostsPerSecond': MAX_POSTS_PER_SECOND,
                'hostReconnectGraceSeconds': HOST_RECONNECT_GRACE_SECONDS,
            }).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Access-Control-Allow-Origin', _cors_origin(origin))
            self.end_headers()
            self.wfile.write(payload)
            return
        if parsed.path != '/events':
            return super().do_GET()

        qs = parse_qs(parsed.query)
        room = (qs.get('room') or [''])[0].upper()
        cid = (qs.get('client') or [''])[0]
        auth = (qs.get('auth') or [''])[0]
        wants_host = (qs.get('host') or ['0'])[0] == '1'
        if not room or not cid or not auth:
            self.send_error(400, 'room, client and auth are required')
            return
        if not ROOM_RE.fullmatch(room) or len(cid) > 128 or len(auth) > 256:
            self.send_error(400, 'invalid room or client identity')
            return

        q = queue.Queue()
        key = (room, cid)
        with lock:
            known_auth = client_auth.get(key)
            if known_auth and known_auth != auth:
                self.send_error(403, 'client identity is already bound to another session secret')
                return
            if key not in clients and sum(1 for r, _ in clients if r == room) >= MAX_CLIENTS_PER_ROOM:
                self.send_error(429, 'room connection limit reached')
                return
            client_auth.setdefault(key, auth)

            if wants_host:
                existing = room_hosts.get(room)
                if existing and existing != cid:
                    self.send_error(409, 'room already has a host')
                    return
                room_hosts[room] = cid
                room_phase.setdefault(room, 'lobby')

            clients[key] = q
            if room_hosts.get(room) == cid:
                host_disconnect_generation[room] = host_disconnect_generation.get(room, 0) + 1
            _put_system_locked(room, {'type': 'presence', 'clientId': cid, 'connected': True}, exclude=cid)

        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.send_header('Access-Control-Allow-Origin', _cors_origin(origin))
        self.end_headers()
        try:
            payload = json.dumps({'hostId': room_hosts.get(room)}).encode()
            self.wfile.write(b'event: connected\ndata: ' + payload + b'\n\n')
            self.wfile.flush()
            while True:
                try:
                    message = q.get(timeout=15)
                    data = json.dumps(message, separators=(',', ':')).encode()
                    self.wfile.write(b'data: ' + data + b'\n\n')
                except queue.Empty:
                    self.wfile.write(b': ping\n\n')
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            was_host = False
            with lock:
                if clients.get(key) is q:
                    clients.pop(key, None)
                    was_host = room_hosts.get(room) == cid
                    _put_system_locked(room, {'type': 'presence', 'clientId': cid, 'connected': False}, exclude=cid)
            if was_host:
                _schedule_host_grace(room, cid)

    def do_POST(self):
        parsed = urlparse(self.path)
        origin = self.headers.get('Origin')
        if not _origin_allowed(origin):
            self.send_error(403, 'origin not allowed')
            return
        if parsed.path != '/send':
            self.send_error(404)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > MAX_POST_BYTES:
                self.send_error(413, 'payload too large or empty')
                return
            body = json.loads(self.rfile.read(length) or b'{}')
            room = str(body.get('room', '')).upper()
            sender = str(body.get('sender', ''))
            auth = str(body.get('auth', ''))
            target = body.get('target')
            message = body.get('message')
            if not room or not sender or not auth or not isinstance(message, dict):
                raise ValueError('invalid payload')
            if not ROOM_RE.fullmatch(room) or len(sender) > 128 or len(auth) > 256:
                raise ValueError('invalid room or sender identity')
            message_type = str(message.get('type', ''))
        except Exception as error:
            self.send_error(400, str(error))
            return

        with lock:
            key = (room, sender)
            if client_auth.get(key) != auth:
                self.send_error(403, 'sender authentication failed')
                return
            if key not in clients:
                self.send_error(409, 'sender has no active event stream')
                return
            if not _rate_allowed_locked(room, sender):
                self.send_error(429, 'sender rate limit exceeded')
                return
            if message_type in SERVER_ONLY_TYPES:
                self.send_error(403, 'server-only message rejected')
                return

            host_id = room_hosts.get(room)
            if message_type in HOST_ONLY_TYPES and sender != host_id:
                self.send_error(403, 'authoritative message rejected: sender is not room host')
                return

            if sender == host_id:
                if message_type == 'lobby':
                    room_phase[room] = 'lobby'
                elif message_type == 'snapshot':
                    room_phase[room] = str(message.get('phase', room_phase.get(room, 'playing')))
                elif message_type == 'start-secret':
                    room_phase[room] = 'playing'
                elif message_type == 'match-ended':
                    room_phase[room] = 'ended'

            envelope = {'sender': sender, 'message': message, 'ts': int(time.time() * 1000)}
            delivered = 0
            for (r, cid), q in list(clients.items()):
                if r != room or cid == sender:
                    continue
                if target and cid != target:
                    continue
                q.put(envelope)
                delivered += 1

        data = json.dumps({'ok': True, 'delivered': delivered, 'hostId': host_id}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Access-Control-Allow-Origin', _cors_origin(origin))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        if '/events' not in self.path:
            super().log_message(fmt, *args)


def main():
    os.chdir(ROOT)
    host = os.environ.get('POLKACREW_HOST', '0.0.0.0')
    port = int(os.environ.get('POLKACREW_PORT') or os.environ.get('PORT', '8765'))
    print(f'PolkaCrew relay v0.5 running on http://localhost:{port}')
    print('Host reconnect grace, lobby host migration and presence events are enabled.')
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == '__main__':
    main()
