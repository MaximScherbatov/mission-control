from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated

import asyncpg
import bcrypt
import httpx
import jwt
from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from pyproj import CRS, Transformer
from shapely.geometry import shape
from shapely.ops import transform, unary_union

from .catalog import BASES, OPTICS, TECHNOLOGY_PROFILES, UAVS, USERS
from .airspace import feature_view, filter_zones, geometry_bbox, load_seed_zones, normalize_feature, summary as airspace_summary
from .dadata import DadataService
from .models import (
    AirspaceImportRequest,
    AirspaceZoneInput,
    AuthorityContactInput,
    LaunchSiteInput,
    LoginRequest,
    MissionRequest,
    PlannedLaunchSite,
    OrderCreate,
    OrderStatusUpdate,
    ScheduleBatchCreate,
    ScheduleEntryCreate,
    ScheduleEntryUpdate,
    UserView,
)
from .mission_export import build_flight_plan, export_mission
from .optimizer import optimize
from .orders import demo_orders, ensure_transition
from .simulation import simulation_elapsed, snapshot
from .settlements import DEFAULT_DB, SettlementStore, background_refresh
from .traffic import AircraftTrafficService
from .weather import WeatherService


JWT_SECRET = os.getenv("JWT_SECRET", "contest-only-change-me")
JWT_ISSUER = "citymetrics-mission-control"
APP_RELEASE = os.getenv("APP_RELEASE", "2026.09.22-replay-airspace.1")
DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_REQUIRED = os.getenv("DB_REQUIRED", "false").lower() == "true"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
ALLOWED_ORIGINS = [item.strip() for item in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:8080").split(",") if item.strip()]


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS missions (
    id uuid PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by text NOT NULL,
    status text NOT NULL DEFAULT 'planned',
    request jsonb NOT NULL,
    result jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS missions_created_at_idx ON missions (created_at DESC);
CREATE TABLE IF NOT EXISTS customers (
    id uuid PRIMARY KEY,
    inn text UNIQUE,
    details jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS customer_orders (
    id uuid PRIMARY KEY,
    number text NOT NULL UNIQUE,
    customer_id uuid NOT NULL REFERENCES customers(id),
    status text NOT NULL,
    payload jsonb NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS customer_orders_status_idx ON customer_orders (status, updated_at DESC);
CREATE TABLE IF NOT EXISTS order_events (
    id bigserial PRIMARY KEY,
    order_id uuid NOT NULL REFERENCES customer_orders(id) ON DELETE CASCADE,
    happened_at timestamptz NOT NULL DEFAULT now(),
    actor text NOT NULL,
    from_status text,
    to_status text NOT NULL,
    note text NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS schedule_entries (
    id uuid PRIMARY KEY,
    payload jsonb NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS schedule_entries_date_idx ON schedule_entries ((payload->>'date'));
CREATE TABLE IF NOT EXISTS airspace_zones (
    id uuid PRIMARY KEY,
    zone_key text NOT NULL UNIQUE,
    category text NOT NULL,
    name text NOT NULL,
    code text,
    geometry jsonb NOT NULL,
    bbox double precision[] NOT NULL,
    details jsonb NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    editable boolean NOT NULL DEFAULT false,
    source_name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS airspace_zones_category_idx ON airspace_zones (category, enabled);
CREATE TABLE IF NOT EXISTS authority_contacts (
    zone_id uuid PRIMARY KEY REFERENCES airspace_zones(id) ON DELETE CASCADE,
    details jsonb NOT NULL,
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS launch_sites (
    id text PRIMARY KEY,
    payload jsonb NOT NULL,
    editable boolean NOT NULL DEFAULT true,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
"""


def _password_hashes() -> dict[str, bytes]:
    return {
        username: bcrypt.hashpw(os.getenv(f"DEMO_{username.upper()}_PASSWORD", data["password"]).encode(), bcrypt.gensalt(rounds=12))
        for username, data in USERS.items()
    }


def _zone_details(zone: dict) -> dict:
    return {
        "lower_limit": zone.get("lower_limit"),
        "upper_limit": zone.get("upper_limit"),
        "vertical_definition": zone.get("vertical_definition"),
        "schedule": zone.get("schedule"),
        "valid_from": zone.get("valid_from"),
        "valid_until": zone.get("valid_until"),
        "properties": zone.get("properties", {}),
    }


async def _seed_airspace(connection, zones: list[dict]) -> None:
    await connection.executemany(
        """INSERT INTO airspace_zones
               (id, zone_key, category, name, code, geometry, bbox, details, enabled, editable, source_name)
           VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8::jsonb,$9,$10,$11)
           ON CONFLICT (zone_key) DO NOTHING""",
        [(
            uuid.UUID(zone["id"]), zone["zone_key"], zone["category"], zone["name"], zone.get("code"),
            json.dumps(zone["geometry"], ensure_ascii=False), zone["bbox"],
            json.dumps(_zone_details(zone), ensure_ascii=False), zone.get("enabled", True),
            zone.get("editable", False), zone["source_name"],
        ) for zone in zones],
    )


def _seed_launch_sites() -> dict[str, dict]:
    return {
        item["id"]: {
            **item,
            "kind": "runway" if item.get("runway_length_m", 0) >= 250 else "mixed",
            "notes": "Штатная площадка флота",
            "editable": True,
        }
        for item in BASES
    }


async def _seed_launch_sites_db(connection, sites: dict[str, dict]) -> None:
    await connection.executemany(
        """INSERT INTO launch_sites (id, payload, editable, created_by)
           VALUES ($1,$2::jsonb,true,'system') ON CONFLICT (id) DO NOTHING""",
        [(site_id, json.dumps(site, ensure_ascii=False)) for site_id, site in sites.items()],
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.password_hashes = _password_hashes()
    app.state.pool = None
    app.state.http = httpx.AsyncClient(
        headers={"User-Agent": "CityMetrics-Mission-Control/0.1 (+https://mission.citymetrics.moscow)"},
        follow_redirects=True,
    )
    app.state.aircraft_traffic = AircraftTrafficService(app.state.http)
    app.state.dadata = DadataService(app.state.http)
    app.state.weather = WeatherService(app.state.http)
    app.state.order_store = {item["id"]: item for item in demo_orders()}
    app.state.schedule_store = {}
    app.state.mission_store = {}
    app.state.airspace_store = {item["id"]: item for item in load_seed_zones()}
    app.state.launch_site_store = _seed_launch_sites()
    app.state.authority_contact_store = {}
    app.state.settlements = SettlementStore(os.getenv("SETTLEMENT_DB_PATH") or DEFAULT_DB)
    app.state.settlement_stop = asyncio.Event()
    app.state.settlement_refresh_task = None
    if DATABASE_URL:
        last_error: Exception | None = None
        for _ in range(18):
            try:
                app.state.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=8)
                async with app.state.pool.acquire() as connection:
                    await connection.execute(SCHEMA_SQL)
                    await _seed_airspace(connection, list(app.state.airspace_store.values()))
                    await _seed_launch_sites_db(connection, app.state.launch_site_store)
                    for item in demo_orders():
                        customer = item["customer"]
                        customer_id = uuid.uuid5(uuid.NAMESPACE_URL, f"citymetrics-customer:{customer.get('inn') or customer['name']}")
                        await connection.execute(
                            """INSERT INTO customers (id, inn, details) VALUES ($1, $2, $3::jsonb)
                               ON CONFLICT (inn) DO UPDATE SET details=EXCLUDED.details, updated_at=now()""",
                            customer_id, customer.get("inn"), json.dumps(customer, ensure_ascii=False),
                        )
                        payload = {key: value for key, value in item.items() if key not in {"id", "number", "status", "customer", "created_by"}}
                        await connection.execute(
                            """INSERT INTO customer_orders (id, number, customer_id, status, payload, created_by)
                               VALUES ($1, $2, $3, $4, $5::jsonb, $6) ON CONFLICT (id) DO NOTHING""",
                            uuid.UUID(item["id"]), item["number"], customer_id, item["status"],
                            json.dumps(payload, ensure_ascii=False), item["created_by"],
                        )
                break
            except Exception as error:  # database can still be starting
                last_error = error
                await asyncio.sleep(2)
        if app.state.pool is None and DB_REQUIRED:
            raise RuntimeError(f"Database is required but unavailable: {last_error}")
    settlement_task = None
    if os.getenv("SETTLEMENT_BACKGROUND_SYNC", "true").lower() == "true":
        settlement_task = asyncio.create_task(background_refresh(
            app.state.settlements, app.state.http, app.state.settlement_stop,
        ))
    yield
    app.state.settlement_stop.set()
    if settlement_task is not None:
        settlement_task.cancel()
        try:
            await settlement_task
        except asyncio.CancelledError:
            pass
    if app.state.settlement_refresh_task is not None:
        app.state.settlement_refresh_task.cancel()
        try:
            await app.state.settlement_refresh_task
        except asyncio.CancelledError:
            pass
    await app.state.http.aclose()
    if app.state.pool is not None:
        await app.state.pool.close()


app = FastAPI(
    title="Mission Control API",
    version="1.0.0",
    description=(
        "Публичный REST API планирования и сопровождения аэрофотосъёмочных работ. "
        "Телеметрия реального времени передаётся отдельно по WebSocket `/api/telemetry/ws` "
        "и поэтому не отображается как операция Swagger/OpenAPI."
    ),
    openapi_tags=[
        {"name": "Система", "description": "Состояние сервиса и подключённых источников."},
        {"name": "Авторизация", "description": "Сеанс пользователя и ролевая модель."},
        {"name": "Справочники", "description": "БВС, полезные нагрузки, технологии и базы."},
        {"name": "Воздушное пространство", "description": "Зоны, препятствия и контакты органов ОрВД."},
        {"name": "Заказы", "description": "Коммерческий бизнес-процесс заказа съёмки."},
        {"name": "Календарь", "description": "Назначение бортов и временных окон."},
        {"name": "Интеграции", "description": "DaData, погода, радар осадков и ADS-B/MLAT."},
        {"name": "Полётные задания", "description": "Расчёт, получение и экспорт полётных планов."},
    ],
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)


def _issue_token(username: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": username, "iss": JWT_ISSUER, "iat": now, "exp": now + timedelta(hours=10)},
        JWT_SECRET,
        algorithm="HS256",
    )


def _read_user(token: str | None) -> UserView:
    if not token:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"], issuer=JWT_ISSUER)
        username = payload["sub"]
        data = USERS[username]
    except (jwt.PyJWTError, KeyError) as error:
        raise HTTPException(status_code=401, detail="Сессия недействительна") from error
    return UserView(username=username, full_name=data["full_name"], role=data["role"])


def current_user(mc_session: Annotated[str | None, Cookie()] = None) -> UserView:
    return _read_user(mc_session)


def roles(*allowed: str):
    def dependency(user: Annotated[UserView, Depends(current_user)]) -> UserView:
        if user.role not in allowed:
            raise HTTPException(status_code=403, detail="Недостаточно прав для операции")
        return user
    return dependency


@app.get("/api/health", tags=["Система"], summary="Проверить состояние сервиса")
async def health(request: Request):
    database = False
    if request.app.state.pool is not None:
        try:
            async with request.app.state.pool.acquire() as connection:
                database = await connection.fetchval("SELECT 1") == 1
        except Exception:
            database = False
    return {
        "status": "ok" if database or not DB_REQUIRED else "degraded",
        "release": APP_RELEASE,
        "database": database,
        "integrations": {
            "basemap": {"status": "operational", "provider": "OpenFreeMap", "api_key_required": False},
            "mission_telemetry": {"status": "operational", "provider": "controlled-simulator"},
            "external_adsb": request.app.state.aircraft_traffic.configuration(),
            "dadata": request.app.state.dadata.configuration(),
            "weather": request.app.state.weather.configuration(),
        },
    }


@app.post("/api/auth/login", response_model=UserView, tags=["Авторизация"], summary="Войти в систему")
async def login(payload: LoginRequest, response: Response, request: Request):
    data = USERS.get(payload.username)
    password_hash = request.app.state.password_hashes.get(payload.username)
    if data is None or password_hash is None or not bcrypt.checkpw(payload.password.encode(), password_hash):
        await asyncio.sleep(0.35)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    response.set_cookie(
        "mc_session", _issue_token(payload.username), max_age=36000,
        httponly=True, secure=COOKIE_SECURE, samesite="lax", path="/",
    )
    return UserView(username=payload.username, full_name=data["full_name"], role=data["role"])


@app.post("/api/auth/logout", status_code=204, tags=["Авторизация"], summary="Завершить сеанс")
async def logout(response: Response):
    response.delete_cookie("mc_session", path="/")


@app.get("/api/auth/me", response_model=UserView, tags=["Авторизация"], summary="Получить текущего пользователя")
async def me(user: Annotated[UserView, Depends(current_user)]):
    return user


@app.get("/api/catalog/uavs", tags=["Справочники"], summary="Получить каталог БВС")
async def uavs(_: Annotated[UserView, Depends(current_user)]):
    return {"items": UAVS, "count": len(UAVS)}


@app.get("/api/catalog/optics", tags=["Справочники"], summary="Получить каталог оптических систем")
async def optics(_: Annotated[UserView, Depends(current_user)]):
    return {"items": OPTICS, "count": len(OPTICS)}


@app.get("/api/catalog/payloads", tags=["Справочники"], summary="Получить каталог полезных нагрузок")
async def payloads(_: Annotated[UserView, Depends(current_user)]):
    return {"items": OPTICS, "count": len(OPTICS)}


@app.get("/api/catalog/technologies", tags=["Справочники"], summary="Получить технологические профили")
async def technologies(_: Annotated[UserView, Depends(current_user)]):
    return {"items": TECHNOLOGY_PROFILES, "count": len(TECHNOLOGY_PROFILES)}


async def _launch_sites(request: Request) -> list[dict]:
    if request.app.state.pool is None:
        return list(request.app.state.launch_site_store.values())
    async with request.app.state.pool.acquire() as connection:
        rows = await connection.fetch("SELECT id, payload, editable FROM launch_sites ORDER BY payload->>'name'")
    return [{"id": row["id"], **_json_object(row["payload"]), "editable": row["editable"]} for row in rows]


@app.get("/api/catalog/bases", tags=["Справочники"], summary="Получить площадки взлёта и посадки")
async def bases(request: Request, _: Annotated[UserView, Depends(current_user)]):
    items = await _launch_sites(request)
    return {"items": items, "count": len(items)}


@app.post("/api/catalog/bases", status_code=201, tags=["Справочники"], summary="Добавить площадку")
async def create_base(payload: LaunchSiteInput, request: Request, user: Annotated[UserView, Depends(roles("dispatcher", "admin"))]):
    site_id = f"site-{uuid.uuid4().hex[:12]}"
    item = {"id": site_id, **jsonable_encoder(payload), "editable": True}
    if request.app.state.pool is None:
        request.app.state.launch_site_store[site_id] = item
    else:
        async with request.app.state.pool.acquire() as connection:
            await connection.execute(
                "INSERT INTO launch_sites (id,payload,editable,created_by) VALUES ($1,$2::jsonb,true,$3)",
                site_id, json.dumps(item, ensure_ascii=False), user.username,
            )
    return item


@app.put("/api/catalog/bases/{site_id}", tags=["Справочники"], summary="Изменить площадку")
async def update_base(site_id: str, payload: LaunchSiteInput, request: Request, user: Annotated[UserView, Depends(roles("dispatcher", "admin"))]):
    item = {"id": site_id, **jsonable_encoder(payload), "editable": True}
    if request.app.state.pool is None:
        if site_id not in request.app.state.launch_site_store:
            raise HTTPException(status_code=404, detail="Площадка не найдена")
        request.app.state.launch_site_store[site_id] = item
    else:
        async with request.app.state.pool.acquire() as connection:
            result = await connection.execute(
                "UPDATE launch_sites SET payload=$2::jsonb, editable=true, updated_at=now() WHERE id=$1",
                site_id, json.dumps(item, ensure_ascii=False),
            )
        if result.endswith(" 0"):
            raise HTTPException(status_code=404, detail="Площадка не найдена")
    return item


@app.delete("/api/catalog/bases/{site_id}", status_code=204, tags=["Справочники"], summary="Удалить площадку")
async def delete_base(site_id: str, request: Request, _: Annotated[UserView, Depends(roles("dispatcher", "admin"))]):
    if request.app.state.pool is None:
        if request.app.state.launch_site_store.pop(site_id, None) is None:
            raise HTTPException(status_code=404, detail="Площадка не найдена")
    else:
        async with request.app.state.pool.acquire() as connection:
            result = await connection.execute("DELETE FROM launch_sites WHERE id=$1", site_id)
        if result.endswith(" 0"):
            raise HTTPException(status_code=404, detail="Площадка не найдена")


def _airspace_row(row) -> dict:
    details = _json_object(row["details"])
    geometry = json.loads(row["geometry"]) if isinstance(row["geometry"], str) else row["geometry"]
    return {
        "id": str(row["id"]), "zone_key": row["zone_key"], "category": row["category"],
        "name": row["name"], "code": row["code"], "geometry": geometry, "bbox": list(row["bbox"]),
        "enabled": row["enabled"], "editable": row["editable"], "source_name": row["source_name"], **details,
    }


async def _all_airspace_zones(request: Request) -> list[dict]:
    if request.app.state.pool is None:
        return list(request.app.state.airspace_store.values())
    async with request.app.state.pool.acquire() as connection:
        rows = await connection.fetch(
            """SELECT id, zone_key, category, name, code, geometry, bbox, details,
                      enabled, editable, source_name
               FROM airspace_zones ORDER BY category, code NULLS LAST, name"""
        )
    return [_airspace_row(row) for row in rows]


def _parse_bbox(value: str | None) -> list[float] | None:
    if not value:
        return None
    try:
        result = [float(item) for item in value.split(",")]
    except ValueError as error:
        raise HTTPException(status_code=422, detail="bbox: четыре числа minLon,minLat,maxLon,maxLat") from error
    if len(result) != 4 or result[0] > result[2] or result[1] > result[3]:
        raise HTTPException(status_code=422, detail="bbox: четыре числа minLon,minLat,maxLon,maxLat")
    return result


async def _save_airspace_zone(request: Request, zone: dict, *, update: bool = False) -> dict:
    if request.app.state.pool is None:
        request.app.state.airspace_store[zone["id"]] = zone
        return zone
    async with request.app.state.pool.acquire() as connection:
        if update:
            await connection.execute(
                """UPDATE airspace_zones SET category=$2, name=$3, code=$4, geometry=$5::jsonb,
                          bbox=$6, details=$7::jsonb, enabled=$8, updated_at=now()
                   WHERE id=$1""",
                uuid.UUID(zone["id"]), zone["category"], zone["name"], zone.get("code"),
                json.dumps(zone["geometry"], ensure_ascii=False), zone["bbox"],
                json.dumps(_zone_details(zone), ensure_ascii=False), zone.get("enabled", True),
            )
        else:
            await connection.execute(
                """INSERT INTO airspace_zones
                       (id, zone_key, category, name, code, geometry, bbox, details, enabled, editable, source_name)
                   VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8::jsonb,$9,true,$10)
                   ON CONFLICT (zone_key) DO UPDATE SET category=EXCLUDED.category, name=EXCLUDED.name,
                       code=EXCLUDED.code, geometry=EXCLUDED.geometry, bbox=EXCLUDED.bbox,
                       details=EXCLUDED.details, enabled=EXCLUDED.enabled, updated_at=now()
                   WHERE airspace_zones.editable=true""",
                uuid.UUID(zone["id"]), zone["zone_key"], zone["category"], zone["name"], zone.get("code"),
                json.dumps(zone["geometry"], ensure_ascii=False), zone["bbox"],
                json.dumps(_zone_details(zone), ensure_ascii=False), zone.get("enabled", True), zone["source_name"],
            )
    return zone


@app.get("/api/airspace/summary", tags=["Воздушное пространство"])
async def get_airspace_summary(request: Request, _: Annotated[UserView, Depends(current_user)]):
    zones = await _all_airspace_zones(request)
    # Reuse locally cached OSM boundaries during routing. Overpass refreshes
    # asynchronously; a missing tile never masquerades as verified clearance.
    mission_geometry = payload.area.get('geometry', payload.area)
    mission_bounds = shape(mission_geometry).bounds
    origins = ([payload.launch_point] if payload.launch_point else
               [(base['lon'], base['lat']) for base in BASES])
    settlement_margin_deg = .15 + payload.settlement_clearance_m / 60000
    settlement_bbox = (
        min(mission_bounds[0], *(point[0] for point in origins)) - settlement_margin_deg,
        min(mission_bounds[1], *(point[1] for point in origins)) - settlement_margin_deg,
        max(mission_bounds[2], *(point[0] for point in origins)) + settlement_margin_deg,
        max(mission_bounds[3], *(point[1] for point in origins)) + settlement_margin_deg,
    )
    for settlement in request.app.state.settlements.polygons(settlement_bbox):
        try:
            bounds = shape(settlement['geometry']).bounds
        except (TypeError, ValueError):
            continue
        zones.append({
            'id': f"settlement-{settlement['osm_type']}-{settlement['osm_id']}",
            'name': settlement['name'] or 'Населённый пункт',
            'category': 'settlement', 'bbox': list(bounds),
            'geometry': settlement['geometry'], 'source_name': settlement['source'],
            'enabled': True, 'properties': {},
        })
    result = airspace_summary(zones)
    result["sources"] = sorted({zone["source_name"] for zone in zones})
    result["storage"] = "database" if request.app.state.pool is not None else "memory"
    return result


@app.get("/api/settlements/coverage", tags=["Воздушное пространство"], summary="Проверить загрузку границ населённых пунктов")
async def settlement_coverage(bbox: str, request: Request, _: Annotated[UserView, Depends(current_user)]):
    bounds = _parse_bbox(bbox)
    return {**request.app.state.settlements.coverage(tuple(bounds)), "bbox": bounds,
            "source": "OSM/Overpass", "legal_status": "preliminary"}


@app.get("/api/settlements/polygons", tags=["Воздушное пространство"], summary="Полигоны населённых пунктов для карты")
async def settlement_polygons(bbox: str, request: Request, _: Annotated[UserView, Depends(current_user)]):
    bounds = tuple(_parse_bbox(bbox))
    items = request.app.state.settlements.polygons(bounds, limit=2001)
    try:
        coverage = request.app.state.settlements.coverage(bounds)
    except ValueError:
        coverage = None  # A national-scale view can exceed the coverage grid limit.
    return {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "id": f'{item["osm_type"]}/{item["osm_id"]}',
                      "properties": {key: item[key] for key in ("name", "place", "region", "district", "source")},
                      "geometry": item["geometry"]} for item in items[:2000]],
        "coverage": coverage, "truncated": len(items) > 2000,
        "last_fetch_error": request.app.state.settlements.last_fetch_error,
        "legal_status": "preliminary",
    }


@app.post("/api/settlements/refresh", tags=["Воздушное пространство"], summary="Обновить границы в выбранной области")
async def refresh_settlements(bbox: str, request: Request, _: Annotated[UserView, Depends(roles("dispatcher", "admin"))]):
    bounds = _parse_bbox(bbox)
    try:
        return await asyncio.wait_for(request.app.state.settlements.ensure(tuple(bounds), request.app.state.http), timeout=55)
    except asyncio.TimeoutError as error:
        raise HTTPException(status_code=504, detail="Источник Overpass не ответил вовремя; неполное покрытие не считается проверенным") from error


@app.post("/api/settlements/import", tags=["Воздушное пространство"], summary="Загрузить полигоны из QGIS GeoJSON")
async def import_settlements(
    collection: dict, request: Request, _: Annotated[UserView, Depends(roles("dispatcher", "admin"))],
    source_name: str = Query(default="qgis.geojson", max_length=120),
):
    try:
        return request.app.state.settlements.import_geojson(collection, source_name)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/airspace/zones", tags=["Воздушное пространство"])
async def list_airspace_zones(
    request: Request,
    _: Annotated[UserView, Depends(current_user)],
    category: str | None = Query(default=None, pattern=r"^(prohibited|danger|orvd|obstacle|custom)$"),
    bbox: str | None = None,
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=2000, ge=1, le=5000),
):
    zones = filter_zones(await _all_airspace_zones(request), category, _parse_bbox(bbox), q, limit)
    return {
        "type": "FeatureCollection", "features": [feature_view(zone) for zone in zones],
        "count": len(zones), "storage": "database" if request.app.state.pool is not None else "memory",
    }


@app.get("/api/airspace/zones/{zone_id}", tags=["Воздушное пространство"])
async def get_airspace_zone(zone_id: uuid.UUID, request: Request, _: Annotated[UserView, Depends(current_user)]):
    zone = next((item for item in await _all_airspace_zones(request) if item["id"] == str(zone_id)), None)
    if zone is None:
        raise HTTPException(status_code=404, detail="Зона не найдена")
    return feature_view(zone, include_raw=True)


@app.post("/api/airspace/zones", status_code=201, tags=["Воздушное пространство"])
async def create_airspace_zone(
    payload: AirspaceZoneInput, request: Request,
    user: Annotated[UserView, Depends(roles("dispatcher", "admin"))],
):
    zone_id = str(uuid.uuid4())
    data = jsonable_encoder(payload)
    zone = {
        "id": zone_id, "zone_key": f"user:{zone_id}", "editable": True,
        "source_name": f"Ручной ввод · {user.username}", "bbox": geometry_bbox(data["geometry"]),
        "vertical_definition": None, **data,
    }
    await _save_airspace_zone(request, zone)
    return feature_view(zone, include_raw=True)


@app.post("/api/airspace/zones/import", status_code=201, tags=["Воздушное пространство"])
async def import_airspace_zones(
    payload: AirspaceImportRequest, request: Request,
    _: Annotated[UserView, Depends(roles("dispatcher", "admin"))],
):
    imported = []
    errors = []
    for index, feature in enumerate(payload.collection["features"]):
        try:
            zone = normalize_feature(feature, payload.category, payload.source_name, index, editable=True)
            await _save_airspace_zone(request, zone)
            imported.append(zone["id"])
        except ValueError as error:
            errors.append({"index": index, "detail": str(error)})
    if not imported:
        raise HTTPException(status_code=422, detail={"message": "Не найдено корректных объектов", "errors": errors[:30]})
    return {"imported": len(imported), "skipped": len(errors), "ids": imported, "errors": errors[:30]}


@app.put("/api/airspace/zones/{zone_id}", tags=["Воздушное пространство"])
async def update_airspace_zone(
    zone_id: uuid.UUID, payload: AirspaceZoneInput, request: Request,
    _: Annotated[UserView, Depends(roles("dispatcher", "admin"))],
):
    current = next((item for item in await _all_airspace_zones(request) if item["id"] == str(zone_id)), None)
    if current is None:
        raise HTTPException(status_code=404, detail="Зона не найдена")
    if not current.get("editable"):
        raise HTTPException(status_code=409, detail="Нормативный набор неизменяем; создайте пользовательскую зону")
    data = jsonable_encoder(payload)
    zone = {**current, **data, "bbox": geometry_bbox(data["geometry"])}
    await _save_airspace_zone(request, zone, update=True)
    return feature_view(zone, include_raw=True)


@app.delete("/api/airspace/zones/{zone_id}", status_code=204, tags=["Воздушное пространство"])
async def delete_airspace_zone(
    zone_id: uuid.UUID, request: Request,
    _: Annotated[UserView, Depends(roles("dispatcher", "admin"))],
):
    current = next((item for item in await _all_airspace_zones(request) if item["id"] == str(zone_id)), None)
    if current is None:
        raise HTTPException(status_code=404, detail="Зона не найдена")
    if not current.get("editable"):
        raise HTTPException(status_code=409, detail="Нормативный набор нельзя удалить")
    if request.app.state.pool is None:
        request.app.state.airspace_store.pop(str(zone_id), None)
    else:
        async with request.app.state.pool.acquire() as connection:
            await connection.execute("DELETE FROM airspace_zones WHERE id=$1", zone_id)


@app.get("/api/airspace/authorities", tags=["Воздушное пространство"])
async def list_airspace_authorities(request: Request, _: Annotated[UserView, Depends(current_user)]):
    zones = [zone for zone in await _all_airspace_zones(request) if zone["category"] == "orvd"]
    contacts: dict[str, dict] = request.app.state.authority_contact_store
    if request.app.state.pool is not None:
        async with request.app.state.pool.acquire() as connection:
            rows = await connection.fetch("SELECT zone_id, details FROM authority_contacts")
        contacts = {str(row["zone_id"]): _json_object(row["details"]) for row in rows}
    items = [{
        "zone_id": zone["id"], "zone_name": zone["name"], "code": zone.get("code"),
        "contact": contacts.get(zone["id"]), "contact_status": "verified" if zone["id"] in contacts else "missing",
    } for zone in zones]
    return {"items": items, "count": len(items), "missing": sum(item["contact"] is None for item in items)}


@app.put("/api/airspace/authorities/{zone_id}", tags=["Воздушное пространство"])
async def update_airspace_authority(
    zone_id: uuid.UUID, payload: AuthorityContactInput, request: Request,
    user: Annotated[UserView, Depends(roles("dispatcher", "admin"))],
):
    zone = next((item for item in await _all_airspace_zones(request) if item["id"] == str(zone_id) and item["category"] == "orvd"), None)
    if zone is None:
        raise HTTPException(status_code=404, detail="Зона ОрВД не найдена")
    data = jsonable_encoder(payload)
    if request.app.state.pool is None:
        request.app.state.authority_contact_store[str(zone_id)] = data
    else:
        async with request.app.state.pool.acquire() as connection:
            await connection.execute(
                """INSERT INTO authority_contacts (zone_id, details, updated_by) VALUES ($1,$2::jsonb,$3)
                   ON CONFLICT (zone_id) DO UPDATE SET details=EXCLUDED.details,
                       updated_by=EXCLUDED.updated_by, updated_at=now()""",
                zone_id, json.dumps(data, ensure_ascii=False), user.username,
            )
    return {"zone_id": str(zone_id), "zone_name": zone["name"], "contact": data, "contact_status": "verified"}


def _memory_order_view(item: dict) -> dict:
    return {**item, "storage": "memory"}


def _json_object(value: object) -> dict:
    """Normalize json/jsonb values returned by asyncpg's default text codec."""
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("Ожидался JSON-объект из базы данных")
    return value


async def _db_order_view(connection, order_id: uuid.UUID) -> dict | None:
    row = await connection.fetchrow(
        """SELECT o.id, o.number, o.status, o.payload, o.created_by, o.created_at, o.updated_at,
                  c.id customer_id, c.details customer
           FROM customer_orders o JOIN customers c ON c.id=o.customer_id WHERE o.id=$1""", order_id,
    )
    if not row:
        return None
    return {
        "id": str(row["id"]), "number": row["number"], "status": row["status"], **_json_object(row["payload"]),
        "customer_id": str(row["customer_id"]), "customer": _json_object(row["customer"]), "created_by": row["created_by"],
        "created_at": row["created_at"], "updated_at": row["updated_at"], "storage": "database",
    }


@app.get("/api/orders", tags=["Заказы"], summary="Получить реестр заказов")
async def list_orders(request: Request, _: Annotated[UserView, Depends(current_user)]):
    if request.app.state.pool is None:
        items = sorted(request.app.state.order_store.values(), key=lambda item: item["number"], reverse=True)
        return {"items": [_memory_order_view(item) for item in items], "count": len(items), "storage": "memory"}
    async with request.app.state.pool.acquire() as connection:
        ids = await connection.fetch("SELECT id FROM customer_orders ORDER BY updated_at DESC, number DESC LIMIT 200")
        items = [await _db_order_view(connection, row["id"]) for row in ids]
    return {"items": items, "count": len(items), "storage": "database"}


@app.post("/api/orders", status_code=201, tags=["Заказы"], summary="Создать заказ")
async def create_order(
    payload: OrderCreate,
    request: Request,
    user: Annotated[UserView, Depends(roles("dispatcher", "manager", "admin"))],
):
    order_id = uuid.uuid4()
    encoded = jsonable_encoder(payload)
    if request.app.state.pool is None:
        sequence = len(request.app.state.order_store) + 92
        item = {"id": str(order_id), "number": f"MC-26{sequence:03d}", "status": "new", **encoded, "created_by": user.username}
        request.app.state.order_store[str(order_id)] = item
        return _memory_order_view(item)
    async with request.app.state.pool.acquire() as connection:
        async with connection.transaction():
            customer = encoded.pop("customer")
            customer_id = None
            if customer.get("inn"):
                customer_id = await connection.fetchval("SELECT id FROM customers WHERE inn=$1", customer["inn"])
            if customer_id is None:
                customer_id = uuid.uuid4()
                await connection.execute(
                    "INSERT INTO customers (id, inn, details) VALUES ($1, $2, $3::jsonb)",
                    customer_id, customer.get("inn"), json.dumps(customer, ensure_ascii=False),
                )
            else:
                await connection.execute(
                    "UPDATE customers SET details=$2::jsonb, updated_at=now() WHERE id=$1",
                    customer_id, json.dumps(customer, ensure_ascii=False),
                )
            sequence = await connection.fetchval("SELECT count(*) + 92 FROM customer_orders")
            number = f"MC-{datetime.now().strftime('%y')}{int(sequence):03d}"
            await connection.execute(
                """INSERT INTO customer_orders (id, number, customer_id, status, payload, created_by)
                   VALUES ($1, $2, $3, 'new', $4::jsonb, $5)""",
                order_id, number, customer_id, json.dumps(encoded, ensure_ascii=False), user.username,
            )
            await connection.execute(
                "INSERT INTO order_events (order_id, actor, to_status, note) VALUES ($1, $2, 'new', 'Заявка зарегистрирована')",
                order_id, user.username,
            )
            return await _db_order_view(connection, order_id)


@app.patch("/api/orders/{order_id}/status", tags=["Заказы"], summary="Изменить статус заказа")
async def update_order_status(
    order_id: uuid.UUID,
    payload: OrderStatusUpdate,
    request: Request,
    user: Annotated[UserView, Depends(roles("dispatcher", "manager", "admin"))],
):
    if payload.status == "approved" and user.role not in {"manager", "admin"}:
        raise HTTPException(status_code=403, detail="Согласование предложения доступно руководителю")
    if payload.status in {"scheduled", "in_progress"} and user.role not in {"dispatcher", "admin"}:
        raise HTTPException(status_code=403, detail="Назначение и запуск доступны диспетчеру")
    key = str(order_id)
    if request.app.state.pool is None:
        item = request.app.state.order_store.get(key)
        if not item:
            raise HTTPException(status_code=404, detail="Заказ не найден")
        try:
            ensure_transition(item["status"], payload.status)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        item["status"] = payload.status
        item.setdefault("events", []).append({"actor": user.username, "to_status": payload.status, "note": payload.note})
        return _memory_order_view(item)
    async with request.app.state.pool.acquire() as connection:
        async with connection.transaction():
            current = await connection.fetchval("SELECT status FROM customer_orders WHERE id=$1 FOR UPDATE", order_id)
            if current is None:
                raise HTTPException(status_code=404, detail="Заказ не найден")
            try:
                ensure_transition(current, payload.status)
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            await connection.execute("UPDATE customer_orders SET status=$2, updated_at=now() WHERE id=$1", order_id, payload.status)
            await connection.execute(
                "INSERT INTO order_events (order_id, actor, from_status, to_status, note) VALUES ($1,$2,$3,$4,$5)",
                order_id, user.username, current, payload.status, payload.note,
            )
            return await _db_order_view(connection, order_id)


def _slot_bounds(item: dict) -> tuple[datetime, datetime]:
    start = datetime.fromisoformat(f"{item['date']}T{item['time']}:00")
    return start, start + timedelta(hours=float(item["duration"]))


@app.get("/api/schedule", tags=["Календарь"], summary="Получить календарь вылетов")
async def schedule_list(request: Request, _: Annotated[UserView, Depends(current_user)]):
    if request.app.state.pool is None:
        items = list(request.app.state.schedule_store.values())
        return {"items": items, "count": len(items), "storage": "memory"}
    async with request.app.state.pool.acquire() as connection:
        rows = await connection.fetch("SELECT id, payload FROM schedule_entries ORDER BY payload->>'date', payload->>'time'")
    items = [{"id": str(row["id"]), **_json_object(row["payload"])} for row in rows]
    return {"items": items, "count": len(items), "storage": "database"}


@app.post("/api/schedule", status_code=201, tags=["Календарь"], summary="Назначить вылет или техническое обслуживание")
async def schedule_create(
    payload: ScheduleEntryCreate,
    request: Request,
    user: Annotated[UserView, Depends(roles("dispatcher", "admin"))],
):
    encoded = jsonable_encoder(payload)
    requested_start, requested_end = _slot_bounds(encoded)
    if request.app.state.pool is None:
        linked_order = None
        if payload.orderId:
            linked_order = request.app.state.order_store.get(payload.orderId)
            if not linked_order:
                raise HTTPException(status_code=404, detail="Связанный заказ не найден")
            if linked_order["status"] != "approved":
                raise HTTPException(status_code=409, detail="В календарь можно передать только согласованный заказ")
        existing = request.app.state.schedule_store.values()
        for item in existing:
            start, end = _slot_bounds(item)
            if item["uavId"] == payload.uavId and requested_start < end and requested_end > start:
                raise HTTPException(status_code=409, detail="Борт уже занят в выбранном интервале")
        entry_id = uuid.uuid4()
        item = {"id": str(entry_id), **encoded}
        request.app.state.schedule_store[str(entry_id)] = item
        if linked_order:
            linked_order["status"] = "scheduled"
            linked_order.setdefault("events", []).append({
                "actor": user.username,
                "from_status": "approved",
                "to_status": "scheduled",
                "note": f"Назначен {payload.uavName}, {payload.date} {payload.time}",
            })
        return item
    async with request.app.state.pool.acquire() as connection:
        async with connection.transaction():
            linked_order_id = None
            if payload.orderId:
                try:
                    linked_order_id = uuid.UUID(payload.orderId)
                except ValueError as error:
                    raise HTTPException(status_code=422, detail="Некорректный идентификатор заказа") from error
                order_status = await connection.fetchval(
                    "SELECT status FROM customer_orders WHERE id=$1 FOR UPDATE", linked_order_id,
                )
                if order_status is None:
                    raise HTTPException(status_code=404, detail="Связанный заказ не найден")
                if order_status != "approved":
                    raise HTTPException(status_code=409, detail="В календарь можно передать только согласованный заказ")
            # Serialize assignments for one aircraft, including the first entry.
            # A date-only lookup misses service windows crossing midnight.
            await connection.execute("SELECT pg_advisory_xact_lock(hashtext($1))", payload.uavId)
            rows = await connection.fetch(
                "SELECT payload FROM schedule_entries WHERE payload->>'uavId'=$1 FOR UPDATE",
                payload.uavId,
            )
            for row in rows:
                start, end = _slot_bounds(_json_object(row["payload"]))
                if requested_start < end and requested_end > start:
                    raise HTTPException(status_code=409, detail="Борт уже занят в выбранном интервале")
            entry_id = uuid.uuid4()
            await connection.execute(
                "INSERT INTO schedule_entries (id, payload, created_by) VALUES ($1,$2::jsonb,$3)",
                entry_id, json.dumps(encoded, ensure_ascii=False), user.username,
            )
            if linked_order_id:
                await connection.execute(
                    "UPDATE customer_orders SET status='scheduled', updated_at=now() WHERE id=$1", linked_order_id,
                )
                await connection.execute(
                    """INSERT INTO order_events (order_id, actor, from_status, to_status, note)
                       VALUES ($1,$2,'approved','scheduled',$3)""",
                    linked_order_id, user.username, f"Назначен {payload.uavName}, {payload.date} {payload.time}",
                )
    return {"id": str(entry_id), **encoded}


@app.post("/api/schedule/batch", status_code=201, tags=["Календарь"], summary="Атомарно назначить все борта плана")
async def schedule_batch_create(
    payload: ScheduleBatchCreate,
    request: Request,
    user: Annotated[UserView, Depends(roles("dispatcher", "admin"))],
):
    entries = [jsonable_encoder(entry) for entry in payload.entries]
    order_ids = {entry.get("orderId") for entry in entries}
    if len(order_ids) > 1:
        raise HTTPException(status_code=422, detail="Борта одного плана должны относиться к одному заказу")
    order_id = next(iter(order_ids), None)
    group_id = entries[0].get("missionGroupId") or str(uuid.uuid4())
    if any(entry.get("missionGroupId") not in {None, group_id} for entry in entries):
        raise HTTPException(status_code=422, detail="Укажите единый идентификатор плана")
    for entry in entries:
        entry["missionGroupId"] = group_id
    slots = [(*_slot_bounds(entry), entry["uavId"]) for entry in entries]
    for index, (start, end, uav_id) in enumerate(slots):
        if any(uav_id == other_id and start < other_end and end > other_start
               for other_start, other_end, other_id in slots[index + 1:]):
            raise HTTPException(status_code=409, detail="Один БВС назначен на пересекающиеся вылеты плана")

    if request.app.state.pool is None:
        linked_order = request.app.state.order_store.get(order_id) if order_id else None
        if order_id and not linked_order:
            raise HTTPException(status_code=404, detail="Связанный заказ не найден")
        if linked_order and linked_order["status"] != "approved":
            raise HTTPException(status_code=409, detail="В календарь можно передать только согласованный заказ")
        for start, end, uav_id in slots:
            for existing in request.app.state.schedule_store.values():
                old_start, old_end = _slot_bounds(existing)
                if existing["uavId"] == uav_id and start < old_end and end > old_start:
                    raise HTTPException(status_code=409, detail=f"Борт {uav_id} уже занят в выбранном интервале")
        items = [{"id": str(uuid.uuid4()), **entry} for entry in entries]
        request.app.state.schedule_store.update({item["id"]: item for item in items})
        if linked_order:
            linked_order["status"] = "scheduled"
            linked_order.setdefault("events", []).append({
                "actor": user.username, "from_status": "approved", "to_status": "scheduled",
                "note": f"Назначено {len(items)} БВС одним производственным планом",
            })
        return {"items": items, "count": len(items), "missionGroupId": group_id}

    async with request.app.state.pool.acquire() as connection:
        async with connection.transaction():
            linked_order_id = None
            if order_id:
                try:
                    linked_order_id = uuid.UUID(order_id)
                except ValueError as error:
                    raise HTTPException(status_code=422, detail="Некорректный идентификатор заказа") from error
                status = await connection.fetchval(
                    "SELECT status FROM customer_orders WHERE id=$1 FOR UPDATE", linked_order_id,
                )
                if status is None:
                    raise HTTPException(status_code=404, detail="Связанный заказ не найден")
                if status != "approved":
                    raise HTTPException(status_code=409, detail="В календарь можно передать только согласованный заказ")
            for uav_id in sorted({entry["uavId"] for entry in entries}):
                await connection.execute("SELECT pg_advisory_xact_lock(hashtext($1))", uav_id)
                rows = await connection.fetch(
                    "SELECT payload FROM schedule_entries WHERE payload->>'uavId'=$1 FOR UPDATE", uav_id,
                )
                for row in rows:
                    old_start, old_end = _slot_bounds(_json_object(row["payload"]))
                    if any(candidate_id == uav_id and start < old_end and end > old_start
                           for start, end, candidate_id in slots):
                        raise HTTPException(status_code=409, detail=f"Борт {uav_id} уже занят в выбранном интервале")
            items = []
            for entry in entries:
                entry_id = uuid.uuid4()
                await connection.execute(
                    "INSERT INTO schedule_entries (id, payload, created_by) VALUES ($1,$2::jsonb,$3)",
                    entry_id, json.dumps(entry, ensure_ascii=False), user.username,
                )
                items.append({"id": str(entry_id), **entry})
            if linked_order_id:
                await connection.execute(
                    "UPDATE customer_orders SET status='scheduled', updated_at=now() WHERE id=$1", linked_order_id,
                )
                await connection.execute(
                    """INSERT INTO order_events (order_id, actor, from_status, to_status, note)
                       VALUES ($1,$2,'approved','scheduled',$3)""",
                    linked_order_id, user.username, f"Назначено {len(items)} БВС одним производственным планом",
                )
    return {"items": items, "count": len(items), "missionGroupId": group_id}


@app.patch("/api/schedule/{entry_id}", tags=["Календарь"], summary="Изменить состояние вылета")
async def schedule_update(
    entry_id: uuid.UUID,
    payload: ScheduleEntryUpdate,
    request: Request,
    _: Annotated[UserView, Depends(roles("dispatcher", "admin"))],
):
    key = str(entry_id)
    allowed = {"planned": {"active"}, "active": {"completed"}, "completed": set()}
    updates = {name: value for name, value in jsonable_encoder(payload).items() if value is not None}
    if request.app.state.pool is None:
        item = request.app.state.schedule_store.get(key)
        if not item:
            raise HTTPException(status_code=404, detail="Задание календаря не найдено")
        current = item.get("status", "planned")
        if payload.status != current and payload.status not in allowed.get(current, set()):
            raise HTTPException(status_code=409, detail=f"Переход {current} → {payload.status} недоступен")
        item.update(updates)
        return item
    async with request.app.state.pool.acquire() as connection:
        async with connection.transaction():
            row = await connection.fetchrow("SELECT payload FROM schedule_entries WHERE id=$1 FOR UPDATE", entry_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Задание календаря не найдено")
            item = _json_object(row["payload"])
            current = item.get("status", "planned")
            if payload.status != current and payload.status not in allowed.get(current, set()):
                raise HTTPException(status_code=409, detail=f"Переход {current} → {payload.status} недоступен")
            item.update(updates)
            await connection.execute(
                "UPDATE schedule_entries SET payload=$2::jsonb WHERE id=$1",
                entry_id, json.dumps(item, ensure_ascii=False),
            )
    return {"id": key, **item}


@app.get("/api/traffic/aircraft", tags=["Интеграции"], summary="Получить воздушную обстановку ADS-B/MLAT")
async def aircraft_traffic(request: Request, _: Annotated[UserView, Depends(current_user)]):
    return await request.app.state.aircraft_traffic.get()


@app.get("/api/integrations/dadata/suggest", tags=["Интеграции"], summary="Найти организацию через DaData")
async def dadata_suggest(
    request: Request,
    query: str,
    _: Annotated[UserView, Depends(current_user)],
):
    normalized = " ".join(query.strip().split())
    if len(normalized) < 3 or len(normalized) > 160:
        raise HTTPException(status_code=422, detail="Введите не менее трёх символов названия или ИНН")
    try:
        return await request.app.state.dadata.suggest(normalized)
    except httpx.HTTPStatusError as error:
        raise HTTPException(status_code=502, detail=f"DaData вернула HTTP {error.response.status_code}") from error
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail="DaData временно недоступна; реквизиты можно заполнить вручную") from error


@app.get("/api/weather/forecast", tags=["Интеграции"], summary="Получить прогноз погоды")
async def weather_forecast(
    request: Request,
    _: Annotated[UserView, Depends(current_user)],
    lat: float = 55.7558,
    lon: float = 37.6173,
):
    if not (-85 <= lat <= 85 and -180 <= lon <= 180):
        raise HTTPException(status_code=422, detail="Некорректные координаты")
    try:
        return await request.app.state.weather.forecast(lat, lon)
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.get("/api/weather/radar", tags=["Интеграции"], summary="Получить описание радара осадков")
async def weather_radar(request: Request, _: Annotated[UserView, Depends(current_user)]):
    try:
        return await request.app.state.weather.radar_manifest()
    except (httpx.HTTPError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=502, detail="Радар осадков временно недоступен") from error


@app.get("/api/weather/radar/frame/{index}.png", tags=["Интеграции"], summary="Получить кадр радара осадков")
async def weather_radar_frame(index: int, request: Request, _: Annotated[UserView, Depends(current_user)]):
    try:
        content = await request.app.state.weather.radar_frame(index)
    except (httpx.HTTPError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=502, detail="Радар осадков временно недоступен") from error
    if content is None:
        raise HTTPException(status_code=404, detail="Кадр радара не найден")
    return Response(content=content, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@app.post("/api/missions/optimize", tags=["Полётные задания"], summary="Рассчитать варианты полётного задания")
async def optimize_mission(
    payload: MissionRequest,
    request: Request,
    user: Annotated[UserView, Depends(roles("dispatcher", "admin"))],
):
    started = time.perf_counter()
    if payload.launch_site_id:
        site = next((item for item in await _launch_sites(request) if item['id'] == payload.launch_site_id), None)
        if site is None or site.get('status') == 'closed':
            raise HTTPException(status_code=422, detail='Выбранная площадка не найдена или закрыта.')
        planned_site = PlannedLaunchSite.model_validate(site)
        payload = payload.model_copy(update={
            'launch_site': planned_site,
            'launch_point': (planned_site.lon, planned_site.lat),
            'launch_site_name': planned_site.name,
        })
    zones = await _all_airspace_zones(request)
    try:
        result = await asyncio.to_thread(optimize, payload, zones)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    routing_ms = round((time.perf_counter() - started) * 1000)
    authority_contacts: dict[str, dict] = request.app.state.authority_contact_store
    if request.app.state.pool is not None and result.get("airspace", {}).get("authorities"):
        authority_ids = [uuid.UUID(item["id"]) for item in result["airspace"]["authorities"]]
        async with request.app.state.pool.acquire() as connection:
            rows = await connection.fetch("SELECT zone_id, details FROM authority_contacts WHERE zone_id=ANY($1::uuid[])", authority_ids)
        authority_contacts = {str(row["zone_id"]): _json_object(row["details"]) for row in rows}
    for authority in result.get("airspace", {}).get("authorities", []):
        authority["contact"] = authority_contacts.get(authority["id"])
        authority["contact_status"] = "verified" if authority["contact"] else "missing"
    primary_contact = next((item.get("contact") for item in result.get("airspace", {}).get("authorities", []) if item.get("contact")), None)
    if primary_contact and result["airspace"]["operation_mode"] != "permission":
        procedure = primary_contact.get("procedure", "unknown")
        if procedure in {"permission", "mixed"}:
            result["airspace"]["operation_mode"] = "permission"
            result["airspace"]["status"] = "permission_required"
    unresolved = max((plan.get("airspace_avoidance", {}).get("unresolved", 0) for plan in result.get("plans", [])), default=0)
    if unresolved:
        if result["airspace"]["status"] not in {"permission_required"}:
            result["airspace"]["status"] = "adjustment_required"
        result["airspace"]["messages"].append(
            f"Для {unresolved} участков не найден доказуемо безопасный обход; план нельзя передавать в автопилот."
        )
    plan_routes = {
        plan["id"]: unary_union([shape(vehicle["route"]) for vehicle in plan.get("vehicles", [])])
        for plan in result.get("plans", []) if plan.get("vehicles")
    }
    recommended_id = result.get("recommended_plan_id") or next(iter(plan_routes), None)
    if recommended_id in plan_routes:
        recommended_bounds = plan_routes[recommended_id].bounds
        route_bbox = (recommended_bounds[0] - 0.002, recommended_bounds[1] - 0.002,
                      recommended_bounds[2] + 0.002, recommended_bounds[3] + 0.002)
        if request.app.state.settlements.coverage(route_bbox)["status"] != "COVERED":
            pending = request.app.state.settlement_refresh_task
            if pending is None or pending.done():
                # A public Overpass response must not extend the interactive
                # route-calculation latency. The current result remains
                # explicitly preliminary until a later assessment.
                async def refresh_route_boundaries():
                    try:
                        await asyncio.wait_for(
                            request.app.state.settlements.ensure(route_bbox, request.app.state.http), timeout=55,
                        )
                    except Exception:
                        pass
                request.app.state.settlement_refresh_task = asyncio.create_task(refresh_route_boundaries())
        assessments = {}
        for plan_id, route_geometry in plan_routes.items():
            west, south, east, north = route_geometry.bounds
            margin_deg = max(.002, payload.settlement_clearance_m / 60000)
            bounds = (west - margin_deg, south - margin_deg, east + margin_deg, north + margin_deg)
            coverage = request.app.state.settlements.coverage(bounds)
            longitude, latitude = route_geometry.centroid.x, route_geometry.centroid.y
            utm_zone = min(60, max(1, int((longitude + 180) // 6) + 1))
            metric_crs = CRS.from_epsg((32600 if latitude >= 0 else 32700) + utm_zone)
            to_metric = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True).transform
            corridor = transform(to_metric, route_geometry).buffer(payload.settlement_clearance_m)
            touched = []
            for settlement in request.app.state.settlements.polygons(bounds):
                try:
                    if corridor.intersects(transform(to_metric, shape(settlement["geometry"]))):
                        touched.append({key: settlement[key] for key in ("osm_type", "osm_id", "name", "place", "region", "district", "source", "geometry")})
                except (TypeError, ValueError):
                    continue
            assessments[plan_id] = {
                **coverage, "intersections": touched, "corridor_margin_m": payload.settlement_clearance_m,
                "boundary_authority": "OSM_PRELIMINARY_NOT_LEGAL",
            }
        result["settlement_assessments"] = assessments
        result["settlement_assessment"] = assessments[recommended_id]
        chosen_assessment = assessments[recommended_id]
        if chosen_assessment["intersections"]:
            names = ", ".join(dict.fromkeys(item["name"] or "без названия" for item in chosen_assessment["intersections"][:3]))
            result["airspace"]["messages"].insert(0, f"Маршрут затрагивает границы населённых пунктов ({names}); OSM — предварительный источник, условия полёта требуют проверки.")
        if chosen_assessment["status"] != "COVERED":
            result["airspace"]["messages"].insert(0, "Границы населённых пунктов загружены не для всего маршрута: отсутствие пересечений не подтверждено.")
            if result["airspace"]["status"] == "clear":
                result["airspace"]["status"] = "adjustment_required"
    mission_id = uuid.uuid4()
    result["mission_id"] = str(mission_id)
    result["status"] = "awaiting_airspace_approval" if result.get("airspace", {}).get("operation_mode") == "permission" else "planned"
    result["calculation_ms"] = round((time.perf_counter() - started) * 1000)
    result["timings_ms"] = {"route_planning": routing_ms,
                            "settlement_lookup": result["calculation_ms"] - routing_ms}
    request.app.state.mission_store[str(mission_id)] = {
        "id": str(mission_id), "created_at": datetime.now(timezone.utc), "created_by": user.username,
        "status": result["status"], "request": jsonable_encoder(payload), "result": result,
    }
    if request.app.state.pool is not None:
        async with request.app.state.pool.acquire() as connection:
            await connection.execute(
                "INSERT INTO missions (id, created_by, request, result) VALUES ($1, $2, $3::jsonb, $4::jsonb)",
                mission_id, user.username,
                json.dumps(jsonable_encoder(payload), ensure_ascii=False),
                json.dumps(result, ensure_ascii=False),
            )
    return result


@app.get("/api/missions", tags=["Полётные задания"], summary="Получить журнал полётных заданий")
async def list_missions(request: Request, _: Annotated[UserView, Depends(current_user)]):
    if request.app.state.pool is None:
        items = sorted(request.app.state.mission_store.values(), key=lambda item: item["created_at"], reverse=True)
        return {"items": [{"id": item["id"], "created_at": item["created_at"], "created_by": item["created_by"], "status": item["status"], **item["result"]} for item in items], "count": len(items), "storage": "memory"}
    async with request.app.state.pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT id, created_at, created_by, status, result FROM missions ORDER BY created_at DESC LIMIT 100"
        )
    items = [{"id": str(row["id"]), "created_at": row["created_at"], "created_by": row["created_by"], "status": row["status"], **_json_object(row["result"])} for row in rows]
    return {"items": items, "count": len(items)}


async def _stored_mission(request: Request, mission_id: uuid.UUID) -> dict:
    stored = request.app.state.mission_store.get(str(mission_id))
    if stored:
        return {
            "id": stored["id"], "created_at": stored["created_at"], "created_by": stored["created_by"],
            "status": stored["status"], "request": stored.get("request"), "result": stored["result"],
        }
    if request.app.state.pool is not None:
        async with request.app.state.pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT id, created_at, created_by, status, request, result FROM missions WHERE id=$1", mission_id,
            )
        if row:
            return {
                "id": str(row["id"]), "created_at": row["created_at"], "created_by": row["created_by"],
                "status": row["status"], "request": _json_object(row["request"]), "result": _json_object(row["result"]),
            }
    raise HTTPException(status_code=404, detail="Полётное задание не найдено")


@app.get("/api/missions/{mission_id}", tags=["Полётные задания"], summary="Получить полётное задание")
async def get_mission(
    mission_id: uuid.UUID,
    request: Request,
    _: Annotated[UserView, Depends(current_user)],
):
    return await _stored_mission(request, mission_id)


@app.get(
    "/api/missions/{mission_id}/flight-plan",
    tags=["Полётные задания"],
    summary="Получить нормализованный план с этапами и временной траекторией",
)
async def get_mission_flight_plan(
    mission_id: uuid.UUID,
    request: Request,
    _: Annotated[UserView, Depends(current_user)],
    plan: str | None = Query(default=None, pattern="^(fast|economy|safe)$"),
):
    stored = await _stored_mission(request, mission_id)
    try:
        return build_flight_plan(stored["result"], plan)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/missions/{mission_id}/export", tags=["Полётные задания"], summary="Экспортировать полётное задание")
async def export_mission_route(
    mission_id: uuid.UUID,
    request: Request,
    _: Annotated[UserView, Depends(current_user)],
    format: str = Query(default="kml", pattern="^(kml|geojson|gpx)$"),
    plan: str | None = Query(default=None, pattern="^(fast|economy|safe)$"),
):
    stored = await _stored_mission(request, mission_id)
    try:
        content, media_type, filename = export_mission(stored["result"], format, plan)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return Response(
        content=content, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "private, no-store"},
    )


@app.websocket("/api/telemetry/ws")
async def telemetry(websocket: WebSocket):
    token = websocket.cookies.get("mc_session")
    try:
        _read_user(token)
    except HTTPException:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    try:
        include_static_geometry = True
        simulation_rate = 1.0
        virtual_elapsed = simulation_elapsed()
        previous_tick = time.monotonic()
        while True:
            tick = time.monotonic()
            virtual_elapsed += (tick - previous_tick) * simulation_rate
            previous_tick = tick
            packet = snapshot(virtual_elapsed, simulation_rate, include_static_geometry)
            await websocket.send_json(packet)
            include_static_geometry = False
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=0.25)
                if message.get("type") == "set_simulation_rate":
                    requested_rate = float(message.get("rate", 1))
                    if requested_rate in {0.0, 1.0, 2.0, 4.0}:
                        simulation_rate = requested_rate
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        return
