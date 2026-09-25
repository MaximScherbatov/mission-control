"""Deterministic operational demo with complete, physically coherent sorties."""
from __future__ import annotations

import math
import time
from bisect import bisect_right
from functools import lru_cache
from typing import NamedTuple

START = time.monotonic()
GROUND_SERVICE_SECONDS = 5 * 60
RUNWAY_HEADINGS = {
    'Полевой аэродром Клин': 64,
    'Аэродром Дмитров': 82,
    'Площадка Яхрома': 90,
    'Площадка Сергиев Посад': 118,
}


class Site(NamedTuple):
    # The first nine fields intentionally preserve the original tuple contract.
    lon: float
    lat: float
    color: str
    uav_id: str
    name: str
    uav_type: str
    speed_kmh: float
    altitude_m: float
    task_name: str
    base_lon: float
    base_lat: float
    base_name: str
    scenario: str
    phase_offset: float
    detour: tuple[tuple[float, float], ...] = ()


# A compact demonstration theatre north of Moscow. Every base and survey area is
# outside the supplied prohibited polygons. Aircraft departing the same base use
# separate lateral corridors; controlled encounters are parallel and vertically
# separated rather than drawn on top of one another.
SITES = [
    Site(36.81, 56.285, '#f15a32', 'uav-geoscan-201-01', 'Геоскан 201 · 01', 'fixed_wing', 84, 150, 'Ортофото · Истринский лес', 36.72, 56.24, 'Полевой аэродром Клин', 'облёт запретной зоны UUP408', .02, ((36.765, 56.255), (36.785, 56.270))),
    Site(36.86, 56.175, '#22b5e5', 'uav-geoscan-401-geo', 'Геоскан 401 · Геодезия', 'multirotor', 38, 95, 'Исполнительная съёмка · водозабор', 36.72, 56.24, 'Полевой аэродром Клин', 'точечное препятствие · безопасный радиус 80 м', .20, ((36.775, 56.215), (36.820, 56.205))),
    Site(37.07, 56.305, '#8c7cff', 'uav-geoscan-gemini-01', 'Геоскан Gemini · 01', 'multirotor', 34, 78, 'Теплотрасса · северный участок', 36.95, 56.35, 'Площадка Яхрома', 'коридорная ИК-съёмка', .48, ((37.005, 56.335), (37.035, 56.320))),
    Site(37.18, 56.205, '#23b26d', 'uav-geoscan-701-01', 'Геоскан 701 · 01', 'fixed_wing', 98, 165, 'ЛЭП · западный коридор', 37.35, 56.25, 'Аэродром Дмитров', 'встречное движение: северный коридор · эшелон 165 м', .54, ((37.305, 56.255), (37.250, 56.245), (37.205, 56.228))),
    Site(37.31, 56.215, '#f4a340', 'uav-geoscan-201-02', 'Геоскан 201 · 02', 'fixed_wing', 82, 135, 'NDVI · опытные поля', 37.35, 56.25, 'Аэродром Дмитров', 'юго-восточный коридор · эшелон 135 м', .72, ((37.372, 56.238), (37.360, 56.216), (37.335, 56.205))),
    Site(37.47, 56.320, '#e9699f', 'uav-geoscan-401-lidar', 'Геоскан 401 · Лидар', 'multirotor', 32, 110, 'LiDAR · карьер', 37.35, 56.25, 'Аэродром Дмитров', 'облёт высотной мачты', .989, ((37.395, 56.285), (37.430, 56.300))),
    Site(37.58, 56.225, '#56c5b0', 'uav-geoscan-401-mag', 'Геоскан 401 · Геофизика', 'multirotor', 28, 70, 'Магнитная съёмка · полигон', 37.55, 56.27, 'Площадка Сергиев Посад', 'восточный коридор · обход ретранслятора', .08, ((37.590, 56.262), (37.612, 56.244), (37.602, 56.228), (37.565, 56.220))),
    Site(37.72, 56.300, '#5e92f3', 'uav-geoscan-gemini-ms', 'Геоскан Gemini · МС', 'multirotor', 36, 90, 'Мультиспектр · лесной квартал', 37.55, 56.27, 'Площадка Сергиев Посад', 'восточный коридор вылета · эшелон 90 м', .49, ((37.610, 56.285), (37.665, 56.298))),
    Site(37.43, 56.125, '#cf6fe6', 'uav-geoscan-gemini-02', 'Геоскан Gemini · 02', 'multirotor', 35, 120, '3D-модель · промышленная зона', 37.55, 56.27, 'Площадка Сергиев Посад', 'юго-западный коридор · эшелон 120 м', .70, ((37.510, 56.252), (37.478, 56.212), (37.450, 56.168))),
    Site(37.20, 56.085, '#9fbe3f', 'uav-geoscan-201-03', 'Геоскан 201 · 03', 'fixed_wing', 86, 145, 'Ортофото · резервный участок', 37.35, 56.25, 'Аэродром Дмитров', 'восточный обход · эшелон 145 м', .985, ((37.415, 56.238), (37.438, 56.182), (37.392, 56.128), (37.305, 56.092))),
]


def geographic(site: Site, x: float, y: float) -> list[float]:
    return [site.lon + x / (111320 * math.cos(math.radians(site.lat))), site.lat + y / 110540]


def local(site: Site, lon: float, lat: float) -> tuple[float, float]:
    return ((lon - site.lon) * 111320 * math.cos(math.radians(site.lat)), (lat - site.lat) * 110540)


def _survey_dimensions(site: Site) -> tuple[float, float, int]:
    # Tighter lanes make the survey pattern visually and operationally denser.
    radius = 72 if site.uav_type == 'fixed_wing' else 26
    half = 560 if site.uav_type == 'fixed_wing' else 270
    return radius, half, 12


def _survey_track(site: Site) -> list[tuple[float, float]]:
    radius, half, rows = _survey_dimensions(site)
    points: list[tuple[float, float]] = [(-half, -rows * radius)]
    for row in range(rows):
        y = (row * 2 - rows) * radius
        side = 1 if row % 2 == 0 else -1
        points.append((side * half, y))
        if row < rows - 1:
            center_y = y + radius
            points.extend((side * half + side * radius * math.cos(-math.pi/2 + i*math.pi/30), center_y + radius * math.sin(-math.pi/2 + i*math.pi/30)) for i in range(1, 31))
    return points


def _chaikin(points: list[tuple[float, float]], iterations: int = 4) -> list[tuple[float, float]]:
    """Corner-cutting spline that preserves route endpoints."""
    current = points
    for _ in range(iterations):
        refined = [current[0]]
        for start, end in zip(current, current[1:]):
            refined.extend(((start[0]*.75+end[0]*.25, start[1]*.75+end[1]*.25), (start[0]*.25+end[0]*.75, start[1]*.25+end[1]*.75)))
        refined.append(current[-1])
        current = refined
    return current


def _cubic_bezier(start, control_1, control_2, end, samples: int = 36):
    return [
        (
            (1-t)**3*start[0] + 3*(1-t)**2*t*control_1[0] + 3*(1-t)*t*t*control_2[0] + t**3*end[0],
            (1-t)**3*start[1] + 3*(1-t)**2*t*control_1[1] + 3*(1-t)*t*t*control_2[1] + t**3*end[1],
        )
        for t in (index/samples for index in range(samples+1))
    ]


def route_parts(site: Site) -> tuple[list[tuple[float, float]], list[tuple[float, float]], list[tuple[float, float]]]:
    """Build short transit legs with tangent joins into and out of the survey."""
    survey = _survey_track(site)
    base = local(site, site.base_lon, site.base_lat)
    # An even lane count leaves both endpoints on one side, with opposing
    # tangents. Mirror the pattern toward the base so neither connector loops.
    if base[0] > 0:
        survey = [(-x, y) for x, y in survey]
    entry = survey[0]
    exit_point = survey[-1]
    heading = math.radians(RUNWAY_HEADINGS.get(site.base_name, 90))
    runway = (math.sin(heading), math.cos(heading))
    entry_length = max(math.dist(survey[0], survey[1]), 1)
    entry_direction = ((survey[1][0]-survey[0][0])/entry_length, (survey[1][1]-survey[0][1])/entry_length)
    exit_length = max(math.dist(survey[-2], survey[-1]), 1)
    exit_direction = ((survey[-1][0]-survey[-2][0])/exit_length, (survey[-1][1]-survey[-2][1])/exit_length)

    def direction_toward(start, end, candidate):
        direct = (end[0]-start[0], end[1]-start[1])
        return candidate if direct[0]*candidate[0] + direct[1]*candidate[1] >= 0 else (-candidate[0], -candidate[1])

    outbound_distance = max(math.dist(base, entry), 1)
    departure_direction = direction_toward(base, entry, runway) if site.uav_type == 'fixed_wing' else (entry[0]-base[0], entry[1]-base[1])
    departure_norm = max(math.hypot(*departure_direction), 1)
    departure_direction = (departure_direction[0]/departure_norm, departure_direction[1]/departure_norm)
    outbound_handle = min(720, max(90, outbound_distance*.22))
    outbound = _cubic_bezier(
        base,
        (base[0]+departure_direction[0]*outbound_handle, base[1]+departure_direction[1]*outbound_handle),
        (entry[0]-entry_direction[0]*outbound_handle, entry[1]-entry_direction[1]*outbound_handle),
        entry,
        samples=56,
    )

    inbound_distance = max(math.dist(exit_point, base), 1)
    arrival_direction = direction_toward(exit_point, base, runway) if site.uav_type == 'fixed_wing' else (base[0]-exit_point[0], base[1]-exit_point[1])
    arrival_norm = max(math.hypot(*arrival_direction), 1)
    arrival_direction = (arrival_direction[0]/arrival_norm, arrival_direction[1]/arrival_norm)
    inbound_handle = min(720, max(90, inbound_distance*.22))
    inbound = _cubic_bezier(
        exit_point,
        (exit_point[0]+exit_direction[0]*inbound_handle, exit_point[1]+exit_direction[1]*inbound_handle),
        (base[0]-arrival_direction[0]*inbound_handle, base[1]-arrival_direction[1]*inbound_handle),
        base,
        samples=56,
    )
    return outbound, survey, inbound


def route_profile(site: Site) -> tuple[list[tuple[float, float]], float, float]:
    outbound, survey, inbound = route_parts(site)
    points = outbound + survey[1:] + inbound[1:]
    out_end = sum(math.dist(a, b) for a, b in zip(outbound, outbound[1:]))
    survey_end = out_end + sum(math.dist(a, b) for a, b in zip(survey, survey[1:]))
    return points, out_end, survey_end


def track(site: Site) -> list[tuple[float, float]]:
    return route_profile(site)[0]


def sample(points: list[tuple[float, float]], distance: float):
    lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
    total = sum(lengths)
    progress = distance % total
    remaining = progress
    for a, b, length in zip(points, points[1:], lengths):
        if remaining <= length:
            ratio = remaining / max(length, 1e-9)
            return (a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio), math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360, progress, total
        remaining -= length
    return points[0], 90, 0, total


def geometry():
    features = []
    for site in SITES:
        survey = _survey_track(site)
        xs, ys = zip(*survey)
        ring = [geographic(site, x, y) for x, y in ((min(xs)-55,min(ys)-55),(max(xs)+55,min(ys)-55),(max(xs)+55,max(ys)+55),(min(xs)-55,max(ys)+55),(min(xs)-55,min(ys)-55))]
        features.append({'type':'Feature','properties':{'color':site.color,'name':site.task_name,'scenario':site.scenario},'geometry':{'type':'Polygon','coordinates':[ring]}})
    return {'type':'FeatureCollection','features':features}


def _phase(site: Site, progress: float, total: float, out_end: float, survey_end: float):
    climb_distance = min(out_end * .48, 1250 if site.uav_type == 'fixed_wing' else 420)
    landing_distance = min((total-survey_end) * .52, 1450 if site.uav_type == 'fixed_wing' else 460)
    if progress < climb_distance:
        ratio = progress / max(climb_distance, 1)
        return 'takeoff', 'Взлёт и набор высоты', ratio * site.altitude_m, max(18, site.speed_kmh * (.38 + .62 * ratio))
    if progress < out_end:
        return 'transit', 'Перелёт к объекту', site.altitude_m, site.speed_kmh
    if progress < survey_end:
        return 'surveying', 'Выполнение съёмки', site.altitude_m + 1.8 * math.sin(progress / 240), site.speed_kmh * (.76 if site.uav_type == 'fixed_wing' else .72)
    if progress < total - landing_distance:
        return 'returning', 'Возврат на площадку', site.altitude_m, site.speed_kmh
    ratio = (total - progress) / max(landing_distance, 1)
    return 'landing', 'Заход и посадка', ratio * site.altitude_m, max(12, site.speed_kmh * (.30 + .70 * ratio))


@lru_cache(maxsize=len(SITES))
def _motion_timeline(site: Site):
    """Map simulation time to distance using the same phase speed sent as telemetry."""
    points, out_end, survey_end = route_profile(site)
    _, _, _, total = sample(points, 0)
    step_m = 5.0
    distances = [0.0]
    times = [0.0]
    distance = 0.0
    while distance < total:
        next_distance = min(total, distance + step_m)
        midpoint = (distance + next_distance) / 2
        *_, speed_kmh = _phase(site, midpoint, total, out_end, survey_end)
        times.append(times[-1] + (next_distance - distance) / max(speed_kmh / 3.6, .25))
        distances.append(next_distance)
        distance = next_distance
    return tuple(distances), tuple(times), total


def _cycle_clock(site: Site, elapsed: float) -> tuple[float, float, float]:
    distances, times, total = _motion_timeline(site)
    offset_distance = site.phase_offset * total
    offset_index = min(len(distances) - 2, max(0, bisect_right(distances, offset_distance) - 1))
    distance_span = distances[offset_index + 1] - distances[offset_index]
    offset_ratio = (offset_distance - distances[offset_index]) / max(distance_span, 1e-9)
    offset_time = times[offset_index] + (times[offset_index + 1] - times[offset_index]) * offset_ratio
    cycle_duration = times[-1] + GROUND_SERVICE_SECONDS
    clock = (elapsed + offset_time) % cycle_duration
    return clock, times[-1], cycle_duration


def _distance_at_time(site: Site, elapsed: float) -> tuple[float, bool, float]:
    distances, times, total = _motion_timeline(site)
    clock, flight_duration, _ = _cycle_clock(site, elapsed)
    if clock >= flight_duration:
        return total - 1e-6, True, clock - flight_duration
    index = min(len(times) - 2, max(0, bisect_right(times, clock) - 1))
    time_span = times[index + 1] - times[index]
    ratio = (clock - times[index]) / max(time_span, 1e-9)
    return distances[index] + (distances[index + 1] - distances[index]) * ratio, False, 0.0


def simulation_elapsed() -> float:
    return time.monotonic() - START


def snapshot(elapsed=None, simulation_rate: float = 1, include_geometry: bool = True):
    elapsed = time.monotonic() - START if elapsed is None else elapsed
    vehicles = []
    route_features = []
    for index, site in enumerate(SITES):
        points, out_end, survey_end = route_profile(site)
        if include_geometry:
            outbound, survey, inbound = route_parts(site)
        _, _, _, total = sample(points, 0)
        distance, servicing, service_elapsed = _distance_at_time(site, elapsed)
        cycle_clock, flight_duration, cycle_duration = _cycle_clock(site, elapsed)
        next_takeoff = (cycle_duration - cycle_clock) % cycle_duration
        next_landing = flight_duration - cycle_clock if cycle_clock < flight_duration else cycle_duration - cycle_clock + flight_duration
        position, heading, progress, _ = sample(points, distance)
        if servicing:
            phase, phase_label, altitude, speed = 'servicing', 'Наземное обслуживание · замена АКБ и карты памяти', 0.0, 0.0
        else:
            phase, phase_label, altitude, speed = _phase(site, progress, total, out_end, survey_end)
        next_distance, next_servicing, _ = _distance_at_time(site, elapsed + 1)
        forward_distance = 0 if servicing or next_servicing else (next_distance - distance) % total
        _, next_heading, next_progress, _ = sample(points, distance + forward_distance)
        _, _, next_altitude, _ = _phase(site, next_progress, total, out_end, survey_end)
        if next_servicing:
            next_altitude = 0.0
        vertical_speed = next_altitude - altitude
        turn_rate = math.radians((next_heading - heading + 180) % 360 - 180)
        roll_limit = 32 if site.uav_type == 'fixed_wing' else 22
        roll = max(-roll_limit, min(roll_limit, math.degrees(math.atan((speed / 3.6) * turn_rate / 9.80665))))
        pitch = max(-10, min(10, math.degrees(math.atan2(vertical_speed, max(speed / 3.6, .5)))))
        lon, lat = geographic(site, *position)
        vehicles.append(dict(
            uav_id=site.uav_id, name=site.name, uav_type=site.uav_type, task_name=site.task_name,
            color=site.color, lon=lon, lat=lat, speed_kmh=round(speed, 1), altitude_m=round(max(0, altitude), 1),
            heading_deg=round(heading, 1), yaw_deg=round(heading, 1), pitch_deg=round(pitch, 1), roll_deg=round(roll, 1),
            vertical_speed_mps=round(vertical_speed, 1), battery_percent=round(47 + service_elapsed/GROUND_SERVICE_SECONDS*49 if servicing else 96 - progress / total * 49, 1),
            link_quality_percent=max(81, 97-index), satellites=23-index//2, mission_progress_percent=100.0 if servicing else round(progress/total*100, 1),
            status=phase, phase_label=phase_label, compliance_note=site.scenario, base_name=site.base_name,
            next_takeoff_seconds=round(next_takeoff, 1), next_landing_seconds=round(next_landing, 1),
            source='controlled-simulator', simulation_rate=simulation_rate,
        ))
        if include_geometry:
            common = {'color':site.color,'name':site.task_name,'scenario':site.scenario,'uav_id':site.uav_id}
            route_features.extend([
                {'type':'Feature','properties':{**common,'kind':'transit','direction':'outbound'},'geometry':{'type':'LineString','coordinates':[geographic(site,*point) for point in outbound]}},
                {'type':'Feature','properties':{**common,'kind':'coverage','direction':'survey'},'geometry':{'type':'LineString','coordinates':[geographic(site,*point) for point in survey]}},
                {'type':'Feature','properties':{**common,'kind':'transit','direction':'inbound'},'geometry':{'type':'LineString','coordinates':[geographic(site,*point) for point in inbound]}},
            ])
    packet = {'type':'fleet_telemetry','simulation_rate':simulation_rate,'vehicles':vehicles}
    if include_geometry:
        packet.update(areas=geometry(), routes={'type':'FeatureCollection','features':route_features})
    return packet
