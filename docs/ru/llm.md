# Срез метрики для отвечающей LLM

**Русский** · [English](../en/llm.md)

Канон джобы — `context.json` / `context.md` (schema `1.13.0`) и `graph.json` / `graph.md` (schema `1.7.0`). Срез ниже — проекция промпта, не артефакт и не замена ряда `blocks[].rows`. Ячейки, AST и рёбра в срез не переносятся.

Связанные документы: [обзор](overview.md), [архитектура](architecture.md), [граф](graph.md).

## Два потребителя

| Кто | Что видит | Чего не видит |
| --- | --- | --- |
| Маппинг (`ChatPort`) | Лейбл, `label_path`, соседи ±2, заголовки периодов | Числа, кэш, единицы в виде суммы |
| Ответ по уже построенной модели | Одно наблюдение: строка × период, поля ниже уже склеены | Сырой `values[]` и просьбу самой склеить ось, `axes` и ячейку |

Маппинг-чат не получает этот срез. Неверный `concept_id` хуже, чем `unknown`.

## Пример

Архитектурный пример. Объект `observation` не пишется в JSON джобы: его собирают из строки, серии этой оси и `links` / trace.

```json
{
  "observation": {
    "row_key": "Operation|10|Operation!r8",
    "label": "Revenue",
    "concept_id": "pnl.revenue",
    "disposition": "mapped",
    "dimensions": {
      "segment": "pc"
    },
    "period_id": "Y5",
    "value": "1234.56",
    "value_status": "cached",
    "normalized_value": "1234560",
    "scale_factor": 1000,
    "period_position": "during_period",
    "aggregation": "sum",
    "scenario": null,
    "unit": {
      "kind": "money",
      "currency": "GBP",
      "scale": "k",
      "sign": "inflow"
    },
    "formula": {
      "text": "=RC[-1]*(1+Growth)",
      "class": "cross_period",
      "precedents": []
    },
    "source": {
      "sheet": "Operation",
      "cell": "H10"
    },
    "timeline": {
      "axis_id": "Operation!r8",
      "phase": "operation",
      "phase_year": 1,
      "start_date": "2026-01-01",
      "end_date": "2026-12-31",
      "group_key": null,
      "flags": {}
    }
  }
}
```

`Y5` и `phase_year: 1` — разные часы. Пятый модельный год может быть первым операционным. `dimensions` необязательна: сегодня в неё попадает `hints.segment` (`pc` / `hv`), если он есть. Это не поле `vehicle_type` и не словарь измерений.

## Поле среза → текущий артефакт

| Поле среза | Откуда сейчас | Как попадает в промпт |
| --- | --- | --- |
| `row_key`, `label`, `concept_id`, `disposition` | `blocks[].rows` | Как есть. `concept_id` может быть `null` при `disposition=abstained` |
| `dimensions` | `hints.segment` | Только если segment задан. Иначе ключ отсутствует |
| `period_id` | `axes[].periods[]` той оси, к которой относится серия строки | Ключ оси (`Y5`, календарный год, дата). Grain берётся у этой оси |
| `value` | `row.series[]` с тем же `axis_id`, иначе `row.values[i]` | Строка кэша Excel или `null`. Не float: формулы не пересчитываются |
| `value_status` | `row.value_statuses[i]` | `cached`, `empty`, `zero_explicit` или `not_applicable`. Пустую ячейку не подменяют нулём |
| `normalized_value` | `row.normalized_values[i]` | Строка в базовых единицах или `null`, если число не разобрать. Не замена `value` |
| `scale_factor` | `row.scale_factor` | Целый множитель `1` / `1000` / `1000000` / `1000000000` или `null` |
| `period_position`, `aggregation` | поля строки | Например `during_period` и `sum`. Скаляр и строка params — `instant` / `none`. Рядом с `hints.time_semantics` |
| `scenario` | заголовок value/scenario-колонки params (`cells[].header`) | `Live` или `Case N`. У timeline-строки ключ отсутствует |
| `timeline.start_date`, `end_date` | `axes[].periods[]` | ISO, если ось собрана из полосы Start/End. Иначе ключи отсутствуют |
| `unit.kind`, `currency`, `scale`, `sign` | `hints.unit`, `hints.currency`, `hints.scale`, `hints.sign` | `scale` — токен `unit` / `k` / `m` / `bn`. Множитель — отдельный `scale_factor`. Без `kind` ставка выглядит как деньги |
| `formula.text` | `row.formula` | Один fingerprint с ячеек ряда. `null` — на колонках ряда нет формулы; формула stub сюда не подставляется. Отличия ячеек ряда — `formula_exceptions` |
| `formula.class` | `links[].formula_class` | Один класс на ячейку формулы. AST не копируется |
| `formula.precedents` | `graph.links[]` или `GET .../graph/trace` | Короткий список `row_key`, `concept_id`, `period_id`. AST не копируется |
| `source.sheet`, `source.cell` | Строка + колонка периода; у формулы ещё `links[].cell` | Цитата. В `context.json` per-cell `source` нет |
| `timeline.phase`, `phase_year`, `group_key`, `flags` | `axes[].periods[]` с тем же `period_key` на оси серии | Копия в срез, чтобы модель не джойнила. В `blocks[].periods` фазу не дублируют. `group_key` есть у месяца под повторяющимся годом |

`flags` (кейс, covenant, repayment и прочие 0/1) берутся с той оси, в чьих строках лежат флаги: фазы недостаточно, чтобы прочитать число.

## Чего в срезе нет

- Замены `context.json` каталогом наблюдений. Полнота остаётся рядом: одна строка, один fingerprint, `values[]` по оси.
- `validation_context`. `phase` / `phase_year` — часы таймлайна, не результат проверки.
- Подмены `value` множителем или JSON-числом. Кэш остаётся строкой; множитель и базовая величина — отдельные поля.
- AST. Он остаётся в `ir/cells.parquet` и попадает в разговор только отдельным запросом, не в обычный промпт.
- Второго JSON джобы. Срез живёт в промпте отвечающей модели.

## Как читать context и graph

- `values[i]` склеивается с `periods[i]`. `period.index` начинается с 1 и индексом массива не является.
- У timeline годы лежат в `axes[]`. Пустой `block.periods` у такого блока — норма. У params колонки лежат в `block.periods`.
- Иерархия строки — `label_path`. `parent_label` может указывать на более крупную секцию.
- `row.formula` пустой — на колонках ряда нет формулы. Формулу года брать из link с тем же `row_key` и `period_id` или из trace. Нет такого link — значение ввод.
- Имя в тексте формулы искать в `refs` и в `workbook.defined_names`. Локальная ячейка и локальный диапазон уже заменены адресом в `refs`. Токен без `!` значит, что имя не одна ячейка и не один диапазон этой книги. Формула имени с `[` или `#REF!` — ссылка вне книги.
- `dangling` равен 0 не значит, что имена резолвятся. Смотреть `unresolved.count` и `external.count`. Поле `ids` — не больше 32 записей, полное число в `count`.
- `concept_id` не ключ метрики. Вопрос «DSCR в 2030» ищется по подписи и `row_key`, затем по периоду. Если `semantic_identity.concept_id` другой, называть оба.
- Год оси без `phase` не операционный.
- `cell_refs` у вопроса маппинга — колонка подписи, не первая ячейка ряда.
- Предупреждение считает формулы без кэша внутри фазы. `workbook.missing_cached_values` считает все такие формулы, включая годы вне фазы.
- В промпт не класть `graph.json`, `graph.md` и широкую таблицу блока. Если `numeric_summary.constant`, цитировать одно значение и адреса первого и последнего периода. Точные `values` в JSON не сжимать.
- Числа не пересчитывать. Кэш Excel остаётся источником значения.
