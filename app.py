"""
app.py
------
Главный модуль GUI-приложения «Disk Space Analyzer».

Определяет класс :class:`DiskAnalyzerApp`, который строит интерфейс
на базе Tkinter: панель инструментов, холст тримапа и таблицу файлов.
Взаимодействует со сканером директорий (:class:`~scanner.DirectoryScanner`)
и алгоритмом раскладки (:func:`~treemap.squarify`).
"""

import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, font, simpledialog, messagebox
from typing import Optional

from file_info import analyze_path, apply_info_to_node, scan_with_windows_defender
from models import FileNode
from scanner import DirectoryScanner
from network_scanner import NetworkScanner
from treemap import squarify
from utils import format_size, COLORS


class DiskAnalyzerApp:
    """Графическое приложение для визуального анализа занятого дискового пространства.

    Отображает содержимое выбранной директории в виде интерактивного тримапа
    (алгоритм Squarified Treemap) и параллельной таблицы с файлами и папками.
    Поддерживает навигацию вглубь дерева двойным кликом и возврат назад.

    Основные возможности:
        - Асинхронное сканирование директорий без заморозки UI.
        - Цветовое кодирование блоков тримапа по глубине дерева.
        - Всплывающие подсказки с именем, размером и путём объекта.
        - Таблица с сортировкой по имени и размеру.
        - Навигация: двойной клик на тримапе или в таблице входит в папку;
          кнопка «← BACK» возвращает на уровень выше.

    Args:
        root (tk.Tk): Корневой виджет Tkinter — главное окно приложения.

    Attributes:
        root (tk.Tk): Главное окно.
        _current_node (Optional[FileNode]): Узел, отображаемый в данный момент.
        _root_node (Optional[FileNode]): Корневой узел отсканированного дерева.
        _scanner (Optional[DirectoryScanner]): Активный объект сканирования.
        _rects (list): Список кортежей ``(node, x1, y1, x2, y2, color)``
            для всех нарисованных блоков тримапа — используется для
            определения объекта под курсором.
        _tooltip (Optional[tk.Toplevel]): Текущее всплывающее окно подсказки.
        _sort_col (str): Активная колонка сортировки таблицы (``"size"`` или
            ``"name"``).
        _sort_rev (bool): Направление сортировки: ``True`` — по убыванию.
        _color_map (dict[str, int]): Отображение пути узла в индекс цвета
            из :data:`~utils.COLORS`.
    """

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Disk Space Analyzer")
        self.root.geometry("1200x750")
        self.root.minsize(800, 550)
        self.root.configure(bg="#0F0F0F")

        self._current_node: Optional[FileNode] = None
        self._root_node: Optional[FileNode] = None
        self._scanner: Optional[DirectoryScanner] = None

        self._rects = []

        self._tooltip = None
        self._sort_col = "size"
        self._sort_rev = True
        self._scan_mode = "local"
        self._remote_host = ""
        self._remote_port = 9090
        self._remote_path = ""

        self._color_map = {}

        self._build_ui()

    # Построение интерфейса
    def _build_ui(self):
        """Создаёт и компонует все виджеты главного окна.

        Структура интерфейса:
            - Верхняя панель (``top``): заголовок, кнопки «OPEN FOLDER»
              и «← BACK».
            - Строка текущего пути (``_path_var``).
            - Строка прогресса: метка и индикатор ``ttk.Progressbar``.
            - Разделённая панель ``PanedWindow``:
                - Левая часть: холст ``tk.Canvas`` для тримапа.
                - Правая часть: ``ttk.Treeview`` с полосой прокрутки.
            - Строка статуса (``_status_var``).

        Привязывает обработчики событий холста и таблицы.
        """
        try:
            title_font = font.Font(family="Consolas", size=13, weight="bold")
            mono_font  = font.Font(family="Consolas", size=10)
            small_font = font.Font(family="Consolas", size=9)
        except Exception:
            title_font = font.Font(size=13, weight="bold")
            mono_font  = font.Font(size=10)
            small_font = font.Font(size=9)

        top = tk.Frame(self.root, bg="#0F0F0F", pady=8)
        top.pack(fill=tk.X, padx=16)

        tk.Label(top, text="◈ DISK ANALYZER", font=title_font,
                 fg="#00FF88", bg="#0F0F0F").pack(side=tk.LEFT)

        self._btn_open = tk.Button(
            top, text="[ OPEN FOLDER ]", font=mono_font,
            fg="#00FF88", bg="#0F0F0F",
            activeforeground="#000", activebackground="#00FF88",
            relief=tk.FLAT, bd=1,
            highlightbackground="#00FF88", highlightthickness=1,
            cursor="hand2", padx=10,
            command=self._choose_directory
        )
        self._btn_open.pack(side=tk.RIGHT)

        self._btn_remote = tk.Button(
            top, text="[ REMOTE SCAN ]", font=mono_font,
            fg="#00CCFF", bg="#0F0F0F",
            activeforeground="#000", activebackground="#00CCFF",
            relief=tk.FLAT, bd=1,
            highlightbackground="#00CCFF", highlightthickness=1,
            cursor="hand2", padx=10,
            command=self._choose_remote_directory
        )
        self._btn_remote.pack(side=tk.RIGHT, padx=(0, 8))

        self._btn_remote_status = tk.Button(
            top, text="[ REMOTE STATUS ]", font=mono_font,
            fg="#E9C46A", bg="#0F0F0F",
            activeforeground="#000", activebackground="#E9C46A",
            relief=tk.FLAT, bd=1,
            highlightbackground="#E9C46A", highlightthickness=1,
            cursor="hand2", padx=10,
            command=self._show_remote_status
        )
        self._btn_remote_status.pack(side=tk.RIGHT, padx=(0, 8))

        self._btn_defender = tk.Button(
            top, text="[ DEFENDER CHECK ]", font=mono_font,
            fg="#FF595E", bg="#0F0F0F",
            activeforeground="#000", activebackground="#FF595E",
            relief=tk.FLAT, bd=1,
            highlightbackground="#FF595E", highlightthickness=1,
            cursor="hand2", padx=10,
            command=self._scan_selected_with_defender
        )
        self._btn_defender.pack(side=tk.RIGHT, padx=(0, 8))

        self._btn_defender_dir = tk.Button(
            top, text="[ SCAN FOLDER ]", font=mono_font,
            fg="#FF595E", bg="#0F0F0F",
            activeforeground="#000", activebackground="#FF595E",
            relief=tk.FLAT, bd=1,
            highlightbackground="#FF595E", highlightthickness=1,
            cursor="hand2", padx=10,
            command=self._scan_dir_with_defender
        )
        self._btn_defender_dir.pack(side=tk.RIGHT, padx=(0, 8))

        self._btn_back = tk.Button(
            top, text="[ ← BACK ]", font=mono_font,
            fg="#888", bg="#0F0F0F",
            activeforeground="#000", activebackground="#888",
            relief=tk.FLAT, bd=1,
            highlightbackground="#444", highlightthickness=1,
            cursor="hand2", padx=10,
            state=tk.DISABLED,
            command=self._go_back
        )
        self._btn_back.pack(side=tk.RIGHT, padx=(0, 8))

        self._path_var = tk.StringVar(value="Папка не выбрана")
        tk.Label(self.root, textvariable=self._path_var, font=small_font,
                 fg="#555", bg="#0F0F0F", anchor="w").pack(fill=tk.X, padx=16)

        self._progress_frame = tk.Frame(self.root, bg="#0F0F0F")
        self._progress_frame.pack(fill=tk.X, padx=16, pady=(4, 0))

        self._progress_label = tk.Label(self._progress_frame, text="", font=small_font,
                                        fg="#555", bg="#0F0F0F")
        self._progress_label.pack(side=tk.LEFT)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Green.Horizontal.TProgressbar",
                        troughcolor="#1A1A1A", background="#00FF88",
                        darkcolor="#00FF88", lightcolor="#00FF88",
                        bordercolor="#0F0F0F")

        self._progress = ttk.Progressbar(
            self._progress_frame,
            style="Green.Horizontal.TProgressbar",
            mode="indeterminate",
            length=200
        )

        pane = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, bg="#0F0F0F",
                              sashwidth=4, sashrelief=tk.FLAT)
        pane.pack(fill=tk.BOTH, expand=True, padx=16, pady=10)

        canvas_frame = tk.Frame(pane, bg="#1A1A1A", bd=0)
        self._canvas = tk.Canvas(canvas_frame, bg="#1A1A1A",
                                 highlightthickness=0, cursor="crosshair")
        self._canvas.pack(fill=tk.BOTH, expand=True)

        self._canvas.bind("<Configure>",       self._on_canvas_resize)
        self._canvas.bind("<Motion>",          self._on_mouse_move)
        self._canvas.bind("<Leave>",           self._on_mouse_leave)
        self._canvas.bind("<Double-Button-1>", self._on_double_click)

        pane.add(canvas_frame, minsize=400)

        table_frame = tk.Frame(pane, bg="#0F0F0F")

        style.configure("Dark.Treeview",
                        background="#0F0F0F", foreground="#CCC",
                        fieldbackground="#0F0F0F", borderwidth=0,
                        rowheight=22, font=("Consolas", 9))
        style.configure("Dark.Treeview.Heading",
                        background="#1A1A1A", foreground="#00FF88",
                        font=("Consolas", 9, "bold"), relief=tk.FLAT)
        style.map("Dark.Treeview",
                  background=[("selected", "#00FF8844")],
                  foreground=[("selected", "#00FF88")])

        cols = ("icon", "name", "size", "pct", "ext", "type", "modified", "owner", "system", "defender")
        self._tree = ttk.Treeview(table_frame, columns=cols, show="headings",
                                  style="Dark.Treeview", selectmode="browse")

        self._tree.heading("icon", text="")
        self._tree.heading("name", text="NAME ↕",
                           command=lambda: self._sort_table("name"))
        self._tree.heading("size", text="SIZE ↕",
                           command=lambda: self._sort_table("size"))
        self._tree.heading("pct",  text="%")
        self._tree.heading("ext", text="EXT")
        self._tree.heading("type", text="TYPE")
        self._tree.heading("modified", text="MODIFIED")
        self._tree.heading("owner", text="OWNER")
        self._tree.heading("system", text="SYSTEM")
        self._tree.heading("defender", text="DEFENDER")

        self._tree.column("icon", width=20,  minwidth=20,  stretch=False, anchor="center")
        self._tree.column("name", width=170, minwidth=100, anchor="w")
        self._tree.column("size", width=80,  minwidth=60,  anchor="e")
        self._tree.column("pct",  width=50,  minwidth=40,  anchor="e")
        self._tree.column("ext", width=70, minwidth=50, anchor="w")
        self._tree.column("type", width=150, minwidth=90, anchor="w")
        self._tree.column("modified", width=135, minwidth=110, anchor="w")
        self._tree.column("owner", width=135, minwidth=80, anchor="w")
        self._tree.column("system", width=70, minwidth=55, anchor="center")
        self._tree.column("defender", width=135, minwidth=95, anchor="w")

        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL,
                                  command=self._tree.yview)
        xscrollbar = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL,
                                   command=self._tree.xview)
        self._tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=xscrollbar.set)

        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        xscrollbar.pack(side=tk.BOTTOM, fill=tk.X)

        self._tree.bind("<Double-Button-1>", self._on_table_double_click)
        self._tree.bind("<Motion>", self._on_table_motion)
        self._tree.bind("<Leave>", self._on_table_leave)

        pane.add(table_frame, minsize=420)
        pane.paneconfigure(table_frame, width=520)

        self._status_var = tk.StringVar(value="Готово")
        tk.Label(self.root, textvariable=self._status_var, font=small_font,
                 fg="#444", bg="#0F0F0F", anchor="w").pack(fill=tk.X, padx=16, pady=(0, 6))

    # Выбор директории и запуск сканирования
    def _choose_directory(self):
        """Открывает диалог выбора директории и запускает сканирование.

        Если пользователь закрывает диалог без выбора, метод завершается
        без каких-либо действий.
        """
        path = filedialog.askdirectory(title="Выберите директорию для анализа")
        if not path:
            return
        self._start_scan(path)

    def _start_scan(self, path: str):
        """Сбрасывает состояние приложения и запускает новое сканирование.

        Останавливает предыдущий сканер (если он был активен), очищает
        холст и таблицу, показывает индикатор прогресса и создаёт новый
        :class:`~scanner.DirectoryScanner`.

        Args:
            path (str): Абсолютный путь к директории для сканирования.
        """
        if self._scanner:
            self._scanner.stop()

        self._scan_mode = "local"
        self._root_node = None
        self._current_node = None
        self._rects = []
        self._canvas.delete("all")
        self._tree.delete(*self._tree.get_children())
        self._path_var.set(path)
        self._status_var.set("Сканирование...")
        self._btn_back.configure(state=tk.DISABLED)
        self._color_map = {}

        self._progress.pack(side=tk.LEFT, padx=(8, 0))
        self._progress.start(10)
        self._progress_label.configure(text="Сканирование: ")

        self._scanner = DirectoryScanner(
            path,
            progress_cb=self._on_progress,
            done_cb=self._on_scan_done
        )
        self._scanner.start()

    def _choose_remote_directory(self):
        """Открывает кастомный диалог подключения к удалённому устройству."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Remote Scan")
        dialog.configure(bg="#0F0F0F")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.lift()
        dialog.attributes("-topmost", True)

        dialog.update_idletasks()
        pw, ph = 380, 230
        rx = self.root.winfo_x() + (self.root.winfo_width() - pw) // 2
        ry = self.root.winfo_y() + (self.root.winfo_height() - ph) // 2
        dialog.geometry(f"{pw}x{ph}+{rx}+{ry}")

        try:
            mf = font.Font(family="Consolas", size=9)
        except Exception:
            mf = font.Font(size=9)

        pad = {"padx": 16, "pady": 4}

        tk.Label(dialog, text="IP или hostname:", fg="#888", bg="#0F0F0F", font=mf, anchor="w").pack(fill=tk.X, **pad)
        host_var = tk.StringVar(value=self._remote_host or "")
        host_entry = tk.Entry(dialog, textvariable=host_var, font=mf, bg="#1A1A1A", fg="#00FF88",
                              insertbackground="#00FF88", relief=tk.FLAT, bd=4)
        host_entry.pack(fill=tk.X, padx=16, pady=(0, 6))

        tk.Label(dialog, text="Порт:", fg="#888", bg="#0F0F0F", font=mf, anchor="w").pack(fill=tk.X, **pad)
        port_var = tk.StringVar(value=str(self._remote_port))
        tk.Entry(dialog, textvariable=port_var, font=mf, bg="#1A1A1A", fg="#00FF88",
                 insertbackground="#00FF88", relief=tk.FLAT, bd=4).pack(fill=tk.X, padx=16, pady=(0, 6))

        tk.Label(dialog, text="Путь для сканирования:", fg="#888", bg="#0F0F0F", font=mf, anchor="w").pack(fill=tk.X, **pad)
        path_var = tk.StringVar(value=self._remote_path if hasattr(self, "_remote_path") else "")
        tk.Entry(dialog, textvariable=path_var, font=mf, bg="#1A1A1A", fg="#00FF88",
                 insertbackground="#00FF88", relief=tk.FLAT, bd=4).pack(fill=tk.X, padx=16, pady=(0, 10))

        status_var = tk.StringVar(value="")
        tk.Label(dialog, textvariable=status_var, fg="#E9C46A", bg="#0F0F0F", font=mf).pack()

        def on_connect():
            host = host_var.get().strip()
            path = path_var.get().strip()
            if not host:
                status_var.set("Введи IP или hostname")
                return
            try:
                port = int(port_var.get().strip())
            except ValueError:
                status_var.set("Порт должен быть числом")
                return

            status_var.set("Подключение...")
            dialog.update()

            probe = NetworkScanner(host, port, "", self._on_progress, self._on_scan_done)
            ok, host_name = probe.ping()
            if not ok:
                status_var.set(f"Нет соединения: {host_name}")
                return

            if not path:
                roots = probe.get_roots()
                path = roots[0] if roots else "C:\\"
                path_var.set(path)

            self._remote_host = host
            self._remote_port = port
            self._remote_path = path

            dialog.destroy()
            self._start_remote_scan(host, port, path)

        tk.Button(
            dialog, text="[ CONNECT & SCAN ]", font=mf,
            fg="#00CCFF", bg="#0F0F0F",
            activeforeground="#000", activebackground="#00CCFF",
            relief=tk.FLAT, bd=1,
            highlightbackground="#00CCFF", highlightthickness=1,
            cursor="hand2", padx=10,
            command=on_connect
        ).pack(pady=(0, 10))

        host_entry.focus_set()
        dialog.bind("<Return>", lambda e: on_connect())

    def _start_remote_scan(self, host: str, port: int, path: str):
        if self._scanner:
            self._scanner.stop()

        self._scan_mode = "remote"
        self._remote_host = host
        self._remote_port = port
        self._root_node = None
        self._current_node = None
        self._rects = []
        self._canvas.delete("all")
        self._tree.delete(*self._tree.get_children())
        self._path_var.set(f"{host}:{port} -> {path}")
        self._status_var.set("Remote scanning...")
        self._btn_back.configure(state=tk.DISABLED)
        self._color_map = {}

        self._progress.pack(side=tk.LEFT, padx=(8, 0))
        self._progress.start(10)
        self._progress_label.configure(text="Remote scanning: ")

        self._scanner = NetworkScanner(
            host,
            port,
            path,
            progress_cb=self._on_progress,
            done_cb=self._on_scan_done
        )
        self._scanner.start()

    def _show_remote_status(self):
        host = self._remote_host or simpledialog.askstring("Remote status", "Remote laptop IP or hostname:")
        if not host:
            return
        port_raw = simpledialog.askstring("Remote status", "Server port:", initialvalue=str(self._remote_port))
        if not port_raw:
            return
        try:
            port = int(port_raw)
        except ValueError:
            messagebox.showerror("Remote status", "Port must be a number.")
            return

        scanner = NetworkScanner(host, port, "", self._on_progress, self._on_scan_done)
        status = scanner.get_status()
        if "error" in status:
            messagebox.showerror("Remote status", status["error"])
            return

        processes = "\n".join(status.get("processes", [])[:30])
        message = (
            f"Host: {status.get('host', '')}\n"
            f"User: {status.get('user', '')}\n"
            f"OS: {status.get('os', '')}\n"
            f"Time: {status.get('time', '')}\n\n"
            f"Running processes:\n{processes}"
        )
        messagebox.showinfo("Remote status", message)

    def _scan_selected_with_defender(self):
        node = self._selected_node()
        if not node:
            messagebox.showinfo("Defender check", "Select a file or folder in the table first.")
            return

        self._status_var.set(f"Defender scan started: {node.path}")
        self._btn_defender.configure(state=tk.DISABLED)

        def worker():
            if self._scan_mode == "remote":
                scanner = NetworkScanner(self._remote_host, self._remote_port, "", self._on_progress, self._on_scan_done)
                info = scanner.analyze_path(node.path, scan_defender=True)
                status = info.get("defender_status", "Remote Defender result unavailable")
                self.root.after(0, lambda: self._apply_defender_result(node, info, status))
            else:
                status = scan_with_windows_defender(node.path)
                info = analyze_path(node.path)
                info["defender_status"] = status
                self.root.after(0, lambda: self._apply_defender_result(node, info, status))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_defender_result(self, node: FileNode, info: dict, status: str):
        apply_info_to_node(node, info)
        if self._current_node:
            self._populate_table(self._current_node)
        self._btn_defender.configure(state=tk.NORMAL)
        self._status_var.set(f"Defender: {status}")
        messagebox.showinfo("Defender check", f"{node.path}\n\n{status}")

    def _scan_dir_with_defender(self):
        """Сканирует текущую открытую папку целиком через Defender."""
        node = self._current_node
        if not node:
            messagebox.showinfo("Defender", "Сначала открой папку.")
            return

        self._status_var.set(f"Defender scan folder: {node.path}")
        self._btn_defender_dir.configure(state=tk.DISABLED)
        self._btn_defender.configure(state=tk.DISABLED)

        def worker():
            if self._scan_mode == "remote":
                scanner = NetworkScanner(
                    self._remote_host, self._remote_port, "",
                    self._on_progress, self._on_scan_done
                )
                info = scanner.analyze_path(node.path, scan_defender=True)
                status = info.get("defender_status", "Defender result unavailable")
            else:
                status = scan_with_windows_defender(node.path)
            self.root.after(0, lambda: self._finish_defender_dir(status))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_defender_dir(self, status: str):
        self._btn_defender_dir.configure(state=tk.NORMAL)
        self._btn_defender.configure(state=tk.NORMAL)
        self._status_var.set(f"Defender: {status}")
        messagebox.showinfo("Defender — папка", status)

    def _selected_node(self) -> Optional[FileNode]:
        sel = self._tree.selection()
        if not sel:
            return None
        return self._find_node_by_path(sel[0])

    # Колбэки сканера
    def _on_progress(self, count: int, current_path: str):
        """Обновляет метку прогресса при обработке очередного объекта ФС.

        Вызывается из фонового потока, поэтому использует
        :meth:`tkinter.Misc.after` для безопасного обновления GUI.

        Args:
            count (int): Общее количество обработанных объектов на данный момент.
            current_path (str): Полный путь к последнему обработанному объекту.
        """
        name = os.path.basename(current_path)
        self.root.after(0, lambda: self._progress_label.configure(
            text=f"Сканирование ({count}): {name[:40]}"
        ))

    def _on_scan_done(self, root_node: FileNode):
        """Получает результат сканирования из фонового потока.

        Передаёт ``root_node`` в главный поток через
        :meth:`tkinter.Misc.after` для последующей отрисовки.

        Args:
            root_node (FileNode): Корневой узел полностью построенного
                файлового дерева.
        """
        self.root.after(0, lambda: self._finish_scan(root_node))

    def _finish_scan(self, root_node: FileNode):
        """Завершает процесс сканирования в главном потоке GUI.

        Скрывает индикатор прогресса, сохраняет корневой узел,
        назначает цвета всем узлам дерева и инициирует первую отрисовку.
        Обновляет строку статуса с итоговой информацией.

        Args:
            root_node (FileNode): Корневой узел готового дерева.
        """
        self._progress.stop()
        self._progress.pack_forget()
        self._progress_label.configure(text="")

        self._root_node = root_node
        self._current_node = root_node
        self._assign_colors(root_node, 0)
        self._render_current()

        self._status_var.set(
            f"Итого: {format_size(root_node.size)}  |  "
            f"{sum(1 for c in root_node.children if not c.is_dir)} файлов, "
            f"{sum(1 for c in root_node.children if c.is_dir)} папок"
        )

    # Цвета и навигация
        if root_node.scan_error:
            self._status_var.set(f"Scan problem: {root_node.scan_error}")
            messagebox.showwarning("Scan problem", root_node.scan_error)

    def _assign_colors(self, node: FileNode, depth: int):
        """Рекурсивно назначает цвета всем узлам дерева.

        Цвет определяется по формуле ``(depth * 7 + i) % len(COLORS)``,
        где ``i`` — порядковый номер потомка. Результат сохраняется в
        :attr:`_color_map` с ключом ``node.path``.

        Args:
            node (FileNode): Узел, чьих потомков нужно покрасить.
            depth (int): Текущая глубина в дереве (0 для корня).
        """
        for i, child in enumerate(node.children):
            self._color_map[child.path] = (depth * 7 + i) % len(COLORS)
            if child.is_dir:
                self._assign_colors(child, depth + 1)

    def _go_back(self):
        """Поднимается на один уровень вверх по дереву директорий.

        Если у текущего узла есть родитель, переключает :attr:`_current_node`
        на него и перерисовывает интерфейс. Не выполняет действий, если
        текущий узел — корневой.
        """
        if self._current_node and self._current_node.parent:
            self._current_node = self._current_node.parent
            self._render_current()

    def _drill_down(self, node: FileNode):
        """Переходит внутрь указанной директории.

        Устанавливает ``node`` как текущий отображаемый узел и
        перерисовывает интерфейс. Не выполняет действий, если
        ``node`` является файлом или пустой директорией.

        Args:
            node (FileNode): Дочерняя директория для перехода.
        """
        if node.is_dir and node.children:
            self._current_node = node
            self._render_current()


    # Отрисовка
    def _render_current(self):
        """Перерисовывает тримап и таблицу для текущего узла.

        Обновляет строку пути, состояние кнопки «← BACK»,
        вызывает :meth:`_draw_treemap` и :meth:`_populate_table`.
        Ничего не делает, если :attr:`_current_node` не задан.
        """
        node = self._current_node
        if node is None:
            return

        self._path_var.set(node.path)

        can_back = node.parent is not None
        self._btn_back.configure(
            state=tk.NORMAL if can_back else tk.DISABLED,
            fg="#00FF88" if can_back else "#444",
            highlightbackground="#00FF88" if can_back else "#333"
        )

        self._draw_treemap(node)
        self._populate_table(node)

    def _draw_treemap(self, node: FileNode):
        """Рисует тримап для дочерних элементов указанного узла.

        Очищает холст, вычисляет раскладку через :func:`~treemap.squarify`
        и отрисовывает прямоугольники с подписями. Для каждого блока
        добавляет запись в :attr:`_rects` для последующего hit-тестирования.
        Метки (имя файла и размер) показываются только при достаточном
        размере блока.

        Args:
            node (FileNode): Узел, чьи дочерние элементы нужно отобразить.
        """
        self._canvas.delete("all")
        self._rects = []

        w = self._canvas.winfo_width()
        h = self._canvas.winfo_height()
        if w < 2 or h < 2:
            return

        children = [c for c in node.children if c.size > 0]
        if not children:
            message = node.scan_error or "Empty or all items are 0 bytes"
            self._canvas.create_text(
                w // 2, h // 2, text="(пусто)", fill="#444", font=("Consolas", 14)
            )
            return

        items = sorted([(c, c.size) for c in children], key=lambda x: -x[1])
        layout = squarify(items, 2, 2, w - 4, h - 4)

        for node_item, rx, ry, rw, rh in layout:
            color_idx = self._color_map.get(node_item.path, 0)
            color = COLORS[color_idx % len(COLORS)]

            self._canvas.create_rectangle(
                rx, ry, rx + rw, ry + rh,
                fill=color, outline="#0F0F0F", width=2
            )

            if rw > 40 and rh > 20:
                label = node_item.name
                if rw < 100:
                    label = label[:8]  + "…" if len(label) > 8  else label
                elif rw < 200:
                    label = label[:16] + "…" if len(label) > 16 else label

                icon     = "📁" if node_item.is_dir else "📄"
                size_str = format_size(node_item.size)

                self._canvas.create_text(
                    rx + rw / 2,
                    ry + rh / 2 - (8 if rh > 36 else 0),
                    text=f"{icon} {label}",
                    fill="white", font=("Consolas", 8, "bold"),
                    width=rw - 8, anchor="center"
                )

                if rh > 36:
                    self._canvas.create_text(
                        rx + rw / 2, ry + rh / 2 + 10,
                        text=size_str,
                        fill="#AAAAAA", font=("Consolas", 7),
                        anchor="center"
                    )

            self._rects.append((node_item, rx, ry, rx + rw, ry + rh, color))

    # ------------------------------------------------------------------
    # Обработчики событий холста
    # ------------------------------------------------------------------

    def _on_canvas_resize(self, event):
        """Перерисовывает тримап при изменении размеров холста.

        Привязывается к событию ``<Configure>`` холста. Если текущий
        узел задан, вызывает :meth:`_draw_treemap`.

        Args:
            event: Объект события Tkinter (атрибуты ``width`` и ``height``
                содержат новые размеры холста).
        """
        if self._current_node:
            self._draw_treemap(self._current_node)

    def _find_rect_at(self, x: float, y: float) -> Optional[FileNode]:
        """Возвращает узел файлового дерева под указанными координатами.

        Перебирает список :attr:`_rects` и возвращает первый узел,
        чей прямоугольник содержит точку ``(x, y)``.

        Args:
            x (float): Координата X в системе координат холста.
            y (float): Координата Y в системе координат холста.

        Returns:
            Optional[FileNode]: Найденный узел или ``None``, если
            курсор не попадает ни в один прямоугольник.
        """
        for node, x1, y1, x2, y2, color in self._rects:
            if x1 <= x <= x2 and y1 <= y <= y2:
                return node
        return None

    def _on_mouse_move(self, event):
        """Показывает всплывающую подсказку при наведении курсора на блок.

        Если курсор находится над каким-либо блоком тримапа, вызывает
        :meth:`_show_tooltip`; иначе скрывает подсказку.

        Args:
            event: Объект события Tkinter с атрибутами ``x``, ``y``.
        """
        node = self._find_rect_at(event.x, event.y)
        if node:
            self._show_tooltip(event, node)
        else:
            self._hide_tooltip()

    def _on_mouse_leave(self, event):
        """Скрывает всплывающую подсказку при выходе курсора за границы холста.

        Args:
            event: Объект события Tkinter (не используется).
        """
        self._hide_tooltip()

    def _on_table_motion(self, event):
        item = self._tree.identify_row(event.y)
        if not item:
            self._hide_tooltip()
            return
        node = self._find_node_by_path(item)
        if node:
            extra = node.system_description if node.is_system else "Нет"
            self._show_tooltip(event, node, extra=extra)
        else:
            self._hide_tooltip()

    def _on_table_leave(self, event):
        self._hide_tooltip()

    def _show_tooltip(self, event, node: FileNode, extra: str = ""):
        """Создаёт всплывающее окно с информацией об узле.

        Уничтожает предыдущую подсказку (если была), затем создаёт
        новое ``tk.Toplevel`` без рамки рядом с курсором. Показывает
        имя, размер (в абсолютном и процентном выражении) и путь узла.

        Args:
            event: Объект события Tkinter; используются ``x_root`` и
                ``y_root`` для позиционирования окна.
            node (FileNode): Узел, информацию о котором нужно показать.
        """
        self._hide_tooltip()
        total = self._current_node.size if self._current_node else 1
        pct = node.size / total * 100 if total else 0
        text = (
            f"{'📁' if node.is_dir else '📄'} {node.name}\n"
            f"Размер: {format_size(node.size)} ({pct:.1f}%)\n"
            f"Путь: {node.path}"
        )
        if extra:
            text += f"\nСистемный файл: {extra}"

        x = event.x_root + 14
        y = event.y_root + 14

        self._tooltip = tk.Toplevel(self.root)
        self._tooltip.wm_overrideredirect(True)
        self._tooltip.wm_geometry(f"+{x}+{y}")
        self._tooltip.configure(bg="#00FF88")

        inner = tk.Frame(self._tooltip, bg="#111", padx=10, pady=6)
        inner.pack(padx=1, pady=1)

        tk.Label(inner, text=text, font=("Consolas", 9),
                 fg="#00FF88", bg="#111",
                 justify=tk.LEFT, wraplength=400).pack()

    def _hide_tooltip(self):
        """Уничтожает текущую всплывающую подсказку, если она существует.

        Сбрасывает :attr:`_tooltip` в ``None`` после уничтожения виджета.
        """
        if self._tooltip:
            self._tooltip.destroy()
            self._tooltip = None

    def _on_double_click(self, event):
        """Обрабатывает двойной клик по холсту тримапа.

        Определяет узел под курсором и, если это директория,
        вызывает :meth:`_drill_down` для перехода внутрь.

        Args:
            event: Объект события Tkinter с атрибутами ``x``, ``y``.
        """
        node = self._find_rect_at(event.x, event.y)
        if node and node.is_dir:
            self._drill_down(node)

    # Обработчики событий таблицы
    def _on_table_double_click(self, event):
        """Обрабатывает двойной клик по строке таблицы.

        Определяет выбранный элемент, находит соответствующий
        :class:`~models.FileNode` через :meth:`_find_node_by_path`
        и, если это директория, входит в неё.

        Args:
            event: Объект события Tkinter (не используется напрямую;
                выделение читается через :attr:`_tree`).
        """
        sel = self._tree.selection()
        if not sel:
            return
        iid = sel[0]
        node = self._find_node_by_path(iid)
        if node and node.is_dir:
            self._drill_down(node)

    def _find_node_by_path(self, path: str) -> Optional[FileNode]:
        """Находит дочерний узел текущей директории по его пути.

        Перебирает потомков :attr:`_current_node` и возвращает первый
        узел с совпадающим ``path``.

        Args:
            path (str): Полный путь искомого узла.

        Returns:
            Optional[FileNode]: Найденный узел или ``None``, если узел
            не является потомком текущего или :attr:`_current_node` не задан.
        """
        if self._current_node is None:
            return None
        for child in self._current_node.children:
            if child.path == path:
                return child
        return None

    # Таблица
    def _populate_table(self, node: FileNode):
        """Заполняет таблицу дочерними элементами указанного узла.

        Очищает все строки таблицы и добавляет новые в порядке,
        определённом :attr:`_sort_col` и :attr:`_sort_rev`. Каждая строка
        содержит иконку типа, имя, отформатированный размер и процент
        от суммарного размера директории.

        Args:
            node (FileNode): Узел, чьих потомков нужно отобразить в таблице.
        """
        self._tree.delete(*self._tree.get_children())
        if not node.children:
            return

        total = node.size or 1

        children = sorted(
            node.children,
            key=lambda c: c.size if self._sort_col == "size" else c.name.lower(),
            reverse=self._sort_rev
        )

        for child in children:
            pct  = child.size / total * 100
            icon = "▶" if child.is_dir else " "

            self._tree.insert(
                "", tk.END, iid=child.path,
                values=(
                    icon,
                    child.name,
                    format_size(child.size),
                    f"{pct:.1f}%",
                    child.extension,
                    child.file_type,
                    child.modified,
                    child.owner,
                    "YES" if child.is_system else "NO",
                    child.defender_status,
                ),
                tags=(child.path,)
            )

    def _sort_table(self, col: str):
        """Переключает сортировку таблицы по указанной колонке.

        Если ``col`` совпадает с активной колонкой :attr:`_sort_col`,
        инвертирует направление. Иначе переключается на новую колонку
        (размер — по убыванию, имя — по возрастанию) и перезаполняет таблицу.

        Args:
            col (str): Имя колонки для сортировки: ``"size"`` или ``"name"``.
        """
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col = col
            self._sort_rev = (col == "size")
        if self._current_node:
            self._populate_table(self._current_node)