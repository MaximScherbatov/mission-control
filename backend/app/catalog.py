from __future__ import annotations

UAVS = [
    {
        "id": "uav-geoscan-201-01", "name": "Геоскан 201 · 01", "series": "GEOSCAN 201", "modification": "Геодезия", "type": "fixed_wing",
        "payload_kg": 1.5, "cruise_speed_kmh": 84, "max_altitude_m": 4000, "endurance_min": 180,
        "energy_kind": "electric", "energy_capacity": 1.45, "consumption_per_hour": 0.52, "max_wind_mps": 12,
        "status": "ready", "current_base_id": "base-north", "flight_hours": 412.6, "maintenance_due_hours": 37.4,
        "cost_per_hour_rub": 9300, "turn_radius_m": 95, "max_bank_deg": 25, "planning_min_runway_m": 260, "turnaround_min": 28,
    },
    {
        "id": "uav-geoscan-201-02", "name": "Геоскан 201 · 02", "series": "GEOSCAN 201", "modification": "Термо / мультиспектр", "type": "fixed_wing",
        "payload_kg": 1.5, "cruise_speed_kmh": 82, "max_altitude_m": 4000, "endurance_min": 175,
        "energy_kind": "electric", "energy_capacity": 1.45, "consumption_per_hour": 0.54, "max_wind_mps": 12,
        "status": "ready", "current_base_id": "base-south", "flight_hours": 401.2, "maintenance_due_hours": 48.8,
        "cost_per_hour_rub": 9100, "turn_radius_m": 95, "max_bank_deg": 25, "planning_min_runway_m": 260, "turnaround_min": 28,
    },
    {
        "id": "uav-geoscan-701-01", "name": "Геоскан 701 · 01", "series": "GEOSCAN 701", "modification": "Геодезия", "type": "fixed_wing",
        "payload_kg": 3.5, "cruise_speed_kmh": 100, "max_altitude_m": 5000, "endurance_min": 600,
        "energy_kind": "fuel", "energy_capacity": 50, "consumption_per_hour": 4.8, "max_wind_mps": 15,
        "status": "ready", "current_base_id": "base-west", "flight_hours": 618.4, "maintenance_due_hours": 81.6,
        "cost_per_hour_rub": 17100, "turn_radius_m": 145, "max_bank_deg": 25, "planning_min_runway_m": 450, "turnaround_min": 42,
    },
    {
        "id": "uav-geoscan-401-geo", "name": "Геоскан 401 · Геодезия", "series": "GEOSCAN 401", "modification": "Геодезия", "type": "multirotor",
        "payload_kg": 2.0, "cruise_speed_kmh": 42, "max_altitude_m": 500, "endurance_min": 60,
        "energy_kind": "electric", "energy_capacity": 1.1, "consumption_per_hour": 1.05, "max_wind_mps": 12,
        "status": "ready", "current_base_id": "base-north", "flight_hours": 194.3, "maintenance_due_hours": 55.7,
        "cost_per_hour_rub": 8200, "turn_radius_m": 0, "turnaround_min": 14,
    },
    {
        "id": "uav-geoscan-401-lidar", "name": "Геоскан 401 · Лидар", "series": "GEOSCAN 401", "modification": "Лидар", "type": "multirotor",
        "payload_kg": 2.0, "cruise_speed_kmh": 36, "max_altitude_m": 500, "endurance_min": 48,
        "energy_kind": "electric", "energy_capacity": 1.1, "consumption_per_hour": 1.18, "max_wind_mps": 12,
        "status": "ready", "current_base_id": "base-south", "flight_hours": 247.1, "maintenance_due_hours": 22.9,
        "cost_per_hour_rub": 11800, "turn_radius_m": 0, "turnaround_min": 18,
    },
    {
        "id": "uav-geoscan-401-mag", "name": "Геоскан 401 · Геофизика", "series": "GEOSCAN 401", "modification": "Геофизика", "type": "multirotor",
        "payload_kg": 2.0, "cruise_speed_kmh": 28, "max_altitude_m": 500, "endurance_min": 50,
        "energy_kind": "electric", "energy_capacity": 1.1, "consumption_per_hour": 1.12, "max_wind_mps": 10,
        "status": "ready", "current_base_id": "base-west", "flight_hours": 249.2, "maintenance_due_hours": 26.8,
        "cost_per_hour_rub": 12600, "turn_radius_m": 0, "turnaround_min": 18,
    },
    {
        "id": "uav-geoscan-gemini-01", "name": "Геоскан Gemini · 01", "series": "GEOSCAN GEMINI", "modification": "Геодезия", "type": "multirotor",
        "payload_kg": 0.7, "cruise_speed_kmh": 43, "max_altitude_m": 500, "endurance_min": 40,
        "energy_kind": "electric", "energy_capacity": 0.145, "consumption_per_hour": 0.22, "max_wind_mps": 10,
        "status": "ready", "current_base_id": "base-north", "flight_hours": 126.8, "maintenance_due_hours": 73.2,
        "cost_per_hour_rub": 6100, "turn_radius_m": 0, "turnaround_min": 12,
    },
    {
        "id": "uav-geoscan-gemini-ms", "name": "Геоскан Gemini · Мультиспектр", "series": "GEOSCAN GEMINI", "modification": "Мультиспектр", "type": "multirotor",
        "payload_kg": 0.7, "cruise_speed_kmh": 40, "max_altitude_m": 500, "endurance_min": 36,
        "energy_kind": "electric", "energy_capacity": 0.145, "consumption_per_hour": 0.24, "max_wind_mps": 10,
        "status": "charging", "current_base_id": "base-west", "flight_hours": 98.4, "maintenance_due_hours": 91.6,
        "cost_per_hour_rub": 6900, "turn_radius_m": 0, "turnaround_min": 75,
    },
    {
        "id": "uav-geoscan-gemini-02", "name": "Геоскан Gemini · 02", "series": "GEOSCAN GEMINI", "modification": "Геодезия", "type": "multirotor",
        "payload_kg": 0.7, "cruise_speed_kmh": 35, "max_altitude_m": 500, "endurance_min": 40,
        "energy_kind": "electric", "energy_capacity": 0.145, "consumption_per_hour": 0.22, "max_wind_mps": 10,
        "status": "ready", "current_base_id": "base-north", "flight_hours": 88.6, "maintenance_due_hours": 111.4,
        "cost_per_hour_rub": 6100, "turn_radius_m": 0, "turnaround_min": 12,
    },
    {
        "id": "uav-geoscan-201-03", "name": "Геоскан 201 · 03", "series": "GEOSCAN 201", "modification": "Геодезия", "type": "fixed_wing",
        "payload_kg": 1.5, "cruise_speed_kmh": 86, "max_altitude_m": 4000, "endurance_min": 180,
        "energy_kind": "electric", "energy_capacity": 1.45, "consumption_per_hour": 0.53, "max_wind_mps": 12,
        "status": "ready", "current_base_id": "base-south", "flight_hours": 322.1, "maintenance_due_hours": 67.9,
        "cost_per_hour_rub": 9300, "turn_radius_m": 95, "max_bank_deg": 25, "planning_min_runway_m": 260, "turnaround_min": 28,
    },
]

MAINTENANCE_RULES = {
    'GEOSCAN 201': ('flights', 80),
    'GEOSCAN 401': ('flights', 80),
    'GEOSCAN 501': ('flight_hours', 160),
    'GEOSCAN 701': ('engine_hours', 100),
    'GEOSCAN 801': ('flight_hours', 160),
}

for uav in UAVS:
    rule = MAINTENANCE_RULES.get(uav['series'])
    if rule:
        uav['maintenance_metric'], uav['maintenance_interval'] = rule
    if uav['series'] in {'GEOSCAN 201', 'GEOSCAN 401'}:
        # The contest FAQ gives the interval, not the fleet's last-service log.
        # Do not convert the old demonstration hour forecast into a fake count.
        uav['flights_since_maintenance'] = None
    elif not rule:
        uav['maintenance_metric'] = 'unverified'
        uav['maintenance_interval'] = None

OPTICS = [
    {
        "id": "payload-sony-61", "name": "Sony ILX-LR1 · 61 Мп / 35 мм", "category": "camera", "mass_kg": 0.49,
        "spectrums": ["rgb"], "max_resolution_cm_px": 1.8, "focal_length_mm": 35, "aperture_mm": 17.5,
        "f_number": 2.0, "sensor_type": "полнокадровая CMOS BSI", "sensor_width_mm": 35.7, "sensor_height_mm": 23.8,
        "resolution_width_px": 9504, "resolution_height_px": 6336, "fps": 3.0,
        "compatible_uav_ids": ["uav-geoscan-201-01", "uav-geoscan-201-02", "uav-geoscan-701-01", "uav-geoscan-401-geo"],
    },
    {
        "id": "payload-gemini-rgb", "name": "Sony UMC-R10C · Gemini (учебный профиль)", "category": "camera", "mass_kg": 0.30,
        "spectrums": ["rgb"], "max_resolution_cm_px": 2.5, "focal_length_mm": 20, "aperture_mm": 12,
        "f_number": 2.0, "sensor_type": "CMOS APS-C", "sensor_width_mm": 23.5, "sensor_height_mm": 15.6,
        "resolution_width_px": 5456, "resolution_height_px": 3632, "fps": 2.0,
        "compatible_uav_ids": ["uav-geoscan-gemini-01"],
    },
    {
        "id": "payload-pollux", "name": "Geoscan Pollux · 8 мм", "category": "multispectral", "mass_kg": 0.26,
        "spectrums": ["rgb", "multispectral"], "max_resolution_cm_px": 4.0, "focal_length_mm": 8,
        "aperture_mm": 4.0, "f_number": 2.0, "sensor_type": "5 каналов · Sony IMX273 · global shutter", "sensor_width_mm": 4.968,
        "sensor_height_mm": 3.726, "resolution_width_px": 1440, "resolution_height_px": 1080, "fps": 1.2,
        "compatible_uav_ids": ["uav-geoscan-201-02", "uav-geoscan-gemini-ms"],
    },
    {
        "id": "payload-thermal-814", "name": "FLIR Vue Pro R · 640 / 13 мм (пример)", "category": "thermal", "mass_kg": 1.1,
        "spectrums": ["ir"], "max_resolution_cm_px": 8.0, "focal_length_mm": 13, "aperture_mm": 6.5,
        "f_number": 2.0, "sensor_type": "радиометрическая LWIR · 17 мкм/пиксель", "sensor_width_mm": 10.88,
        "sensor_height_mm": 8.704, "resolution_width_px": 640, "resolution_height_px": 512, "fps": 2.0,
        "compatible_uav_ids": ["uav-geoscan-201-01", "uav-geoscan-201-02"],
    },
    {
        "id": "payload-agm-lidar", "name": "RIEGL miniVUX-1UAV (пример интеграции)", "category": "lidar", "mass_kg": 1.8,
        "spectrums": ["lidar"], "max_resolution_cm_px": 0, "focal_length_mm": 0, "aperture_mm": 0,
        "f_number": 0, "sensor_type": "LiDAR + GNSS геодезического класса", "sensor_width_mm": 0, "sensor_height_mm": 0,
        "resolution_width_px": 0, "resolution_height_px": 0, "fps": 200.0, "fixed_swath_m": 160,
        "compatible_uav_ids": ["uav-geoscan-401-lidar"],
    },
    {
        "id": "payload-quantum-mag", "name": "Geoscan GeoShark", "category": "magnetometer", "mass_kg": 1.5,
        "spectrums": ["geophysical"], "max_resolution_cm_px": 0, "focal_length_mm": 0, "aperture_mm": 0,
        "f_number": 0, "sensor_type": "квантовый магнитометр · 1 пТл/√Гц", "sensor_width_mm": 0, "sensor_height_mm": 0,
        "resolution_width_px": 0, "resolution_height_px": 0, "fps": 1000.0, "fixed_swath_m": 25,
        "compatible_uav_ids": ["uav-geoscan-401-mag"],
    },
]

TECHNOLOGY_PROFILES = [
    {"id": "tech-orthophoto", "name": "Площадная аэрофотосъёмка", "result_type": "orthophoto", "survey_type": "rgb", "purpose": "Ортофотоплан и цифровая модель территории", "acceptance": ["GSD до 5 см/пикс", "полнота покрытия 100%", "геодезическая привязка"], "deliverables": ["GeoTIFF", "ЦМР", "облако точек"], "recommended_payload_ids": ["payload-sony-61", "payload-gemini-rgb"]},
    {"id": "tech-thermal", "name": "Тепловизионная съёмка", "result_type": "thermal_map", "survey_type": "ir", "purpose": "Поиск теплопотерь, утечек и перегрева", "acceptance": ["диапазон 8–14 мкм", "попиксельное RGB/ИК", "стабильное погодное окно"], "deliverables": ["тепловая карта", "RGB/ИК", "отчёт аномалий"], "recommended_payload_ids": ["payload-thermal-814"]},
    {"id": "tech-lidar", "name": "Воздушное лазерное сканирование", "result_type": "point_cloud", "survey_type": "lidar", "purpose": "Рельеф под растительностью и точная 3D-геометрия", "acceptance": ["60–80 точек/м²", "СКО ЦМР до 15 см", "классификация точек"], "deliverables": ["LAS/LAZ", "ЦМР/ЦММ", "3D-модель"], "recommended_payload_ids": ["payload-agm-lidar"]},
    {"id": "tech-magnetic", "name": "Высокоточная магнитная съёмка", "result_type": "magnetic_map", "survey_type": "geophysical", "purpose": "Картирование магнитного поля и аномалий", "acceptance": ["СКО до ±2 нТ", "контрольные маршруты ≥ 5%", "огибание рельефа"], "deliverables": ["карта аномалий", "XYZ", "GeoTIFF"], "recommended_payload_ids": ["payload-quantum-mag"]},
    {"id": "tech-powerline", "name": "Обследование ЛЭП", "result_type": "powerline_report", "survey_type": "rgb", "purpose": "Габариты, провес, растительность и дефекты", "acceptance": ["фото от 5 см/пикс", "3D-реконструкция проводов", "полнота опор"], "deliverables": ["KML", "XLSX", "фото", "ЦМП"], "recommended_payload_ids": ["payload-sony-61"]},
    {"id": "tech-digital-twin", "name": "Цифровой двойник", "result_type": "digital_twin", "survey_type": "rgb", "purpose": "Текстурированная модель для ГИС, BIM и города", "acceptance": ["полнота ≥ 98%", "единая система координат", "LOD и тайлинг"], "deliverables": ["3D Tiles", "glTF", "ортофото"], "recommended_payload_ids": ["payload-sony-61"]},
    {"id": "tech-ndvi", "name": "Мультиспектральный агромониторинг", "result_type": "ndvi", "survey_type": "multispectral", "purpose": "NDVI, зоны угнетения и карты предписаний", "acceptance": ["радиометрическая калибровка", "сопоставимое освещение", "геопривязка"], "deliverables": ["GeoTIFF", "SHP", "карта предписаний"], "recommended_payload_ids": ["payload-pollux"]},
]

BASES = [
    {
        "id": "base-north", "name": "ВПП Север", "lat": 55.878, "lon": 37.305,
        "geometry": {"type": "Polygon", "coordinates": [[[37.297, 55.874], [37.315, 55.874], [37.316, 55.882], [37.298, 55.882], [37.297, 55.874]]]},
        "surface": "асфальт", "runway_length_m": 460, "heading_deg": 82,
        "supports": ["fixed_wing", "multirotor", "vtol"], "has_charging": True,
        "has_fuel": False, "status": "open",
    },
    {
        "id": "base-south", "name": "ВПП Юг", "lat": 55.644, "lon": 37.724,
        "geometry": {"type": "Polygon", "coordinates": [[[37.713, 55.640], [37.734, 55.640], [37.735, 55.648], [37.714, 55.648], [37.713, 55.640]]]},
        "surface": "грунт", "runway_length_m": 380, "heading_deg": 28,
        "supports": ["fixed_wing", "multirotor", "vtol"], "has_charging": True,
        "has_fuel": True, "status": "open",
    },
    {
        "id": "base-west", "name": "Площадка Запад", "lat": 55.771, "lon": 37.205,
        "geometry": {"type": "Polygon", "coordinates": [[[37.199, 55.767], [37.212, 55.767], [37.212, 55.775], [37.199, 55.775], [37.199, 55.767]]]},
        "surface": "полевой старт", "runway_length_m": 260, "heading_deg": 104,
        "supports": ["fixed_wing", "multirotor", "vtol"], "has_charging": False,
        "has_fuel": True, "status": "open",
    },
    {
        "id": "base-klin", "name": "Полевой аэродром Клин", "lat": 56.24, "lon": 36.72,
        "geometry": {"type": "Point", "coordinates": [36.72, 56.24]},
        "surface": "подготовленный грунт", "runway_length_m": 420, "heading_deg": 64,
        "supports": ["fixed_wing", "multirotor", "vtol"], "has_charging": True,
        "has_fuel": False, "status": "open",
    },
    {
        "id": "base-yakhroma", "name": "Площадка Яхрома", "lat": 56.35, "lon": 36.95,
        "geometry": {"type": "Point", "coordinates": [36.95, 56.35]},
        "surface": "бетон", "runway_length_m": 40, "heading_deg": 0,
        "supports": ["multirotor", "vtol"], "has_charging": True,
        "has_fuel": False, "status": "open",
    },
    {
        "id": "base-dmitrov", "name": "Аэродром Дмитров", "lat": 56.25, "lon": 37.35,
        "geometry": {"type": "Point", "coordinates": [37.35, 56.25]},
        "surface": "асфальт", "runway_length_m": 680, "heading_deg": 82,
        "supports": ["fixed_wing", "multirotor", "vtol"], "has_charging": True,
        "has_fuel": True, "status": "open",
    },
    {
        "id": "base-sergiev-posad", "name": "Площадка Сергиев Посад", "lat": 56.27, "lon": 37.55,
        "geometry": {"type": "Point", "coordinates": [37.55, 56.27]},
        "surface": "полевой старт", "runway_length_m": 310, "heading_deg": 118,
        "supports": ["fixed_wing", "multirotor", "vtol"], "has_charging": True,
        "has_fuel": False, "status": "open",
    },
    {
        "id": "base-demo-ligachevo", "name": "Учебная ВПП Лигачёво", "lat": 55.975, "lon": 37.25,
        "geometry": {"type": "Point", "coordinates": [37.25, 55.975]},
        "surface": "подготовленный грунт · учебная", "runway_length_m": 460, "heading_deg": 180,
        "supports": ["fixed_wing", "multirotor", "vtol"], "has_charging": True,
        "has_fuel": False, "status": "open",
    },
]

USERS = {
    "dispatcher": {"password": "dispatcher", "full_name": "Алексей Волков", "role": "dispatcher"},
    "manager": {"password": "manager", "full_name": "Елена Орлова", "role": "manager"},
    "admin": {"password": "admin", "full_name": "Системный администратор", "role": "admin"},
}
