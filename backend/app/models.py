from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=2, max_length=128)


class PlannedLaunchSite(BaseModel):
    id: str = Field(max_length=100)
    name: str = Field(max_length=160)
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-85, le=85)
    kind: Literal['runway', 'vtol', 'mixed']
    runway_length_m: int = Field(ge=20, le=5000)
    heading_deg: int = Field(ge=0, le=359)
    supports: list[Literal['fixed_wing', 'multirotor', 'vtol']]
    status: Literal['open', 'limited', 'closed']


class MissionRequest(BaseModel):
    name: str = Field(default="Новая миссия", max_length=200)
    area: dict[str, Any]
    result_type: Literal["orthophoto", "thermal_map", "point_cloud", "magnetic_map", "powerline_report", "digital_twin", "ndvi"] = "orthophoto"
    result_types: list[Literal['orthophoto','thermal_map','point_cloud','magnetic_map','powerline_report','digital_twin','ndvi']] = Field(default_factory=list, max_length=7)
    survey_line_spacing_m: float = Field(default=40, ge=5, le=200)
    corridor_width_m: float = Field(default=80, ge=10, le=1000)
    launch_point: tuple[float, float] | None = None
    launch_site_name: str | None = Field(default=None, max_length=160)
    launch_site_id: str | None = Field(default=None, max_length=100)
    launch_site: PlannedLaunchSite | None = None
    control_link_mode: Literal['radio', 'external'] = 'radio'
    radio_equipment_range_km: float = Field(default=50, ge=1, le=500)
    ground_antenna_height_m: float = Field(default=5, ge=0, le=100)
    survey_type: Literal["rgb", "ir", "multispectral", "lidar", "geophysical"] = "rgb"
    payload_id: str | None = Field(default=None, max_length=100)
    gsd_cm_px: float = Field(default=5.0, gt=0.2, le=100)
    max_flight_altitude_m: float = Field(default=150.0, ge=25, le=5000)
    side_overlap: float = Field(default=0.60, ge=0.0, lt=0.95)
    forward_overlap: float = Field(default=0.70, ge=0.0, lt=0.95)
    deadline: datetime | None = None
    max_uavs: int | None = Field(default=None, ge=1, le=10)
    earliest_start: datetime | None = None
    wind_speed_mps: float = Field(default=3.2, ge=0, le=60)
    wind_direction_deg: float = Field(default=315, ge=0, lt=360)
    precipitation_probability: float = Field(default=0.05, ge=0, le=1)
    visibility_m: float = Field(default=18000, ge=0)
    immediate: bool = False
    airspace_check: bool = True
    prohibited_clearance_m: float = Field(default=300, ge=0, le=10000)
    danger_clearance_m: float = Field(default=150, ge=0, le=10000)
    obstacle_clearance_m: float = Field(default=50, ge=0, le=3000)
    settlement_clearance_m: float = Field(default=100, ge=0, le=3000)
    vehicle_separation_m: float = Field(default=100, ge=20, le=3000)

    @field_validator("area")
    @classmethod
    def validate_area(cls, value: dict[str, Any]) -> dict[str, Any]:
        geometry = value.get("geometry") if value.get("type") == "Feature" else value
        if not isinstance(geometry, dict) or geometry.get("type") not in {"Polygon", "MultiPolygon", "LineString", "MultiLineString"}:
            raise ValueError("Требуется Polygon/MultiPolygon либо LineString/MultiLineString для коридорной миссии")
        return value

    @model_validator(mode="after")
    def validate_geometry_for_result(self):
        geometry = self.area.get("geometry") if self.area.get("type") == "Feature" else self.area
        outputs = self.result_types or [self.result_type]
        if geometry.get("type") in {"LineString", "MultiLineString"} and any(item not in {"powerline_report", "thermal_map"} for item in outputs):
            raise ValueError("Линейный маршрут поддерживается для обследования ЛЭП и теплотрасс")
        return self

    @field_validator('launch_point')
    @classmethod
    def validate_launch(cls, value):
        if value is not None and not (-180 <= value[0] <= 180 and -85 <= value[1] <= 85):
            raise ValueError('Некорректные координаты точки старта WGS 84')
        return value


class LaunchSiteInput(BaseModel):
    name: str = Field(min_length=3, max_length=160)
    kind: Literal["runway", "vtol", "mixed"] = "mixed"
    lat: float = Field(ge=-85, le=85)
    lon: float = Field(ge=-180, le=180)
    surface: str = Field(default="полевой старт", min_length=2, max_length=100)
    runway_length_m: int = Field(default=60, ge=20, le=5000)
    heading_deg: int = Field(default=0, ge=0, le=359)
    supports: list[Literal["fixed_wing", "multirotor", "vtol"]] = Field(default_factory=lambda: ["multirotor"], min_length=1)
    has_charging: bool = False
    has_fuel: bool = False
    status: Literal["open", "limited", "closed"] = "open"
    notes: str = Field(default="", max_length=500)


class UserView(BaseModel):
    username: str
    full_name: str
    role: Literal["admin", "dispatcher", "manager"]


OrderStatus = Literal["new", "qualification", "proposal", "approved", "scheduled", "in_progress", "processing", "qc", "delivered", "cancelled"]


class CustomerInput(BaseModel):
    name: str = Field(min_length=2, max_length=240)
    inn: str | None = Field(default=None, pattern=r"^(?:\d{10}|\d{12})$")
    kpp: str | None = Field(default=None, pattern=r"^\d{9}$")
    ogrn: str | None = Field(default=None, pattern=r"^(?:\d{13}|\d{15})$")
    legal_address: str | None = Field(default=None, max_length=500)
    management_name: str | None = Field(default=None, max_length=240)
    status: str | None = Field(default=None, max_length=32)


class ContactInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    phone: str = Field(min_length=5, max_length=40)
    email: str = Field(min_length=5, max_length=240)


class OrderCreate(BaseModel):
    title: str = Field(min_length=5, max_length=240)
    result_type: Literal["orthophoto", "thermal_map", "point_cloud", "magnetic_map", "powerline_report", "digital_twin", "ndvi"]
    desired_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    desired_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    location: str = Field(min_length=2, max_length=240)
    area_km2: float = Field(gt=0, le=100000)
    budget_rub: int | None = Field(default=None, ge=0)
    priority: Literal["normal", "high", "critical"] = "normal"
    geometry: dict[str, Any]
    customer: CustomerInput
    contact: ContactInput
    requirements: dict[str, Any] = Field(default_factory=dict)
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_order(self):
        try:
            requested = date.fromisoformat(self.desired_date)
            datetime.strptime(self.desired_time, "%H:%M")
        except ValueError as error:
            raise ValueError("Некорректная дата или время") from error
        if requested < date.today():
            raise ValueError("Нельзя зарегистрировать заявку на прошедшую дату")
        geometry_type = self.geometry.get("type")
        if geometry_type not in {"Polygon", "LineString"}:
            raise ValueError("Поддерживается территория Polygon или трасса LineString")
        if geometry_type == "LineString" and self.result_type not in {"thermal_map", "powerline_report"}:
            raise ValueError("Линейный объект поддерживается для теплотрасс и ЛЭП")
        return self


class OrderStatusUpdate(BaseModel):
    status: OrderStatus
    note: str = Field(default="", max_length=1000)


class ScheduleEntryCreate(BaseModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    time: str = Field(pattern=r"^\d{2}:\d{2}$")
    title: str = Field(min_length=3, max_length=240)
    location: str = Field(min_length=2, max_length=240)
    uavId: str = Field(min_length=3, max_length=100)
    uavName: str = Field(min_length=3, max_length=160)
    duration: float = Field(gt=0, le=48)
    status: Literal["planned", "maintenance"] = "planned"
    product: str = Field(min_length=2, max_length=160)
    payloadId: str | None = None
    areaKm2: float | None = Field(default=None, gt=0)
    costRub: int | None = Field(default=None, ge=0)
    source: Literal["planner"] = "planner"
    orderId: str | None = None
    orderNumber: str | None = None
    customerName: str | None = None
    missionGroupId: str | None = Field(default=None, max_length=100)
    simulation: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_slot(self):
        if date.fromisoformat(self.date) < date.today():
            raise ValueError("Нельзя планировать задание на прошедшую дату")
        datetime.strptime(self.time, "%H:%M")
        return self


class ScheduleBatchCreate(BaseModel):
    entries: list[ScheduleEntryCreate] = Field(min_length=1, max_length=4)


class ScheduleEntryUpdate(BaseModel):
    status: Literal["planned", "active", "completed"]
    demoStartedAt: datetime | None = None
    demoCompletedAt: datetime | None = None


AirspaceCategory = Literal["prohibited", "danger", "orvd", "obstacle", "custom"]


class AirspaceZoneInput(BaseModel):
    """A user-maintained airspace restriction or obstacle in WGS 84."""

    category: AirspaceCategory
    name: str = Field(min_length=2, max_length=300)
    code: str | None = Field(default=None, max_length=120)
    geometry: dict[str, Any]
    lower_limit: str | None = Field(default=None, max_length=160)
    upper_limit: str | None = Field(default=None, max_length=160)
    schedule: str | None = Field(default=None, max_length=1000)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    enabled: bool = True
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("geometry")
    @classmethod
    def validate_geometry(cls, value: dict[str, Any]) -> dict[str, Any]:
        geometry = value.get("geometry") if value.get("type") == "Feature" else value
        if not isinstance(geometry, dict) or geometry.get("type") not in {"Point", "Polygon", "MultiPolygon"}:
            raise ValueError("Допустимы Point, Polygon или MultiPolygon в WGS 84")
        return geometry

    @model_validator(mode="after")
    def validate_period(self):
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            raise ValueError("Окончание действия должно быть позже начала")
        return self


class AirspaceImportRequest(BaseModel):
    category: AirspaceCategory
    collection: dict[str, Any]
    source_name: str = Field(default="user-import", min_length=2, max_length=240)

    @field_validator("collection")
    @classmethod
    def validate_collection(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("type") != "FeatureCollection" or not isinstance(value.get("features"), list):
            raise ValueError("Ожидается GeoJSON FeatureCollection")
        if len(value["features"]) > 5000:
            raise ValueError("За один импорт можно загрузить не более 5000 объектов")
        return value


class AuthorityContactInput(BaseModel):
    organization: str = Field(min_length=2, max_length=300)
    dispatch_center: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=240)
    address: str | None = Field(default=None, max_length=500)
    procedure: Literal["notification", "permission", "mixed", "unknown"] = "unknown"
    notes: str = Field(default="", max_length=2000)
    source: str | None = Field(default=None, max_length=500)
