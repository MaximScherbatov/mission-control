from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta


ORDER_TRANSITIONS = {
    "new": {"qualification", "cancelled"},
    "qualification": {"proposal", "cancelled"},
    "proposal": {"approved", "qualification", "cancelled"},
    "approved": {"scheduled", "cancelled"},
    "scheduled": {"in_progress", "cancelled"},
    "in_progress": {"processing", "cancelled"},
    "processing": {"qc", "cancelled"},
    "qc": {"delivered", "processing"},
    "delivered": set(),
    "cancelled": set(),
}


def ensure_transition(current: str, target: str) -> None:
    if target not in ORDER_TRANSITIONS.get(current, set()):
        raise ValueError(f"Переход «{current}» → «{target}» не разрешён бизнес-процессом")


def demo_orders() -> list[dict]:
    today = date.today()
    base_geometry = {
        "type": "Polygon",
        "coordinates": [[[37.457, 55.787], [37.480, 55.787], [37.480, 55.777], [37.457, 55.777], [37.457, 55.787]]],
    }
    corridor = {
        "type": "LineString",
        "coordinates": [[37.455, 55.786], [37.462, 55.783], [37.470, 55.784], [37.480, 55.778]],
    }
    rows = [
        {
            "id": "10000000-0000-4000-8000-000000000001", "number": "MC-26091", "status": "new",
            "title": "Цифровой двойник логистического комплекса", "result_type": "digital_twin",
            "desired_date": str(today + timedelta(days=7)), "desired_time": "10:00", "location": "Химки",
            "area_km2": 1.9, "budget_rub": 185000, "priority": "normal", "geometry": base_geometry,
            "customer": {"name": "ООО «Северный терминал»", "inn": "5047281097", "kpp": "504701001", "ogrn": "1215000048120", "legal_address": "Московская область, г. Химки", "management_name": "Орлов Михаил Андреевич", "status": "ACTIVE"},
            "contact": {"name": "Анна Корнеева", "phone": "+7 495 120-24-18", "email": "project@example.ru"},
            "requirements": {"gsd_cm_px": 3, "deliverables": ["3D Tiles", "ортофотоплан"], "crs": "МСК-50"},
            "notes": "Нужна оценка сроков до заключения договора.", "created_by": "manager",
        },
        {
            "id": "10000000-0000-4000-8000-000000000002", "number": "MC-26088", "status": "qualification",
            "title": "Тепловизионное обследование теплотрассы", "result_type": "thermal_map",
            "desired_date": str(today + timedelta(days=9)), "desired_time": "06:30", "location": "Щукино",
            "area_km2": 0.8, "budget_rub": 94000, "priority": "high", "geometry": corridor,
            "customer": {"name": "ПАО «МОЭК»", "inn": "7720518494", "kpp": "772001001", "ogrn": "1047796974092", "legal_address": "г. Москва", "management_name": None, "status": "ACTIVE"},
            "contact": {"name": "Илья Воронцов", "phone": "+7 495 587-77-88", "email": "inspection@example.ru"},
            "requirements": {"gsd_cm_px": 8, "deliverables": ["тепловая карта", "ведомость аномалий"], "time_window": "до восхода"},
            "notes": "Линейный объект, требуется стабильный температурный контраст.", "created_by": "dispatcher",
        },
        {
            "id": "10000000-0000-4000-8000-000000000003", "number": "MC-26084", "status": "proposal",
            "title": "Ортофотоплан территории реновации", "result_type": "orthophoto",
            "desired_date": str(today + timedelta(days=12)), "desired_time": "09:00", "location": "Хорошёво-Мнёвники",
            "area_km2": 4.6, "budget_rub": 148000, "priority": "normal", "geometry": base_geometry,
            "customer": {"name": "ГБУ «Мосгоргеотрест»", "inn": "7709197610", "kpp": "770901001", "ogrn": "1027739518050", "legal_address": "г. Москва", "management_name": None, "status": "ACTIVE"},
            "contact": {"name": "Мария Нестерова", "phone": "+7 495 629-45-61", "email": "survey@example.ru"},
            "requirements": {"gsd_cm_px": 5, "deliverables": ["GeoTIFF", "ЦМР"], "crs": "МСК Москвы"},
            "notes": "Коммерческое предложение подготовлено, ожидается решение.", "created_by": "manager",
        },
        {
            "id": "10000000-0000-4000-8000-000000000004", "number": "MC-26079", "status": "approved",
            "title": "NDVI-мониторинг опытных полей", "result_type": "ndvi",
            "desired_date": str(today + timedelta(days=4)), "desired_time": "08:00", "location": "Красногорский район",
            "area_km2": 1.6, "budget_rub": 72000, "priority": "high", "geometry": base_geometry,
            "customer": {"name": "АО «АгроТех»", "inn": "5024198840", "kpp": "502401001", "ogrn": "1195081028401", "legal_address": "Московская область, г. Красногорск", "management_name": "Котов Сергей Павлович", "status": "ACTIVE"},
            "contact": {"name": "Олег Миронов", "phone": "+7 495 777-18-40", "email": "agro@example.ru"},
            "requirements": {"deliverables": ["NDVI GeoTIFF", "SHP зон"], "repeatability": "одинаковое освещение"},
            "notes": "Утверждено заказчиком, требуется назначить борт.", "created_by": "manager",
        },
        {
            "id": "10000000-0000-4000-8000-000000000005", "number": "MC-26062", "status": "delivered",
            "title": "Лазерное сканирование карьера", "result_type": "point_cloud",
            "desired_date": str(today - timedelta(days=18)), "desired_time": "09:30", "location": "Домодедовский район",
            "area_km2": 1.4, "budget_rub": 164000, "priority": "normal", "geometry": base_geometry,
            "customer": {"name": "ООО «Неруд Гео»", "inn": "5009123018", "kpp": "500901001", "ogrn": "1185027009123", "legal_address": "Московская область, г. Домодедово", "management_name": None, "status": "ACTIVE"},
            "contact": {"name": "Павел Романов", "phone": "+7 495 980-30-10", "email": "geo@example.ru"},
            "requirements": {"point_density": "60–80 точек/м²", "deliverables": ["LAS/LAZ", "ЦМР"]},
            "notes": "Результат передан, акт подписан.", "created_by": "dispatcher",
        },
    ]
    return deepcopy(rows)
