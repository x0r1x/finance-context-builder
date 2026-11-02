# finance-context-builder

**Русский** · [English](README.md)

Парсер документов Excel. Read-only сервис читает книгу cash-flow `.xlsx`, `.xlsm` или старый `.xls` и преобразует её в JSON и Markdown: `context.json`, `context.md`, `graph.json` и `graph.md`. Формулы сохраняются. Числа берутся из кэша Excel и не пересчитываются.

`context` хранит строки блоков, периоды и значения. `graph` хранит связи формул. Trace одной ячейки читает этот граф.

Гайды: [обзор](docs/ru/overview.md), [layout](docs/ru/layout.md), [граф](docs/ru/graph.md), [срезы](docs/ru/slices.md), [архитектура](docs/ru/architecture.md).

Адаптировано из [cashflow-audit](https://github.com/x0r1x/cashflow-audit) (Apache-2.0). См. `NOTICE`.

## Вход

- Принимаются `.xlsx` и `.xlsm`.
- Старый `.xls` (BIFF8, OLE2) принимается, если установлен LibreOffice: джоба один раз конвертирует его через `soffice --headless --convert-to xlsx` (чистый профиль, `XLS_CONVERT_TIMEOUT_SEC`), затем переносит кэш из `.xls` через `xlrd`, чтобы пересчёт LibreOffice не подменил кэш Excel. Docker-образ содержит headless LibreOffice, поэтому в контейнере `soffice` есть в `PATH`. На машине без `soffice` в `PATH` или в `SOFFICE_BIN` загрузка — `unsupported_media_type`.
- `.xlsb` и зашифрованная книга отклоняются. Зашифрованный `.xlsx` — тоже OLE2-файл; это `encrypted_workbook`, а не старый `.xls`.
- Кэш формул уже должен лежать в файле.

## Окружение

Нужны Python 3.12+ и [uv](https://docs.astral.sh/uv/). На macOS установка `uv`, Python и виртуальное окружение:

```bash
brew install uv
uv python install 3.12
uv sync
cp .env.example .env
```

`uv sync` создаёт `.venv` и ставит версии из `uv.lock`. Команды ниже идут через `uv run`, активировать окружение не обязательно. Вручную: `source .venv/bin/activate`. `.env` в git не попадает.

## Локальный запуск

### CLI

```bash
uv run finance-context build path/to/model.xlsx -o ./out
```

Пишет `context.json`, `context.md`, `graph.json` и `graph.md`. Если эти документы уже лежат в каталоге и в `meta.json` тот же хэш `publisher`, команда печатает `reused` и пайплайн не запускает. Нет поля `publisher` или нет карты `stages` — удаляются layout и опубликованные документы, затем они собираются заново; parse и formula IR сохраняются. `meta.stages` называет первую изменившуюся стадию (`compile`, `layout`, `graph`, `publish`), и удаляется только она и всё после неё. Смена `publish` переписывает context и оставляет graph. Смена `compile` удаляет ещё `raw/` и formula IR.

### HTTP API

`POST` только ставит книгу в очередь. Redis должен быть доступен. `finance-context serve` запускает воркер RQ дочерним процессом (`EMBEDDED_WORKER`, по умолчанию true). `REDIS_URL` по умолчанию — `redis://redis:6379/0`. Локально Redis берётся из `deploy/docker-compose.override.yml` (`redis:8.6.7-alpine` на `127.0.0.1:6380`). Процесс на хосте использует этот опубликованный порт:

```bash
REDIS_URL=redis://127.0.0.1:6380/0 uv run finance-context serve --host 127.0.0.1 --port 8080
```

`EMBEDDED_WORKER=false` — только API. Отдельный пул по-прежнему `uv run finance-context worker`. С локальным Redis из Compose контейнер `finance-context` использует `redis://redis:6379/0`. В проде override не подключается, и адрес внешний (см. Docker). Клиентские скрипты только вызывают HTTP; сервер они не запускают и не останавливают.

Проверка:

```bash
curl -s http://127.0.0.1:8080/healthz
curl -s http://127.0.0.1:8080/readyz
```

Отправка книги. `POST` возвращает **202** и ставит книгу в общую очередь RQ. Лошадь воркера поднимает процесс пайплайна. Повторный POST, пока джоба в очереди или уже стартовала, вторую не ставит, если в `meta.json` есть штамп `publisher` и код на диске всё ещё совпадает с ним. Пустой штамп живую джобу не отменяет. Повторный POST книги, снимок которой собран этим же кодом, отдаёт этот снимок и не пересобирает. Нет поля `publisher` или хэш другой — сборка начинается с первой устаревшей стадии. Без карты `stages` это layout, graph и context, а parse и formula IR переиспользуются, если `ir/compile.json` совпал. При старте API список джоб не обходит. `GET` пишет `failed` с `error` `queue_lost`, когда `meta.json` всё ещё `queued` или `running`, блокировка прогона свободна, а джобы RQ нет или она failed, canceled либо finished. Имя файла, хэш содержимого и warnings сохраняются; `publisher` и `stages` снимаются, и повторная отправка стартует прогон с поколением выше сохранённого. Опрашивайте, пока `status` не станет терминальным:

| status | Смысл |
| --- | --- |
| `queued` / `running` | Ещё считается |
| `succeeded` | Документы готовы. |
| `failed` | Ошибка пайплайна с кодом в `error` (`zip_rejected`, `encrypted_workbook`, `unsupported_media_type`, `conversion_failed`, `workbook_too_large`), процесс книги остановлен по серверному `JOB_TIMEOUT_SEC` (`error` — `TimeoutError`), воркер умер (`error` — `process_lost`) или его убила нехватка памяти (`error` — `oom_killed`), или GET увидел, что джобы RQ нет (`error` — `queue_lost`). Отправьте книгу снова. |

Старые снимки могут ещё говорить `needs_input` или `degraded`. Такая джоба уже закончена, те же четыре документа отдаются.

```bash
JOB=$(curl -sS -F "file=@path/to/model.xlsx" http://127.0.0.1:8080/v1/context-jobs)
echo "$JOB"
ID=$(python -c "import json,sys; print(json.loads(sys.argv[1])['job_id'])" "$JOB")

curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID"
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/context.json" -o context.json
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/context.md" -o context.md
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/graph.json" -o graph.json
curl -sS "http://127.0.0.1:8080/v1/context-jobs/$ID/graph.md" -o graph.md
```

Живой прогресс пишется в Redis Stream, а читается через `GET /v1/context-jobs/$ID/events` (`text/event-stream`). Первое SSE-событие — текущий `meta.json`. Дальше идут `queued`, `running`, старт и конец стадий (`compile`, `layout`, `graph`, `publish`), `succeeded`, `failed`, `queue_lost` и `retry`, если общий кэш занят. Источник истины остаётся `meta.json`. Ошибка Redis при публикации джобу не роняет. Поток закрывается после терминального события. Продолжение — заголовок `Last-Event-ID`.

```bash
curl -N "http://127.0.0.1:8080/v1/context-jobs/$ID/events"
```

Эндпоинты:

- `POST /v1/context-jobs` — загрузка книги
- `GET /v1/context-jobs/{id}` — статус (`context_json_url`, `context_md_url`, `graph_json_url`, `graph_md_url`)
- `GET /v1/context-jobs/{id}/events` — SSE. Сначала снимок `meta.json`, затем поток Redis. `Last-Event-ID` продолжает с записи. Строка-комментарий — heartbeat.
- `GET /v1/context-jobs/{id}/context.json`
- `GET /v1/context-jobs/{id}/context.md`
- `GET /v1/context-jobs/{id}/graph.json`
- `GET /v1/context-jobs/{id}/graph.md`
- `GET /v1/context-jobs/{id}/graph/trace?from=&direction=precedents&depth=8`
- `GET /v1/context-jobs/{id}/graph/trace.md?from=&direction=precedents&depth=8`
- `GET /v1/context-jobs?status=&q=` — книги сессии только из `meta.json` (`job_id`, `status`, `stage`, `source_filename`, `content_sha256`, `schema_version` context). `q` — фрагмент имени файла без регистра
- `GET /v1/context-jobs/{id}/summary` — паспорт готовой джобы: счётчики книги, счётчики графа и предупреждения (`unresolved`, `external`, `dangling`, циклы). Без `links` и без кэша ячеек. Пока джоба считается, статус по-прежнему смотрят через `GET /v1/context-jobs/{id}`
- `GET /v1/context-jobs/{id}/catalog` — строки и оси без чисел. У периода также есть `flags` и `group_key`, а `start_date` / `end_date` — когда они есть у оси. Фильтры: `q` (подстрока собственной подписи без регистра; `label_path` в иглу не входит), `sheet`, `limit`, `offset`
- `GET /v1/context-jobs/{id}/observations` — единственный роут с кэшем ячеек. Нужен повторяемый `row_key` или `q` (та же подстрока собственной подписи). Без селектора ответ 400 `selector_required`. `limit` по умолчанию 24 и не больше 48 (`truncated`). Прецеденты не режутся лимитом строк. Полнота видна по `precedents_total`. `precedent_depth` по умолчанию 0 и не больше 3
- `GET /healthz`, `GET /readyz`

Живая схема роутов строится из этих обработчиков: [Swagger UI](http://127.0.0.1:8080/docs), [ReDoc](http://127.0.0.1:8080/redoc) и `GET /openapi.json`. `context.json`, `graph.json` и trace описаны теми же моделями, которыми эти файлы пишутся. Каталог, паспорт и наблюдения — модели ответа. На диске таких файлов нет.

Коды `error`:

| code | HTTP |
| --- | --- |
| `unsupported_media_type` | 400 |
| `empty_file` | 400 |
| `file_too_large` | 413 |
| `workbook_too_large` | только `error` джобы |
| `encrypted_workbook` | 422 |
| `zip_rejected` | 422 |
| `conversion_failed` | только `error` джобы |
| `selector_required` | 400 |
| `not_found` | 404 |
| `report_not_ready` | 409 |
| `job_still_running` | 409 |
| `too_many_jobs` | 429 |
| `queue_unavailable` | 503 |
| `shared_cache_busy` | только `error` джобы |

`report_not_ready` — артефакт или trace запрошены до того, как джоб записал этот файл. Каталогу нужен `context.json`. Паспорту и наблюдениям нужен ещё и `graph.json`. Наблюдения без `row_key` или `q` — это `selector_required`, а не вся книга. `workbook_too_large` — завершённая джоба: оценка по формулам и диапазонам или резидентная память выше бюджета одной джобы. `error` — код, `detail` — короткая причина. Трейсбэка нет.

Окружение: `DATA_DIR`, `DATA_DIR_ALLOW_NETWORK_FS` (false; старт отказывается от nfs, cifs/smb, sshfs, afp и webdav и продолжает работу с записью в лог, если тип ФС определить нельзя), `SESSION_ID`, `SESSION_ID_PATTERN` (`^[A-Za-z0-9][A-Za-z0-9_-]*$`), `SESSION_ID_MAX_LENGTH` (64), `MAX_UPLOAD_BYTES`, `MAX_CONCURRENT_JOBS` (4), `JOB_MEMORY_BUDGET_BYTES` (лимит RSS одной джобы; если не задан — меньшее из остатка cgroup после `JOB_MEMORY_API_RESERVE_FRACTION` и `JOB_MEMORY_DEFAULT_BUDGET_BYTES`, по умолчанию 1536 МиБ; явное значение не обрезается; число слотов этот потолок не делит), `JOB_MEMORY_API_RESERVE_FRACTION` (0.25), `JOB_MEMORY_DEFAULT_BUDGET_BYTES` (1610612736), `JOB_MEMORY_WATCH_INTERVAL_SEC` (0.2), `RANGE_EXPAND_CAP` (2000; только для диапазонов, которые раскрываются в формуле, то есть до `RANGE_NODE_MIN_CELLS`), `RANGE_NODE_MIN_CELLS` (64; прямоугольник больше этого, целый столбец или строка — один узел-диапазон, см. docs/ru/graph.md; смена значения пересобирает закэшированный IR), `TRACE_RANGE_MEMBERS` (200; сколько ячеек одного узла-диапазона проходит trace, 0 — все), `JOB_OOM_SCORE_ADJ` (1000), `JOB_TIMEOUT_SEC` (серверный дефолт 3600), `SOFFICE_BIN` (по умолчанию `soffice` / `libreoffice` из `PATH`), `XLS_CONVERT_TIMEOUT_SEC` (300), `LOG_LEVEL`, `LOG_JSON`. См. `.env.example`. Сверх `JOB_QUEUE_MAX_PER_SESSION` `POST` отвечает 429 `too_many_jobs` с `Retry-After`. Недоступный Redis — это 503 `queue_unavailable` с `Retry-After`. Повторный POST не ждёт блокировку пайплайна. Если прежний id всё ещё в полёте после grace на SIGTERM и SIGKILL, POST отвечает 409 `job_still_running` с `Retry-After` и не ставит новую джобу поверх него. `process_lost` пишется только когда `JOB_RETRY_ON_LOST` уже исчерпан. Джоба `started` становится `queue_lost`, если run lock свободен и лошадь не держит `.horse.lock` для этого поколения. Блокировка лежит на общем томе данных, общий PID namespace API и воркеру не нужен. `GET /healthz` не делает ввода-вывода. `GET /readyz` проверяет, что `DATA_DIR` — доступный для записи каталог, и что Redis отвечает на ping; обе проверки идут в пул потоков API, и результат используется `READYZ_CACHE_TTL_SEC` секунд (по умолчанию 10). Обычная проба открывает безымянный inode (`O_TMPFILE`) и не оставляет имени в каталоге. Если файловая система отвергает этот флаг, проба создаёт короткий файл `.ready-`, пишет один байт и удаляет его. `queue` — имя очереди. `jobs` — число джоб этой сессии в полёте, или 0, если Redis не ответил. Неудачный ping, недоступный для записи каталог или упавший встроенный воркер под наблюдением — это 503. Проверка воркера не держится `READYZ_CACHE_TTL_SEC`. `API_THREAD_LIMIT` (по умолчанию 40, как у AnyIO) ограничивает потоки для блокирующей работы API. Строки flag, helper и check остаются в блоке со своим `kind`. Лошадь воркера следит за RSS ребёнка и убивает его при превышении бюджета. Ребёнок ставит `oom_score_adj` в `JOB_OOM_SCORE_ADJ` (по умолчанию 1000), если ядро позволяет. Каждая строка `stage_done` несёт `duration_ms`, `rss_in_mb`, `rss_out_mb`, `rss_peak_mb` и счётчики стадии. `LOG_LEVEL=DEBUG` добавляет `memory_budget` (оценка против бюджета, включая переключатели ниже) и `memory_refused`, если книга отклонена.

Переключатели памяти, все включены по умолчанию: `PARSE_STREAMING` (iterparse, значения raw parquet совпадают с DOM-разбором), `JSON_STREAM` (`json.dump`, документы побайтно те же), `PARQUET_ROW_GROUP_ROWS` (65536; `0` — одна группа строк и побайтно тот же IR/graph parquet; меньшая группа меняет только байты файла), `COMPILE_STRING_POOL` (локальный пул compile, не `sys.intern`), `COMPILE_AST_ZLIB` (сжатие до relations; опубликованный JSON тот же), `GRAPH_STREAM` (пачки колонок, без второго списка рёбер), `LAYOUT_COLUMNAR_FACTS` (indent/outline из Arrow; `layout_cells_read` остаётся). `JOB_MALLOC_ARENA_MAX` (2; `0` или пустое значение оставляет унаследованное) ставится порождённому ребёнку джобы. `JOB_MALLOC_TRIM` (true) вызывается после каждой стадии и ничего не делает без `malloc_trim`. Любой из них можно выключить (`false` или размер группы `0`). См. `.env.example`.

Настройки очереди, их читают API и `finance-context worker`: `REDIS_URL` (по умолчанию `redis://redis:6379/0`, без учётных данных), `REDIS_USERNAME` (пусто; пользователь по умолчанию), `REDIS_PASSWORD` (пусто; без AUTH), `REDIS_CONNECT_TIMEOUT_SEC` (2; только клиент API и `worker --check`), `QUEUE_NAME` (`finance-context`), `JOB_QUEUE_MAX_PER_SESSION` (3), `QUEUE_JOB_TIMEOUT_MARGIN_SEC` (120), `QUEUE_JOB_TTL_SEC` (0 — джоба может ждать в очереди), `QUEUE_RESULT_TTL_SEC` (3600), `QUEUE_FAILURE_TTL_SEC` (604800), `JOB_RETRY_ON_LOST` (1), `SHARED_CACHE_BUSY_RETRY_SEC` (30), `SHARED_CACHE_BUSY_MAX_RETRIES` (120), `JOB_ADMISSION_CGROUP_CHECK` (true), `JOB_ADMISSION_RETRY_SEC` (30), `JOB_PARENT_WATCH_INTERVAL_SEC` (1), `JOB_RETRY_AFTER_SEC` (30), `JOB_TERMINATE_GRACE_SEC` (5), `JOB_KILL_GRACE_SEC` (5), `EMBEDDED_WORKER` (true), `EMBEDDED_WORKER_RESTART_BACKOFF_SEC` (1). `finance-context serve` запускает пул из `MAX_CONCURRENT_JOBS` воркеров со scheduler дочерним процессом. `finance-context worker` — тот же пул, когда `EMBEDDED_WORKER` выключен. Упавший ребёнок запускается снова после паузы, и `/readyz` не готов, пока его нет, даже внутри `READYZ_CACHE_TTL_SEC`. Потолок RSS одной джобы — меньшее из лимита cgroup минус `JOB_MEMORY_API_RESERVE_FRACTION` и `JOB_MEMORY_DEFAULT_BUDGET_BYTES` (1536 МиБ). Число слотов этот потолок не делит. Этот резерв покрывает процесс API и встроенный воркер в одном cgroup. Явный `JOB_MEMORY_BUDGET_BYTES` и есть потолок, и он не обрезается. Пустые `REDIS_USERNAME` и `REDIS_PASSWORD` означают отсутствие AUTH: клиент получает URL без учётных данных и не передаёт `username` и `password`. Непустое значение перекрывает одноимённое поле userinfo в URL. Имя пользователя без пароля, в переменной или в URL, отклоняется. Пароль в лог не пишется, userinfo URL не попадает в `queue_config`, и в `/readyz` его нет. Старт воркера пишет `queue_config` через JSON-форматтер. Старт API пишет `api_config` с `thread_limit` (`API_THREAD_LIMIT`, по умолчанию 40) и `readyz_ttl_sec` (`READYZ_CACHE_TTL_SEC`, по умолчанию 10). См. `.env.example`.

## Данные

`data/shared/books/{job_id}/` хранит `source/source.xlsx`, `raw/`, formula IR в `ir/` и `layout/layout.json`. Они общие для всех сессий. POST их не удаляет. Ребёнок джобы берёт `shared/books/{job_id}/.lock` через `LOCK_NB`, снимает там устаревший хвост и публикует каждый файл через fsync и `os.replace`. `stages.json` — файл отпечатков стадий, это не опубликованный артефакт. `.lock` не удаляется. Если лок уже занят, лошадь возвращает RQ `Retry(interval=SHARED_CACHE_BUSY_RETRY_SEC)` (по умолчанию 30) и освобождает слот воркера, не больше `SHARED_CACHE_BUSY_MAX_RETRIES` дополнительных раз (по умолчанию 120). Пока джоба ждёт, `meta.json` остаётся `queued`, и GET не помечает её `queue_lost`. Последняя попытка падает с `shared_cache_busy`. CLI `build` по-прежнему ждёт в процессе и пишет ту же ошибку, если следующее ожидание дошло бы до `JOB_TIMEOUT_SEC`. См. `.env.example`. `data/sessions/{session}/jobs/{job_id}/` хранит `context/`, `graph/` и `meta/meta.json` этой сессии. `job_id` — это SHA-256 строки `context:` и `content_sha256` книги, поэтому имя каталога не совпадает с хэшем файла. Сессия по умолчанию `local`. Id должен совпадать с `SESSION_ID_PATTERN` и умещаться в `SESSION_ID_MAX_LENGTH`; старт отклоняет разделитель и `..`. GET ищет джобу только внутри этой сессии. Сетевая ФС под `DATA_DIR` не даёт стартовать, пока `DATA_DIR_ALLOW_NETWORK_FS` не равен `true`.

Память контейнера задаётся `mem_limit`. Одна книга обрывается на 1536 МиБ RSS: это запас над известной большой книгой, а не обычный расход. Число слотов этот потолок не делит. Одновременно по умолчанию 4 книги. Оценки размера книги нет. Имена `JOB_MEMORY_*` остаются в `.env.example`. Одновременно считается не больше `MAX_CONCURRENT_JOBS` книг в дочернем воркере, который запускает `finance-context serve`. Один контейнер — это этот процесс плюс Redis. Они делят один `DATA_DIR` и одну очередь. `EMBEDDED_WORKER=false` оставляет API без этого ребёнка.

## Docker

```bash
cp .env.example .env   # задайте REDIS_URL; см. ниже
mkdir -p data && sudo chown 1000:1000 data
# Прод. Redis снаружи. Файл override не используется.
docker compose -f deploy/docker-compose.yml up --build
```

Оставьте Compose запущенным. Команды запускайте из корня репозитория, если не сказано иначе. `deploy/docker-compose.yml` поднимает один контейнер, `finance-context`. В проде `deploy/docker-compose.override.yml` не подключается. Redis тогда снаружи, например в compose пользователя finance-stack. Задайте `REDIS_URL` в `.env`, например `redis://host.docker.internal:6379/0` (`host.docker.internal` прописан в compose). На Linux это адрес моста хоста. Redis должен слушать этот адрес моста или другой непубличный адрес, доступный контейнерам. Слушатель только на `127.0.0.1` из контейнеров недоступен.

Локальная проверка добавляет Redis из override (`redis:8.6.7-alpine` на `127.0.0.1:6380`, append-only, именованный том). В проекте один контейнер `finance-context` и Redis:

```bash
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.override.yml up --build
```

`cd deploy && docker compose up` подмешивает этот override сам. Override задаёт контейнеру `REDIS_URL=redis://redis:6379/0`, поэтому адрес `localhost` в `.env` хоста остаётся для процессов на хосте. Процесс на хосте, например `scripts/run.sh` к API, использует `redis://127.0.0.1:6380/0`. Это опубликованный порт на хосте. Из контейнера он недоступен.

Пустые `MINIO_ENDPOINT`, `MINIO_BUCKET`, `MINIO_ACCESS_KEY` и `MINIO_SECRET_KEY` оставляют DuckDB на `DATA_DIR`. Endpoint — это `http://` или `https://` плюс хост и порт. Необязательные `MINIO_SESSION_TOKEN` и `MINIO_KMS_KEY_ID` уходят в запрос только если заданы. Если заданы все четыре, DuckDB по-прежнему пишет `DATA_DIR`, а затем копирует те же байты в `books/{job_id}/` бакета (`raw/cells.parquet`, `context.json`, `context.md` и остальные файлы книги). Локальный override поднимает MinIO и задаёт эти четыре переменные. Образ сервера — `alpine/minio:RELEASE.2025-10-15T17-29-55Z`, потому что `minio/minio` на Docker Hub больше не отдаётся. Продовый compose MinIO не поднимает и их не задаёт. Host pytest остаётся на `DATA_DIR`, пока своё окружение не задаст четвёрку.

API опубликован только на `127.0.0.1:8080`. Общий кэш книг и публикации сессии попадают в `./data` на хосте. Создайте этот каталог и отдайте его uid 1000 до `up` (команды выше). Контейнер работает от этого пользователя, и на свежем Linux-чекауте Docker иначе создаст каталог от root. Compose монтирует **только `./data`**, не `src/`. У сервиса `mem_limit: 4g`. `DATA_DIR` должен быть локальной файловой системой на одном узле. Старт отказывается от сетевой ФС, пока `DATA_DIR_ALLOW_NETWORK_FS` не равен `true`.

Образ работает от пользователя `app` (uid 1000) с `HOME=/app` и содержит `libreoffice-calc-nogui`. Загрузка `.xls` конвертируется внутри контейнера. Конвертация пишет временный профиль в каталог книги, а не в домашний каталог образа. `XLS_CONVERT_TIMEOUT_SEC` (по умолчанию 300) меньше `JOB_TIMEOUT_SEC` (по умолчанию 3600), поэтому зависшая конвертация роняет джобу раньше, чем сервер остановит процесс. `.env` по-прежнему может задать `SOFFICE_BIN` и `XLS_CONVERT_TIMEOUT_SEC`; Compose их не перезаписывает. `scripts/docker-xls-smoke.sh` проверяет этот путь на настоящем `.xls`. Скрипту нужен Docker, и он не входит в `uv run pytest`.

Контейнер API без Compose. Команда образа — `finance-context serve`:

```bash
docker build -f deploy/Dockerfile -t finance-context-builder .
docker run --rm -v "$PWD/data:/app/data" --env-file .env -p 127.0.0.1:8080:8080 \
  --add-host=host.docker.internal:host-gateway finance-context-builder
```

Разовый CLI build, с другой командой:

```bash
docker run --rm -v "$PWD/data:/app/data" --env-file .env \
  --add-host=host.docker.internal:host-gateway \
  finance-context-builder \
  finance-context build /app/data/model.xlsx -o /app/data/out
```

## Тесты

```bash
uv run pytest
uv run ruff check src tests
```

Когда HTTP-сервер **уже запущен** в другом терминале, `scripts/run.sh` вызывает `check-service.sh`, затем `run-context-job.sh` и пишет прогон в `out/<timestamp>/`:

```text
json/context.json  json/graph.json  json/trace.json
md/context.md      md/graph.md      md/trace.md
healthz.json  readyz.json  post-job.json  job-status.json  summary.txt
```

Клиент проверяет четыре URL документов, что в `context.json` нет второго каталога строк и нет AST, что `graph.json` — схема `1.7`, а `context.json` — схема `1.14` с `links` (диапазон остаётся одной ссылкой), и что Markdown-близнецы повторяют те же блоки и links, затем дымит `GET .../graph/trace` и `.../graph/trace.md`. Выход скрипта с `OK` значит, что клиент закончил; сервер должен по-прежнему слушать 8080. Таймаут клиента процесс книги не останавливает.

```bash
bash scripts/run.sh path/to/model.xlsx
```

Если путь не задан, скрипт ищет `resources/cashflow.xlsx`. Можно переопределить `BASE_URL` (по умолчанию `http://127.0.0.1:8080`), `OUT_DIR` (по умолчанию `./out`) и `JOB_TIMEOUT_SEC` (опрос клиента, по умолчанию `300`, если переменная не задана). Серверный `JOB_TIMEOUT_SEC` в `.env` — другие часы: он останавливает процесс. Экспортируйте то же значение перед `run.sh`, если клиент должен ждать столько же.
