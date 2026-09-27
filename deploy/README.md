# Развёртывание на демонстрационном стенде

Адрес: **https://mission.citymetrics.moscow**.

Схема трафика:

```text
Интернет → Caddy → сеть web → mission-control:80 → внутренняя сеть → mission-api:8000 → db:5432
                                                       └→ mission_egress → внешние API
```

Наружу не публикуются ни API, ни PostgreSQL, ни порт приложения. Единственная точка входа — существующий Caddy.

## 1. Подготовка

Скопируйте проект на сервер и выполните в его каталоге:

```bash
cp .env.example .env
docker network inspect web >/dev/null 2>&1 || docker network create web
```

Заполните `.env`. Для секретов удобно использовать:

```bash
openssl rand -hex 32
```

Сгенерируйте отдельное значение для `POSTGRES_PASSWORD`, `JWT_SECRET` и каждого пользовательского пароля. Файл `.env` не следует добавлять в Git или передавать вместе с архивом проекта.

Для автозаполнения карточки заказчика укажите `DADATA_API_KEY`. Без него раздел заказов продолжит работать с ручным вводом. Open-Meteo и погодный радар ключей не требуют; API-контейнеру нужен исходящий HTTPS-доступ через сеть `mission_egress`.

## 2. Caddy

Добавьте содержимое `deploy/Caddyfile.mission-control` в основной Caddyfile. Контейнер Caddy должен находиться во внешней сети `web`. Если это ещё не настроено, добавьте к его Compose-конфигурации:

```yaml
services:
  caddy:
    networks:
      - web

networks:
  web:
    external: true
```

Проверьте и перечитайте конфигурацию вашим обычным способом. Например, если сервис называется `caddy`:

```bash
docker compose exec caddy caddy validate --config /etc/caddy/Caddyfile
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
```

## 3. Запуск

```bash
docker compose pull
docker compose build --pull
docker compose up -d
docker compose ps
```

Проверки после запуска:

```bash
curl -fsS https://mission.citymetrics.moscow/healthz
curl -fsS https://mission.citymetrics.moscow/api/health
```

Первый запрос должен вернуть `ok`, второй — JSON со статусом `ok` и `database: true`.

Проверка необязательного слоя воздушного движения выполняется после входа в интерфейс: в «Центре полётов» нажмите «Показать» в карточке «Воздушная обстановка». Оставьте `AIRCRAFT_CACHE_TTL_SECONDS` пустым: тогда сервис выберет 240 секунд для анонимного OpenSky, 25 секунд при наличии реквизитов OpenSky или 3 секунды для явно выбранного локального `readsb`. Между ответами источника маркеры плавно перемещаются по скорости и курсу, а наземные борта можно показать отдельной кнопкой.

Там же проверьте карточку Open-Meteo и кнопку «Радар осадков». В разделе «Заказы» кнопка «Новая заявка» должна показывать поиск DaData, если ключ настроен, или понятный ручной режим, если ключ отсутствует. Согласованный заказ передаётся в планировщик, а после выбора плана появляется в серверном календаре.

## Обновление без удаления данных

Перед обновлением передайте на сервер проверенную версию проекта: `git pull --ff-only` подходит для чистого Git-репозитория после публикации коммита, а при ручном копировании сверяйте состав файлов отдельно. Не сочетайте `git pull` с непроверенными локальными изменениями. Файл `.env` сохраняйте — не заменяйте его примером из репозитория. Команды ниже выполняются из фактического каталога проекта на сервере (в нашем развёртывании — `/data/projects/mission-control`). Набор `backend/app/data/airspace/geoscan_height_obstacles_primorsky.geojson` не загружается в справочник автоматически.

```bash
cd /data/projects/mission-control
mkdir -p ../mission-control-backups
docker compose exec -T db pg_dump -U mission -d mission_control -Fc > "../mission-control-backups/mission-control-$(date +%Y%m%d-%H%M%S).dump"
docker compose config --quiet
docker compose build api mission-control
docker compose up -d api mission-control
docker compose ps
docker compose logs --tail=100 api mission-control
```

Миграции таблиц выполняются API при старте. Том `mission_db` сохраняется между пересборками контейнеров.
SQL-инициализация PostGIS копируется в образ БД при сборке и не использует bind-mount,
поэтому запуск не зависит от ACL и прав каталогов на NAS/домашнем сервере.

Проверьте, что файл резервной копии создан и не пустой; храните его вне Git-репозитория. Не выполняйте `docker compose down -v`: ключ `-v` удалит тома PostgreSQL и кеша населённых пунктов. Ранее
загруженный набор Геоскана остаётся в БД после обновления кода; для его удаления
есть отдельный проверяемый скрипт `backend/tools/remove_legacy_geoscan_obstacles.sql`.

После обновления проверьте релиз, здоровье сервисов и загрузку справочника:

```bash
curl -fsS https://mission.citymetrics.moscow/api/health
docker compose exec -T db psql -U mission -d mission_control -c \
  "SELECT source_name, count(*) FROM airspace_zones WHERE source_name = 'Высотные препятствия Геоскан — Приморский край' GROUP BY source_name;"
docker compose logs --tail=100 api mission-control
```

Если запрос показывает `3201` объект, после резервной копии можно выполнить **только при наличии локального скрипта на сервере**:

```bash
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U mission -d mission_control < backend/tools/remove_legacy_geoscan_obstacles.sql
```

Скрипт удаляет только старый неизменяемый набор с точным именем источника и
отказывается работать при неожиданном числе совпадений (ожидается 0 или 3201). Демонстрационные и пользовательские объекты не затрагиваются. Скрипт не обязателен для сборки и не включён в опубликованный Git-репозиторий; если вы не передавали его на сервер отдельно, не пытайтесь заменить его общим `DELETE`. После выполнения повторите запрос на число записей: ожидается 0. Перезапуск контейнеров для этой очистки не нужен.

## Резервная копия PostgreSQL

```bash
docker compose exec -T db pg_dump -U mission -d mission_control -Fc > mission-control.dump
```

Восстановление в существующую пустую базу:

```bash
docker compose exec -T db pg_restore -U mission -d mission_control --clean --if-exists < mission-control.dump
```

## Локальный порт для диагностики

На сервере приложение обычно доступно только через Caddy. Если временно нужен порт на loopback-интерфейсе:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d
```

После этого оно будет доступно только на `http://127.0.0.1:8080`. Для возврата к обычной схеме снова запустите основной Compose без override.
