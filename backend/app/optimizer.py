from __future__ import annotations

import math
from copy import deepcopy
from itertools import chain, combinations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from pyproj import CRS, Geod, Transformer
from shapely import affinity
from shapely.geometry import LineString, MultiLineString, Point, mapping, shape
from shapely.ops import substring, transform, unary_union

from .catalog import BASES, OPTICS, UAVS
from .airspace_planning import avoid_line, prepare_airspace_context
from .curvature_path import dubins_connectors
from .models import MissionRequest


@dataclass
class CompatiblePair:
    uav: dict[str, Any]
    optic: dict[str, Any]
    altitude_m: float
    swath_m: float
    footprint_length_m: float
    achieved_gsd_cm_px: float
    line_spacing_m: float
    trigger_spacing_m: float
    trigger_interval_s: float
    required_fps: float
    speed_kmh: float
    productivity_km2_h: float


def _projection(geometry):
    centroid = geometry.centroid
    zone = int((centroid.x + 180) // 6) + 1
    epsg = 32600 + zone if centroid.y >= 0 else 32700 + zone
    to_metric = Transformer.from_crs("EPSG:4326", CRS.from_epsg(epsg), always_xy=True).transform
    to_wgs84 = Transformer.from_crs(CRS.from_epsg(epsg), "EPSG:4326", always_xy=True).transform
    return to_metric, to_wgs84, CRS.from_epsg(epsg)


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371008.8 * 2 * math.asin(min(1, math.sqrt(h)))


AUTO_MOBILE_DEPLOYMENT_RADIUS_KM = 25.0
RADIO_HORIZON_PLANNING_FACTOR = 0.8
UAV_MOBILIZATION_COST_RUB = 2800
SORTIE_PREPARATION_COST_RUB = 950
WGS84_GEOD = Geod(ellps='WGS84')
# Computational safety bounds only; operational limits are explicit request
# constraints and the endurance / maintenance state of each aircraft.
MAX_OPERATION_WINDOW_MIN = 30 * 24 * 60
MAX_SORTIES_PER_UAV = 100


def _available_minutes(request: MissionRequest) -> float | None:
    if request.deadline is None:
        return None
    start = request.earliest_start or datetime.now(timezone.utc)
    if request.deadline.tzinfo is None or start.tzinfo is None:
        raise ValueError('Срок и время старта должны содержать часовой пояс.')
    minutes = (request.deadline - start).total_seconds() / 60
    if minutes <= 0:
        raise ValueError('Срок окончания должен быть позже времени старта.')
    return minutes


def _direct_radio_limit_km(request: MissionRequest, altitude_m: float) -> float:
    # ITU-R SM.2012-2 radio horizon, with a scenario planning margin. This is
    # not a terrain/obstacle or link-budget analysis.
    horizon_km = 4.14 * (math.sqrt(request.ground_antenna_height_m) + math.sqrt(max(0.0, altitude_m)))
    return min(request.radio_equipment_range_km, horizon_km * RADIO_HORIZON_PLANNING_FACTOR)


def _phase_trajectory(
    coordinates: list[tuple[float, float]],
    start_at: datetime,
    duration_s: float,
    start_altitude_m: float,
    end_altitude_m: float | None = None,
) -> list[dict[str, Any]]:
    """Create a deterministic time/position series without pretending it is telemetry."""
    end_altitude = start_altitude_m if end_altitude_m is None else end_altitude_m
    if len(coordinates) == 1:
        coordinates = [coordinates[0], coordinates[0]]
    lengths = [_haversine_m(coordinates[index - 1], coordinates[index]) for index in range(1, len(coordinates))]
    total = sum(lengths)
    travelled = 0.0
    points: list[dict[str, Any]] = []
    for index, (lon, lat) in enumerate(coordinates):
        if index:
            travelled += lengths[index - 1]
        fraction = travelled / total if total > 0 else index / max(1, len(coordinates) - 1)
        timestamp = start_at + timedelta(seconds=duration_s * fraction)
        altitude = start_altitude_m + (end_altitude - start_altitude_m) * fraction
        points.append({
            "time": timestamp.isoformat(),
            "lon": round(float(lon), 8),
            "lat": round(float(lat), 8),
            "altitude_m": round(altitude, 1),
        })
    return points


def _planned_flight_phases(
    *,
    start_at: datetime,
    departure_offset_min: float,
    altitude_m: float,
    speed_kmh: float,
    phases: list[dict[str, Any]],
    base_coordinate: tuple[float, float],
    coverage_segments: list[LineString],
    transfer_segments: list[LineString],
    sortie_starts: set[int],
    to_wgs84,
    ground_speed_mps: float,
    runway_departure: LineString | None = None,
    runway_arrival: LineString | None = None,
) -> list[dict[str, Any]]:
    """Turn aggregate estimates into ordered, georeferenced planning phases."""
    duration_by_name = {phase["name"]: max(0.0, float(phase["minutes"]) * 60) for phase in phases}
    coverage_total = duration_by_name.get("Съёмка", 0.0)
    turns_total = duration_by_name.get("Развороты", 0.0)
    coverage_length = sum(segment.length for segment in coverage_segments)
    sortie_ends = {
        index for index in range(len(coverage_segments))
        if index + 1 == len(coverage_segments) or index + 1 in sortie_starts
    }
    prefix_segments: list[LineString] = []
    return_segments: dict[int, LineString] = {}
    transfer_index = 0
    for index in range(len(coverage_segments)):
        prefix_segments.append(transfer_segments[transfer_index])
        transfer_index += 1
        if index in sortie_ends:
            return_segments[index] = transfer_segments[transfer_index]
            transfer_index += 1
    internal_transfers = [prefix_segments[index] for index in range(1, len(prefix_segments)) if index not in sortie_starts]
    turn_length = sum(segment.length for segment in internal_transfers)
    wgs_base = transform(to_wgs84, Point(base_coordinate))
    base = (float(wgs_base.x), float(wgs_base.y))
    cursor = start_at
    sequence = 0
    result: list[dict[str, Any]] = []

    def append_phase(
        code: str,
        name: str,
        duration_s: float,
        coordinates: list[tuple[float, float]],
        *,
        payload_active: bool = False,
        start_altitude: float = altitude_m,
        end_altitude: float | None = None,
    ) -> None:
        nonlocal cursor, sequence
        if duration_s <= 0:
            return
        sequence += 1
        phase_start = cursor
        phase_end = cursor + timedelta(seconds=duration_s)
        trajectory = _phase_trajectory(coordinates, phase_start, duration_s, start_altitude, end_altitude)
        geometry = {
            "type": "Point" if len(set(coordinates)) == 1 else "LineString",
            "coordinates": list(coordinates[0]) if len(set(coordinates)) == 1 else [list(point) for point in coordinates],
        }
        result.append({
            "id": f"phase-{sequence:03d}", "sequence": sequence, "type": code, "name": name,
            "start_at": phase_start.isoformat(), "end_at": phase_end.isoformat(),
            "duration_s": round(duration_s, 1), "planned_speed_kmh": round(speed_kmh, 1),
            "start_altitude_m": round(start_altitude, 1),
            "end_altitude_m": round(start_altitude if end_altitude is None else end_altitude, 1),
            "payload_active": payload_active, "geometry": geometry, "trajectory": trajectory,
        })
        cursor = phase_end

    sortie_count = max(1, len(sortie_starts))
    append_phase("HOLD", "Слот временного разведения", departure_offset_min * 60, [base], start_altitude=0, end_altitude=0)
    for index, coverage in enumerate(coverage_segments):
        prefix = prefix_segments[index]
        if index in sortie_starts:
            climb_s = duration_by_name.get("Набор высоты", 0) / sortie_count
            # The aircraft keeps climbing during its curved departure. A
            # runway-length straight is only needed to clear the runway, not
            # to gain the entire survey altitude before the first turn.
            climb_end = min(prefix.length, max(
                runway_departure.length if runway_departure is not None else 0.0,
                ground_speed_mps * climb_s,
            ))
            climb_line = transform(to_wgs84, substring(prefix, 0, climb_end))
            outbound_line = transform(to_wgs84, substring(prefix, climb_end, prefix.length))
            append_phase("CLIMB", "Взлёт и набор высоты", duration_by_name.get("Набор высоты", 0) / sortie_count,
                         list(climb_line.coords) if isinstance(climb_line, LineString) else [base], start_altitude=0, end_altitude=altitude_m)
            outbound_s = max(0.0, prefix.length / ground_speed_mps - climb_s)
            if isinstance(outbound_line, LineString):
                append_phase("OUTBOUND", "Перелёт к зоне работ", outbound_s, list(outbound_line.coords))
        else:
            prefix_line = transform(to_wgs84, prefix)
            turn_share = prefix.length / turn_length if turn_length else 1 / max(1, len(internal_transfers))
            append_phase("TURN", f"Разворот к галсу {index + 1}", turns_total * turn_share, list(prefix_line.coords))
        line = transform(to_wgs84, coverage)
        share = coverage.length / coverage_length if coverage_length else 1 / max(1, len(coverage_segments))
        append_phase("SURVEY", f"Съёмочный галс {index + 1}", coverage_total * share, list(line.coords), payload_active=True)
        if index in sortie_ends:
            inbound = return_segments[index]
            landing_s = duration_by_name.get("Снижение и посадка", 0) / sortie_count
            landing_length = min(inbound.length, max(
                runway_arrival.length if runway_arrival is not None else 0.0,
                ground_speed_mps * landing_s,
            ))
            landing_start = inbound.length - landing_length
            inbound_line = transform(to_wgs84, substring(inbound, 0, landing_start))
            landing_line = transform(to_wgs84, substring(inbound, landing_start, inbound.length))
            inbound_s = max(0.0, inbound.length / ground_speed_mps - landing_s)
            if isinstance(inbound_line, LineString):
                append_phase("INBOUND", "Возврат к месту посадки", inbound_s, list(inbound_line.coords))
            append_phase("LANDING", "Снижение и посадка", duration_by_name.get("Снижение и посадка", 0) / sortie_count,
                         list(landing_line.coords) if isinstance(landing_line, LineString) else [base],
                         start_altitude=altitude_m, end_altitude=0)
            if index + 1 < len(coverage_segments):
                append_phase("TURNAROUND", "Межполётное обслуживание",
                             duration_by_name.get("Обслуживание между вылетами", 0) / max(1, sortie_count - 1),
                             [base], start_altitude=0, end_altitude=0)
    return result


def _best_pair(uav: dict[str, Any], request: MissionRequest) -> CompatiblePair | None:
    if uav['status'] != 'ready':
        return None
    if uav.get('maintenance_metric') in {'flight_hours', 'engine_hours'} and uav['maintenance_due_hours'] <= 2:
        return None
    if uav.get('maintenance_metric') == 'flights' and uav.get('flights_since_maintenance') is not None and uav['flights_since_maintenance'] >= uav['maintenance_interval']:
        return None
    best: CompatiblePair | None = None
    for optic in OPTICS:
        survey_speed_mps = uav['cruise_speed_kmh'] / 3.6
        if request.payload_id and optic["id"] != request.payload_id:
            continue
        if request.survey_type not in optic["spectrums"] or optic["mass_kg"] > uav["payload_kg"]:
            continue
        if optic.get("compatible_uav_ids") and uav["id"] not in optic["compatible_uav_ids"]:
            continue
        if optic.get("fixed_swath_m"):
            altitude = min(request.max_flight_altitude_m, uav["max_altitude_m"])
            swath = float(optic["fixed_swath_m"])
            footprint_length = 0
            achieved_gsd = 0.0
            trigger_spacing = 0
            required_fps = 0
        else:
            gsd_m = request.gsd_cm_px / 100
            required_altitude = gsd_m * optic["focal_length_mm"] * optic["resolution_width_px"] / optic["sensor_width_mm"]
            altitude = min(required_altitude, request.max_flight_altitude_m, uav["max_altitude_m"])
            swath = altitude * optic["sensor_width_mm"] / optic["focal_length_mm"]
            footprint_length = altitude * optic["sensor_height_mm"] / optic["focal_length_mm"]
            achieved_gsd = altitude * optic["sensor_width_mm"] / optic["focal_length_mm"] / optic["resolution_width_px"] * 100
            trigger_spacing = max(1.0, footprint_length * (1 - request.forward_overlap))
            # Trigger rate designed for tailwind; duration uses headwind bound below.
            survey_speed_mps = min(survey_speed_mps, trigger_spacing * optic['fps'] - request.wind_speed_mps)
            minimum_speed = 60 / 3.6 if uav['type'] == 'fixed_wing' else 2.0
            if survey_speed_mps < minimum_speed:
                continue
            required_fps = (survey_speed_mps + request.wind_speed_mps) / trigger_spacing
        if altitude > uav["max_altitude_m"] or altitude < 25:
            continue
        spacing = request.survey_line_spacing_m if optic.get('fixed_swath_m') else swath * (1 - request.side_overlap)
        productivity = spacing * survey_speed_mps * 3600 / 1_000_000
        pair = CompatiblePair(
            uav=uav, optic=optic, altitude_m=altitude, swath_m=swath,
            footprint_length_m=footprint_length, achieved_gsd_cm_px=achieved_gsd,
            line_spacing_m=spacing, trigger_spacing_m=trigger_spacing,
            trigger_interval_s=1 / max(required_fps, .001), required_fps=required_fps,
            productivity_km2_h=productivity, speed_kmh=survey_speed_mps * 3.6,
        )
        if best is None or pair.productivity_km2_h > best.productivity_km2_h:
            best = pair
    return best


def _line_parts(geometry) -> list[LineString]:
    if isinstance(geometry, LineString):
        return [geometry]
    if isinstance(geometry, MultiLineString):
        return list(geometry.geoms)
    if hasattr(geometry, "geoms"):
        return [g for item in geometry.geoms for g in _line_parts(item)]
    return []


def _wind_axis_metrics(
    angle_deg: float,
    airspeed_mps: float,
    wind_speed_mps: float,
    wind_direction_from_deg: float,
) -> tuple[float, float, float]:
    """Return ground speeds in both directions and the crosswind component.

    ``angle_deg`` is a mathematical axis angle (0° east, counter-clockwise),
    while the weather input follows the meteorological convention: direction
    *from* north, clockwise. The aircraft is assumed to crab into the wind to
    hold the requested ground track.
    """
    wind_to_angle = math.radians((270.0 - wind_direction_from_deg) % 360)
    speeds: list[float] = []
    crosswind = 0.0
    for heading_deg in (angle_deg, angle_deg + 180.0):
        relative = wind_to_angle - math.radians(heading_deg)
        along = wind_speed_mps * math.cos(relative)
        cross = wind_speed_mps * math.sin(relative)
        crosswind = max(crosswind, abs(cross))
        if abs(cross) >= airspeed_mps:
            speeds.append(0.0)
        else:
            speeds.append(math.sqrt(max(0.0, airspeed_mps ** 2 - cross ** 2)) + along)
    return speeds[0], speeds[1], crosswind


def _sweep_lines(
    polygon,
    spacing_m: float,
    force_angle=None,
    *,
    airspeed_mps: float = 20.0,
    wind_speed_mps: float = 0.0,
    wind_direction_deg: float = 0.0,
    turn_radius_m: float = 0.0,
    reference_point: tuple[float, float] | None = None,
) -> tuple[list[LineString], float]:
    rectangle = polygon.minimum_rotated_rectangle
    coords = list(rectangle.exterior.coords)
    edges = []
    for left, right in zip(coords, coords[1:]):
        edges.append((_haversine_like(left, right), math.degrees(math.atan2(right[1] - left[1], right[0] - left[0]))))
    base_angle = max(edges, key=lambda item: item[0])[1]
    approach_angle = (math.degrees(math.atan2(polygon.centroid.y - reference_point[1],
                                               polygon.centroid.x - reference_point[0]))
                      if reference_point is not None else base_angle)
    candidates = [force_angle] if force_angle is not None else list(dict.fromkeys(
        round(value % 180, 3) for value in
        (base_angle, base_angle + 90, approach_angle, approach_angle + 90, 0, 90)
    ))
    best_lines: list[LineString] = []
    best_angle = 0.0
    best_score = float("inf")
    for angle in candidates:
        rotated = affinity.rotate(polygon, -angle, origin="centroid")
        minx, miny, maxx, maxy = rotated.bounds
        line_entries: list[tuple[float, float, LineString]] = []
        rows = max(1, math.ceil((maxy - miny) / spacing_m))
        actual_spacing = (maxy - miny) / rows
        y = miny + actual_spacing / 2
        while y <= maxy:
            cut = rotated.intersection(LineString([(minx - spacing_m, y), (maxx + spacing_m, y)]))
            for part in _line_parts(cut):
                if part.length > max(4, spacing_m * 0.15):
                    line_entries.append((y, part.centroid.x, affinity.rotate(part, angle, origin=polygon.centroid)))
            y += actual_spacing
        line_entries.sort(key=lambda item: (item[0], item[1]))
        lines = [item[2] for item in line_entries]
        connector = 0.0
        cursor = None
        previous_heading = None
        for line in lines:
            coordinates = list(line.coords)
            if cursor is not None:
                if _haversine_like(cursor, coordinates[-1]) < _haversine_like(cursor, coordinates[0]):
                    coordinates.reverse()
                chord = _haversine_like(cursor, coordinates[0])
                heading = _unit_vector(coordinates[0], coordinates[-1])
                if turn_radius_m and previous_heading is not None:
                    angle_change = math.acos(max(-1.0, min(1.0, previous_heading[0] * heading[0] + previous_heading[1] * heading[1])))
                    connector += max(chord, turn_radius_m * angle_change)
                else:
                    connector += chord
            cursor = coordinates[-1]
            previous_heading = _unit_vector(coordinates[0], coordinates[-1])
        survey_length = sum(line.length for line in lines)
        # A ground wind measured before take-off is not an aloft forecast and
        # must not rotate the survey grid. It is considered in feasibility and
        # time margins later, not in this geometric objective.
        transit = 0.0
        if reference_point is not None and lines:
            endpoints = [tuple(lines[0].coords[0]), tuple(lines[0].coords[-1]),
                         tuple(lines[-1].coords[0]), tuple(lines[-1].coords[-1])]
            transit = min(math.dist(reference_point, point) for point in endpoints[:2])
            transit += min(math.dist(reference_point, point) for point in endpoints[2:])
        score = survey_length + connector * 2.2 + transit
        if lines and score < best_score:
            best_lines, best_angle, best_score = lines, angle % 180, score
    return best_lines, best_angle


def _corridor_lines(centerline, spacing_m: float, width_m: float) -> tuple[list[LineString], float]:
    """Create longitudinal inspection passes inside a buffered linear asset."""
    source_parts = _line_parts(centerline)
    if not source_parts:
        return [], 0.0
    rows = max(1, math.ceil(width_m / spacing_m))
    actual_spacing = width_m / rows
    offsets = [0.0] if rows == 1 else [-width_m / 2 + actual_spacing / 2 + index * actual_spacing for index in range(rows)]
    lines: list[LineString] = []
    for offset in offsets:
        for source in source_parts:
            if abs(offset) < 1e-6:
                shifted = source
            else:
                shifted = source.parallel_offset(abs(offset), "left" if offset > 0 else "right", join_style="round")
            lines.extend(part for part in _line_parts(shifted) if part.length > 4)
    first = source_parts[0]
    start, end = first.coords[0], first.coords[-1]
    angle = math.degrees(math.atan2(end[1] - start[1], end[0] - start[0])) % 180
    return lines, angle


def _haversine_like(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _unit_vector(start: tuple[float, float], end: tuple[float, float]) -> tuple[float, float]:
    distance = _haversine_like(start, end)
    if distance < 1e-9:
        return (0.0, 0.0)
    return ((end[0] - start[0]) / distance, (end[1] - start[1]) / distance)


def _planning_turn_radius(pair: CompatiblePair) -> float:
    """Steady coordinated turn at survey airspeed, never below the catalog floor."""
    if pair.uav['type'] != 'fixed_wing':
        return 0.0
    bank_deg = pair.uav['max_bank_deg']
    speed_mps = pair.speed_kmh / 3.6
    return max(pair.uav['turn_radius_m'], speed_mps ** 2 / (9.80665 * math.tan(math.radians(bank_deg))))


def _runway_direction(
    base_point: tuple[float, float], target: tuple[float, float], axis: tuple[float, float],
    heading_deg: float, wind_speed_mps: float, wind_direction_from_deg: float,
) -> tuple[float, float]:
    # For a meaningful along-runway wind, prefer taking off into the wind.
    # A near-crosswind or calm case uses the direction toward the first sector.
    headwind = wind_speed_mps * math.cos(math.radians(heading_deg - wind_direction_from_deg))
    if abs(headwind) >= 1.0:
        return axis if headwind > 0 else (-axis[0], -axis[1])
    toward = _unit_vector(base_point, target)
    return axis if axis[0] * toward[0] + axis[1] * toward[1] >= 0 else (-axis[0], -axis[1])


def _runway_departure(base_point: tuple[float, float], direction: tuple[float, float], length_m: float, extension_m: float) -> LineString:
    distance = length_m / 2 + extension_m
    threshold = (base_point[0] + direction[0] * length_m / 2, base_point[1] + direction[1] * length_m / 2)
    exit_point = (base_point[0] + direction[0] * distance, base_point[1] + direction[1] * distance)
    return LineString([base_point, threshold, exit_point])


def _runway_arrival(base_point: tuple[float, float], direction: tuple[float, float], length_m: float, extension_m: float) -> LineString:
    distance = length_m / 2 + extension_m
    approach = (base_point[0] - direction[0] * distance, base_point[1] - direction[1] * distance)
    threshold = (base_point[0] - direction[0] * length_m / 2, base_point[1] - direction[1] * length_m / 2)
    return LineString([approach, threshold, base_point])


def _order_wide_turn_strips(legs: list[LineString], spacing_m: float, radius_m: float) -> list[LineString]:
    """Fly interleaved lanes so most reversals have at least a diameter of space.

    Each residue group is traversed in the opposite cross-track direction to
    its predecessor. Every source strip is visited exactly once; sector
    boundaries remain unchanged. At most ``stride - 1`` narrow group joins
    remain and are still resolved with curvature-bounded connectors.
    """
    if len(legs) < 3 or radius_m <= spacing_m / 2:
        return legs
    stride = min(len(legs), max(2, math.ceil(2 * radius_m / max(spacing_m, 1))))
    result: list[LineString] = []
    for offset in range(stride):
        group = legs[offset::stride]
        result.extend(group if offset % 2 == 0 else reversed(group))
    return result


def _select_strip_sequence(
    legs: list[LineString], base_point: tuple[float, float], obstacles, turn_radius_m: float,
) -> list[LineString]:
    """Compare both sweep directions for the whole sortie.

    This is a cheap global candidate stage, not an assertion of optimality:
    actual Dubins connectors and the complete aircraft route are validated
    later.  It prevents a locally nearest first pass from forcing a long
    return or an avoidable crossing of a protected area.
    """
    if len(legs) < 2:
        return legs

    def transit_length(start, end):
        direct = LineString([start, end])
        if obstacles is None or obstacles.is_empty or not direct.intersects(obstacles):
            return direct.length
        detour, _, failed = avoid_line(direct, obstacles)
        return float("inf") if failed else detour.length

    best_score = float("inf")
    best_legs = legs
    for ordered in (legs, list(reversed(legs))):
        cursor = base_point
        oriented = []
        score = 0.0
        previous_heading = None
        for index, leg in enumerate(ordered):
            coordinates = list(leg.coords)
            if math.dist(cursor, coordinates[-1]) < math.dist(cursor, coordinates[0]):
                coordinates.reverse()
            start, end = coordinates[0], coordinates[-1]
            if index == 0:
                score += transit_length(cursor, start)
            else:
                score += math.dist(cursor, start)
                if previous_heading is not None and turn_radius_m:
                    next_heading = _unit_vector(start, end)
                    cosine = max(-1.0, min(1.0, previous_heading[0] * next_heading[0] + previous_heading[1] * next_heading[1]))
                    score += turn_radius_m * math.acos(cosine)
            score += leg.length
            oriented.append(LineString(coordinates))
            previous_heading = _unit_vector(start, end)
            cursor = end
        score += transit_length(cursor, base_point)
        if score < best_score:
            best_score, best_legs = score, oriented
    return best_legs


def _smooth_turn(
    start: tuple[float, float],
    start_heading: tuple[float, float],
    end: tuple[float, float],
    end_heading: tuple[float, float],
    turn_radius_m: float,
    handle_limit_m: float | None = None,
) -> LineString:
    """Build a display/planning U-turn tangent to the adjacent survey strips.

    This remains a preliminary cubic Bezier geometry rather than an autopilot
    command.  The catalogue turn radius controls fixed-wing handles; short
    multirotor transitions receive a smaller, still visually continuous arc.
    """
    chord = _haversine_like(start, end)
    if chord < 1e-6:
        return LineString([start, end])
    handle = min(max(turn_radius_m * 0.72, chord * 0.35, 6.0), max(chord * 1.6, 12.0))
    if handle_limit_m is not None:
        handle = min(handle, handle_limit_m)
    control_1 = (start[0] + start_heading[0] * handle, start[1] + start_heading[1] * handle)
    control_2 = (end[0] - end_heading[0] * handle, end[1] - end_heading[1] * handle)
    steps = max(10, min(24, math.ceil(math.pi * max(turn_radius_m, chord / 2) / 18)))
    points: list[tuple[float, float]] = []
    for index in range(steps + 1):
        t = index / steps
        u = 1 - t
        points.append((
            u ** 3 * start[0] + 3 * u ** 2 * t * control_1[0] + 3 * u * t ** 2 * control_2[0] + t ** 3 * end[0],
            u ** 3 * start[1] + 3 * u ** 2 * t * control_1[1] + 3 * u * t ** 2 * control_2[1] + t ** 3 * end[1],
        ))
    return LineString(points)


def _flight_connector(
    start: tuple[float, float], start_heading: tuple[float, float],
    end: tuple[float, float], end_heading: tuple[float, float],
    turn_radius_m: float, obstacles, *, survey_turn: bool = False,
) -> tuple[LineString, bool, bool]:
    """Choose a tangent, curvature-bounded connector when the UAV cannot hover."""
    if turn_radius_m > 0:
        # Three-arc RLR/LRL paths are mathematically short but make the tight,
        # overlapping curls seen at closely spaced survey-strip boundaries.
        # Prefer wider arc-straight-arc maneuvering outside the photographed area.
        # A legal minimum-radius turn can look like a right angle on a
        # kilometre-scale map. For long transfers use a gentler planned bank,
        # while retaining the smaller physical radius for confined survey
        # turns. This also reduces unnecessary high-bank manoeuvres.
        design_radius_m = (max(turn_radius_m, min(450.0, _haversine_like(start, end) * 0.03))
                           if not survey_turn else turn_radius_m)
        first_candidate = None
        radii = [design_radius_m]
        if design_radius_m > turn_radius_m + 0.1:
            radii.append(turn_radius_m)
        for radius in radii:
            # Prefer the shortest curvature-feasible path. A three-arc turn
            # may be necessary when the runway is close to the survey area;
            # obstacle detours below are instead guided by straight waypoints.
            candidates = iter(dubins_connectors(start, start_heading, end, end_heading, radius,
                                                allow_three_arc=not survey_turn))
            shortest = next(candidates, None)
            if shortest is None:
                continue
            if first_candidate is None:
                first_candidate = shortest
            if obstacles is None or obstacles.is_empty:
                return shortest, False, False
            for index, candidate in enumerate(chain((shortest,), candidates)):
                if candidate.distance(obstacles) >= 3.0:
                    return candidate, index > 0 or radius != design_radius_m, False
        # First find a wide polygonal guide around the protected airspace,
        # then replace every guide leg with a curvature-bounded connector.
        # A raw visibility-graph line must never be returned for a fixed wing:
        # its corners can require an instantaneous change of heading.
        if first_candidate is not None:
            for margin in (max(60.0, turn_radius_m * 0.8),
                           max(180.0, turn_radius_m * 1.8),
                           max(360.0, turn_radius_m * 3.5)):
                inflated = obstacles.buffer(margin + 3.0, join_style="mitre")
                # Plan from the direct reference line first. Its visibility
                # graph gives the shortest polygonal obstacle bypass instead
                # of chasing an already curled Dubins candidate.
                for guide_input in (LineString([start, end]), first_candidate):
                    guide, detoured, failed = avoid_line(guide_input, inflated)
                    if failed or not detoured:
                        continue
                    guide_points = list(guide.simplify(max(12.0, turn_radius_m * 0.15)).coords)
                    if len(guide_points) < 3:
                        continue
                    guide_points[0], guide_points[-1] = start, end
                    current_point, current_heading = start, start_heading
                    pieces: list[LineString] = []
                    for point_index in range(1, len(guide_points)):
                        waypoint = guide_points[point_index]
                        heading = (end_heading if point_index == len(guide_points) - 1 else
                                   _unit_vector(waypoint, guide_points[point_index + 1]))
                        safe_piece = next((
                            candidate for candidate in dubins_connectors(
                                current_point, current_heading, waypoint, heading, turn_radius_m,
                                allow_three_arc=False,
                            ) if candidate.distance(obstacles) >= 3.0
                        ), None)
                        if safe_piece is None:
                            break
                        pieces.append(safe_piece)
                        current_point, current_heading = waypoint, heading
                    if len(pieces) == len(guide_points) - 1:
                        joined = LineString([
                            *pieces[0].coords,
                            *(point for piece in pieces[1:] for point in list(piece.coords)[1:]),
                        ])
                        if joined.distance(obstacles) >= 3.0:
                            return joined, True, False
        # No safe tangent route was demonstrated. The caller must reject this
        # candidate rather than show a forbidden crossing as an option.
        return first_candidate if first_candidate is not None else LineString([start, end]), False, True
    # Multirotors can change heading at a waypoint. A straight reference line
    # and visibility-graph bypass make every transfer leg auditable on the map.
    line = LineString([start, end])
    return avoid_line(line, obstacles)


def _base_for(uav: dict[str, Any]) -> dict[str, Any]:
    return next(base for base in BASES if base["id"] == uav["current_base_id"])


def _build_plan(
    mode: str, pairs: list[CompatiblePair], polygon, metric_crs, to_wgs84, request: MissionRequest,
    count: int, corridor=None, transit_obstacles=None, coverage_target=None,
    allocation_strategy: str = 'endurance',
) -> dict[str, Any] | None:
    chosen = sorted(pairs, key=lambda pair: pair.productivity_km2_h, reverse=True)[:count]
    if not chosen:
        return None
    spacing = min(pair.line_spacing_m for pair in chosen)
    orientation_airspeed = min(pair.speed_kmh for pair in chosen) / 3.6
    if request.launch_site:
        reference_lonlat = (request.launch_site.lon, request.launch_site.lat)
    elif request.launch_point:
        reference_lonlat = request.launch_point
    elif chosen[0].uav['type'] == 'fixed_wing':
        first_base = _base_for(chosen[0].uav)
        reference_lonlat = (first_base['lon'], first_base['lat'])
    else:
        reference_lonlat = None
    if reference_lonlat is not None:
        projected = Transformer.from_crs('EPSG:4326', metric_crs, always_xy=True).transform(*reference_lonlat)
        reference_point = (projected[0], projected[1])
    else:
        reference_point = None
    strips, angle = _corridor_lines(corridor, spacing, request.corridor_width_m) if corridor is not None else _sweep_lines(
        polygon,
        spacing,
        airspeed_mps=orientation_airspeed,
        wind_speed_mps=request.wind_speed_mps,
        wind_direction_deg=request.wind_direction_deg,
        turn_radius_m=max(_planning_turn_radius(pair) for pair in chosen),
        reference_point=reference_point,
    )
    if corridor is None and 'digital_twin' in (request.result_types or [request.result_type]):
        cross_strips, _ = _sweep_lines(
            polygon,
            spacing,
            angle + 90,
            airspeed_mps=orientation_airspeed,
            wind_speed_mps=request.wind_speed_mps,
            wind_direction_deg=request.wind_direction_deg,
            turn_radius_m=max(_planning_turn_radius(pair) for pair in chosen),
        )
        strips += cross_strips
    if not strips:
        return None

    # Каждому аппарату отдаётся непрерывный пространственный сектор. Старое
    # round-robin распределение раздавало соседние галсы разным БВС и создавало
    # неестественные диагональные перелёты через всю территорию.
    active_count = min(len(chosen), len(strips))
    chosen = chosen[:active_count]
    assignments: list[list[LineString]] = [[] for _ in chosen]
    total_length = sum(strip.length for strip in strips)
    # The economic partition allocates more contiguous strips to aircraft that
    # can cover them without another battery/fuel turnaround. Merely splitting
    # in proportion to speed can give a short-endurance aircraft two sorties
    # while a long-endurance aircraft lands with unused capacity.
    speed_weights = [
        pair.speed_kmh * (pair.uav['endurance_min'] / 180.0 if allocation_strategy == 'endurance' else 1.0)
        for pair in chosen
    ]
    total_weight = sum(speed_weights)
    strip_index = 0
    for sector_index, pair in enumerate(chosen):
        if sector_index == len(chosen) - 1:
            assignments[sector_index].extend(strips[strip_index:])
            break
        target_length = total_length * speed_weights[sector_index] / total_weight
        assigned_length = 0.0
        remaining_sectors = len(chosen) - sector_index - 1
        while strip_index < len(strips) - remaining_sectors:
            strip = strips[strip_index]
            assignments[sector_index].append(strip)
            assigned_length += strip.length
            strip_index += 1
            if assigned_length >= target_length:
                break

    vehicles = []
    makespan_min = 0.0
    total_hours = 0.0
    total_cost = 0.0
    plan_risk = 0.0
    plan_coverage_lines: list[LineString] = []
    plan_detours = 0
    plan_unresolved_avoidance = 0
    metric_vehicle_routes: list[LineString] = []
    departure_offsets: list[float] = []
    route_ground_speeds: list[float] = []
    separation_conflicts = 0
    plan_start = request.earliest_start or datetime.now(timezone.utc)
    colors = {
        "fast": ["#f15a32", "#ff7957", "#dc4420", "#ff9b7f"],
        "economy": ["#22b5e5", "#52c8eb", "#149dcc", "#7bd8f2"],
        "safe": ["#789cff", "#9bb5ff", "#6688e8", "#b7c8ff"],
    }[mode]
    for idx, (pair, legs) in enumerate(zip(chosen, assignments)):
        if not legs:
            continue
        turn_radius_m = _planning_turn_radius(pair)
        if corridor is None and 'digital_twin' not in (request.result_types or [request.result_type]):
            legs = _order_wide_turn_strips(legs, spacing, turn_radius_m)
        base = _base_for(pair.uav)
        if request.launch_site:
            base = request.launch_site.model_dump()
        elif request.launch_point:
            base = {'id':'planned-field-site','name':'Планируемый полевой старт (не обследован)','lon':request.launch_point[0],'lat':request.launch_point[1]}
        if request.launch_point:
            metric_base = transform(Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True).transform, Point(base["lon"], base["lat"]))
        elif pair.uav['type'] == 'multirotor':
            metric_base = Point(list(_line_parts(corridor)[0].coords)[0]) if corridor is not None else polygon.centroid
            field_site = transform(to_wgs84, metric_base)
            base = {'id':'assumed-mobile-site','name':'Расчётная мобильная площадка у объекта (требует обследования)','lon':field_site.x,'lat':field_site.y}
        else:
            metric_base = transform(Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True).transform, Point(base["lon"], base["lat"]))
        base_point = (metric_base.x, metric_base.y)
        legs = _select_strip_sequence(legs, base_point, transit_obstacles, turn_radius_m)
        runway_direction = None
        runway_length_m = None
        departure_heading_deg = None
        runway_departure = None
        runway_arrival = None
        if turn_radius_m:
            runway_length_m = base['runway_length_m']
            first_target = tuple(legs[0].centroid.coords[0])
            runway_lon, runway_lat, _ = WGS84_GEOD.fwd(base['lon'], base['lat'], base['heading_deg'], 100.0)
            runway_point = transform(Transformer.from_crs('EPSG:4326', metric_crs, always_xy=True).transform,
                                     Point(runway_lon, runway_lat))
            runway_axis = _unit_vector(base_point, (runway_point.x, runway_point.y))
            runway_direction = _runway_direction(base_point, first_target, runway_axis,
                                                  base['heading_deg'], request.wind_speed_mps,
                                                  request.wind_direction_deg)
            departure_heading_deg = base['heading_deg'] if runway_direction == runway_axis else (base['heading_deg'] + 180) % 360
            airspeed_mps = pair.speed_kmh / 3.6
            # Keep the initial and final straight aligned with the surveyed
            # runway. Climb/descent continues along the tangent transit arcs;
            # otherwise a 150 m survey altitude forces kilometre-long runway
            # extensions and tiny, visually abrupt turns on an overview map.
            runway_departure = _runway_departure(base_point, runway_direction, runway_length_m,
                                                 max(200.0, airspeed_mps * 12.0))
            runway_arrival = _runway_arrival(base_point, runway_direction, runway_length_m,
                                             max(450.0, airspeed_mps * 20.0))
        route_points = [base_point]
        transfer_segments: list[LineString] = []
        coverage_segments: list[LineString] = []
        vehicle_detours = 0
        vehicle_unresolved = 0
        useful_m = 0.0
        previous_leg_coordinates: list[tuple[float, float]] | None = None
        for leg in legs:
            coordinates = list(leg.coords)
            # Direction is selected against both outbound and return transit,
            # not greedily against just the previous pass.
            incoming_heading = (_unit_vector(previous_leg_coordinates[-2], previous_leg_coordinates[-1])
                                if previous_leg_coordinates else None)
            next_heading = _unit_vector(coordinates[0], coordinates[1])
            heading_mismatch = (incoming_heading is not None and
                                incoming_heading[0] * next_heading[0] + incoming_heading[1] * next_heading[1] < 0.999)
            if route_points[-1] != coordinates[0] or heading_mismatch:
                if previous_leg_coordinates:
                    transition, detoured, unresolved = _flight_connector(
                        route_points[-1], incoming_heading,
                        coordinates[0], next_heading,
                        turn_radius_m, transit_obstacles, survey_turn=True,
                    )
                else:
                    if turn_radius_m and runway_direction and runway_departure:
                        departure = runway_departure
                        transition, detoured, unresolved = _flight_connector(
                            tuple(departure.coords[-1]), runway_direction, coordinates[0],
                            _unit_vector(coordinates[0], coordinates[1]),
                            turn_radius_m, transit_obstacles,
                        )
                        transition = LineString([*list(departure.coords), *list(transition.coords)[1:]])
                    else:
                        transition, detoured, unresolved = _flight_connector(
                            route_points[-1], _unit_vector(route_points[-1], coordinates[0]),
                            coordinates[0], next_heading, 0.0, transit_obstacles,
                        )
                vehicle_detours += int(detoured)
                vehicle_unresolved += int(unresolved)
                transfer_segments.append(transition)
                route_points.extend(list(transition.coords)[1:])
            route_points.extend(coordinates[1:])
            coverage_leg = LineString(coordinates)
            coverage_segments.append(coverage_leg)
            plan_coverage_lines.append(coverage_leg)
            useful_m += coverage_leg.length
            previous_leg_coordinates = coordinates
        if turn_radius_m and runway_direction and runway_arrival:
            arrival = runway_arrival
            return_segment, detoured, unresolved = _flight_connector(
                route_points[-1], _unit_vector(previous_leg_coordinates[-2], previous_leg_coordinates[-1]),
                tuple(arrival.coords[0]), runway_direction,
                turn_radius_m, transit_obstacles,
            )
            return_segment = LineString([*list(return_segment.coords), *list(arrival.coords)[1:]])
        else:
            return_segment, detoured, unresolved = _flight_connector(
                route_points[-1], _unit_vector(previous_leg_coordinates[-2], previous_leg_coordinates[-1]),
                (metric_base.x, metric_base.y), _unit_vector(route_points[-1], (metric_base.x, metric_base.y)),
                0.0, transit_obstacles,
            )
        vehicle_detours += int(detoured)
        vehicle_unresolved += int(unresolved)
        transfer_segments.append(return_segment)
        route_points.extend(list(return_segment.coords)[1:])
        route = LineString(route_points)
        distance_km = route.length / 1000
        # Conservative constant headwind bound. Rates are explicit planning assumptions.
        reserve = 30 if mode == 'safe' else 22
        ground_speed = pair.speed_kmh / 3.6 - request.wind_speed_mps
        if ground_speed <= 2 or request.wind_speed_mps > pair.uav['max_wind_mps']:
            return None
        climb_rate, descent_rate = (3.0, 2.0) if pair.uav['type'] == 'multirotor' else (2.5, 2.0)
        climb_s = pair.altitude_m / climb_rate
        landing_s = pair.altitude_m / descent_rate + 60
        # Each sortie pays the farthest base-to-sector return bound; no free battery resets.
        radius = max(math.dist((metric_base.x, metric_base.y), point) for leg in coverage_segments for point in leg.coords)
        # For sortie packing, keep a conservative farthest-point bound, but
        # do not add climb/landing a second time to transfers flown during
        # those phases. The initial actual transfer includes runway geometry.
        first_external_s = max(climb_s, transfer_segments[0].length / ground_speed)
        last_external_s = max(landing_s, transfer_segments[-1].length / ground_speed)
        farthest_external_s = (2 * radius + 4 * math.pi * turn_radius_m) / ground_speed
        turn_s = max(6.0, math.pi * turn_radius_m / ground_speed)
        useful_s = useful_m / ground_speed
        connector_times = [max(turn_s, segment.length / ground_speed) for segment in transfer_segments[1:-1]]
        turns_s = sum(connector_times)
        usable_s = pair.uav['endurance_min'] * 60 * (1 - reserve / 100)
        overhead_s = max(first_external_s + last_external_s, farthest_external_s)
        capacity_s = usable_s - overhead_s
        if capacity_s <= 0 or any(leg.length / ground_speed + turn_s > capacity_s for leg in legs):
            return None
        # Pack whole strips in order. A strip is never split across batteries.
        sorties, packed_s, longest_packed_s = 1, 0.0, 0.0
        sortie_starts = {0}
        for leg_index, leg in enumerate(legs):
            leg_s = leg.length / ground_speed + (connector_times[leg_index-1] if leg_index > 0 else 0)
            if leg_s > capacity_s:
                return None
            if packed_s + leg_s > capacity_s:
                sorties += 1
                packed_s = 0.0
                sortie_starts.add(leg_index)
            packed_s += leg_s
            longest_packed_s = max(longest_packed_s, packed_s)
        if sorties > MAX_SORTIES_PER_UAV:
            return None
        maintenance_metric = pair.uav.get('maintenance_metric')
        flights_since_maintenance = pair.uav.get('flights_since_maintenance')
        if maintenance_metric == 'flights' and flights_since_maintenance is not None:
            if flights_since_maintenance + sorties > pair.uav['maintenance_interval']:
                return None
        if sorties > 1:
            # A battery change is a return to the launch point, not a
            # continuous connector to the next survey strip. Preserve the
            # geometry in the exported route and calendar simulation.
            old_connectors = transfer_segments[1:-1]
            sortie_points = [(metric_base.x, metric_base.y)]
            sortie_transfers: list[LineString] = []
            for leg_index, coverage_leg in enumerate(coverage_segments):
                start_point = tuple(coverage_leg.coords[0])
                if leg_index in sortie_starts:
                    if turn_radius_m and runway_direction and runway_departure:
                        departure = runway_departure
                        connector, detoured, unresolved = _flight_connector(
                            tuple(departure.coords[-1]), runway_direction,
                            start_point, _unit_vector(coverage_leg.coords[0], coverage_leg.coords[1]),
                            turn_radius_m, transit_obstacles,
                        )
                        connector = LineString([*list(departure.coords), *list(connector.coords)[1:]])
                    else:
                        connector, detoured, unresolved = _flight_connector(
                            sortie_points[-1], _unit_vector(sortie_points[-1], start_point),
                            start_point, _unit_vector(coverage_leg.coords[0], coverage_leg.coords[1]),
                            0.0, transit_obstacles,
                        )
                    vehicle_detours += int(detoured)
                    vehicle_unresolved += int(unresolved)
                else:
                    connector = old_connectors[leg_index - 1]
                sortie_transfers.append(connector)
                sortie_points.extend(list(connector.coords)[1:])
                sortie_points.extend(list(coverage_leg.coords)[1:])
                if leg_index + 1 == len(coverage_segments) or leg_index + 1 in sortie_starts:
                    home_point = (metric_base.x, metric_base.y)
                    if turn_radius_m and runway_direction and runway_arrival:
                        arrival = runway_arrival
                        home_leg, detoured, unresolved = _flight_connector(
                            sortie_points[-1], _unit_vector(coverage_leg.coords[-2], coverage_leg.coords[-1]),
                            tuple(arrival.coords[0]), runway_direction,
                            turn_radius_m, transit_obstacles,
                        )
                        home_leg = LineString([*list(home_leg.coords), *list(arrival.coords)[1:]])
                    else:
                        home_leg, detoured, unresolved = _flight_connector(
                            sortie_points[-1], _unit_vector(coverage_leg.coords[-2], coverage_leg.coords[-1]),
                            home_point, _unit_vector(sortie_points[-1], home_point),
                            0.0, transit_obstacles,
                        )
                    vehicle_detours += int(detoured)
                    vehicle_unresolved += int(unresolved)
                    sortie_transfers.append(home_leg)
                    sortie_points.extend(list(home_leg.coords)[1:])
            route = LineString(sortie_points)
            transfer_segments = sortie_transfers
            distance_km = route.length / 1000
        if vehicle_unresolved or (
            transit_obstacles is not None and not transit_obstacles.is_empty
            and route.distance(transit_obstacles) < 1.0
        ):
            # Neither the UI nor an export may advertise a route which crosses
            # a protected zone, even as a visually pleasing preview.
            return None
        # Price and display the actual flown connectors, not the original
        # farthest-point bound used to conservatively pack strips into sorties.
        # Climb and landing overlap their corresponding curved transfers.
        outbound_s = 0.0
        inbound_s = 0.0
        turns_s = 0.0
        transfer_index = 0
        for leg_index in range(len(coverage_segments)):
            prefix = transfer_segments[transfer_index]
            transfer_index += 1
            if leg_index in sortie_starts:
                outbound_s += max(0.0, prefix.length / ground_speed - climb_s)
            else:
                turns_s += max(turn_s, prefix.length / ground_speed)
            if leg_index + 1 == len(coverage_segments) or leg_index + 1 in sortie_starts:
                home_leg = transfer_segments[transfer_index]
                transfer_index += 1
                inbound_s += max(0.0, home_leg.length / ground_speed - landing_s)
        flight_hours = (useful_s + turns_s + sorties * (climb_s + landing_s) + outbound_s + inbound_s) / 3600
        if maintenance_metric == 'engine_hours' and flight_hours > pair.uav['maintenance_due_hours']:
            return None
        elapsed_min = flight_hours * 60 + (sorties - 1) * pair.uav['turnaround_min']
        if elapsed_min > MAX_OPERATION_WINDOW_MIN:
            return None
        wgs_route = transform(to_wgs84, route)
        base_lonlat = (base['lon'], base['lat'])
        max_control_distance_km = max(_haversine_m(base_lonlat, point) for point in wgs_route.coords) / 1000
        radio_limit_km = _direct_radio_limit_km(request, pair.altitude_m)
        if request.control_link_mode == 'radio' and max_control_distance_km > radio_limit_km:
            return None
        home_base = _base_for(pair.uav)
        relocation_km = _haversine_m((home_base['lon'], home_base['lat']), base_lonlat) / 1000
        link_assessment = {
            'mode': request.control_link_mode,
            'status': 'within_planning_limit' if request.control_link_mode == 'radio' else 'coverage_unverified',
            'max_distance_km': round(max_control_distance_km, 1),
            'planning_limit_km': round(radio_limit_km, 1) if request.control_link_mode == 'radio' else None,
            'equipment_range_km': request.radio_equipment_range_km if request.control_link_mode == 'radio' else None,
            'ground_antenna_height_m': request.ground_antenna_height_m if request.control_link_mode == 'radio' else None,
            'coverage_verified': False,
        }
        conflicting_routes = [
            route_index for route_index, previous_route in enumerate(metric_vehicle_routes)
            if route.distance(previous_route) < request.vehicle_separation_m
        ]
        departure_offset_min = 0.0
        if conflicting_routes:
            # Spatially close sectors and shared departure paths receive separate
            # departure slots. This is a conservative pre-deconfliction step;
            # the response explicitly requires a later 4D trajectory check.
            separation_conflicts += len(conflicting_routes)
            departure_offset_min = max(
                departure_offsets[route_index]
                + max(2.0, request.vehicle_separation_m / min(ground_speed, route_ground_speeds[route_index]) / 60 + 1.0)
                for route_index in conflicting_routes
            )
            elapsed_min += departure_offset_min
        phases = [
            *([{'name':'Слот разведения', 'minutes':departure_offset_min}] if departure_offset_min else []),
            {'name':'Набор высоты', 'minutes':sorties * climb_s / 60},
            {'name':'Перелёт к съёмке', 'minutes':outbound_s / 60},
            {'name':'Съёмка', 'minutes':useful_s / 60},
            {'name':'Развороты', 'minutes':turns_s / 60},
            {'name':'Возврат', 'minutes':inbound_s / 60},
            {'name':'Снижение и посадка', 'minutes':sorties * landing_s / 60},
            {'name':'Обслуживание между вылетами', 'minutes':(sorties - 1) * pair.uav['turnaround_min']},
        ]
        flight_phases = _planned_flight_phases(
            start_at=plan_start,
            departure_offset_min=departure_offset_min,
            altitude_m=pair.altitude_m,
            speed_kmh=pair.speed_kmh,
            phases=phases,
            base_coordinate=(metric_base.x, metric_base.y),
            coverage_segments=coverage_segments,
            transfer_segments=transfer_segments,
            sortie_starts=sortie_starts,
            to_wgs84=to_wgs84,
            ground_speed_mps=ground_speed,
            runway_departure=runway_departure,
            runway_arrival=runway_arrival,
        )
        energy_units = flight_hours * pair.uav["consumption_per_hour"]
        energy_price = 9.5 if pair.uav["energy_kind"] == "electric" else 67.0
        operating = flight_hours * pair.uav["cost_per_hour_rub"]
        energy_cost = energy_units * energy_price
        maintenance = flight_hours * 1650
        battery_wear = sorties * 780 if pair.uav["energy_kind"] == "electric" else 0
        labor = elapsed_min / 60 * 1900
        risk = 0.0  # Probability of failure is not inferable from this model.
        cost_components = {
            'Эксплуатация': operating, 'Энергия': energy_cost,
            'ТО': maintenance, 'Батареи': battery_wear, 'Экипаж': labor,
            'Подготовка вылетов': sorties * SORTIE_PREPARATION_COST_RUB,
            'Мобилизация борта': UAV_MOBILIZATION_COST_RUB,
        }
        cost = sum(cost_components.values())
        vehicles.append({
            "uav_id": pair.uav["id"], "uav_name": pair.uav["name"],
            "uav_type": pair.uav["type"], "optic_id": pair.optic["id"],
            "optic_name": pair.optic["name"], "base_id": base["id"], "base_name": base["name"],
            "altitude_m": round(pair.altitude_m), "speed_kmh": round(pair.speed_kmh,1),
            "engineering": {
                "requested_gsd_cm_px": request.gsd_cm_px,
                "trigger_design_speed_mps": round(pair.speed_kmh / 3.6 + request.wind_speed_mps,2),
                "achieved_gsd_cm_px": round(pair.achieved_gsd_cm_px, 2),
                "sensor_width_mm": pair.optic.get("sensor_width_mm", 0),
                "focal_length_mm": pair.optic.get("focal_length_mm", 0),
                "resolution_width_px": pair.optic.get("resolution_width_px", 0),
                "footprint_width_m": round(pair.swath_m, 1),
                "footprint_length_m": round(pair.footprint_length_m, 1),
                "line_spacing_m": round(pair.line_spacing_m, 1),
                "trigger_spacing_m": round(pair.trigger_spacing_m, 1),
                "trigger_interval_s": round(pair.trigger_interval_s, 2),
                "required_fps": round(pair.required_fps, 3),
                "camera_fps": pair.optic.get("fps", 0),
                "estimated_frames": sum(math.ceil(leg.length / pair.trigger_spacing_m) + 1 for leg in legs) if pair.trigger_spacing_m else 0,
                "forward_overlap_percent": round(request.forward_overlap * 100),
                "side_overlap_percent": round(request.side_overlap * 100),
            },
            "distance_km": round(distance_km, 1), "useful_distance_km": round(useful_m / 1000, 1),
            "flight_time_min": round(flight_hours * 60), "elapsed_time_min": round(elapsed_min),
            "departure_offset_min": round(departure_offset_min, 1),
            "spatial_conflicts": len(conflicting_routes),
            "sorties": sorties, "energy_units": round(energy_units, 2),
            "strip_count": len(coverage_segments), "sector_index": idx,
            "reserve_percent": reserve, "cost_rub": round(cost),
            "phases": phases, "flight_phases": flight_phases,
            "planned_start_at": flight_phases[0]["start_at"] if flight_phases else plan_start.isoformat(),
            "planned_completion_at": flight_phases[-1]["end_at"] if flight_phases else plan_start.isoformat(),
            "cost_components": {key: round(value, 2) for key, value in cost_components.items()},
            "ground_speed_mps": round(ground_speed, 2), "climb_rate_mps": climb_rate, "descent_rate_mps": descent_rate,
            "endurance_min": pair.uav["endurance_min"],
            "turn_radius_m": round(turn_radius_m, 1),
            "turn_bank_deg": pair.uav.get('max_bank_deg') if turn_radius_m else None,
            "turn_speed_kmh": round(pair.speed_kmh, 1) if turn_radius_m else None,
            "runway_heading_deg": base.get('heading_deg') if turn_radius_m else None,
            "runway_length_m": runway_length_m,
            "departure_heading_deg": departure_heading_deg,
            "runway_headwind_mps": round(request.wind_speed_mps * math.cos(math.radians(departure_heading_deg - request.wind_direction_deg)), 1) if departure_heading_deg is not None else None,
            "runway_crosswind_mps": round(abs(request.wind_speed_mps * math.sin(math.radians(departure_heading_deg - request.wind_direction_deg))), 1) if departure_heading_deg is not None else None,
            "turn_geometry": "dubins_csc" if turn_radius_m else "hover_spline",
            "usable_endurance_min": round(usable_s / 60, 1),
            "max_sortie_min": round((overhead_s + longest_packed_s) / 60, 1),
            "risk": round(risk, 3), "color": colors[idx % len(colors)],
            "link_assessment": link_assessment,
            "deployment": {'required': relocation_km > AUTO_MOBILE_DEPLOYMENT_RADIUS_KM,
                           'distance_km': round(relocation_km, 1), 'cost_included': False},
            "avoidance": {"detours": vehicle_detours, "unresolved": vehicle_unresolved},
            "route": mapping(wgs_route),
            "coverage_route": mapping(transform(to_wgs84, MultiLineString(coverage_segments))),
            "transit_route": mapping(transform(to_wgs84, MultiLineString(transfer_segments))),
        })
        makespan_min = max(makespan_min, elapsed_min)
        total_hours += flight_hours
        total_cost += cost
        plan_risk = max(plan_risk, risk)
        plan_detours += vehicle_detours
        plan_unresolved_avoidance += vehicle_unresolved
        metric_vehicle_routes.append(route)
        departure_offsets.append(departure_offset_min)
        route_ground_speeds.append(ground_speed)

    start = plan_start
    completion = start + timedelta(minutes=makespan_min)
    forward_ground_speed, reverse_ground_speed, crosswind_component = _wind_axis_metrics(
        angle,
        orientation_airspeed,
        request.wind_speed_mps,
        request.wind_direction_deg,
    )
    # Convert the internal mathematical axis into a bidirectional compass
    # azimuth: 0° north, clockwise, normalized to [0°, 180°).
    flight_axis_azimuth = (90.0 - angle) % 180.0
    # Покрытие создаёт полная полоса захвата камеры, тогда как spacing уже
    # уменьшен на боковое перекрытие и не подходит для проверки полноты.
    coverage_half_width = min(pair.swath_m for pair in chosen) / 2
    coverage_reference = coverage_target if coverage_target is not None else polygon
    coverage_geometry = unary_union(plan_coverage_lines).buffer(coverage_half_width, cap_style="square").intersection(coverage_reference)
    coverage_percent = min(100.0, round(coverage_geometry.area / coverage_reference.area * 100, 1))
    labels = {"fast": "Быстрый", "economy": "Минимум налёта", "safe": "Сбалансированный"}
    return {
        "id": mode, "label": labels[mode], "recommended": mode == "economy",
        "sector_allocation": allocation_strategy,
        "start_at": start.isoformat(), "completion_at": completion.isoformat(),
        "duration_min": round(makespan_min), "total_flight_time_min": round(total_hours * 60),
        "cost_rub": sum(v['cost_rub'] for v in vehicles), "uav_count": len(vehicles), "reserve_percent": 30 if mode == 'safe' else 22,
        "sorties": sum(v["sorties"] for v in vehicles), "risk": round(plan_risk, 3),
        "link_assessment": {
            'mode': request.control_link_mode,
            'status': 'within_planning_limit' if request.control_link_mode == 'radio' else 'coverage_unverified',
            'max_distance_km': max(v['link_assessment']['max_distance_km'] for v in vehicles),
            'planning_limit_km': min(v['link_assessment']['planning_limit_km'] for v in vehicles) if request.control_link_mode == 'radio' else None,
            'coverage_verified': False,
        },
        "deployment": {
            'required': any(v['deployment']['required'] for v in vehicles),
            'max_distance_km': max(v['deployment']['distance_km'] for v in vehicles),
            'cost_included': False,
        },
        "coverage_percent": coverage_percent, "flight_direction_deg": round(flight_axis_azimuth, 1),
        "wind_analysis": {
            "model": "vector_projection_with_conservative_bounds",
            "wind_speed_mps": round(request.wind_speed_mps, 2),
            "wind_direction_from_deg": round(request.wind_direction_deg, 1),
            "flight_axis_azimuth_deg": round(flight_axis_azimuth, 1),
            "ground_speed_forward_mps": round(forward_ground_speed, 2),
            "ground_speed_reverse_mps": round(reverse_ground_speed, 2),
            "crosswind_component_mps": round(crosswind_component, 2),
            "duration_ground_speed_mps": round(min(route_ground_speeds), 2),
            "selection_objective": "survey_distance + 2.2*turn_connectors + base_transit; wind is a feasibility margin",
            "duration_policy": "worst_case_headwind",
            "trigger_policy": "worst_case_tailwind",
        },
        "airspace_avoidance": {"detours": plan_detours, "unresolved": plan_unresolved_avoidance},
        "deconfliction": {
            "minimum_distance_m": request.vehicle_separation_m,
            "spatial_conflicts": separation_conflicts,
            "departure_slots_applied": sum(1 for offset in departure_offsets if offset > 0),
            "method": "sectorization_and_staggered_departure",
            "verified_4d": False,
        },
        "vehicles": vehicles,
        "maintenance_policy": "201/401: 80 полётов; 701: 100 моточасов. Счётчик полётов с последнего ТО для учебного флота не загружен — перед назначением нужна проверка журнала.",
        "selection_reason": (
            f"Минимальное время: {len(vehicles)} БВС, число вылетов — {sum(v['sorties'] for v in vehicles)}; работа выполняется параллельно."
            if mode == "fast" else
            f"Резерв 30%: {len(vehicles)} БВС, число вылетов — {sum(v['sorties'] for v in vehicles)}."
            if mode == "safe" else
            f"Суммарный налёт с подлётом и возвратом: {len(vehicles)} БВС, число вылетов — {sum(v['sorties'] for v in vehicles)}."
        ),
        "explanations": [
            f"Ось галсов {round(flight_axis_azimuth)}° выбрана по длине съёмки, разворотам и подлёту от площадки; ветер не задаёт геометрию галсов.",
            f"Территория разбита на {len(vehicles)} непрерывных сектора без взаимного пересечения галсов.",
            f"Для трасс ближе {request.vehicle_separation_m:.0f} м назначены разнесённые слоты старта; перед вылетом требуется проверка 4D-траекторий.",
            "Стоимость = эксплуатация + энергия + ТО + батареи + экипаж + подготовка вылетов + мобилизация бортов; тарифы демонстрационные. Доставка к удалённой площадке не включена.",
            "Перелёты рассчитаны консервативно по удалённейшей точке сектора; ветер принят встречным. Это верхняя оценка времени, не прогноз погоды.",
            "Для мультикоптера без заданной точки старта принята мобильная площадка у объекта; перед полётом она требует обследования и согласования.",
            "Запас времени полёта 30% в надёжном варианте, 22% в остальных; вероятность отказа не оценивается.",
        ],
    }


def optimize(request: MissionRequest, airspace_zones: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    available_minutes = _available_minutes(request)
    if request.launch_site and request.launch_site.status == 'closed':
        raise ValueError('Выбранная площадка закрыта.')
    outputs = request.result_types or [request.result_type]
    spectra = {'orthophoto':'rgb','digital_twin':'rgb','powerline_report':'rgb','thermal_map':'ir','point_cloud':'lidar','magnetic_map':'geophysical','ndvi':'multispectral'}
    if any(spectra[item] != request.survey_type for item in outputs):
        raise ValueError('Несовместимые результаты: для разных сенсоров создайте отдельные задания.')
    raw_geometry = request.area.get("geometry") if request.area.get("type") == "Feature" else request.area
    try:
        geographic = shape(raw_geometry)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Некорректная структура координат задания") from error
    if not geographic.is_valid:
        raise ValueError('Геометрия самопересекается или некорректна. Исправьте вершины.')
    if geographic.is_empty:
        raise ValueError("Геометрия задания пуста или некорректна")
    to_metric, to_wgs84, metric_crs = _projection(geographic)
    metric_source = transform(to_metric, geographic)
    corridor = metric_source if geographic.geom_type in {'LineString', 'MultiLineString'} else None
    if corridor is not None and corridor.length < 20:
        raise ValueError('Длина коридорного маршрута должна быть не менее 20 м.')
    metric = corridor.buffer(request.corridor_width_m / 2, cap_style='flat', join_style='round') if corridor is not None else metric_source
    if metric.area > 200_000_000 or metric.area < 10:
        raise ValueError('Поддерживается площадь от 10 м² до 200 км²; разделите большую территорию на задания.')
    compatible_pairs = [pair for uav in UAVS if (pair := _best_pair(uav, request))]
    def site_accepts(pair: CompatiblePair) -> bool:
        site = request.launch_site.model_dump() if request.launch_site else (
            None if request.launch_point else _base_for(pair.uav)
        )
        if site is None:
            # Arbitrary map clicks have no surveyed runway geometry.
            return pair.uav['type'] != 'fixed_wing'
        if pair.uav['type'] not in site['supports']:
            return False
        if pair.uav['type'] == 'fixed_wing':
            return site['runway_length_m'] >= pair.uav['planning_min_runway_m']
        return True
    compatible_pairs = [pair for pair in compatible_pairs if site_accepts(pair)]
    if not compatible_pairs:
        raise ValueError("Нет БВС с подходящей нагрузкой и площадкой. Для самолёта выберите ВПП с известным курсом и достаточной длиной; произвольная точка на карте не считается ВПП.")
    target = (geographic.centroid.x, geographic.centroid.y)
    def origin_distance_km(pair: CompatiblePair) -> float:
        if request.launch_site:
            origin = (request.launch_site.lon, request.launch_site.lat)
        elif request.launch_point:
            origin = request.launch_point
        elif pair.uav['type'] == 'multirotor':
            return 0.0  # Local mobile start is allowed only near the fleet's current base.
        else:
            home = _base_for(pair.uav)
            origin = (home['lon'], home['lat'])
        return _haversine_m(origin, target) / 1000

    pairs = compatible_pairs
    if request.launch_point is None:
        pairs = [pair for pair in pairs if pair.uav['type'] != 'multirotor' or
                 _haversine_m((_base_for(pair.uav)['lon'], _base_for(pair.uav)['lat']), target) / 1000
                 <= AUTO_MOBILE_DEPLOYMENT_RADIUS_KM]
        if not pairs:
            raise ValueError('Объект далеко от баз флота: автоматическая мобильная площадка без доставки БВС не допускается. Укажите местную площадку или точку старта и отдельно подтвердите перебазирование.')
    nearest_distance_km = min(origin_distance_km(pair) for pair in pairs)
    optimistic_roundtrip_km = max(
        max(0.0, pair.uav['endurance_min'] * 60 * .78 - pair.altitude_m / (3.0 if pair.uav['type'] == 'multirotor' else 2.5)
            - pair.altitude_m / 2.0 - 60) * max(0.0, pair.speed_kmh / 3.6 - request.wind_speed_mps) / 1000 / 2
        for pair in pairs
    )
    if all(origin_distance_km(pair) > optimistic_roundtrip_km for pair in pairs):
        raise ValueError(f'Объект примерно в {nearest_distance_km:.0f} км от точки старта; даже без съёмки доступный радиус полёта с возвратом и резервом не более {optimistic_roundtrip_km:.0f} км. Выберите площадку ближе к объекту или организуйте перебазирование БВС.')
    if corridor is None and available_minutes is not None:
        # A lower bound: even with zero turns, no transit and every compatible
        # aircraft surveying in parallel, the area cannot fit this day.
        capacity_m2_s = sum(sorted((pair.line_spacing_m * pair.speed_kmh / 3.6 for pair in pairs), reverse=True)[:request.max_uavs or 4])
        passes = 2 if 'digital_twin' in outputs else 1
        if capacity_m2_s <= 0 or metric.area * passes / capacity_m2_s > available_minutes * 60:
            optimistic_hours = metric.area * passes / max(capacity_m2_s, .001) / 3600
            best_aircraft_capacity = max(pair.line_spacing_m * pair.speed_kmh / 3.6 for pair in pairs)
            optimistic_aircraft = math.ceil(metric.area * passes / max(best_aircraft_capacity * available_minutes * 60, 1))
            raise ValueError(f'Заданный срок невыполним даже без подлёта и разворотов: одной только съёмке нужно не менее {optimistic_hours:.1f} ч с выбранным флотом и не менее {optimistic_aircraft} БВС при скорости лучшего совместимого борта. Увеличьте срок или число БВС; шаг профилей меняйте только если это допускает качество результата.')
    minx, miny, maxx, maxy = geographic.bounds
    # A selected local launch site replaces the fleet bases for this mission.
    # Querying the full corridor back to the fleet's home city would include
    # unrelated airspace when an aircraft is explicitly relocated far away.
    route_origins = [[request.launch_site.lon, request.launch_site.lat]] if request.launch_site else [request.launch_point] if request.launch_point else [
        (base['lon'], base['lat']) for base in BASES
    ]
    route_region = [
        min(minx, *(point[0] for point in route_origins)) - .15,
        min(miny, *(point[1] for point in route_origins)) - .15,
        max(maxx, *(point[0] for point in route_origins)) + .15,
        max(maxy, *(point[1] for point in route_origins)) + .15,
    ]
    airspace = prepare_airspace_context(
        request, airspace_zones or [], geographic, to_metric, metric, route_region,
    )
    blocked_coverage = [
        zone for zone in airspace["assessment"]["conflicts"]
        if zone["category"] in {"prohibited", "danger", "settlement"}
    ]
    if blocked_coverage:
        zone_names = ", ".join(dict.fromkeys(
            zone.get("code") or zone["name"] for zone in blocked_coverage
        ))
        raise ValueError(
            f"Контур задания пересекает запретную/опасную зону либо границу населённого пункта с проектным отступом ({zone_names}) "
            "с учётом заданного отступа. Полное покрытие без входа в неё невозможно: "
            "измените контур, время или условия задания. Расчёт маршрута остановлен."
        )
    planning_metric = metric
    planning_corridor = corridor
    if airspace["coverage_obstacles"] is not None:
        planning_metric = metric.difference(airspace["coverage_obstacles"])
        if corridor is not None:
            planning_corridor = corridor.difference(airspace["coverage_obstacles"])
        if planning_metric.is_empty or planning_metric.area < 10:
            raise ValueError('Безопасный коридор полностью перекрыт препятствиями.')
    feasible_count = min(4, len(pairs))
    # Cross-grid is sequential on one aircraft until spatially separated 3D sectors are supported.
    search_count = 1 if 'digital_twin' in outputs else feasible_count
    # A fleet cap without a deadline cannot benefit from building larger,
    # inevitably discarded combinations. Keep them only when a deadline is
    # supplied so we can report how many aircraft would meet it.
    if request.max_uavs is not None and available_minutes is None:
        search_count = min(search_count, request.max_uavs)
    groups = [list(group) for count in range(1, search_count + 1) for group in combinations(pairs, count)]
    allocations = [
        (group, strategy)
        for group in groups
        for strategy in (('speed', 'endurance') if len(group) > 1 else ('endurance',))
    ]
    evaluated_candidates = [
        _build_plan('economy', group, planning_metric, metric_crs, to_wgs84, request,
                    len(group), planning_corridor, airspace["transit_obstacles"], metric, strategy)
        for group, strategy in allocations
    ]
    all_candidates = [plan for plan in evaluated_candidates if plan]
    economy_candidates = [plan for plan in all_candidates if request.max_uavs is None or plan['uav_count'] <= request.max_uavs]
    if available_minutes is not None:
        within_deadline = [plan for plan in economy_candidates if plan['duration_min'] <= available_minutes]
        if not within_deadline and economy_candidates:
            fastest = min(economy_candidates, key=lambda plan: plan['duration_min'])
            extra_fleet = [plan for plan in all_candidates if plan['duration_min'] <= available_minutes]
            if extra_fleet:
                required = min(plan['uav_count'] for plan in extra_fleet)
                raise ValueError(f'Срок невыполним при ограничении {request.max_uavs or feasible_count} БВС: потребуется не менее {required} БВС. Самый быстрый план в заданном составе требует {fastest["duration_min"]} мин.')
            raise ValueError(f'Срок невыполним даже всем совместимым флотом: при лимите {request.max_uavs or feasible_count} БВС самый быстрый план требует {fastest["duration_min"]} мин. Продлите срок либо измените условия задания.')
        economy_candidates = within_deadline
    if not economy_candidates:
        if request.control_link_mode == 'radio' and all(origin_distance_km(pair) > _direct_radio_limit_km(request, pair.altitude_m) for pair in pairs):
            raise ValueError('Маршрут выходит за расчётную дальность прямой радиосвязи. Выберите площадку ближе, измените параметры канала или укажите внешний канал с отдельной проверкой покрытия.')
        if all_candidates and request.max_uavs is not None:
            required = min(plan['uav_count'] for plan in all_candidates)
            raise ValueError(f'При лимите {request.max_uavs} БВС план не найден. Для этого контура требуется не менее {required} БВС; проверьте также срок и доступность флота.')
        raise ValueError('Нет выполнимого маршрута: проверьте зоны и защитные отступы, дальность, автономность каждого вылета, радиосвязь, ресурс до ТО и размер контура. При длительной работе разбейте территорию на задания по дням.')
    fast = deepcopy(min(economy_candidates, key=lambda plan: (plan['duration_min'], plan['total_flight_time_min'])))
    fast.update(id='fast', label='Быстрый', recommended=False)
    for index, vehicle in enumerate(fast['vehicles']):
        vehicle['color'] = ['#f15a32', '#ee8b36', '#d54c79', '#aa6bd4'][index % 4]
    fast["selection_reason"] = (
        f"Минимальное время: {fast['uav_count']} БВС, число вылетов — {fast['sorties']}; работа выполняется параллельно."
    )
    # Aircraft-hours, including independent transit and return on each sortie,
    # is the Geoscan maintenance-sensitive objective. Money is reported, not
    # substituted for flight time with an arbitrary take-off penalty.
    economy = min(economy_candidates, key=lambda plan: (plan['total_flight_time_min'], plan['duration_min'], plan['cost_rub']))
    economy["selection_reason"] = (
        f"Минимальный суммарный налёт среди {len(economy_candidates)} выполнимых сочетаний: "
        f"{economy['total_flight_time_min']} мин, {economy['uav_count']} БВС, {economy['sorties']} выл. "
        "Подлёт и возврат учитываются для каждого вылета; рублёвая оценка показана отдельно."
    )
    # The reserve variant uses the same fleet/sector search space but needs
    # full geometry only near the Pareto frontier. Rebuilding every feasible
    # fleet with a different reserve doubled interactive calculation time.
    eligible = [
        (group, strategy, plan) for (group, strategy), plan in zip(allocations, evaluated_candidates)
        if plan and (request.max_uavs is None or plan['uav_count'] <= request.max_uavs)
        and (available_minutes is None or plan['duration_min'] <= available_minutes)
    ]
    ranked = sorted(eligible, key=lambda item: (item[2]['total_flight_time_min'], item[2]['duration_min']))
    fastest_candidates = sorted(eligible, key=lambda item: (item[2]['duration_min'], item[2]['total_flight_time_min']))
    priority = ranked[:4] + fastest_candidates[:2] + ranked[4:]
    safe_candidates = []
    seen_allocations = set()
    for group, strategy, _ in priority:
        key = (tuple(pair.uav['id'] for pair in group), strategy)
        if key in seen_allocations:
            continue
        seen_allocations.add(key)
        candidate = _build_plan('safe', group, planning_metric, metric_crs, to_wgs84, request,
                                len(group), planning_corridor, airspace["transit_obstacles"], metric, strategy)
        if candidate and (available_minutes is None or candidate['duration_min'] <= available_minutes):
            safe_candidates.append(candidate)
            if len(safe_candidates) >= 4:
                break
    safe = min(
        (p for p in safe_candidates if p and (request.max_uavs is None or p['uav_count'] <= request.max_uavs) and (available_minutes is None or p['duration_min'] <= available_minutes)),
        key=lambda p:(p['total_flight_time_min'], p['duration_min'], p['uav_count']), default=None,
    )
    plans = [plan for plan in [fast, economy, safe] if plan]
    if not plans:
        raise ValueError("Не удалось построить выполнимый план")
    area_km2 = metric.area / 1_000_000
    baseline_cost = fast['cost_rub']
    for plan in plans:
        plan["savings_vs_baseline_rub"] = baseline_cost - plan["cost_rub"]
    comparison = []
    for fleet_size in sorted({plan['uav_count'] for plan in economy_candidates}):
        candidate = min(
            (plan for plan in economy_candidates if plan['uav_count'] == fleet_size),
            key=lambda plan: (plan['total_flight_time_min'], plan['duration_min']),
        )
        comparison.append({
            'uav_count': fleet_size, 'sorties': candidate['sorties'],
            'duration_min': candidate['duration_min'], 'total_flight_time_min': candidate['total_flight_time_min'], 'cost_rub': candidate['cost_rub'],
            'vehicle_names': [vehicle['uav_name'] for vehicle in candidate['vehicles']],
            'vehicle_sorties': [vehicle['sorties'] for vehicle in candidate['vehicles']],
            'cost_components': {
                name: round(sum(vehicle['cost_components'].get(name, 0) for vehicle in candidate['vehicles']))
                for name in candidate['vehicles'][0]['cost_components']
            },
            'recommended': candidate is economy,
        })
    return {
        "mission": {"name": request.name, "area_km2": round(area_km2, 2), "route_length_km": round(corridor.length / 1000, 2) if corridor is not None else None, "geometry_mode": "corridor" if corridor is not None else "area", "geometry": mapping(geographic), "corridor_width_m": request.corridor_width_m if corridor is not None else None, "result_type": request.result_type, "survey_type": request.survey_type, "gsd_cm_px": request.gsd_cm_px, "payload_id": request.payload_id},
        "analysis": {
            "available_uavs": sum(1 for item in UAVS if item["status"] == "ready"),
            "compatible_uavs": len(pairs), "bases": len(BASES),
            "weather_source": "scenario-assumption",
        },
        "engineering": {
            "method": "central_projection" if request.survey_type in {"rgb", "ir", "multispectral"} else "profile_spacing",
            "altitude_formula": "H = GSD × f × Nx / Sw",
            "swath_formula": "W = H × Sw / f",
            "line_spacing_formula": "L = W × (1 − side_overlap)",
            "trigger_formula": "T = footprint_length × (1 − forward_overlap) / speed",
            "assumptions": ([
                "GSD считается в надире по модели центральной проекции.",
                "Высота ограничивается заданным потолком полёта и характеристиками БВС.",
                "Производительность учитывает полезные галсы; экономика — весь маршрут, развороты и возврат.",
            ] if request.survey_type in {"rgb", "ir", "multispectral"} else [
                "Шаг профилей и полоса захвата являются параметрами предварительного проекта.",
                "Плотность LiDAR либо чувствительность магнитной съёмки требуют паспортной модели датчика и отдельной валидации.",
                "Производительность учитывает полезные профили; экономика — весь маршрут, развороты и возврат.",
            ]),
        },
        "airspace": airspace["assessment"],
        "recommended_plan_id": "economy", "plans": plans,
        "fleet_comparison": comparison,
        "economics": {
            "method": "enumerated_feasible_fleets_with_contiguous_sectors",
            "candidate_count": len(economy_candidates),
            "uav_mobilization_cost_rub": UAV_MOBILIZATION_COST_RUB,
            "sortie_preparation_cost_rub": SORTIE_PREPARATION_COST_RUB,
            "ground_relocation_cost_included": False,
            "tariff_status": "demonstration_assumptions",
        },
    }
