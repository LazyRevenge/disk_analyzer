"""
network_scanner.py
------------------
Клиентская часть для сканирования удалённого устройства по сети.
"""

import json
import gzip
import threading
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode

from file_info import apply_info_to_node
from models import FileNode


class NetworkScanner:
    def __init__(self, host: str, port: int, path: str, progress_cb, done_cb):
        self.host = host
        self.port = port
        self.path = path
        self.progress_cb = progress_cb
        self.done_cb = done_cb
        self._stop = False
        self._base_url = f"http://{host}:{port}"

    def start(self):
        t = threading.Thread(target=self._scan, daemon=True)
        t.start()

    def stop(self):
        self._stop = True

    def ping(self) -> tuple[bool, str]:
        try:
            url = f"{self._base_url}/ping"
            with urlopen(url, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return True, data.get("host", self.host)
        except Exception as e:
            return False, str(e)

    def get_roots(self) -> list[str]:
        try:
            url = f"{self._base_url}/roots"
            with urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("roots", [])
        except Exception:
            return []

    def get_status(self) -> dict:
        try:
            url = f"{self._base_url}/status"
            with urlopen(url, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"error": str(e)}

    def analyze_path(self, path: str, scan_defender: bool = False) -> dict:
        try:
            params = urlencode({"path": path, "defender": "1" if scan_defender else "0"})
            url = f"{self._base_url}/analyze?{params}"
            with urlopen(url, timeout=190) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"defender_status": f"Remote analyze failed: {e}"}

    def _scan(self):
        try:
            params = urlencode({"path": self.path, "max_items": 10000, "max_depth": 25})
            url = f"{self._base_url}/scan?{params}"

            self.progress_cb(0, "Подключение к серверу...")

            request = Request(url, headers={"Accept-Encoding": "gzip"})
            with urlopen(request, timeout=600) as resp:
                self.progress_cb(1, "Reading response from server...")
                raw_bytes = resp.read()
                self.progress_cb(2, f"Received {len(raw_bytes) / 1024 / 1024:.1f} MB")
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    self.progress_cb(3, "Decompressing response...")
                    raw_bytes = gzip.decompress(raw_bytes)
                self.progress_cb(4, "Parsing response...")
                raw = raw_bytes.decode("utf-8")

            del raw_bytes

            self.progress_cb(1, "Обработка данных...")
            data = json.loads(raw)
            del raw
            self.progress_cb(5, "Building file tree...")

            root = self._build_tree(data)
            self.done_cb(root)

        except HTTPError as e:
            self.done_cb(FileNode(name=f"Ошибка {e.code}", path=self.path, is_dir=True, scan_error=str(e)))
        except URLError as e:
            self.done_cb(FileNode(name=f"Нет соединения: {e.reason}", path=self.path, is_dir=True, scan_error=str(e.reason)))
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.done_cb(FileNode(name=f"Ошибка: {e}", path=self.path, is_dir=True, scan_error=str(e)))

    def _build_tree(self, data: dict) -> FileNode:
        """Итеративно строит дерево FileNode из JSON словаря (без рекурсии)."""
        root = FileNode(
            name=data.get("name", ""),
            path=data.get("path", ""),
            size=data.get("size", 0),
            is_dir=data.get("is_dir", False),
        )
        apply_info_to_node(root, data)

        count = 0
        # Каждый элемент стека: (dict узла, FileNode родителя)
        # Кладём по одному узлу, а не списком — нет вложенных for внутри while
        stack = [(child, root) for child in reversed(data.get("children", []))]

        while stack and not self._stop:
            child_data, parent_node = stack.pop()

            if not isinstance(child_data, dict):
                continue

            count += 1
            if count == 1 or count % 250 == 0:
                self.progress_cb(count, child_data.get("path", ""))

            child = FileNode(
                name=child_data.get("name", ""),
                path=child_data.get("path", ""),
                size=child_data.get("size", 0),
                is_dir=child_data.get("is_dir", False),
                parent=parent_node,
            )
            apply_info_to_node(child, child_data)
            parent_node.children.append(child)

            for grandchild in reversed(child_data.get("children", [])):
                stack.append((grandchild, child))

        return root