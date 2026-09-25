"""Forward-only, curvature-bounded planar connectors for preliminary UAV plans.

The six circular-arc/straight families are the Dubins candidates. Coordinates
must be in a local metric CRS. This is still a horizontal planning path, not
an autopilot command: climb, bank-angle limits and terrain need separate checks.
"""

import math
from typing import Iterable

from shapely.geometry import LineString


TAU = 2 * math.pi


def _mod(angle: float) -> float:
    return angle % TAU


def _candidates(distance: float, alpha: float, beta: float) -> Iterable[tuple[str, tuple[float, float, float]]]:
    sa, sb = math.sin(alpha), math.sin(beta)
    ca, cb = math.cos(alpha), math.cos(beta)
    cosine = ca * cb + sa * sb
    d = distance

    value = 2 + d * d - 2 * (cosine - d * (sa - sb))
    if value >= -1e-9:
        theta = math.atan2(cb - ca, d + sa - sb)
        yield "LSL", (_mod(-alpha + theta), math.sqrt(max(0, value)), _mod(beta - theta))

    value = 2 + d * d - 2 * (cosine - d * (sb - sa))
    if value >= -1e-9:
        theta = math.atan2(ca - cb, d - sa + sb)
        yield "RSR", (_mod(alpha - theta), math.sqrt(max(0, value)), _mod(-beta + theta))

    value = d * d - 2 + 2 * (cosine - d * (sa + sb))
    if value >= -1e-9:
        length = math.sqrt(max(0, value))
        theta = math.atan2(ca + cb, d - sa - sb) - math.atan2(2, length)
        yield "RSL", (_mod(alpha - theta), length, _mod(beta - theta))

    value = d * d - 2 + 2 * (cosine + d * (sa + sb))
    if value >= -1e-9:
        length = math.sqrt(max(0, value))
        theta = math.atan2(-ca - cb, d + sa + sb) - math.atan2(-2, length)
        yield "LSR", (_mod(-alpha + theta), length, _mod(-beta + theta))

    value = (6 - d * d + 2 * (cosine + d * (sa - sb))) / 8
    if -1 <= value <= 1:
        middle = TAU - math.acos(value)
        theta = math.atan2(ca - cb, d - sa + sb)
        first = _mod(alpha - theta + middle / 2)
        yield "RLR", (first, middle, _mod(alpha - beta - first + middle))

    value = (6 - d * d + 2 * (cosine - d * (sa - sb))) / 8
    if -1 <= value <= 1:
        middle = TAU - math.acos(value)
        theta = math.atan2(-ca + cb, d + sa - sb)
        first = _mod(-alpha + theta + middle / 2)
        yield "LRL", (first, middle, _mod(beta - alpha - first + middle))


def _sample(
    start: tuple[float, float], heading: float, radius: float,
    family: str, lengths: tuple[float, float, float],
) -> LineString:
    points = [start]
    x, y = start
    for kind, length in zip(family, lengths):
        if length <= 1e-10:
            continue
        initial_x, initial_y, initial_heading = x, y, heading
        distance = length * radius
        steps = max(1, math.ceil(distance / min(18.0, max(6.0, radius * .18))))
        for index in range(1, steps + 1):
            fraction = index / steps
            if kind == "S":
                x = initial_x + distance * fraction * math.cos(initial_heading)
                y = initial_y + distance * fraction * math.sin(initial_heading)
            else:
                direction = 1 if kind == "L" else -1
                new_heading = initial_heading + direction * length * fraction
                x = initial_x + radius / direction * (math.sin(new_heading) - math.sin(initial_heading))
                y = initial_y - radius / direction * (math.cos(new_heading) - math.cos(initial_heading))
            points.append((x, y))
        heading = initial_heading + (length if kind == "L" else -length if kind == "R" else 0)
    return LineString(points)


def dubins_connectors(
    start: tuple[float, float], start_heading: tuple[float, float],
    end: tuple[float, float], end_heading: tuple[float, float],
    radius_m: float,
    *, allow_three_arc: bool = True,
) -> Iterable[LineString]:
    """Yield feasible path families shortest first, sampling only as needed."""
    if radius_m <= 0:
        raise ValueError("A positive minimum turn radius is required")
    dx, dy = end[0] - start[0], end[1] - start[1]
    bearing = math.atan2(dy, dx)
    alpha = _mod(math.atan2(start_heading[1], start_heading[0]) - bearing)
    beta = _mod(math.atan2(end_heading[1], end_heading[0]) - bearing)
    options = sorted(
        (item for item in _candidates(math.hypot(dx, dy) / radius_m, alpha, beta)
         if allow_three_arc or 'S' in item[0]),
        key=lambda item: sum(item[1]),
    )
    for family, lengths in options:
        line = _sample(start, math.atan2(start_heading[1], start_heading[0]), radius_m, family, lengths)
        if math.dist(line.coords[-1], end) <= .01:
            coordinates = list(line.coords)
            coordinates[-1] = end
            yield LineString(coordinates)
