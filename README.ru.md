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

`POST` только ставит книгу в очередь. Redis должен быть доступен, а `finance-context worker` должен работать в другом терминале. `REDIS_URL` по умолчанию — `redis://redis:6379/0`, имя сервиса Compose. На хосте укажите обоим процессам локальный Redis и один и тот же `DATA_DIR`. Клиентские скрипты только вызывают HTTP; сервер они не запускают и не останавливают.

```bash
REDIS_URL=redis://127.0.0.1:6379/0 uv run finance-context serve --host 127.0.0.1 --port 8080
REDIS_URL=redis://127.0.0.1:6379/0 uv run finance-context worker
```

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

Эндпоинты:

- `POST /v1/context-jobs` — загрузка книги
- `GET /v1/context-jobs/{id}` — статус (`context_json_url`, `context_md_url`, `graph_json_url`, `graph_md_url`)
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

Окружение: `DATA_DIR`, `DATA_DIR_ALLOW_NETWORK_FS` (false; старт отказывается от nfs, cifs/smb, sshfs, afp и webdav и продолжает работу с записью в лог, если тип ФС определить нельзя), `SESSION_ID`, `SESSION_ID_PATTERN` (`^[A-Za-z0-9][A-Za-z0-9_-]*$`), `SESSION_ID_MAX_LENGTH` (64), `MAX_UPLOAD_BYTES`, `MAX_CONCURRENT_JOBS` (2), `JOB_MEMORY_BUDGET_BYTES` (лимит RSS одной джобы; если не задан — лимит cgroup за вычетом `JOB_MEMORY_API_RESERVE_FRACTION`, по умолчанию 0.25, делится на `MAX_CONCURRENT_JOBS`; заданный лимит ниже `JOB_MEMORY_FLOOR_BYTES`, по умолчанию 128 МиБ, отклоняет джобу и не поднимается; выведенная доля ниже этого порога сохраняется; без лимита cgroup берётся `JOB_MEMORY_DEFAULT_BUDGET_BYTES`, по умолчанию 1536 МиБ), `JOB_ESTIMATE_BASE_BYTES` (174063616), `JOB_ESTIMATE_BYTES_PER_FORMULA` (128), `JOB_ESTIMATE_BYTES_PER_EXPANDED_CELL` (220), `JOB_ESTIMATE_BYTES_PER_PRESENT_CELL` (1120), `JOB_ESTIMATE_UNSTREAMED_BASE_BYTES` (138412032), `JOB_ESTIMATE_UNSTREAMED_BYTES_PER_FORMULA` (3840), `JOB_ESTIMATE_UNSTREAMED_BYTES_PER_EXPANDED_CELL` (960), `JOB_ESTIMATE_UNSTREAMED_BYTES_PER_PRESENT_CELL` (960; эти четыре заменяют потоковые коэффициенты, если `PARSE_STREAMING`, `GRAPH_STREAM` или `COMPILE_AST_ZLIB` выключен), `JOB_MEMORY_FLOOR_BYTES` (134217728), `JOB_MEMORY_API_RESERVE_FRACTION` (0.25), `JOB_MEMORY_DEFAULT_BUDGET_BYTES` (1610612736), `JOB_MEMORY_WATCH_INTERVAL_SEC` (0.2), `RANGE_EXPAND_CAP` (2000), `JOB_OOM_SCORE_ADJ` (1000), `JOB_MEMORY_PREFLIGHT_REFUSAL` (true; при false оценка не отклоняет джобу, сторож RSS по-прежнему отклоняет), `JOB_TIMEOUT_SEC` (серверный дефолт 3600), `SOFFICE_BIN` (по умолчанию `soffice` / `libreoffice` из `PATH`), `XLS_CONVERT_TIMEOUT_SEC` (300), `LOG_LEVEL`, `LOG_JSON`. См. `.env.example`. Сверх `JOB_QUEUE_MAX_PER_SESSION` `POST` отвечает 429 `too_many_jobs` с `Retry-After`. Недоступный Redis — это 503 `queue_unavailable` с `Retry-After`. Повторный POST не ждёт блокировку пайплайна. Если прежний id всё ещё в полёте после grace на SIGTERM и SIGKILL, POST отвечает 409 `job_still_running` с `Retry-After` и не ставит новую джобу поверх него. `process_lost` пишется только когда `JOB_RETRY_ON_LOST` уже исчерпан. Джоба `started` становится `queue_lost`, если run lock свободен и лошадь не держит `.horse.lock` для этого поколения. Блокировка лежит на общем томе данных, общий PID namespace API и воркеру не нужен. `GET /healthz` не делает ввода-вывода. `GET /readyz` проверяет, что `DATA_DIR` — доступный для записи каталог, и что Redis отвечает на ping; обе проверки идут в пул потоков API, и результат используется `READYZ_CACHE_TTL_SEC` секунд (по умолчанию 10). Обычная проба открывает безымянный inode (`O_TMPFILE`) и не оставляет имени в каталоге. Если файловая система отвергает этот флаг, проба создаёт короткий файл `.ready-`, пишет один байт и удаляет его. `queue` — имя очереди. `jobs` — число джоб этой сессии в полёте, или 0, если Redis не ответил. Неудачный ping или недоступный для записи каталог — это 503. `API_THREAD_LIMIT` (по умолчанию 40, как у AnyIO) ограничивает потоки для блокирующей работы API. Строки flag, helper и check остаются в блоке со своим `kind`. Лошадь воркера следит за RSS ребёнка и убивает его при превышении бюджета. Ребёнок ставит `oom_score_adj` в `JOB_OOM_SCORE_ADJ` (по умолчанию 1000), если ядро позволяет. Каждая строка `stage_done` несёт `duration_ms`, `rss_in_mb`, `rss_out_mb`, `rss_peak_mb` и счётчики стадии. `LOG_LEVEL=DEBUG` добавляет `memory_budget` (оценка против бюджета, включая переключатели ниже) и `memory_refused`, если книга отклонена.

Переключатели памяти, все включены по умолчанию: `PARSE_STREAMING` (iterparse, raw parquet побайтно тот же), `JSON_STREAM` (`json.dump`, документы побайтно те же), `PARQUET_ROW_GROUP_ROWS` (65536; `0` — одна группа строк и побайтно тот же IR/graph parquet; меньшая группа меняет только байты файла), `COMPILE_STRING_POOL` (локальный пул compile, не `sys.intern`), `COMPILE_AST_ZLIB` (сжатие до relations; опубликованный JSON тот же), `GRAPH_STREAM` (пачки колонок, без второго списка рёбер), `LAYOUT_COLUMNAR_FACTS` (indent/outline из Arrow; `layout_cells_read` остаётся). `JOB_MALLOC_ARENA_MAX` (2; `0` или пустое значение оставляет унаследованное) ставится порождённому ребёнку джобы. `JOB_MALLOC_TRIM` (true) вызывается после каждой стадии и ничего не делает без `malloc_trim`. Любой из них можно выключить (`false` или размер группы `0`). См. `.env.example`.

Настройки очереди, их читают API и `finance-context worker`: `REDIS_URL` (по умолчанию `redis://redis:6379/0`, без учётных данных), `REDIS_USERNAME` (пусто; пользователь по умолчанию), `REDIS_PASSWORD` (пусто; без AUTH), `REDIS_CONNECT_TIMEOUT_SEC` (2; только клиент API и `worker --check`), `QUEUE_NAME` (`finance-context`), `JOB_QUEUE_MAX_PER_SESSION` (3), `QUEUE_JOB_TIMEOUT_MARGIN_SEC` (120), `QUEUE_JOB_TTL_SEC` (0 — джоба может ждать в очереди), `QUEUE_RESULT_TTL_SEC` (3600), `QUEUE_FAILURE_TTL_SEC` (604800), `JOB_RETRY_ON_LOST` (1), `SHARED_CACHE_BUSY_RETRY_SEC` (30), `SHARED_CACHE_BUSY_MAX_RETRIES` (120), `JOB_ADMISSION_CGROUP_CHECK` (true), `JOB_ADMISSION_RETRY_SEC` (30), `JOB_PARENT_WATCH_INTERVAL_SEC` (1), `JOB_RETRY_AFTER_SEC` (30), `JOB_TERMINATE_GRACE_SEC` (5), `JOB_KILL_GRACE_SEC` (5). `finance-context worker` запускает пул из `MAX_CONCURRENT_JOBS` воркеров со scheduler. Бюджет RSS одной джобы — лимит cgroup воркера минус `JOB_MEMORY_API_RESERVE_FRACTION`, делённый на слоты. Явный `JOB_MEMORY_BUDGET_BYTES` и есть потолок, он на слоты не делится. Пустые `REDIS_USERNAME` и `REDIS_PASSWORD` означают отсутствие AUTH: клиент получает URL без учётных данных и не передаёт `username` и `password`. Непустое значение перекрывает одноимённое поле userinfo в URL. Имя пользователя без пароля, в переменной или в URL, отклоняется. Пароль в лог не пишется, userinfo URL не попадает в `queue_config`, и в `/readyz` его нет. Старт воркера пишет `queue_config` через JSON-форматтер. Старт API пишет `api_config` с `thread_limit` (`API_THREAD_LIMIT`, по умолчанию 40) и `readyz_ttl_sec` (`READYZ_CACHE_TTL_SEC`, по умолчанию 10). См. `.env.example`.

## Данные

`data/shared/books/{job_id}/` хранит книгу, raw, formula IR и layout. Они общие для всех сессий. POST их не удаляет. Ребёнок джобы берёт `shared/books/{job_id}/.lock` через `LOCK_NB`, снимает там устаревший хвост и публикует каждый файл через fsync и `os.replace`. `stages.json` — файл отпечатков стадий, это не опубликованный артефакт. `.lock` не удаляется. Если лок уже занят, лошадь возвращает RQ `Retry(interval=SHARED_CACHE_BUSY_RETRY_SEC)` (по умолчанию 30) и освобождает слот воркера, не больше `SHARED_CACHE_BUSY_MAX_RETRIES` дополнительных раз (по умолчанию 120). Пока джоба ждёт, `meta.json` остаётся `queued`, и GET не помечает её `queue_lost`. Последняя попытка падает с `shared_cache_busy`. CLI `build` по-прежнему ждёт в процессе и пишет ту же ошибку, если следующее ожидание дошло бы до `JOB_TIMEOUT_SEC`. См. `.env.example`. `data/sessions/{session}/jobs/{job_id}/` хранит context и graph этой сессии. `job_id` — это SHA-256 строки `context:` и `content_sha256` книги, поэтому имя каталога не совпадает с хэшем файла. Сессия по умолчанию `local`. Id должен совпадать с `SESSION_ID_PATTERN` и умещаться в `SESSION_ID_MAX_LENGTH`; старт отклоняет разделитель и `..`. GET ищет джобу только внутри этой сессии. Сетевая ФС под `DATA_DIR` не даёт стартовать, пока `DATA_DIR_ALLOW_NETWORK_FS` не равен `true`.

Одновременно считается не больше `MAX_CONCURRENT_JOBS` книг, по умолчанию 2, на пуле воркера. `finance-context serve` — это API, один процесс на `SESSION_ID`. Эти процессы API и `finance-context worker` делят один `DATA_DIR` и одну очередь.

## Docker

```bash
cp .env.example .env   # необязательно
docker compose up --build
```

Оставьте Compose запущенным. API и воркер подключаются к внешней сети `finance-stack` (один раз `docker network create finance-stack`) и используют её сервис `redis`. `REDIS_URL` по умолчанию `redis://redis:6379/0`. `docker compose --profile dev up --build` поднимает Redis из этого репозитория (AOF каждую секунду, `noeviction`) в той же сети. API опубликован только на `127.0.0.1:8080`. Общий кэш книг и публикации сессии попадают в `./data` на хосте. Compose монтирует **только `./data`**, не `src/`. У API `mem_limit: 512m`. У воркера `mem_limit: 4g`, `stop_grace_period: 3730s`, healthcheck — `finance-context worker --check`. При двух слотах джоб из лимита воркера получается потолок RSS 1536 МиБ на джобу. `DATA_DIR` должен быть локальной файловой системой на одном узле. API и воркер делят flock на этом каталоге, и старт отказывается от сетевой ФС, пока `DATA_DIR_ALLOW_NETWORK_FS` не равен `true`.

Образ работает от пользователя `app` (uid 1000) с `HOME=/app` и содержит `libreoffice-calc-nogui`. Загрузка `.xls` конвертируется внутри контейнера. Конвертация пишет временный профиль в каталог книги, а не в домашний каталог образа. `XLS_CONVERT_TIMEOUT_SEC` (по умолчанию 300) меньше `JOB_TIMEOUT_SEC` (по умолчанию 3600), поэтому зависшая конвертация роняет джобу раньше, чем сервер остановит процесс. `.env` по-прежнему может задать `SOFFICE_BIN` и `XLS_CONVERT_TIMEOUT_SEC`; Compose их не перезаписывает. `scripts/docker-xls-smoke.sh` проверяет этот путь на настоящем `.xls`. Скрипту нужен Docker, и он не входит в `uv run pytest`.

Контейнер API без Compose. Команда образа — `finance-context serve`:

```bash
docker build -t finance-context-builder .
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
