"""
server.py
---------
Запускается на втором устройстве (которое хочешь просканировать).
Поднимает HTTP сервер на порту 9090 и отвечает на запросы от клиента.

Запуск:
    python server.py

После запуска узнай IP этого устройства (ipconfig на Windows / ip a на Linux)
и введи его в приложении на первом ноуте.
"""

import os
import json
import gzip
import threading
import subprocess
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from file_info import analyze_path, get_system_status

PORT = 9090
DEFAULT_MAX_ITEMS = 100000
DEFAULT_MAX_DEPTH = 50
PROGRESS_EVERY = 500


class ScanState:
    def __init__(self, max_items: int = DEFAULT_MAX_ITEMS, max_depth: int = DEFAULT_MAX_DEPTH):
        self.max_items = max_items
        self.max_depth = max_depth
        self.items = 0
        self.truncated = False
        self.visited_dirs = set()


def scan_directory(path: str, state: ScanState | None = None, depth: int = 0) -> dict:
    """Рекурсивно сканирует директорию и возвращает дерево в виде словаря."""
    if state is None:
        state = ScanState()

    result = {
        "name": os.path.basename(path) or path,
        "path": path,
        "is_dir": True,
        "size": 0,
        "children": [],
        "scan_error": "",
    }
    result.update(analyze_path(path))

    real_path = os.path.normcase(os.path.realpath(path))
    if real_path in state.visited_dirs:
        result["scan_error"] = "Skipped: directory was already visited"
        state.truncated = True
        return result
    state.visited_dirs.add(real_path)

    if depth >= state.max_depth:
        result["scan_error"] = f"Max depth reached ({state.max_depth})"
        state.truncated = True
        return result

    if state.items >= state.max_items:
        result["scan_error"] = f"Max items reached ({state.max_items})"
        state.truncated = True
        return result

    try:
        entries = list(os.scandir(path))
    except PermissionError as exc:
        result["scan_error"] = f"Access denied: {exc}"
        print(f"[scan error] {path}: {result['scan_error']}", flush=True)
        return result
    except Exception as exc:
        result["scan_error"] = f"Read error: {exc}"
        print(f"[scan error] {path}: {result['scan_error']}", flush=True)
        return result

    for entry in entries:
        if state.items >= state.max_items:
            result["scan_error"] = f"Scan stopped: max items reached ({state.max_items})"
            state.truncated = True
            break

        state.items += 1
        if state.items % PROGRESS_EVERY == 0:
            print(f"[scan progress] {state.items} items, current: {entry.path}", flush=True)

        if entry.is_dir(follow_symlinks=False):
            if _is_reparse_or_symlink(entry):
                child = {
                    "name": entry.name,
                    "path": entry.path,
                    "is_dir": True,
                    "size": 0,
                    "children": [],
                    "scan_error": "Skipped: symlink or reparse point",
                }
                child.update(analyze_path(entry.path))
                result["children"].append(child)
                continue

            child = scan_directory(entry.path, state, depth + 1)
            result["children"].append(child)
            result["size"] += child["size"]
        else:
            try:
                size = entry.stat(follow_symlinks=False).st_size
            except (PermissionError, OSError):
                size = 0
            child = {
                "name": entry.name,
                "path": entry.path,
                "is_dir": False,
                "size": size,
                "children": [],
                "scan_error": "",
            }
            child.update(analyze_path(entry.path))
            result["children"].append(child)
            result["size"] += size

    return result


def _parse_positive_int(value, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _is_reparse_or_symlink(entry) -> bool:
    if entry.is_symlink():
        return True
    if os.name != "nt":
        return False
    try:
        attrs = entry.stat(follow_symlinks=False).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attrs & 0x400)


class ScanHandler(BaseHTTPRequestHandler):
    """Обработчик HTTP запросов."""

    def do_GET(self):
        parsed = urlparse(self.path)

        # GET /ping - проверка что сервер живой
        if parsed.path == "/ping":
            self._respond(200, {"status": "ok", "host": os.environ.get("COMPUTERNAME", os.uname().nodename if hasattr(os, 'uname') else "unknown")})
            return

        # GET /roots - список корневых директорий (диски на Windows, / на Linux)
        if parsed.path == "/roots":
            roots = self._get_roots()
            self._respond(200, {"roots": roots})
            return

        # GET /status - short overview of the remote computer.
        if parsed.path == "/status":
            self._respond(200, get_system_status())
            return

        # GET /analyze?path=C:\file.txt&defender=1 - metadata and optional Defender scan.
        if parsed.path == "/analyze":
            params = parse_qs(parsed.query)
            path = params.get("path", [None])[0]
            defender = params.get("defender", ["0"])[0] == "1"

            if not path or not os.path.exists(path):
                self._respond(400, {"error": "path not found"})
                return

            self._respond(200, analyze_path(path, scan_defender=defender))
            return

        # GET /scan?path=C:\ - сканировать конкретную папку
        if parsed.path == "/scan":
            params = parse_qs(parsed.query)
            path = params.get("path", [None])[0]

            if not path or not os.path.isdir(path):
                detail = "empty path" if not path else f"not found or not a directory: {path}"
                print(f"[scan error] {detail}", flush=True)
                self._respond(400, {"error": detail})
                return

            max_items = _parse_positive_int(params.get("max_items", [DEFAULT_MAX_ITEMS])[0], DEFAULT_MAX_ITEMS)
            max_depth = _parse_positive_int(params.get("max_depth", [DEFAULT_MAX_DEPTH])[0], DEFAULT_MAX_DEPTH)
            state = ScanState(max_items=max_items, max_depth=max_depth)

            print(f"[scan] {path} (max_items={max_items}, max_depth={max_depth})", flush=True)
            data = scan_directory(path, state)
            data["scan_items"] = state.items
            data["scan_truncated"] = state.truncated
            print(f"[scan done] {path}: {state.items} scanned, {len(data.get('children', []))} top items, {data.get('size', 0)} bytes", flush=True)
            print("[response] preparing JSON...", flush=True)
            self._respond(200, data)
            print("[response] sent", flush=True)
            return

        self._respond(404, {"error": "not found"})

    def _get_roots(self):
        """Возвращает список корневых путей."""
        roots = []
        if os.name == "nt":  # Windows
            import string
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if os.path.exists(drive):
                    roots.append(drive)
        else:  # Linux / Mac
            roots.append("/")
            home = os.path.expanduser("~")
            if home != "/":
                roots.append(home)
        return roots

    def _respond(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        raw_size = len(body)
        accepts_gzip = "gzip" in self.headers.get("Accept-Encoding", "").lower()
        if accepts_gzip:
            body = gzip.compress(body)
        print(
            f"[response] {raw_size / 1024 / 1024:.2f} MB raw"
            f"{f', {len(body) / 1024 / 1024:.2f} MB gzip' if accepts_gzip else ''}",
            flush=True
        )
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if accepts_gzip:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        print("[response] writing to client...", flush=True)
        try:
            chunk_size = 1024 * 256
            for start in range(0, len(body), chunk_size):
                self.wfile.write(body[start:start + chunk_size])
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError) as exc:
            print(f"[response error] client disconnected: {exc}", flush=True)
            raise

    def log_message(self, format, *args):
        print(f"[{self.client_address[0]}] {format % args}")


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), ScanHandler)
    print(f"Сервер запущен на порту {PORT}")
    print(f"Подключись с другого устройства, введя IP этого компьютера")
    print(f"Остановить: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен")
        server.server_close()


if __name__ == "__main__":
    main()
