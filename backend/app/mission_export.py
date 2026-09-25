from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree as ET


KML_NS = "http://www.opengis.net/kml/2.2"
GX_NS = "http://www.google.com/kml/ext/2.2"
MISSION_GPX_NS = "urn:citymetrics:mission:1"
ET.register_namespace("", KML_NS)
ET.register_namespace("gx", GX_NS)
ET.register_namespace("mission", MISSION_GPX_NS)


def _q(name: str) -> str:
    return f"{{{KML_NS}}}{name}"


def _gx(name: str) -> str:
    return f"{{{GX_NS}}}{name}"


def _selected_plan(result: dict[str, Any], plan_id: str | None) -> dict[str, Any]:
    target = plan_id or result.get("recommended_plan_id")
    plan = next((item for item in result.get("plans", []) if item.get("id") == target), None)
    if plan is None:
        raise ValueError("Вариант плана не найден")
    return plan


def _routes(result: dict[str, Any], plan_id: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    plan = _selected_plan(result, plan_id)
    routes = []
    for index, vehicle in enumerate(plan.get("vehicles", []), start=1):
        geometry = vehicle.get("route")
        if not geometry or geometry.get("type") != "LineString":
            continue
        routes.append({
            "type": "Feature",
            "properties": {
                "name": vehicle.get("uav_name") or f"БВС {index}",
                "uav_id": vehicle.get("uav_id"), "plan_id": plan.get("id"),
                "altitude_m": vehicle.get("altitude_m"), "speed_kmh": vehicle.get("speed_kmh"),
                "departure_offset_min": vehicle.get("departure_offset_min", 0),
                "planned_start_at": vehicle.get("planned_start_at"),
                "planned_completion_at": vehicle.get("planned_completion_at"),
            },
            "geometry": geometry,
        })
    if not routes:
        raise ValueError("В выбранном плане нет маршрутов для экспорта")
    return plan, routes


def build_flight_plan(result: dict[str, Any], plan_id: str | None = None) -> dict[str, Any]:
    """Return the versioned, transport-independent mission contract."""
    plan = _selected_plan(result, plan_id)
    mission = result.get("mission", {})
    return {
        "schema_name": "citymetrics-flight-mission", "schema_version": "1.0",
        "generator": "CityMetrics Mission Control", "generated_at": datetime.now(timezone.utc).isoformat(),
        "mission_id": result.get("mission_id"), "name": mission.get("name", "Полётное задание"),
        "status": result.get("status", "planned"),
        "plan": {
            "id": plan.get("id"), "name": plan.get("label"),
            "start_at": plan.get("start_at"), "completion_at": plan.get("completion_at"),
            "duration_min": plan.get("duration_min"), "flight_direction_deg": plan.get("flight_direction_deg"),
        },
        "operation_area": mission.get("geometry"), "geometry_mode": mission.get("geometry_mode"),
        "vehicles": [{
            "uav_id": vehicle.get("uav_id"), "uav_name": vehicle.get("uav_name"),
            "uav_type": vehicle.get("uav_type"), "base_id": vehicle.get("base_id"),
            "base_name": vehicle.get("base_name"), "payload_id": vehicle.get("optic_id"),
            "payload_name": vehicle.get("optic_name"), "sortie_count": vehicle.get("sorties", 1),
            "altitude_m": vehicle.get("altitude_m"), "speed_kmh": vehicle.get("speed_kmh"),
            "planned_start_at": vehicle.get("planned_start_at", plan.get("start_at")),
            "planned_completion_at": vehicle.get("planned_completion_at", plan.get("completion_at")),
            "route": vehicle.get("route"), "phases": vehicle.get("flight_phases", []),
        } for vehicle in plan.get("vehicles", [])],
    }


def _coordinates_text(coordinates, altitude: float | int | None = None) -> str:
    height = float(altitude or 0)
    return " ".join(f"{lon:.8f},{lat:.8f},{height:.1f}" for lon, lat, *_ in coordinates)


def _extended_data(parent: ET.Element, values: dict[str, Any]) -> None:
    extended = ET.SubElement(parent, _q("ExtendedData"))
    for name, value in values.items():
        item = ET.SubElement(extended, _q("Data"), {"name": name})
        ET.SubElement(item, _q("value")).text = "" if value is None else str(value)


def _add_polygon(parent: ET.Element, rings) -> None:
    polygon = ET.SubElement(parent, _q("Polygon"))
    ET.SubElement(polygon, _q("altitudeMode")).text = "clampToGround"
    for index, ring in enumerate(rings):
        boundary = ET.SubElement(polygon, _q("outerBoundaryIs" if index == 0 else "innerBoundaryIs"))
        linear = ET.SubElement(boundary, _q("LinearRing"))
        ET.SubElement(linear, _q("coordinates")).text = _coordinates_text(ring)


def _add_operation_area(document: ET.Element, geometry: dict[str, Any] | None) -> None:
    if not geometry or geometry.get("type") not in {"Polygon", "MultiPolygon", "LineString", "MultiLineString"}:
        return
    placemark = ET.SubElement(document, _q("Placemark"))
    ET.SubElement(placemark, _q("name")).text = "Область выполнения работ"
    _extended_data(placemark, {"object_type": "OPERATION_AREA", "geometry_type": geometry["type"]})
    if geometry["type"] == "Polygon":
        _add_polygon(placemark, geometry["coordinates"])
    elif geometry["type"] == "MultiPolygon":
        multi = ET.SubElement(placemark, _q("MultiGeometry"))
        for polygon in geometry["coordinates"]:
            _add_polygon(multi, polygon)
    else:
        lines = [geometry["coordinates"]] if geometry["type"] == "LineString" else geometry["coordinates"]
        container = placemark if len(lines) == 1 else ET.SubElement(placemark, _q("MultiGeometry"))
        for coordinates in lines:
            line = ET.SubElement(container, _q("LineString"))
            ET.SubElement(line, _q("tessellate")).text = "1"
            ET.SubElement(line, _q("coordinates")).text = _coordinates_text(coordinates)


def _add_phase(folder: ET.Element, phase: dict[str, Any], vehicle_id: str) -> None:
    phase_folder = ET.SubElement(folder, _q("Folder"), {"id": f"{vehicle_id}-{phase.get('id', 'phase')}"})
    name = str(phase.get("name", phase.get("type", "Этап")))
    ET.SubElement(phase_folder, _q("name")).text = name
    timespan = ET.SubElement(phase_folder, _q("TimeSpan"))
    ET.SubElement(timespan, _q("begin")).text = phase.get("start_at")
    ET.SubElement(timespan, _q("end")).text = phase.get("end_at")
    _extended_data(phase_folder, {
        "phase_type": phase.get("type"), "sequence": phase.get("sequence"),
        "duration_s": phase.get("duration_s"), "planned_speed_kmh": phase.get("planned_speed_kmh"),
        "start_altitude_m": phase.get("start_altitude_m"), "end_altitude_m": phase.get("end_altitude_m"),
        "payload_active": str(bool(phase.get("payload_active"))).lower(),
    })
    geometry = phase.get("geometry", {})
    placemark = ET.SubElement(phase_folder, _q("Placemark"))
    ET.SubElement(placemark, _q("name")).text = f"{name} — геометрия"
    if geometry.get("type") == "Point":
        point = ET.SubElement(placemark, _q("Point"))
        ET.SubElement(point, _q("altitudeMode")).text = "relativeToGround"
        ET.SubElement(point, _q("coordinates")).text = _coordinates_text([geometry.get("coordinates", [0, 0])], phase.get("end_altitude_m"))
    elif geometry.get("type") == "LineString":
        line = ET.SubElement(placemark, _q("LineString"))
        ET.SubElement(line, _q("tessellate")).text = "1"
        ET.SubElement(line, _q("altitudeMode")).text = "relativeToGround"
        trajectory = phase.get("trajectory", [])
        if trajectory and len(trajectory) == len(geometry.get("coordinates", [])):
            coordinates = " ".join(f"{point['lon']:.8f},{point['lat']:.8f},{float(point['altitude_m']):.1f}" for point in trajectory)
        else:
            coordinates = _coordinates_text(geometry.get("coordinates", []), phase.get("end_altitude_m"))
        ET.SubElement(line, _q("coordinates")).text = coordinates
    trajectory = phase.get("trajectory", [])
    if trajectory:
        track_placemark = ET.SubElement(phase_folder, _q("Placemark"))
        ET.SubElement(track_placemark, _q("name")).text = f"{name} — плановая временная траектория"
        track = ET.SubElement(track_placemark, _gx("Track"))
        ET.SubElement(track, _q("altitudeMode")).text = "relativeToGround"
        ET.SubElement(track, _gx("interpolate")).text = "1"
        for point in trajectory:
            ET.SubElement(track, _q("when")).text = point["time"]
        for point in trajectory:
            ET.SubElement(track, _gx("coord")).text = f"{point['lon']:.8f} {point['lat']:.8f} {float(point['altitude_m']):.1f}"


def _kml_content(flight_plan: dict[str, Any]) -> bytes:
    root = ET.Element(_q("kml"))
    document = ET.SubElement(root, _q("Document"), {"id": "flight-mission"})
    ET.SubElement(document, _q("name")).text = flight_plan["name"]
    _extended_data(document, {
        "schema_name": flight_plan["schema_name"], "schema_version": flight_plan["schema_version"],
        "generator": flight_plan["generator"], "generated_at": flight_plan["generated_at"],
        "mission_id": flight_plan.get("mission_id"), "plan_id": flight_plan["plan"].get("id"),
        "start_at": flight_plan["plan"].get("start_at"), "completion_at": flight_plan["plan"].get("completion_at"),
    })
    area_folder = ET.SubElement(document, _q("Folder"))
    ET.SubElement(area_folder, _q("name")).text = "Территория задания"
    _add_operation_area(area_folder, flight_plan.get("operation_area"))
    vehicles_folder = ET.SubElement(document, _q("Folder"))
    ET.SubElement(vehicles_folder, _q("name")).text = "Полёты БВС"
    for vehicle in flight_plan["vehicles"]:
        vehicle_folder = ET.SubElement(vehicles_folder, _q("Folder"), {"id": f"uav-{vehicle.get('uav_id') or 'unknown'}"})
        ET.SubElement(vehicle_folder, _q("name")).text = vehicle.get("uav_name") or "БВС"
        _extended_data(vehicle_folder, {
            "uav_id": vehicle.get("uav_id"), "uav_type": vehicle.get("uav_type"),
            "base_id": vehicle.get("base_id"), "base_name": vehicle.get("base_name"),
            "payload_id": vehicle.get("payload_id"), "payload_name": vehicle.get("payload_name"),
            "sortie_count": vehicle.get("sortie_count"),
        })
        phases_folder = ET.SubElement(vehicle_folder, _q("Folder"))
        ET.SubElement(phases_folder, _q("name")).text = "Этапы полёта"
        for phase in vehicle.get("phases", []):
            _add_phase(phases_folder, phase, str(vehicle.get("uav_id") or "uav"))
        route = vehicle.get("route")
        if route and route.get("type") == "LineString":
            placemark = ET.SubElement(vehicle_folder, _q("Placemark"))
            name = ET.SubElement(placemark, _q("name"))
            line = ET.SubElement(placemark, _q("LineString"))
            ET.SubElement(line, _q("tessellate")).text = "1"
            trajectory = [point for phase in vehicle.get("phases", []) for point in phase.get("trajectory", [])]
            if trajectory:
                name.text = "Маршрут целиком (профиль высоты AGL)"
                ET.SubElement(line, _q("altitudeMode")).text = "relativeToGround"
                ET.SubElement(line, _q("coordinates")).text = " ".join(
                    f"{point['lon']:.8f},{point['lat']:.8f},{float(point['altitude_m']):.1f}"
                    for point in trajectory
                )
            else:
                # A 2D legacy route has no validated vertical profile.
                name.text = "Маршрут целиком (только план, без профиля высоты)"
                ET.SubElement(line, _q("altitudeMode")).text = "clampToGround"
                ET.SubElement(line, _q("coordinates")).text = " ".join(
                    f"{lon:.8f},{lat:.8f}" for lon, lat, *_ in route["coordinates"]
                )
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def export_mission(result: dict[str, Any], output_format: str, plan_id: str | None = None) -> tuple[bytes, str, str]:
    output_format = output_format.lower()
    plan, routes = _routes(result, plan_id)
    flight_plan = build_flight_plan(result, plan_id)
    mission_id = result.get("mission_id", "mission")
    base_name = f"mission-{mission_id}-{plan.get('id', 'plan')}"
    if output_format == "geojson":
        phase_features = [
            {"type": "Feature", "properties": {key: value for key, value in phase.items() if key not in {"geometry", "trajectory"}}, "geometry": phase["geometry"]}
            for vehicle in flight_plan["vehicles"] for phase in vehicle.get("phases", []) if phase.get("geometry")
        ]
        payload = {"type": "FeatureCollection", "name": flight_plan["name"], "features": [*routes, *phase_features]}
        return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"), "application/geo+json", f"{base_name}.geojson"
    if output_format == "kml":
        return _kml_content(flight_plan), "application/vnd.google-earth.kml+xml", f"{base_name}.kml"
    if output_format == "gpx":
        root = ET.Element("gpx", {"version": "1.1", "creator": "CityMetrics Mission Control", "xmlns": "http://www.topografix.com/GPX/1/1"})
        for vehicle in flight_plan["vehicles"]:
            track = ET.SubElement(root, "trk")
            ET.SubElement(track, "name").text = vehicle.get("uav_name") or "БВС"
            segment = ET.SubElement(track, "trkseg")
            for phase in vehicle.get("phases", []):
                for point in phase.get("trajectory", []):
                    item = ET.SubElement(segment, "trkpt", {"lat": f"{point['lat']:.8f}", "lon": f"{point['lon']:.8f}"})
                    ET.SubElement(item, "time").text = point["time"]
                    # GPX elevation is not an AGL field; keep the planning
                    # height in a namespaced extension instead of mislabelling it.
                    extension = ET.SubElement(item, "extensions")
                    ET.SubElement(extension, f"{{{MISSION_GPX_NS}}}altitude_agl_m").text = f"{float(point['altitude_m']):.1f}"
            if not vehicle.get("phases") and vehicle.get("route"):
                for lon, lat, *_ in vehicle["route"]["coordinates"]:
                    ET.SubElement(segment, "trkpt", {"lat": f"{lat:.8f}", "lon": f"{lon:.8f}"})
        ET.indent(root, space="  ")
        return ET.tostring(root, encoding="utf-8", xml_declaration=True), "application/gpx+xml", f"{base_name}.gpx"
    raise ValueError("Поддерживаются форматы kml, geojson и gpx")
