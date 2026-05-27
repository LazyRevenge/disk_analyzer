"""
treemap.py
----------
Реализация алгоритма Squarified Treemap для визуализации иерархических данных.

Алгоритм разбивает прямоугольник на блоки, площадь которых пропорциональна
весу (размеру) соответствующего элемента, стремясь минимизировать соотношение
сторон каждого блока (то есть сделать блоки как можно более квадратными).

"""

from typing import Any


def squarify(items: list[tuple[Any, float]],
             x: float, y: float,
             w: float, h: float) -> list[tuple[Any, float, float, float, float]]:
    """Вычисляет раскладку тримапа для списка элементов с весами.

    Масштабирует веса элементов до суммарной площади прямоугольника
    и рекурсивно размещает их с помощью алгоритма Squarified Treemap.

    Args:
        items (list[tuple[Any, float]]): Список пар ``(объект, вес)``.
            Вес должен быть положительным числом, задающим относительный
            размер блока.
        x (float): Координата X левого верхнего угла области размещения.
        y (float): Координата Y левого верхнего угла области размещения.
        w (float): Ширина области размещения в пикселях.
        h (float): Высота области размещения в пикселях.

    Returns:
        list[tuple[Any, float, float, float, float]]: Список кортежей
        ``(объект, x, y, ширина, высота)`` — координаты и размеры
        прямоугольника для каждого элемента. Пустой список, если входные
        данные некорректны или площадь равна нулю.
    """
    if not items or w <= 0 or h <= 0:
        return []

    total = sum(s for _, s in items)
    if total == 0:
        return []

    sorted_items = sorted(items, key=lambda t: t[1], reverse=True)

    area = w * h
    scaled = [(node, size / total * area) for node, size in sorted_items]

    result = []
    _squarify(scaled, x, y, w, h, result)
    return result


def _worst_ratio(areas: list[float], stripe: float) -> float:
    """Вычисляет наихудшее соотношение сторон среди блоков текущей полосы.

    Для каждого блока в полосе рассчитывается отношение большей стороны
    к меньшей. Возвращается максимальное из таких значений — чем оно
    ближе к 1.0, тем «квадратнее» блоки.

    Args:
        areas (list[float]): Список масштабированных площадей блоков,
            которые предполагается разместить в текущей полосе.
        stripe (float): Длина полосы (ширина или высота в зависимости
            от ориентации прямоугольника).

    Returns:
        float: Наихудшее (максимальное) соотношение сторон.
        ``float('inf')`` если ``areas`` пуст, ``stripe`` равен нулю
        или длина хотя бы одного блока равна нулю.
    """
    if not areas or stripe == 0:
        return float('inf')

    s = sum(areas)
    thick = s / stripe

    worst = 0.0
    for a in areas:
        length = a / thick if thick > 0 else 0
        if length == 0:
            return float('inf')
        ratio = max(thick / length, length / thick)
        if ratio > worst:
            worst = ratio

    return worst


def _squarify(scaled: list[tuple[Any, float]],
              x: float, y: float,
              w: float, h: float,
              result: list):
    """Рекурсивно распределяет масштабированные блоки по прямоугольнику.

    Выбирает ориентацию полосы (горизонтальную или вертикальную) в
    зависимости от соотношения сторон текущей области, размещает одну
    полосу блоков, а затем рекурсивно обрабатывает оставшиеся элементы
    в уменьшенной области.

    Args:
        scaled (list[tuple[Any, float]]): Элементы с масштабированными
            площадями, отсортированные по убыванию площади.
        x (float): Координата X текущей области.
        y (float): Координата Y текущей области.
        w (float): Ширина текущей области.
        h (float): Высота текущей области.
        result (list): Накопительный список результатов; каждый вызов
            :func:`_place_strip` добавляет в него кортежи
            ``(объект, x, y, ширина, высота)``.
    """
    if not scaled:
        return

    if len(scaled) == 1:
        node, _ = scaled[0]
        result.append((node, x, y, w, h))
        return

    if w > h:
        _place_strip(scaled, x, y, w, h, result, vertical=True)
    else:
        _place_strip(scaled, x, y, w, h, result, vertical=False)


def _place_strip(scaled: list[tuple[Any, float]],
                 x: float, y: float,
                 w: float, h: float,
                 result: list,
                 vertical: bool):
    """Размещает одну полосу блоков и рекурсивно обрабатывает остаток.

    Жадно добавляет элементы в текущую полосу до тех пор, пока
    :func:`_worst_ratio` не начнёт ухудшаться. Затем фиксирует
    координаты для блоков полосы и рекурсивно вызывает :func:`_squarify`
    для оставшихся элементов в свободной части прямоугольника.

    Args:
        scaled (list[tuple[Any, float]]): Элементы с масштабированными
            площадями для размещения.
        x (float): Координата X текущей области.
        y (float): Координата Y текущей области.
        w (float): Ширина текущей области.
        h (float): Высота текущей области.
        result (list): Список для записи результирующих прямоугольников.
        vertical (bool): Если ``True`` — полоса вертикальная (блоки
            укладываются сверху вниз вдоль левого края); если ``False``
            — горизонтальная (блоки укладываются слева направо вдоль
            верхнего края).

    Note:
        Последний блок полосы растягивается до границы области, чтобы
        устранить накопленные ошибки округления с плавающей точкой.
    """
    stripe = h if vertical else w

    row_areas = []
    row_nodes = []
    best = float('inf')

    for node, area in scaled:
        candidate = row_areas + [area]
        ratio = _worst_ratio(candidate, stripe)

        if row_areas and ratio > best:
            break

        row_areas.append(area)
        row_nodes.append(node)
        best = ratio

    row_sum = sum(row_areas)
    thick = row_sum / stripe if stripe > 0 else 0

    offset = 0.0
    n = len(row_areas)
    for i in range(n):
        node = row_nodes[i]
        area = row_areas[i]
        length = area / thick if thick > 0 else 0

        if vertical:
            rx, ry, rw, rh = x, y + offset, thick, length
            if i == n - 1:
                rh = (y + h) - ry
        else:
            rx, ry, rw, rh = x + offset, y, length, thick
            if i == n - 1:
                rw = (x + w) - rx

        result.append((node, rx, ry, rw, rh))
        offset += length

    remaining = scaled[n:]
    if not remaining:
        return

    if vertical:
        _squarify(remaining, x + thick, y, w - thick, h, result)
    else:
        _squarify(remaining, x, y + thick, w, h - thick, result)