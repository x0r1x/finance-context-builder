# finance-context-builder

**Русский** · [English](README.md)

Read-only сервис: Excel-модели cash-flow (`.xlsx` / `.xlsm`) превращаются в версионированный JSON и Markdown. Формулы сохраняются; значения берутся из кэша Excel и не пересчитываются.

Каждая строка layout хранится один раз внутри своего блока в `context.json` (схема `1.13.0`), включая таблицы допущений без оси периодов (`params`) и скаляры слева от таймлайна с ролями (`value` / `unit` / `total` / `stub`) и `header` колонки (Start, Live Case, Min). У периода могут быть `start_date` и `end_date`. Скаляры и строки params используют `period_position=instant` и `aggregation=none`. В `context.md` есть колонки `Path` и `Cells` и строки `Start` / `End` под `## Axes`. Колонка total или stub — не период (`period_id` у её link в графе равен null). У строки плоский `values`, выровненные `value_statuses` и `normalized_values`, плюс `scale_factor`, `period_position` и `aggregation`. `context.md` повторяет те же блоки, строки, `row_key`, профиль времени и значения; пустая ячейка — `empty`, период вне фазы строки — `n/a`. В заголовке периода — ключ периода и буква колонки. Ячейки, AST формул и граф зависимостей остаются в `raw/` и `ir/`: они не сливаются в один JSON или один Markdown. Принятый `concept_id` — отчётный слот; `semantic_identity`, `reporting_roles` и `cash_semantics` разделяют экономический смысл, роль в layout и начисление против денег. Оси листа публикуются один раз в `axes` (зерно, периоды, `group_key` и фазы construction/operation из flag-строк этой оси). Timeline-блок хранит `axis_ids` и одну серию значений на ось; фаза на блок не копируется. Повторённый год над месяцами — `group_key`, не вторая ось. Год и месяц рядом под одной полосой заголовков остаются одной таблицей. `mapping_stats.concept_coverage` — доля строк, которые можно аннотировать и у которых принят концепт. `mapping_stats.mapping_quality` оценивает лейбл, семантику, единицы, время и формулу; coverage 1.0 не значит, что эти проверки прошли. Пустой `unmapped.json` — не «у каждой строки блока есть концепт». Маленькая таксономия может поставить строке `concept_id` или воздержаться: неверный тег хуже `unknown`. Сначала structure (граф формул и соседи), затем лейблы, затем эмбеддинги, затем необязательный LLM rerank. У unknown остаются hints, соседи, одна формула, role-ячейки и top-3 кандидатов. `graph.json` и `graph.md` (схема `1.7.0`) публикуют сводные счётчики и links уровня формулы с `formula_class`, `row_key` и `period_id`. Колонка Class в `graph.md` повторяет `formula_class`. AST и развёрнутый cell-граф остаются в `ir/*.parquet` — [граф](docs/ru/graph.md).

Гайды: [обзор](docs/ru/overview.md), [layout](docs/ru/layout.md), [маппинг](docs/ru/mapping.md), [таксономия](docs/ru/taxonomy.md), [граф](docs/ru/graph.md), [срез для LLM](docs/ru/llm.md), [разбор unmapped](docs/ru/review.md), [архитектура](docs/ru/architecture.md).

## Ограничения (MVP)

- `.xls`, `.xlsb` и зашифрованные книги отклоняются
- Кэш формул уже должен быть в файле
- LLM и эмбеддинги необязательны: маппинг откатывается к structure + labels, затем `unknown`
- Каждая новая книга считается в своём процессе. Parquet в `data/jobs/` — кэш, который переживает этот процесс. Внутри одного прогона стадии передают списки строк и не перечитывают файл, который только что записали. Читает и пишет эти файлы PyArrow. Несколько реплик требуют внешнюю очередь; не запускайте `uvicorn` больше чем с одним worker.

Адаптировано из [cashflow-audit](https://github.com/x0r1x/cashflow-audit) (Apache-2.0). См. `NOTICE`.

## Окружение

Нужны Python 3.12+ и [uv](https://docs.astral.sh/uv/). На macOS установка `uv`, Python и виртуальное окружение:

```bash
brew install uv
uv python install 3.12
uv sync
cp .env.example .env
```

`uv sync` создаёт `.venv` и ставит версии из `uv.lock`. Команды ниже идут через `uv run`, активировать окружение не обязательно. Вручную: `source .venv/bin/activate`.

LLM и эмбеддинги необязательны: без них маппинг использует structure + labels, затем `unknown`. Если локальный OpenAI-совместимый сервер нужен, заполните `LLM_API_KEY` / `EMBEDDING_API_KEY` (токен LM Studio) и id моделей. URL по умолчанию — `http://127.0.0.1:1234/v1`. `.env` в git не попадает.

## Локальный запуск

### CLI

```bash
uv run finance-context build path/to/model.xlsx -o ./out
```

Пишет `context.json`, `context.md`, `graph.json` и `graph.md`.

Строки, которым маппинг не нашёл концепт:

```bash
uv run python scripts/extract-unmapped.py data/<job-id>/mapping.json
```

Скрипт принимает и готовый `context.json` и читает `disposition=abstained` из `blocks[].rows`. По умолчанию пишет `unmapped.json` рядом с входным файлом. Другой путь — `-o path/to/file.json`. Форма ответа: `{"count": <number>, "rows": [<атрибуты без значений периодов>]}`. Excluded-строки и ряды `values` опускаются.

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

Отправка книги. `POST` возвращает **202**, и API поднимает процесс для этой книги. Повторный POST, пока процесс жив, второй не стартует. Повторный POST книги, у которой уже есть context и graph, отдаёт этот снимок и не пересобирает. `POST /v1/context-jobs?remap=1` останавливает старый процесс, затем пересобирает layout, mapping и context, переиспользует parse и formula IR, если `ir/compile.json` совпал, и вызывает эмбеддинги/LLM для строк, которые structure и labels не закрыли. Опрашивайте, пока `status` не станет терминальным:

| status | Смысл |
| --- | --- |
| `queued` / `running` | Ещё считается |
| `succeeded` | Замаплено без открытых вопросов |
| `needs_input` | Контекст готов; часть fact-строк осталась `unknown` (вопросы на разбор) |
| `degraded` | Как `needs_input`, но LLM и эмбеддинги не настроены |
| `failed` | Ошибка пайплайна, или процесс книги остановлен по серверному `JOB_TIMEOUT_SEC` (`error` — `TimeoutError`); usable context нет. Отправьте книгу снова. |

`needs_input` — не падение. `context.json`, `context.md`, `graph.json` и `graph.md` всё равно отдаются.

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
- `GET /healthz`, `GET /readyz`

Живая схема роутов строится из этих обработчиков: [Swagger UI](http://127.0.0.1:8080/docs), [ReDoc](http://127.0.0.1:8080/redoc) и `GET /openapi.json`. `context.json`, `graph.json` и trace описаны теми же моделями, которыми эти файлы пишутся.

Коды `error`:

| code | HTTP |
| --- | --- |
| `unsupported_media_type` | 400 |
| `empty_file` | 400 |
| `file_too_large` | 413 |
| `encrypted_workbook` | 422 |
| `zip_rejected` | 422 |
| `not_found` | 404 |
| `report_not_ready` | 409 |

`report_not_ready` — артефакт или trace запрошены до того, как джоб записал этот файл.

Окружение: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` (если не задан, дефолт процесса `qwen3.6-27b-fp8`), `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_BATCH_SIZE` (32), `EMBEDDING_CONCURRENCY` (1), `LLM_CONCURRENCY` (1, на книгу), `DATA_DIR`, `JOB_TIMEOUT_SEC` (серверный дефолт 3600). См. `.env.example`. `GET /readyz` сообщает `queue: in_process`: список джобов живёт в этом процессе API, а каждая книга считается в его дочернем процессе.

Выученные high-confidence пары лежат в `$DATA_DIR/glossary.json` и используются на следующих джобах. Таксономия — `src/finance_context/ontology/taxonomy.yaml`. Как добавить концепт или alias и как каскад использует эти поля: [docs/ru/taxonomy.md](docs/ru/taxonomy.md) и [docs/ru/mapping.md](docs/ru/mapping.md). Строки check/helper/flag в вопросы на разбор не входят; они остаются в блоке с `disposition=excluded`. Несмапленные бизнес-строки остаются `unknown` с кандидатами, а не берут ближайший тег.

## Docker

```bash
cp .env.example .env   # необязательно; LLM и эмбеддинги можно не задавать
docker compose up --build
```

Оставьте Compose запущенным. API: `http://127.0.0.1:8080`. Артефакты джобов и `glossary.json` попадают в `./data` на хосте. Compose монтирует **только `./data`**, не `src/`: таксономия и код маппинга — те, что запечены в образ. После правок онтологии пересоберите образ.

Loopback-URL LLM в `.env` (`http://127.0.0.1:1234/v1`) внутри контейнера переписываются на `host.docker.internal`, чтобы LM Studio на хосте оставался доступен. Сервер моделей должен слушать все интерфейсы или gateway хоста, а не только другую изолированную сеть.

Контейнер API без Compose. Команда образа — `finance-context serve`:

```bash
docker build -t finance-context-builder .
docker run --rm -v "$PWD/data:/app/data" --env-file .env -p 8080:8080 \
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
healthz.json  readyz.json  post-job.json  job-status.json  summary.txt  unmapped.json
```

Клиент проверяет четыре URL документов, что в `context.json` нет второго каталога строк и нет AST, что `graph.json` — схема `1.7`, а `context.json` — схема `1.13` с `links` (диапазон остаётся одной ссылкой), и что Markdown-близнецы повторяют те же блоки и links, затем дымит `GET .../graph/trace` и `.../graph/trace.md`. Выход скрипта с `OK` значит, что клиент закончил; сервер должен по-прежнему слушать 8080. Таймаут клиента процесс книги не останавливает.

```bash
bash scripts/run.sh path/to/model.xlsx
```

Если путь не задан, скрипт ищет `resources/cashflow.xlsx`. Можно переопределить `BASE_URL` (по умолчанию `http://127.0.0.1:8080`), `OUT_DIR` (по умолчанию `./out`) и `JOB_TIMEOUT_SEC` (опрос клиента, по умолчанию `300`, если переменная не задана). Серверный `JOB_TIMEOUT_SEC` в `.env` — другие часы: он останавливает процесс. Экспортируйте то же значение перед `run.sh`, если клиент должен ждать столько же.
