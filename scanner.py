"""
scanner.py
----------
Асинхронный сканер файловой системы на основе потока демона.
Обходит директорию рекурсивно и строит дерево :class:`~models.FileNode`.
"""

import os
import threading

from file_info import analyze_path, apply_info_to_node
from models import FileNode


class DirectoryScanner:
    """Рекурсивный сканер директории, работающий в фоновом потоке.

    Обходит файловую систему начиная с заданного пути, накапливает размеры
    файлов и строит дерево :class:`~models.FileNode`. По завершении вызывает
    переданный колбэк с корневым узлом результирующего дерева.

    Сканирование выполняется в потоке-демоне, поэтому не блокирует
    главный поток GUI. Симлинки на директории намеренно не разыменовываются,
    чтобы избежать циклов.

    Attributes:
        path (str): Корневой путь для сканирования.
        progress_cb (callable): Колбэк вида ``progress_cb(count, current_path)``,
            вызываемый при обработке каждого нового элемента файловой системы.
        done_cb (callable): Колбэк вида ``done_cb(root_node)``, вызываемый
            однократно при завершении сканирования.

    Args:
        path (str): Абсолютный или относительный путь к сканируемой директории.
        progress_cb (callable): Функция обратного вызова для отображения прогресса.
        done_cb (callable): Функция обратного вызова, получающая корневой
            :class:`~models.FileNode` готового дерева.

    Examples:
        >>> def on_progress(count, path):
        ...     print(f"[{count}] {path}")
        >>> def on_done(root):
        ...     print(f"Total: {root.size} bytes")
        >>> scanner = DirectoryScanner("/home/user", on_progress, on_done)
        >>> scanner.start()   # запускает фоновый поток
        >>> # ... позже, при необходимости:
        >>> scanner.stop()    # устанавливает флаг остановки
    """

    def __init__(self, path: str, progress_cb, done_cb):
        self.path = path
        self.progress_cb = progress_cb
        self.done_cb = done_cb
        self._stop = False

    def start(self):
        """Запускает сканирование в фоновом потоке-демоне.

        Создаёт и немедленно запускает ``threading.Thread`` с
        ``daemon=True``, поэтому поток автоматически завершится
        при закрытии главного процесса.
        """
        t = threading.Thread(target=self._scan, daemon=True)
        t.start()

    def stop(self):
        """Запрашивает досрочную остановку сканирования.

        Устанавливает внутренний флаг ``_stop`` в ``True``. Фоновый
        поток проверяет этот флаг перед обработкой каждого элемента
        и завершает работу при первой же возможности.

        Note:
            Метод не блокирует вызывающий поток и не гарантирует
            мгновенной остановки — поток завершится после текущей итерации.
        """
        self._stop = True

    def _scan(self):
        """Точка входа фонового потока.

        Создаёт корневой :class:`~models.FileNode` для :attr:`path`,
        запускает рекурсивный обход :meth:`_scan_dir` и по завершении
        вызывает :attr:`done_cb` с готовым деревом.
        """
        root = FileNode(
            name=os.path.basename(self.path) or self.path,
            path=self.path,
            is_dir=True
        )
        apply_info_to_node(root, analyze_path(self.path))
        count = [0]
        self._scan_dir(root, count)
        self.done_cb(root)

    def _scan_dir(self, node: FileNode, count: list):
        """Рекурсивно сканирует директорию и заполняет дерево узлов.

        Использует :func:`os.scandir` для перебора записей директории.
        Для каждой поддиректории создаёт дочерний :class:`~models.FileNode`
        и рекурсивно вызывает себя. Для файлов считывает размер через
        ``entry.stat()``. После обработки всех потомков накапливает
        суммарный размер в родительском узле ``node``.

        Ошибки доступа (:exc:`PermissionError`) и прочие исключения
        при открытии директории или чтении статистики файла
        молча игнорируются.

        Args:
            node (FileNode): Узел директории, которую нужно заполнить
                дочерними элементами.
            count (list[int]): Одноэлементный список-счётчик обработанных
                элементов. Передаётся по ссылке для накопления значения
                между рекурсивными вызовами.

        Note:
            Симлинки на директории не разыменовываются (``follow_symlinks=False``),
            чтобы предотвратить бесконечную рекурсию.
        """
        if self._stop:
            return

        try:
            entries = list(os.scandir(node.path))
        except PermissionError as exc:
            node.scan_error = f"Access denied: {exc}"
            return
        except Exception as exc:
            node.scan_error = f"Read error: {exc}"
            return

        for entry in entries:
            if self._stop:
                return

            count[0] += 1
            self.progress_cb(count[0], entry.path)

            if entry.is_dir(follow_symlinks=False):
                child = FileNode(
                    name=entry.name,
                    path=entry.path,
                    is_dir=True,
                    parent=node
                )
                apply_info_to_node(child, analyze_path(entry.path))
                node.children.append(child)
                self._scan_dir(child, count)
                node.size += child.size
            else:
                try:
                    size = entry.stat(follow_symlinks=False).st_size
                except (PermissionError, OSError):
                    size = 0
                child = FileNode(
                    name=entry.name,
                    path=entry.path,
                    size=size,
                    is_dir=False,
                    parent=node
                )
                apply_info_to_node(child, analyze_path(entry.path))
                node.children.append(child)
                node.size += size
