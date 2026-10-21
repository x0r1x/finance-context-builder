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

Держите этот процесс в своём терминале. Клиентские скрипты только вызывают HTTP; сервер они не запускают и не останавливают.

```bash
uv run finance-context serve --host 127.0.0.1 --port 8080
```

Проверка:

```bash
curl -s http://127.0.0.1:8080/healthz
curl -s http://127.0.0.1:8080/readyz
```

Отправка книги. `POST` возвращает **202**, и API поднимает процесс для этой книги. Повторный POST, пока процесс жив, второй не стартует, если в `meta.json` есть штамп `publisher` и код на диске всё ещё совпадает с ним. Пустой штамп живой процесс не останавливает. Повторный POST книги, снимок которой собран этим же кодом, отдаёт этот снимок и не пересобирает. Нет поля `publisher` или хэш другой — сборка начинается с первой устаревшей стадии. Без карты `stages` это layout, graph и context, а parse и formula IR переиспользуются, если `ir/compile.json` совпал. После рестарта API книга, оставшаяся в `queued` или `running`, становится `failed` с `error` `process_lost`. Тот же исход у `GET`, если процесса уже нет. Опрашивайте, пока `status` не станет терминальным:

| status | Смысл |
| --- | --- |
| `queued` / `running` | Ещё считается |
| `succeeded` | Документы готовы. |
| `failed` | Ошибка пайплайна с кодом в `error` (`zip_rejected`, `encrypted_workbook`, `unsupported_media_type`, `conversion_failed`), процесс книги остановлен по серверному `JOB_TIMEOUT_SEC` (`error` — `TimeoutError`), или API перезапустился, пока книга была в `queued` или `running` (`error` — `process_lost`). Отправьте книгу снова. |

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
| `too_many_jobs` | 429 |

`report_not_ready` — артефакт или trace запрошены до того, как джоб записал этот файл. Каталогу нужен `context.json`. Паспорту и наблюдениям нужен ещё и `graph.json`. Наблюдения без `row_key` или `q` — это `selector_required`, а не вся книга. `workbook_too_large` — завершённая джоба: оценка по формулам и диапазонам или резидентная память выше бюджета одной джобы. `error` — код, `detail` — короткая причина. Трейсбэка нет.

Окружение: `DATA_DIR`, `SESSION_ID`, `MAX_UPLOAD_BYTES`, `MAX_CONCURRENT_JOBS` (2), `JOB_MEMORY_BUDGET_BYTES` (лимит RSS одной джобы; если не задан — три четверти лимита памяти cgroup, поделённые на `MAX_CONCURRENT_JOBS`, оставшаяся четверть остаётся процессу API), `JOB_TIMEOUT_SEC` (серверный дефолт 3600), `SOFFICE_BIN` (по умолчанию `soffice` / `libreoffice` из `PATH`), `XLS_CONVERT_TIMEOUT_SEC` (300), `LOG_LEVEL`, `LOG_JSON`. См. `.env.example`. Сверх `MAX_CONCURRENT_JOBS` `POST` отвечает 429 `too_many_jobs`. `GET /readyz` сообщает `queue: in_process` и `jobs` — число живых дочерних процессов. Если `DATA_DIR` нельзя создать или в него нельзя записать, ответ 503. Строки flag, helper и check остаются в блоке со своим `kind`. Родитель следит за RSS ребёнка и убивает его при превышении бюджета. Ребёнок ставит `oom_score_adj` в 1000, если ядро позволяет. Каждая строка `stage_done` несёт `duration_ms`, `rss_in_mb`, `rss_out_mb`, `rss_peak_mb` и счётчики стадии. `LOG_LEVEL=DEBUG` добавляет `memory_budget` (оценка против бюджета) и `memory_refused`, если книга отклонена.

## Данные

`data/shared/books/{job_id}/` хранит книгу, raw, formula IR и layout. Они общие для всех сессий. `data/sessions/{session}/jobs/{job_id}/` хранит context и graph этой сессии. `job_id` — это SHA-256 строки `context:` и `content_sha256` книги, поэтому имя каталога не совпадает с хэшем файла. Сессия по умолчанию `local`.

Одновременно считается не больше `MAX_CONCURRENT_JOBS` книг, по умолчанию 2. `finance-context serve` запускает один worker. Несколько реплик API на одном каталоге не поддерживаются.

## Docker

```bash
cp .env.example .env   # необязательно
docker compose up --build
```

Оставьте Compose запущенным. API опубликован только на `127.0.0.1:8080`. Общий кэш книг и публикации сессии попадают в `./data` на хосте. Compose монтирует **только `./data`**, не `src/`.

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
