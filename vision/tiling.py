"""Deterministic overlapping raster windows shared by train and inference."""
from __future__ import annotations


def tile_windows(*, width: int, height: int, tile_size: int | tuple[int, int],
                 overlap: int) -> list[tuple[int, int]]:
    """Return top-left origins that cover a raster with a small edge overlap."""
    if isinstance(tile_size, tuple) and len(tile_size) == 2:
        tile_width, tile_height = tile_size
    else:
        tile_width = tile_height = tile_size
    dimensions = (width, height, tile_width, tile_height)
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
           for value in dimensions):
        raise ValueError("raster and tile dimensions must be positive integers")
    if isinstance(overlap, bool) or not isinstance(overlap, int):
        raise ValueError("overlap must be an integer")
    if overlap < 0 or overlap >= min(tile_width, tile_height):
        raise ValueError("overlap must be non-negative and smaller than both tile dimensions")

    def origins(dimension: int, tile: int) -> list[int]:
        if dimension <= tile:
            return [0]
        step = tile - overlap
        result = list(range(0, dimension - tile + 1, step))
        last = dimension - tile
        if result[-1] != last:
            if len(result) > 1 and last - result[-1] < overlap:
                result[-1] = last
            else:
                result.append(last)
        return result

    return [(x, y) for y in origins(height, tile_height) for x in origins(width, tile_width)]
