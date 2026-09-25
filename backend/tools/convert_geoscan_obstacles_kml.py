"""Convert the Geoscan Primorsky KML obstacle catalogue to seed GeoJSON.

Polygon footprints are preserved in WGS 84. KML LineString obstacles are
expanded by 30 metres so the route planner can apply its configured safety
clearance to an areal obstacle instead of a zero-width line.
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from pyproj import CRS, Transformer
from shapely.geometry import LineString, mapping
from shapely.ops import transform


KML = {"kml": "http://www.opengis.net/kml/2.2"}
LINE_BUFFER_METRES = 30.0


def coordinates(text: str | None) -> list[list[float]]:
    result: list[list[float]] = []
    for token in (text or "").split():
        values = token.split(",")
        if len(values) >= 2:
            result.append([float(values[0]), float(values[1]), float(values[2]) if len(values) > 2 else 0.0])
    return result


def polygon_geometry(placemark: ET.Element) -> tuple[dict, list[float]] | None:
    polygon = placemark.find(".//kml:Polygon", KML)
    if polygon is None:
        return None
    rings: list[list[list[float]]] = []
    altitudes: list[float] = []
    outer = polygon.find("./kml:outerBoundaryIs/kml:LinearRing/kml:coordinates", KML)
    candidates = [outer, *polygon.findall("./kml:innerBoundaryIs/kml:LinearRing/kml:coordinates", KML)]
    for candidate in candidates:
        if candidate is None:
            continue
        raw = coordinates(candidate.text)
        if len(raw) < 4:
            continue
        rings.append([[lon, lat] for lon, lat, _ in raw])
        altitudes.extend(point[2] for point in raw)
    return ({"type": "Polygon", "coordinates": rings}, altitudes) if rings else None


def buffered_line_geometry(placemark: ET.Element) -> tuple[dict, list[float]] | None:
    element = placemark.find(".//kml:LineString/kml:coordinates", KML)
    if element is None:
        return None
    raw = coordinates(element.text)
    if len(raw) < 2:
        return None
    line = LineString([(lon, lat) for lon, lat, _ in raw])
    center = line.centroid
    local = CRS.from_proj4(f"+proj=aeqd +lat_0={center.y} +lon_0={center.x} +datum=WGS84 +units=m +no_defs")
    forward = Transformer.from_crs("EPSG:4326", local, always_xy=True).transform
    backward = Transformer.from_crs(local, "EPSG:4326", always_xy=True).transform
    footprint = transform(backward, transform(forward, line).buffer(LINE_BUFFER_METRES, cap_style="round", join_style="round"))
    return mapping(footprint), [point[2] for point in raw]


def convert(source: Path) -> dict:
    root = ET.parse(source).getroot()
    features = []
    for index, placemark in enumerate(root.findall(".//kml:Placemark", KML), start=1):
        name = (placemark.findtext("kml:name", default="", namespaces=KML) or "").strip() or f"Препятствие {index}"
        parsed = polygon_geometry(placemark)
        original_geometry = "Polygon"
        if parsed is None:
            parsed = buffered_line_geometry(placemark)
            original_geometry = "LineString"
        if parsed is None:
            continue
        geometry, altitudes = parsed
        height = max(altitudes, default=0.0)
        code = name.split()[0]
        obstacle_type = name.split("OTHER:", 1)[1] if "OTHER:" in name else None
        properties = {
            "name": name,
            "code": code,
            "upper_limit": f"{height:.1f} м AGL",
            "vertical_definition": f"Высота препятствия {height:.1f} м относительно земли",
            "height_m": round(height, 3),
            "altitude_mode": "relativeToGround",
            "obstacle_type": obstacle_type,
            "original_geometry": original_geometry,
            "source_organization": "Геоскан",
            "source_file": source.name,
        }
        if original_geometry == "LineString":
            properties["line_buffer_m"] = LINE_BUFFER_METRES
        features.append({"type": "Feature", "properties": properties, "geometry": geometry})
    return {
        "type": "FeatureCollection",
        "name": "Высотные препятствия Геоскан — Приморский край",
        "features": features,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    collection = convert(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(collection, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Converted {len(collection['features'])} obstacles to {args.output}")


if __name__ == "__main__":
    main()
