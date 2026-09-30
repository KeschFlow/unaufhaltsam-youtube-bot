"""Read visible box positions from a PNG screenshot; never inspect game state."""

from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path

from PIL import Image


def _components(mask, width, height):
    visited = bytearray(width * height)
    result = []
    for start in range(width * height):
        if not mask[start] or visited[start]:
            continue
        queue = deque((start,))
        visited[start] = 1
        left = right = start % width
        top = bottom = start // width
        area = 0
        while queue:
            index = queue.popleft()
            x, y = index % width, index // width
            area += 1
            left, right = min(left, x), max(right, x)
            top, bottom = min(top, y), max(bottom, y)
            for next_index in (index - 1, index + 1, index - width, index + width):
                if next_index < 0 or next_index >= width * height or visited[next_index] or not mask[next_index]:
                    continue
                nx, ny = next_index % width, next_index // width
                if abs(nx - x) + abs(ny - y) != 1:
                    continue
                visited[next_index] = 1
                queue.append(next_index)
        result.append((left, top, right, bottom, area))
    return result


def observe(image_path: str, centers=None):
    image = Image.open(image_path).convert("RGB")
    width, height = image.size
    scale = 3
    small = image.resize((width // scale, height // scale))
    sw, sh = small.size
    pixels = list(small.get_flattened_data())
    # White rectangular box interiors; exclude page labels by shape and area.
    white = bytearray(1 if min(rgb) > 205 and max(rgb) - min(rgb) < 35 else 0 for rgb in pixels)
    boxes = []
    for l, t, r, b, area in _components(white, sw, sh):
        bw, bh = (r - l + 1) * scale, (b - t + 1) * scale
        if 48 <= bw <= 105 and 48 <= bh <= 105 and area * scale * scale >= 800 and t * scale > 300:
            boxes.append([round((l + r + 1) * scale / 2), round((t + b + 1) * scale / 2)])
    # The marked card has a large purple fill. Purple headings and controls
    # are rejected by the square bounds and minimum height.
    purple = bytearray(1 if r > 65 and b > 60 and g < 85 and r > g * 1.5 and b > g * 1.4 else 0 for r, g, b in pixels)
    focus = None
    for l, t, r, b, area in _components(purple, sw, sh):
        bw, bh = (r - l + 1) * scale, (b - t + 1) * scale
        if 55 <= bw <= 120 and 55 <= bh <= 120 and area * scale * scale >= 1500 and t * scale > 300:
            focus = [round((l + r + 1) * scale / 2), round((t + b + 1) * scale / 2)]
    cells = sorted(boxes + ([focus] if focus else []), key=lambda xy: (round(xy[1] / 75), xy[0]))
    ratios = []
    for x, y in centers or []:
        crop = image.crop((max(0, x - 22), max(0, y - 22), min(width, x + 22), min(height, y + 22)))
        sample = list(crop.get_flattened_data())
        ratios.append(sum(min(rgb) > 205 and max(rgb) - min(rgb) < 35 for rgb in sample) / max(1, len(sample)))
    return {"cells": cells, "focus": focus, "white_ratios": ratios, "image_size": [width, height]}


if __name__ == "__main__":
    path = sys.argv[1]
    known = json.loads(sys.argv[2]) if len(sys.argv) > 2 else None
    print(json.dumps(observe(path, known)))
