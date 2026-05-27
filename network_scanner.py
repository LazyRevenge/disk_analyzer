"""
network_scanner.py
------------------
Клиентская часть для сканирования удалённого устройства по сети.
Работает так же как DirectoryScanner из scanner.py, только данные
берёт не с локального диска, а с сервера на другом устройстве.
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
    """Сканирует удалённое устройство через HTTP сервер (server.py).

    Интерфейс намеренно совпадает с DirectoryScanner - start(), stop(),
    те же колбэки - чтобы app.py мог использовать оба без изменений.

    Args:
        host (str): IP адрес или hostname удалённого устройства.
        port (int): Порт сервера (по умолчанию 9090).
        path (str): Путь для сканирования на удалённом устройстве.
        progress_cb (callable): Колбэк вида progress_cb(count, current_path).
        done_cb (callable): Колбэк вида done_cb(root_node).
    """

    def __init__(self, host: str, port: int, path: str, progress_cb, done_cb):
        self.host = host
        self.port = port
        self.path = path
        self.progress_cb = progress_cb
        self.done_cb = done_cb
        self._stop = False
        self._base_url = f"http://{host}:{port}"

    def start(self):
        """Запускает сканирование в фоновом потоке."""
        t = threading.Thread(target=self._scan, daemon=True)
        t.start()

    def stop(self):
        """Запрашивает остановку."""
        self._stop = True

    def ping(self) -> tuple[bool, str]:
        """Проверяет что сервер доступен.

        Returns:
            tuple: (успех: bool, имя хоста или сообщение об ошибке: str)
        """
        try:
            url = f"{self._base_url}/ping"
            with urlopen(url, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return True, data.get("host", self.host)
        except Exception as e:
            return False, str(e)

    def get_roots(self) -> list[str]:
        """Получает список корневых директорий с удалённого устройства.

        Returns:
            list[str]: Список путей (например ['C:\\', 'D:\\'] или ['/', '/home/user'])
        """
        try:
            url = f"{self._base_url}/roots"
            with urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("roots", [])
        except Exception:
            return []

    def get_status(self) -> dict:
        """Returns a short status report from the remote computer."""
        try:
            url = f"{self._base_url}/status"
            with urlopen(url, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"error": str(e)}

    def analyze_path(self, path: str, scan_defender: bool = False) -> dict:
        """Returns metadata for one remote file and can run Defender there."""
        try:
            params = urlencode({"path": path, "defender": "1" if scan_defender else "0"})
            url = f"{self._base_url}/analyze?{params}"
            with urlopen(url, timeout=190) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"defender_status": f"Remote analyze failed: {e}"}

    def _scan(self):
        """Запрашивает данные с сервера и строит дерево FileNode."""
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

            # Освобождаем байты до парсинга
            del raw_bytes

            self.progress_cb(1, "Обработка данных...")
            data = json.loads(raw)
            del raw  # освобождаем строку до построения дерева
            self.progress_cb(5, "Building file tree...")

            root = self._build_tree(data)
            self.done_cb(root)

        except HTTPError as e:
            error_node = FileNode(
                name=f"Ошибка {e.code}",
                path=self.path,
                is_dir=True,
                scan_error=str(e)
            )
            self.done_cb(error_node)
        except URLError as e:
            error_node = FileNode(
                name=f"Нет соединения: {e.reason}",
                path=self.path,
                is_dir=True,
                scan_error=str(e.reason)
            )
            self.done_cb(error_node)
        except Exception as e:
            import traceback
            traceback.print_exc()
            error_node = FileNode(
                name=f"Ошибка: {e}",
                path=self.path,
                is_dir=True,
                scan_error=str(e)
            )
            self.done_cb(error_node)

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
        # Стек: (список дочерних dict'ов, FileNode-родитель)
        stack = [(data.get("children", []), root)]

        while stack:
            if self._stop:
                break

            children_data, parent_node = stack.pop()

            for child_data in children_data:
                count += 1

                # на случай если в children оказался уже готовый FileNode
                if isinstance(child_data, FileNode):
                    child_data.parent = parent_node
                    parent_node.children.append(child_data)
                    if count == 1 or count % 250 == 0:
                        self.progress_cb(count, child_data.path)
                    if child_data.children:
                        stack.append((child_data.children, child_data))
                    continue

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

                grandchildren = child_data.get("children", [])
                if grandchildren:
                    stack.append((grandchildren, child))

        return root