"""
models.py
---------
Модель данных для представления файловой системы в виде дерева узлов.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FileNode:
    """Узел файлового дерева, представляющий файл или директорию.

    Используется для построения иерархической структуры файловой системы
    в процессе сканирования директории. Каждый узел хранит метаданные
    объекта файловой системы и ссылки на дочерние узлы и родителя.

    Attributes:
        name (str): Имя файла или директории (без полного пути).
        path (str): Абсолютный путь к файлу или директории.
        size (int): Размер в байтах. Для директорий — суммарный размер
            всего содержимого. По умолчанию 0.
        is_dir (bool): ``True``, если узел является директорией,
            ``False`` — если файлом. По умолчанию ``False``.
        children (list[FileNode]): Список дочерних узлов. Заполняется
            в процессе сканирования; для файлов всегда пуст.
        parent (Optional[FileNode]): Ссылка на родительский узел.
            ``None`` для корневого узла дерева.
    """
    name: str
    path: str
    size: int = 0
    is_dir: bool = False
    children: list = field(default_factory=list)
    parent: Optional['FileNode'] = None
    extension: str = ""
    file_type: str = ""
    modified: str = ""
    owner: str = ""
    attributes: str = ""
    is_system: bool = False
    system_description: str = ""
    defender_status: str = "Not scanned"
    scan_error: str = ""
