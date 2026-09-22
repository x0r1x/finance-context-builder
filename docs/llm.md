# Срез метрики для отвечающей LLM

Канон джобы не меняется: `context.json` / `context.md` (schema `1.9.0`) и `graph.json` / `graph.md` (schema `1.6.0`). Срез ниже — проекция промпта, не артефакт и не замена ряда `blocks[].rows`.

Связанные документы: [обзор](overview.md), [архитектура](architecture.md), [граф](graph.md).

## Два потребителя

| Кто | Что видит | Чего не видит |
| --- | --- | --- |
| Маппинг (`ChatPort`) | Лейбл, `label_path`, соседи ±2, заголовки периодов | Числа, кэш, единицы в виде суммы |
| Ответ по уже построенной модели | Одно наблюдение: строка × период, поля ниже уже склеены | Сырой `values[]` и просьбу самой склеить ось, `timeline` и ячейку |

Маппинг-чат не получает этот срез. Неверный `concept_id` хуже, чем `unknown`.

## Пример

Архитектурный пример. Этих полей нет в текущем JSON джобы: их собирают из строки, оси блока, `timeline` и `links` / trace.

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
    "unit": {
      "kind": "money",
      "currency": "GBP",
      "scale": "k",
      "sign": "inflow"
    },
    "formula": {
      "text": "=RC[-1]*(1+Growth)",
      "precedents": []
    },
    "source": {
      "sheet": "Operation",
      "cell": "H10"
    },
    "timeline": {
      "phase": "operation",
      "phase_year": 1,
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
| `period_id` | `block.periods[]`, тот же индекс, что у `values[]` | Ключ оси (`Y5`, календарный год, дата). Grain берётся у блока |
| `value` | `row.values[i]` | Строка кэша Excel или `null`. Не float: формулы не пересчитываются |
| `unit.kind`, `currency`, `scale`, `sign` | `hints.unit`, `hints.currency`, `hints.scale`, `hints.sign` | `scale` — токен `unit` / `k` / `m` / `bn`, не множитель `1000`. Без `kind` ставка выглядит как деньги |
| `formula.text` | `row.formula` | Один fingerprint на строку. Отличия ячеек — `formula_exceptions`, не второй текст по умолчанию |
| `formula.precedents` | `graph.links[]` или `GET .../graph/trace` | Короткий список `row_key`, `concept_id`, `period_id`. AST не копируется |
| `source.sheet`, `source.cell` | Строка + колонка периода; у формулы ещё `links[].cell` | Цитата. В `context.json` per-cell `source` нет |
| `timeline.phase`, `phase_year`, `flags` | `timeline.periods[]` с тем же `period_id` | Копия в срез, чтобы модель не джойнила. В `blocks[].periods` фазу не дублируют |

`flags` (кейс, covenant, repayment и прочие 0/1 на главной оси) входят в `timeline`: фазы недостаточно, чтобы прочитать число.

## Чего в срезе нет

- Замены `context.json` каталогом наблюдений. Полнота остаётся рядом: одна строка, один fingerprint, `values[]` по оси.
- `validation_context`. `phase` / `phase_year` — часы таймлайна, не результат проверки.
- `scale: 1000` и JSON-числа вместо строки кэша.
- AST. Он остаётся в `ir/cells.parquet` и попадает в разговор только отдельным запросом, не в обычный промпт.
- Второго JSON джобы. Срез живёт в промпте отвечающей модели.
