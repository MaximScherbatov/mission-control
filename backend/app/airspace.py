from __future__ import annotations

import json
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable


DATA_DIR = Path(__file__).with_name("data") / "airspace"
DATASETS = (
    ("prohibited_zones_rf_2026.geojson", "prohibited", "Запретные зоны РФ, 2026"),
    ("danger_zones_rf_2022.geojson", "danger", "Опасные зоны РФ, 2022"),
    ("orvd_zones_order_248.geojson", "orvd", "Зоны ЕС ОрВД, приказ № 248"),
    ("demo_height_obstacles.geojson", "obstacle", "Демонстрационные высотные объекты"),
)


def _coordinates(geometry: dict[str, Any]) -> Iterable[tuple[float, float]]:
    def walk(value: Any):
        if isinstance(value, (list, tuple)):
            if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
                yield float(value[0]), float(value[1])
            else:
                for item in value:
                    yield from walk(item)

    yield from walk(geometry.get("coordinates", []))


def geometry_bbox(geometry: dict[str, Any]) -> list[float]:
    points = list(_coordinates(geometry))
    if not points:
        raise ValueError("Геометрия не содержит координат")
    if any(abs(lon) > 180 or abs(lat) > 90 for lon, lat in points):
        raise ValueError("Координаты должны быть в WGS 84: долгота, широта")
    return [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]


def bbox_intersects(left: list[float], right: list[float]) -> bool:
    return left[0] <= right[2] and left[2] >= right[0] and left[1] <= right[3] and left[3] >= right[1]


def _first(properties: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = properties.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def normalize_feature(
    feature: dict[str, Any], category: str, source_name: str, index: int, *, editable: bool = False,
) -> dict[str, Any]:
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("type") not in {"Point", "Polygon", "MultiPolygon"}:
        raise ValueError(f"Объект {index + 1}: неподдерживаемая геометрия")
    properties = deepcopy(feature.get("properties") or {})
    code = _first(properties, "zone_code", "zone_order_number", "code", "id")
    name = _first(properties, "zone_name", "name", "title") or code or f"Зона {index + 1}"
    seed_key = f"{source_name}:{index}:{code or name}"
    zone_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"citymetrics-airspace:{seed_key}"))
    return {
        "id": zone_id,
        "zone_key": seed_key,
        "category": category,
        "name": name,
        "code": code,
        "geometry": geometry,
        "bbox": geometry_bbox(geometry),
        "lower_limit": _first(properties, "lower_limit"),
        "upper_limit": _first(properties, "upper_limit"),
        "vertical_definition": _first(properties, "official_vertical_definition", "vertical_definition", "fpln_altitude_text"),
        "schedule": _first(properties, "official_operating_schedule", "fpln_schedule_text"),
        "valid_from": _first(properties, "effective_from"),
        "valid_until": _first(properties, "official_valid_until"),
        "enabled": True,
        "editable": editable,
        "source_name": source_name,
        "properties": properties,
    }


def load_seed_zones() -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []
    for filename, category, label in DATASETS:
        path = DATA_DIR / filename
        with path.open("r", encoding="utf-8-sig") as source:
            collection = json.load(source)
        for index, feature in enumerate(collection.get("features", [])):
            zone = normalize_feature(feature, category, label, index)
            zone["properties"]["dataset_file"] = filename
            zones.append(zone)
    return zones


def feature_view(zone: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    properties = {
        "id": zone["id"],
        "category": zone["category"],
        "name": zone["name"],
        "code": zone.get("code"),
        "bbox": zone.get("bbox"),
        "lower_limit": zone.get("lower_limit"),
        "upper_limit": zone.get("upper_limit"),
        "vertical_definition": zone.get("vertical_definition"),
        "schedule": zone.get("schedule"),
        "valid_from": zone.get("valid_from"),
        "valid_until": zone.get("valid_until"),
        "enabled": zone.get("enabled", True),
        "editable": zone.get("editable", False),
        "source_name": zone.get("source_name"),
    }
    if include_raw:
        properties["source_properties"] = zone.get("properties", {})
    return {"type": "Feature", "id": zone["id"], "properties": properties, "geometry": zone["geometry"]}


def filter_zones(
    zones: Iterable[dict[str, Any]], category: str | None = None, bbox: list[float] | None = None,
    query: str | None = None, limit: int = 2000,
) -> list[dict[str, Any]]:
    needle = (query or "").casefold().strip()
    result = []
    for zone in zones:
        if category and zone["category"] != category:
            continue
        if bbox and not bbox_intersects(zone["bbox"], bbox):
            continue
        if needle and needle not in f"{zone.get('code') or ''} {zone.get('name') or ''}".casefold():
            continue
        result.append(zone)
        if len(result) >= limit:
            break
    return result


def summary(zones: Iterable[dict[str, Any]]) -> dict[str, Any]:
    counts = {"prohibited": 0, "danger": 0, "orvd": 0, "obstacle": 0, "custom": 0}
    editable = 0
    for zone in zones:
        counts[zone["category"]] = counts.get(zone["category"], 0) + 1
        editable += int(zone.get("editable", False))
    return {"total": sum(counts.values()), "counts": counts, "editable": editable}
