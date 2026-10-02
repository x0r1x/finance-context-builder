# finance-context-builder

**Русский** · [English](README.md)

Парсер документов Excel. Read-only сервис читает книгу cash-flow `.xlsx` или `.xlsm` и преобразует её в JSON и Markdown: `context.json`, `context.md`, `graph.json` и `graph.md`. Формулы сохраняются. Числа берутся из кэша Excel и не пересчитываются.

`context` хранит строки блоков, периоды и значения. `graph` хранит связи формул. Trace одной ячейки читает этот граф. Концепт строки ставит каскад: structure, лейблы, эмбеддинги, затем необязательный вызов модели. Строка без концепта остаётся `abstained`.

Гайды: [обзор](docs/ru/overview.md), [layout](docs/ru/layout.md), [маппинг](docs/ru/mapping.md), [таксономия](docs/ru/taxonomy.md), [граф](docs/ru/graph.md), [срез для LLM](docs/ru/llm.md), [разбор unmapped](docs/ru/review.md), [архитектура](docs/ru/architecture.md).

Адаптировано из [cashflow-audit](https://github.com/x0r1x/cashflow-audit) (Apache-2.0). См. `NOTICE`.

## Вход

- Принимаются `.xlsx` и `.xlsm`.
- `.xls`, `.xlsb` и зашифрованная книга отклоняются.
- Кэш формул уже должен лежать в файле.

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

Пишет `context.json`, `context.md`, `graph.json` и `graph.md`. Если эти документы уже лежат в каталоге и в `meta.json` тот же хэш `publisher`, команда печатает `reused` и пайплайн не запускает. Нет поля `publisher` или нет карты `stages` — удаляются layout, mapping и опубликованные документы, затем они собираются заново; parse и formula IR сохраняются. `meta.stages` называет первую изменившуюся стадию (`compile`, `layout`, `mapping`, `graph`, `publish`), и удаляется только она и всё после неё. Смена `publish` переписывает context и оставляет mapping и graph. Смена `compile` удаляет ещё `raw/` и formula IR.

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

Отправка книги. `POST` возвращает **202**, и API поднимает процесс для этой книги. Повторный POST, пока процесс жив, второй не стартует, если в `meta.json` есть штамп `publisher` и код на диске всё ещё совпадает с ним. Пустой штамп живой процесс не останавливает. Повторный POST книги, снимок которой собран этим же кодом, отдаёт этот снимок и не пересобирает. Нет поля `publisher` или хэш другой — сборка начинается с первой устаревшей стадии. Без карты `stages` это layout, mapping и context, а parse и formula IR переиспользуются, если `ir/compile.json` совпал. Эмбеддинги и LLM вызываются снова только если пересобирается сам mapping, и только для строк, которые structure и labels не закрыли. После рестарта API книга, оставшаяся в `queued` или `running`, становится `failed` с `error` `process_lost`. Тот же исход у `GET`, если процесса уже нет. Опрашивайте, пока `status` не станет терминальным:

| status | Смысл |
| --- | --- |
| `queued` / `running` | Ещё считается |
| `succeeded` | Документы готовы. Fact без концепта остаётся на строке со статусом `abstained`. |
| `failed` | Ошибка пайплайна, процесс книги остановлен по серверному `JOB_TIMEOUT_SEC` (`error` — `TimeoutError`), или API перезапустился, пока книга была в `queued` или `running` (`error` — `process_lost`). Отправьте книгу снова. |

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
- `GET /v1/context-jobs/{id}/summary` — паспорт готовой джобы: покрытие, счётчики книги и счётчики графа (`unresolved`, `external`, `dangling`, циклы). Без `links` и без кэша ячеек. Пока джоба считается, статус по-прежнему смотрят через `GET /v1/context-jobs/{id}`
- `GET /v1/context-jobs/{id}/catalog` — строки и оси без чисел. У периода также есть `phase_year`, `flags` и `group_key`. Фильтры: `q`, повторяемый `concept_id`, `sheet`, `disposition`, `limit`, `offset`
- `GET /v1/context-jobs/{id}/observations` — единственный роут с кэшем ячеек. Нужен повторяемый `row_key`, повторяемый `concept_id` или `q`. Без селектора ответ 400 `selector_required`. `limit` по умолчанию 24 и не больше 48 (`truncated`). Прецеденты не режутся лимитом строк. Полнота видна по `precedents_total`. `precedent_depth` по умолчанию 0 и не больше 3
- `GET /healthz`, `GET /readyz`

Живая схема роутов строится из этих обработчиков: [Swagger UI](http://127.0.0.1:8080/docs), [ReDoc](http://127.0.0.1:8080/redoc) и `GET /openapi.json`. `context.json`, `graph.json` и trace описаны теми же моделями, которыми эти файлы пишутся. Каталог, паспорт и наблюдения — модели ответа. На диске таких файлов нет.

Коды `error`:

| code | HTTP |
| --- | --- |
| `unsupported_media_type` | 400 |
| `empty_file` | 400 |
| `file_too_large` | 413 |
| `encrypted_workbook` | 422 |
| `zip_rejected` | 422 |
| `selector_required` | 400 |
| `not_found` | 404 |
| `report_not_ready` | 409 |
| `too_many_jobs` | 429 |

`report_not_ready` — артефакт или trace запрошены до того, как джоб записал этот файл. Каталогу нужен `context.json`. Паспорту и наблюдениям нужен ещё и `graph.json`. Наблюдения без `row_key`, `concept_id` или `q` — это `selector_required`, а не вся книга.

Окружение: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` (если не задан, дефолт процесса `qwen3.6-27b-fp8`), `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_BATCH_SIZE` (32), `EMBEDDING_CONCURRENCY` (1), `LLM_CONCURRENCY` (1, на книгу), `MAX_CONCURRENT_JOBS` (2), `DATA_DIR`, `JOB_TIMEOUT_SEC` (серверный дефолт 3600). См. `.env.example`. Сверх `MAX_CONCURRENT_JOBS` `POST` отвечает 429 `too_many_jobs`. `GET /readyz` сообщает `queue: in_process` и `jobs` — число живых дочерних процессов. Если `DATA_DIR` нельзя создать или в него нельзя записать, ответ 503.

Пороги каскада. Пустая или отсутствующая переменная оставляет значение из таблицы. Балл должен быть больше 0 и не больше 1, отрыв от 0 до 1, `EMBED_TOP_K` — целое от 1 до 32. Число вне диапазона останавливает процесс. Та же таблица в [docs/ru/mapping.md](docs/ru/mapping.md#thresholds).

| Поле | Переменная | Значение | Смысл |
| --- | --- | --- | --- |
| `concept_accept_min` | `CONCEPT_ACCEPT_MIN` | 0.82 | Строка получает концепт, когда балл лучшего кандидата не ниже этого числа. Ниже него в отчёте стоит `unknown`, а кандидаты остаются. Флаг качества `confidence_threshold_passed` требует, чтобы каждая принятая строка достигла этого балла. |
| `embed_score_min` | `EMBED_SCORE_MIN` | 0.85 | Совпадение по эмбеддингу, косинус между текстом строки и концептом, берётся по более жёсткой планке, чем остальные сигналы. Оно выбирается, когда косинус не ниже этого числа, затем считается уверенным и запоминается для следующих книг. |
| `embed_score_gap` | `EMBED_SCORE_GAP` | 0.08 | Этот эмбеддинг выбирается, когда его косинус обгоняет следующий минимум на это число. Более близкий второй кандидат оставляет строку `unknown`. |
| `embed_top_k` | `EMBED_TOP_K` | 5 | Сколько ближайших концептов остаётся на строке для поиска по эмбеддингу и для короткого списка, который видит модель. В опубликованных context и Markdown видны первые три. |
| `mint_score_max` | `MINT_SCORE_MAX` | 0.5 | Новый концепт таксономии создаётся для строки `unknown`, у которой уже есть ближайший существующий концепт, когда балл этого концепта ниже этого числа. |

Принятые строки лежат в `$DATA_DIR/shared/label_memory.json` и читаются всеми сессиями. Ключ — нормализованный лейбл, ближайшая секция (`section_path[-1]`, иначе родитель) и единица (`money`, `rate`, `years` или пусто). Балл строго выше заменяет концепт. Равный балл оставляет прежнюю запись. Эмбеддинг с уверенностью high пишется туда, ответ модели не пишется. Второй концепт или воздержавшаяся строка на том же ключе стирает запись. Ключ `section_class` в этот файл не пишется. Старый `sessions/{session}/glossary.json` ещё читается как пара лейбла и родителя. Каскад берёт таксономию из `$DATA_DIR/shared/taxonomy.json`: пустой файл один раз наполняется из `src/finance_context/ontology/taxonomy.yaml`. Новый id дописывается только у воздержавшейся строки, чей ближайший альтернативный балл ниже `mint_score_max`. Пустой список альтернатив остаётся без id. Months per year, Thousand, On, Off и голый Total остаются без id. Как устроены поля и каскад: [docs/ru/taxonomy.md](docs/ru/taxonomy.md) и [docs/ru/mapping.md](docs/ru/mapping.md). Строки check/helper/flag остаются в блоке с `disposition=excluded`. Несмапленные бизнес-строки остаются `abstained` с кандидатами, а не берут ближайший тег.

## Данные

`data/shared/books/{sha256}/` хранит книгу, raw, formula IR и layout. Они общие для всех сессий. Рядом, в `data/shared/`, лежат `taxonomy.json`, `label_memory.json` и кэш эмбеддингов `embeddings/`. `data/sessions/{session}/jobs/{sha256}/` хранит mapping, context и graph этой сессии. Старый `glossary.json` сессии ещё читается, новые пары в него не пишутся. Сессия по умолчанию `local`.

Одновременно считается не больше `MAX_CONCURRENT_JOBS` книг, по умолчанию 2. `finance-context serve` запускает один worker. Несколько реплик API на одном каталоге не поддерживаются.

## Docker

```bash
cp .env.example .env   # необязательно; LLM и эмбеддинги можно не задавать
docker compose up --build
```

Оставьте Compose запущенным. API опубликован только на `127.0.0.1:8080`. Общий кэш книг, таксономия, память лейблов и публикации сессии попадают в `./data` на хосте. Compose монтирует **только `./data`**, не `src/`. Пустой `data/shared/taxonomy.json` один раз копируется из yaml в образе. Уже существующий файл прогон не перезаписывает из yaml.

Loopback-URL LLM в `.env` (`http://127.0.0.1:1234/v1`) внутри контейнера переписываются на `host.docker.internal`, чтобы LM Studio на хосте оставался доступен. Сервер моделей должен слушать все интерфейсы или gateway хоста, а не только другую изолированную сеть.

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
healthz.json  readyz.json  post-job.json  job-status.json  summary.txt  unmapped.json
```

Клиент проверяет четыре URL документов, что в `context.json` нет второго каталога строк и нет AST, что `graph.json` — схема `1.7`, а `context.json` — схема `1.13` с `links` (диапазон остаётся одной ссылкой), и что Markdown-близнецы повторяют те же блоки и links, затем дымит `GET .../graph/trace` и `.../graph/trace.md`. Выход скрипта с `OK` значит, что клиент закончил; сервер должен по-прежнему слушать 8080. Таймаут клиента процесс книги не останавливает.

```bash
bash scripts/run.sh path/to/model.xlsx
```

Если путь не задан, скрипт ищет `resources/cashflow.xlsx`. Можно переопределить `BASE_URL` (по умолчанию `http://127.0.0.1:8080`), `OUT_DIR` (по умолчанию `./out`) и `JOB_TIMEOUT_SEC` (опрос клиента, по умолчанию `300`, если переменная не задана). Серверный `JOB_TIMEOUT_SEC` в `.env` — другие часы: он останавливает процесс. Экспортируйте то же значение перед `run.sh`, если клиент должен ждать столько же.
