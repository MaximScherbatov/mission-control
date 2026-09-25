from __future__ import annotations

import heapq
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from shapely.geometry import LineString, Point, Polygon, shape
from shapely.ops import transform, unary_union

from .airspace import bbox_intersects


def _aware(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(str(value)[:10])
        except ValueError:
            return None
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def _time_relevant(zone: dict[str, Any], target: datetime, window_end: datetime | None = None) -> tuple[bool, str]:
    properties = zone.get("properties", {})
    start = _aware(zone.get("valid_from") or properties.get("effective_from"))
    end = _aware(zone.get("valid_until") or properties.get("official_valid_until"))
    date_range = properties.get("fpln_schedule_date_range")
    if isinstance(date_range, list) and len(date_range) >= 2:
        start = _aware(date_range[0]) or start
        end = _aware(date_range[1]) or end
    window_end = window_end or target
    if start and window_end < start:
        return False, "outside_validity"
    if end and target > end.replace(hour=23, minute=59, second=59):
        return False, "outside_validity"
    if properties.get("active_at_retrieval") is True:
        return True, "source_active"
    return True, "conservative_schedule"


def _altitude_relevant(zone: dict[str, Any], altitude_m: float) -> tuple[bool, str]:
    value = zone.get("properties", {}).get("fpln_altitude_range")
    if isinstance(value, list) and len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
        # The aircraft passes through lower heights during climb and descent.
        # Cruise altitude alone cannot exclude a vertically limited zone.
        return float(value[0]) <= altitude_m and float(value[1]) >= 0, "structured_range_climb_to_cruise"
    return True, "conservative_vertical"


def _polygons(geometry):
    if isinstance(geometry, Polygon):
        return [geometry]
    if hasattr(geometry, "geoms"):
        return [polygon for item in geometry.geoms for polygon in _polygons(item)]
    return []


def _safe_segment(left: tuple[float, float], right: tuple[float, float], interior) -> bool:
    segment = LineString([left, right])
    return segment.length > 0 and not segment.crosses(interior) and not segment.within(interior) and segment.intersection(interior).length < 0.05


def avoid_line(line: LineString, obstacles, *, _box_fallback: bool = True,
               _search_margin: float | None = None) -> tuple[LineString, bool, bool]:
    """Return the shortest visibility-graph detour around buffered polygons.

    The third result is true when a conflict existed but no safe detour could be
    proven.  Callers must expose that state instead of silently accepting it.
    """
    if obstacles is None or obstacles.is_empty or not line.intersects(obstacles):
        return line, False, False
    start, end = tuple(line.coords[0]), tuple(line.coords[-1])
    if obstacles.contains(Point(start)) or obstacles.contains(Point(end)):
        return line, False, True
    # Include nearby polygons as well as the one hit by the direct chord.  A
    # detour around the first polygon may otherwise be routed straight through
    # its neighbour because that neighbour did not intersect the original
    # line's (sometimes very narrow) envelope.
    search_margin = _search_margin or max(500.0, min(5000.0, line.length * 0.2))
    relevant = [polygon for polygon in _polygons(obstacles) if polygon.distance(line) <= search_margin]
    if not relevant:
        return line, False, False
    combined = unary_union(relevant)
    # The regulatory clearance is already part of ``obstacles``.  A small
    # additional navigation guard prevents the visibility graph from placing
    # a route exactly on that boundary, which is both numerically fragile and
    # looks like a zone intersection on the map.
    guards = []
    for polygon in relevant:
        tolerance = max(0.5, math.sqrt(max(polygon.area, 1.0)) / 360)
        candidate = polygon.buffer(3.0, join_style="mitre")
        # Dropping every Nth vertex can cut directly through a concave zone.
        # Simplify only when needed, then inflate and prove containment before
        # allowing those vertices into the graph.
        while len(candidate.exterior.coords) > 90 and tolerance < 500:
            approximate = polygon.simplify(tolerance, preserve_topology=True)
            expanded = approximate.buffer(tolerance + 3.0, join_style="mitre")
            if expanded.covers(polygon):
                candidate = expanded
            tolerance *= 2
        guards.append(candidate)
    guarded = unary_union(guards)
    if guarded.contains(Point(start)) or guarded.contains(Point(end)):
        guarded = combined
    interior = guarded.buffer(-0.05)
    vertices: list[tuple[float, float]] = [start, end]
    for polygon in _polygons(guarded):
        coordinates = list(polygon.exterior.coords)[:-1]
        vertices.extend((float(x), float(y)) for x, y in coordinates)
    # De-duplicate rounded boundary vertices to keep the graph small.
    vertices = list(dict.fromkeys((round(x, 3), round(y, 3)) for x, y in vertices))
    start_index, end_index = vertices.index((round(start[0], 3), round(start[1], 3))), vertices.index((round(end[0], 3), round(end[1], 3)))
    graph: list[list[tuple[int, float]]] = [[] for _ in vertices]
    for left_index, left in enumerate(vertices):
        for right_index in range(left_index + 1, len(vertices)):
            right = vertices[right_index]
            if _safe_segment(left, right, interior):
                distance = math.dist(left, right)
                graph[left_index].append((right_index, distance))
                graph[right_index].append((left_index, distance))
    distances = [float("inf")] * len(vertices)
    previous: list[int | None] = [None] * len(vertices)
    distances[start_index] = 0.0
    queue = [(0.0, start_index)]
    while queue:
        distance, node = heapq.heappop(queue)
        if distance != distances[node]:
            continue
        if node == end_index:
            break
        for neighbor, weight in graph[node]:
            candidate = distance + weight
            if candidate < distances[neighbor]:
                distances[neighbor], previous[neighbor] = candidate, node
                heapq.heappush(queue, (candidate, neighbor))
    if not math.isfinite(distances[end_index]):
        if _box_fallback:
            # Simplifying a rounded regulatory buffer can place graph vertices
            # just inside the curved edge, disconnecting the visibility graph.
            # An enclosing rectangle is a conservative, small-vertex fallback:
            # it may lengthen a detour, but can never cut through the zone.
            enclosure = unary_union([polygon.minimum_rotated_rectangle for polygon in relevant])
            if not enclosure.contains(Point(start)) and not enclosure.contains(Point(end)):
                fallback, detoured, unresolved = avoid_line(line, enclosure, _box_fallback=False)
                if not unresolved and fallback.distance(combined) >= 0.05:
                    return fallback, detoured, False
        return line, False, True
    indexes = []
    cursor: int | None = end_index
    while cursor is not None:
        indexes.append(cursor)
        cursor = previous[cursor]
    coordinates = [vertices[index] for index in reversed(indexes)]
    result = LineString(coordinates)
    # Never advertise a detour unless it is verifiably outside the protected
    # geometry.  This catches invalid/touching graph paths before they reach
    # the operator UI.
    if result.crosses(combined) or result.within(combined) or result.intersection(combined).length >= 0.05:
        return line, False, True
    # The shortest bypass around the first zone may approach another zone
    # farther from the direct chord. Expand the graph instead of presenting a
    # locally valid route as globally safe.
    if result.intersects(obstacles) and result.intersection(obstacles).length >= 0.05:
        if search_margin < max(20000.0, line.length):
            return avoid_line(line, obstacles, _box_fallback=_box_fallback,
                              _search_margin=search_margin * 2)
        return line, False, True
    return result, len(coordinates) > 2, False


def prepare_airspace_context(request, zones: list[dict[str, Any]], geographic, to_metric, mission_metric, route_region_bounds) -> dict[str, Any]:
    if not request.airspace_check:
        return {
            "assessment": {"status": "not_checked", "operation_mode": "unknown", "conflicts": [], "authorities": [], "messages": ["Проверка воздушного пространства отключена."]},
            "coverage_obstacles": None, "transit_obstacles": None,
        }
    target = request.earliest_start or datetime.now(timezone.utc)
    target = target.replace(tzinfo=target.tzinfo or timezone.utc).astimezone(timezone.utc)
    deadline = getattr(request, "deadline", None)
    window_end = (deadline.replace(tzinfo=deadline.tzinfo or timezone.utc).astimezone(timezone.utc)
                  if deadline else target + timedelta(hours=24))
    clearances = {
        "prohibited": request.prohibited_clearance_m,
        "danger": request.danger_clearance_m,
        "obstacle": request.obstacle_clearance_m,
        "settlement": getattr(request, "settlement_clearance_m", 100),
        "custom": request.obstacle_clearance_m,
    }
    coverage_obstacles = []
    transit_obstacles = []
    conflicts = []
    authorities = []
    region_bbox = list(route_region_bounds)
    center = geographic.centroid
    for zone in zones:
        if not zone.get("enabled", True) or not bbox_intersects(zone["bbox"], region_bbox):
            continue
        try:
            zone_geographic = shape(zone["geometry"])
        except (TypeError, ValueError):
            continue
        if zone["category"] == "orvd":
            if zone_geographic.intersects(geographic) or zone_geographic.covers(center):
                authorities.append({
                    "id": zone["id"], "code": zone.get("code"), "name": zone["name"],
                    "source_name": zone["source_name"], "area_rank": zone_geographic.area,
                })
            continue
        if zone["category"] not in clearances:
            continue
        altitude_active, altitude_basis = _altitude_relevant(zone, request.max_flight_altitude_m)
        time_active, time_basis = _time_relevant(zone, target, window_end)
        if not altitude_active or not time_active:
            continue
        metric_zone = transform(to_metric, zone_geographic)
        clearance = clearances[zone["category"]]
        protected = metric_zone.buffer(clearance) if clearance else metric_zone
        mission_conflict = mission_metric.intersects(protected)
        item = {
            "id": zone["id"], "category": zone["category"], "code": zone.get("code"), "name": zone["name"],
            "clearance_m": clearance, "mission_intersection": mission_conflict,
            "altitude_basis": altitude_basis, "time_basis": time_basis,
            "vertical_definition": zone.get("vertical_definition"), "schedule": zone.get("schedule"),
            "source_name": zone["source_name"], "geometry": zone["geometry"],
        }
        if mission_conflict:
            conflicts.append(item)
        # Every active protected zone is a transit obstacle, even if it also
        # intersects the requested survey area. The optimizer rejects such a
        # survey rather than silently flying through it or dropping coverage.
        if zone["category"] in {"obstacle", "custom"}:
            coverage_obstacles.append(protected)
            transit_obstacles.append(protected)
        else:
            transit_obstacles.append(protected)
    authorities.sort(key=lambda item: item["area_rank"])
    for authority in authorities:
        authority.pop("area_rank", None)
    prohibited = [item for item in conflicts if item["category"] == "prohibited"]
    danger = [item for item in conflicts if item["category"] == "danger"]
    obstacles = [item for item in conflicts if item["category"] in {"obstacle", "custom"}]
    settlements = [item for item in conflicts if item["category"] == "settlement"]
    if settlements:
        status, operation_mode = "permission_required", "permission"
    elif prohibited:
        status, operation_mode = "permission_required", "permission"
    elif danger:
        status, operation_mode = "permission_required", "permission"
    elif obstacles:
        status, operation_mode = "adjustment_required", "notification"
    elif authorities:
        status, operation_mode = "notification_required", "notification"
    else:
        status, operation_mode = "clear", "none"
    messages = []
    if prohibited:
        messages.append(f"Территория пересекает {len(prohibited)} запретных зон: расчёт не является разрешением на полёт.")
    if danger:
        messages.append(f"В зоне работ обнаружено {len(danger)} опасных зон; требуется проверка режима и согласование.")
    if obstacles:
        messages.append(f"Галсы и переходы перестроены с учётом {len(obstacles)} препятствий.")
    if settlements:
        messages.append(f"Контур затрагивает {len(settlements)} населённых пунктов с учётом проектного отступа; без проверки границ и разрешений расчёт нельзя считать готовым к вылету.")
    if authorities:
        messages.append(f"Определена зона ОрВД: {authorities[0]['name']}. Контакт и режим взаимодействия нужно подтвердить перед вылетом.")
    if not messages:
        messages.append("По загруженным наборам прямые конфликты не обнаружены; актуальность данных всё равно проверяется перед вылетом.")
    return {
        "assessment": {
            "status": status, "operation_mode": operation_mode, "checked_at": datetime.now(timezone.utc).isoformat(),
            "target_time": target.isoformat(), "conflicts": conflicts, "authorities": authorities,
            "clearances_m": clearances, "messages": messages,
        },
        "coverage_obstacles": unary_union(coverage_obstacles) if coverage_obstacles else None,
        "transit_obstacles": unary_union(transit_obstacles) if transit_obstacles else None,
    }
