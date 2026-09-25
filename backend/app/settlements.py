"""Locally cached OSM settlement boundaries with explicit coverage provenance.

OSM is an operational map layer, not proof of a legal boundary.  A missing
polygon is never interpreted as an empty/checked area without a coverage row.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shapely.geometry import LineString, Polygon, mapping, shape
from shapely.ops import polygonize, unary_union


OVERPASS_URL = os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
TILE_DEGREES = 0.2
MAX_ON_DEMAND_TILES = 4
REFRESH_DAYS = 30
DEFAULT_DB = Path(__file__).parent / "data" / "settlements.sqlite"
DEFAULT_BACKGROUND_BBOX = (36.8, 55.3, 38.0, 56.1)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def tiles_for_bbox(bbox: tuple[float, float, float, float], *, size: float = TILE_DEGREES) -> list[tuple[int, int]]:
    west, south, east, north = bbox
    if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
        raise ValueError("Некорректные координаты области загрузки")
    # The half-open upper edge prevents loading an adjacent tile for a route
    # that ends exactly at the boundary.
    x0, x1 = math.floor(west / size), math.floor((east - 1e-10) / size)
    y0, y1 = math.floor(south / size), math.floor((north - 1e-10) / size)
    if (x1 - x0 + 1) * (y1 - y0 + 1) > 10000:
        raise ValueError("Область слишком велика для одной загрузки; разделите её на регионы")
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def tile_bbox(tile: tuple[int, int], size: float = TILE_DEGREES) -> tuple[float, float, float, float]:
    x, y = tile
    return (round(x * size, 8), round(y * size, 8), round((x + 1) * size, 8), round((y + 1) * size, 8))


def overpass_query(bbox: tuple[float, float, float, float]) -> str:
    west, south, east, north = bbox
    bounds = f"({south:.7f},{west:.7f},{north:.7f},{east:.7f})"
    return (
        "[out:json][timeout:45];("
        f'relation["boundary"="administrative"]["place"~"^(city|town|village|hamlet)$"]{bounds};'
        f'relation["boundary"="place"]["place"~"^(city|town|village|hamlet)$"]{bounds};'
        f'way["place"~"^(city|town|village|hamlet)$"]{bounds};'
        ");out meta geom;"
    )


def _line(member: dict) -> LineString | None:
    coordinates = [(point["lon"], point["lat"]) for point in member.get("geometry", []) if "lon" in point and "lat" in point]
    return LineString(coordinates) if len(coordinates) >= 2 else None


def _geometry(element: dict):
    if element.get("type") == "way":
        line = _line(element)
        if line is None or not line.is_ring:
            return None
        geometry = Polygon(line)
    elif element.get("type") == "relation":
        members = element.get("members", [])
        outer = [_line(member) for member in members if member.get("role") == "outer"]
        inner = [_line(member) for member in members if member.get("role") == "inner"]
        outer_polygons = list(polygonize(unary_union([line for line in outer if line is not None])))
        if not outer_polygons:
            return None
        geometry = unary_union(outer_polygons)
        inner_polygons = list(polygonize(unary_union([line for line in inner if line is not None])))
        if inner_polygons:
            geometry = geometry.difference(unary_union(inner_polygons))
    else:
        return None
    if geometry.is_empty or not geometry.is_valid or geometry.area <= 0:
        return None
    return geometry


def parse_overpass(payload: dict) -> tuple[list[dict], int]:
    polygons: list[dict] = []
    invalid = 0
    seen: set[tuple[str, int]] = set()
    for element in payload.get("elements", []):
        tags = element.get("tags", {})
        if element.get("type") not in {"way", "relation"} or tags.get("place") not in {"city", "town", "village", "hamlet"}:
            continue
        key = (element["type"], element["id"])
        if key in seen:
            continue
        seen.add(key)
        geometry = _geometry(element)
        if geometry is None:
            invalid += 1
            continue
        polygons.append({
            "osm_type": key[0], "osm_id": key[1], "name": tags.get("name"),
            "place": tags["place"], "region": tags.get("addr:region"),
            "district": tags.get("addr:district"), "geometry": mapping(geometry),
            "bbox": geometry.bounds, "osm_timestamp": element.get("timestamp"),
            "source_version": element.get("version"),
        })
    return polygons, invalid


class SettlementStore:
    def __init__(self, path: str | Path = DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._sync_lock = asyncio.Lock()
        self._retry_after = 0.0
        self.last_fetch_error: str | None = None
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settlements (
                    osm_type TEXT NOT NULL, osm_id INTEGER NOT NULL, name TEXT,
                    place TEXT NOT NULL, region TEXT, district TEXT,
                    geometry TEXT NOT NULL, minx REAL NOT NULL, miny REAL NOT NULL,
                    maxx REAL NOT NULL, maxy REAL NOT NULL, source TEXT NOT NULL DEFAULT 'OSM/Overpass',
                    osm_timestamp TEXT, source_version INTEGER, first_seen_at TEXT,
                    geometry_quality TEXT NOT NULL DEFAULT 'polygon_valid',
                    last_checked_at TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (osm_type, osm_id)
                );
                CREATE INDEX IF NOT EXISTS settlements_bbox_idx ON settlements (minx, maxx, miny, maxy);
                CREATE TABLE IF NOT EXISTS settlement_coverage (
                    tile_x INTEGER NOT NULL, tile_y INTEGER NOT NULL, checked_at TEXT NOT NULL,
                    status TEXT NOT NULL, source TEXT NOT NULL, count INTEGER NOT NULL,
                    PRIMARY KEY (tile_x, tile_y)
                );
                CREATE TABLE IF NOT EXISTS settlement_tile_members (
                    tile_x INTEGER NOT NULL, tile_y INTEGER NOT NULL,
                    osm_type TEXT NOT NULL, osm_id INTEGER NOT NULL,
                    PRIMARY KEY (tile_x,tile_y,osm_type,osm_id)
                );
                CREATE TABLE IF NOT EXISTS settlement_versions (
                    osm_type TEXT NOT NULL, osm_id INTEGER NOT NULL,
                    observed_at TEXT NOT NULL, source_version INTEGER,
                    geometry TEXT NOT NULL,
                    PRIMARY KEY (osm_type,osm_id,observed_at)
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(settlements)")}
            for name, sql_type in (("source_version", "INTEGER"), ("first_seen_at", "TEXT"),
                                   ("geometry_quality", "TEXT NOT NULL DEFAULT 'polygon_valid'")):
                if name not in columns:
                    db.execute(f"ALTER TABLE settlements ADD COLUMN {name} {sql_type}")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def count(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM settlements WHERE is_active=1").fetchone()[0])

    def coverage(self, bbox: tuple[float, float, float, float]) -> dict:
        tiles = tiles_for_bbox(bbox)
        if not tiles:
            return {"status": "NOT_COVERED", "tiles_total": 0, "tiles_fresh": 0}
        cutoff = (datetime.now(timezone.utc) - timedelta(days=REFRESH_DAYS)).isoformat()
        with self._connect() as db:
            rows = {(row[0], row[1]): row for row in db.execute(
                "SELECT tile_x,tile_y,checked_at,status FROM settlement_coverage"
            )}
        fresh = sum(1 for tile in tiles if tile in rows and rows[tile][2] >= cutoff and rows[tile][3] == "COVERED")
        status = "COVERED" if fresh == len(tiles) else "PARTIALLY_COVERED" if fresh else "NOT_COVERED"
        return {"status": status, "tiles_total": len(tiles), "tiles_fresh": fresh}

    def polygons(self, bbox: tuple[float, float, float, float], *, limit: int | None = None) -> list[dict]:
        west, south, east, north = bbox
        with self._connect() as db:
            rows = db.execute(
                "SELECT osm_type,osm_id,name,place,region,district,geometry,source FROM settlements "
                "WHERE is_active=1 AND maxx>=? AND minx<=? AND maxy>=? AND miny<=? "
                "ORDER BY name LIMIT ?",
                (west, east, south, north, limit if limit is not None else -1),
            ).fetchall()
        return [{"osm_type": row[0], "osm_id": row[1], "name": row[2], "place": row[3],
                 "region": row[4], "district": row[5], "geometry": json.loads(row[6]), "source": row[7]}
                for row in rows]

    def import_geojson(self, collection: dict, source_name: str) -> dict:
        """Import a QGIS polygon export without pretending it covers all tiles."""
        if collection.get("type") != "FeatureCollection" or not isinstance(collection.get("features"), list):
            raise ValueError("Требуется GeoJSON FeatureCollection")
        if len(collection["features"]) > 10000:
            raise ValueError("Разделите большой файл на регионы до 10 000 объектов")
        imported = skipped = 0
        stamp = _now()
        with self._connect() as db:
            for feature in collection["features"]:
                properties = feature.get("properties") or {}
                try:
                    geometry = shape(feature["geometry"])
                except (KeyError, TypeError, ValueError):
                    skipped += 1
                    continue
                if geometry.geom_type not in {"Polygon", "MultiPolygon"} or not geometry.is_valid or geometry.is_empty:
                    skipped += 1
                    continue
                name = properties.get("name")
                if not name:
                    skipped += 1
                    continue
                source_key = str(feature.get("id") or properties.get("osm_id") or "")
                geometry_json = json.dumps(mapping(geometry), ensure_ascii=False)
                identity = source_key or f"{name}:{properties.get('addr:region')}:{properties.get('addr:district')}:{geometry.centroid.x:.3f}:{geometry.centroid.y:.3f}"
                fingerprint = f"{source_name}:{identity}".encode()
                imported_id = int.from_bytes(hashlib.sha256(fingerprint).digest()[:8], "big") & 0x7fffffffffffffff
                old = db.execute("SELECT geometry FROM settlements WHERE osm_type='import' AND osm_id=?", (imported_id,)).fetchone()
                if old is None or old[0] != geometry_json:
                    db.execute("INSERT INTO settlement_versions (osm_type,osm_id,observed_at,source_version,geometry) VALUES ('import',?,?,NULL,?)",
                               (imported_id, stamp, geometry_json))
                db.execute("""INSERT INTO settlements
                    (osm_type,osm_id,name,place,region,district,geometry,minx,miny,maxx,maxy,source,first_seen_at,last_checked_at)
                    VALUES ('import',?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(osm_type,osm_id) DO UPDATE SET
                    name=excluded.name,place=excluded.place,region=excluded.region,district=excluded.district,
                    geometry=excluded.geometry,minx=excluded.minx,miny=excluded.miny,maxx=excluded.maxx,maxy=excluded.maxy,
                    source=excluded.source,last_checked_at=excluded.last_checked_at,is_active=1""",
                    (imported_id, name, properties.get("place", "unknown"),
                     properties.get("addr:region") or properties.get("region"),
                     properties.get("addr:district") or properties.get("district"),
                     geometry_json, *geometry.bounds, f"QGIS/{source_name}", stamp, stamp))
                imported += 1
        return {"imported": imported, "skipped": skipped, "coverage_status": "NOT_ASSERTED",
                "message": "Импорт не доказывает полноту покрытия территории; фоновые проверки Overpass продолжаются."}

    async def sync_tile(self, tile: tuple[int, int], client) -> dict:
        async with self._sync_lock:
            if time.monotonic() < self._retry_after:
                raise RuntimeError("Overpass cooling down after rate limit or timeout")
            bbox = tile_bbox(tile)
            try:
                response = await client.post(OVERPASS_URL, data={"data": overpass_query(bbox)}, timeout=50)
                if getattr(response, "status_code", 200) in {406, 429, 504}:
                    self._retry_after = time.monotonic() + 30
                response.raise_for_status()
            except Exception:
                self._retry_after = max(self._retry_after, time.monotonic() + 30)
                raise
            polygons, invalid = parse_overpass(response.json())
            stamp = _now()
            # A failed or incomplete geometry is not recorded as full coverage.
            status = "PARTIALLY_COVERED" if invalid else "COVERED"
            with self._connect() as db:
                previous_members = {(row[0], row[1]) for row in db.execute(
                    "SELECT osm_type,osm_id FROM settlement_tile_members WHERE tile_x=? AND tile_y=?", tile
                )}
                for item in polygons:
                    geometry_json = json.dumps(item["geometry"], ensure_ascii=False)
                    old = db.execute(
                        "SELECT geometry FROM settlements WHERE osm_type=? AND osm_id=?",
                        (item["osm_type"], item["osm_id"]),
                    ).fetchone()
                    if old is None or old[0] != geometry_json:
                        db.execute("""INSERT INTO settlement_versions
                            (osm_type,osm_id,observed_at,source_version,geometry) VALUES (?,?,?,?,?)""",
                            (item["osm_type"], item["osm_id"], stamp, item["source_version"], geometry_json))
                    db.execute("""INSERT INTO settlements
                        (osm_type,osm_id,name,place,region,district,geometry,minx,miny,maxx,maxy,osm_timestamp,
                         source_version,first_seen_at,last_checked_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(osm_type,osm_id) DO UPDATE SET
                        name=excluded.name,place=excluded.place,region=excluded.region,district=excluded.district,
                        geometry=excluded.geometry,minx=excluded.minx,miny=excluded.miny,maxx=excluded.maxx,maxy=excluded.maxy,
                        osm_timestamp=excluded.osm_timestamp,source_version=excluded.source_version,
                        last_checked_at=excluded.last_checked_at,is_active=1""",
                        (item["osm_type"], item["osm_id"], item["name"], item["place"], item["region"], item["district"],
                         geometry_json, *item["bbox"], item["osm_timestamp"], item["source_version"], stamp, stamp))
                if status == "COVERED":
                    current_members = {(item["osm_type"], item["osm_id"]) for item in polygons}
                    db.execute("DELETE FROM settlement_tile_members WHERE tile_x=? AND tile_y=?", tile)
                    db.executemany("INSERT INTO settlement_tile_members (tile_x,tile_y,osm_type,osm_id) VALUES (?,?,?,?)",
                                   [(tile[0], tile[1], osm_type, osm_id) for osm_type, osm_id in current_members])
                    for osm_type, osm_id in previous_members - current_members:
                        member = db.execute(
                            "SELECT 1 FROM settlement_tile_members WHERE osm_type=? AND osm_id=? LIMIT 1", (osm_type, osm_id),
                        ).fetchone()
                        if member is None:
                            db.execute("UPDATE settlements SET is_active=0 WHERE osm_type=? AND osm_id=?", (osm_type, osm_id))
                db.execute("""INSERT INTO settlement_coverage (tile_x,tile_y,checked_at,status,source,count)
                    VALUES (?,?,?,?,?,?) ON CONFLICT(tile_x,tile_y) DO UPDATE SET
                    checked_at=excluded.checked_at,status=excluded.status,source=excluded.source,count=excluded.count""",
                    (tile[0], tile[1], stamp, status, "OSM/Overpass", len(polygons)))
            self.last_fetch_error = None
            return {"bbox": bbox, "status": status, "count": len(polygons), "invalid": invalid}

    async def ensure(self, bbox: tuple[float, float, float, float], client, *, limit: int = MAX_ON_DEMAND_TILES) -> dict:
        tiles = tiles_for_bbox(bbox)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=REFRESH_DAYS)).isoformat()
        with self._connect() as db:
            fresh = {(row[0], row[1]) for row in db.execute(
                "SELECT tile_x,tile_y FROM settlement_coverage WHERE checked_at>=? AND status='COVERED'", (cutoff,)
            )}
        missing = [tile for tile in tiles if tile not in fresh]
        errors = []
        for tile in missing[:limit]:
            try:
                await self.sync_tile(tile, client)
            except Exception as error:
                response = getattr(error, "response", None)
                reason = f"HTTP {response.status_code}" if response is not None else type(error).__name__
                self.last_fetch_error = reason
                errors.append(f"{tile}: {reason}")
                break  # Do not hammer a struggling public endpoint.
        return {**self.coverage(bbox), "fetch_errors": errors, "tiles_deferred": max(0, len(missing) - limit)}


async def background_refresh(store: SettlementStore, client, stop: asyncio.Event) -> None:
    configured = os.getenv("SETTLEMENT_BACKGROUND_BBOXES", "36.8,55.3,38.0,56.1;19.5,54.2,22.9,55.4")
    try:
        regions = [tuple(float(value) for value in part.split(",")) for part in configured.split(";")]
        if not regions:
            return
        for bbox in regions:
            tiles_for_bbox(bbox)
    except ValueError:
        return
    # Let interactive mission checks take priority immediately after startup.
    try:
        await asyncio.wait_for(stop.wait(), timeout=30)
        return
    except asyncio.TimeoutError:
        pass
    index = 0
    while not stop.is_set():
        bbox = regions[index % len(regions)]
        index += 1
        await store.ensure(bbox, client, limit=1)
        try:
            await asyncio.wait_for(stop.wait(), timeout=120)
        except asyncio.TimeoutError:
            pass
