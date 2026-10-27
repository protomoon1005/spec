# Validator API — 하드캡 화면용

전략서를 4단(스키마 → 참조 → 논리 + 범위 보정 → 하드캡)으로 검사한다.
알고리즘 정본은 `docs/m2-algorithms.md` 1장·6장, 구현은 `backend/app/m2/`.

## 요청

```http
POST /specs/{spec_id}/validate
Authorization: Bearer <access_token>
```

- 본문 없음. 전략 완료 화면이 남긴 `spec_id` 를 그대로 넣는다
- 내 전략서, `draft` 상태만 된다
- 여러 번 불러도 결과가 같다. 고친 범위는 전략서에 저장되고 원래 값(`_raw`)은 그대로 남는다
- 검사 기준일(`as_of`)은 가격 데이터의 마지막 거래일이다. 오늘이 아니다

## 응답 예시 (200) — 범위 보정과 클램프가 일어난 경우

공격투자형, 종목 3개. 요청 상한의 합(하드캡 반영 후 0.75)이 투자 가능 금액 0.95 에 못 미쳐
case B 로 상한을 늘렸고, 하드캡을 넘는 값 넷을 깎았다.

```json
{
  "spec_id": "STR-3f9a1c2b7d01",
  "hardcap_version": "v0.1",
  "as_of": "2025-12-30",
  "passed": true,
  "blocked_at": null,
  "regeneration": { "required": false, "reasons": [] },
  "cash_target": 0.10,
  "stages": [
    { "stage": 1, "name": "schema",    "status": "passed", "violations": [], "adjusted_bounds": null, "clamped_fields": null },
    { "stage": 2, "name": "reference", "status": "passed", "violations": [], "adjusted_bounds": null, "clamped_fields": null },
    {
      "stage": 3, "name": "logic", "status": "passed", "violations": [],
      "adjusted_bounds": {
        "case": "B",
        "cash_min": 0.05,
        "cash_target": 0.10,
        "scale": null,
        "sum_min_before": 0.25, "sum_max_before": 0.75,
        "sum_min_after": 0.25,  "sum_max_after": 0.90,
        "reduce_universe": false,
        "items": [
          { "ticker": "069500", "ceiling": 0.30, "min_before": 0.10, "min_after": 0.10, "max_before": 0.30, "max_after": 0.30 },
          { "ticker": "133690", "ceiling": 0.30, "min_before": 0.05, "min_after": 0.05, "max_before": 0.20, "max_after": 0.30 },
          { "ticker": "148070", "ceiling": 0.30, "min_before": 0.10, "min_after": 0.10, "max_before": 0.25, "max_after": 0.30 }
        ]
      },
      "clamped_fields": null
    },
    {
      "stage": 4, "name": "hardcap", "status": "passed", "violations": [],
      "adjusted_bounds": null,
      "clamped_fields": [
        { "field": "universe.069500.weight_max",      "requested": 0.40, "applied": 0.30, "limit": "max_weight_per_asset" },
        { "field": "constraint.max_weight_per_asset", "requested": 0.40, "applied": 0.30, "limit": "max_weight_per_asset" },
        { "field": "constraint.max_drawdown",         "requested": 0.35, "applied": 0.25, "limit": "max_drawdown" },
        { "field": "rebalance.min_interval_days",     "requested": 3,    "applied": 5,    "limit": "min_interval_days" }
      ]
    }
  ],
  "universe": [
    { "ticker": "069500", "weight_min_raw": 0.10, "weight_max_raw": 0.40, "weight_min": 0.10, "weight_max": 0.30, "was_adjusted": true },
    { "ticker": "133690", "weight_min_raw": 0.05, "weight_max_raw": 0.20, "weight_min": 0.05, "weight_max": 0.30, "was_adjusted": true },
    { "ticker": "148070", "weight_min_raw": 0.10, "weight_max_raw": 0.25, "weight_min": 0.10, "weight_max": 0.30, "was_adjusted": true }
  ]
}
```

**숫자는 반올림해서 보여 줄 것.** 실제 응답은 계산값 그대로라 `0.10000000000000009` 처럼 온다.
`rebalance.min_interval_days` 의 `requested`·`applied` 도 `3.0`·`5.0` 처럼 실수로 온다.

## 필드

**최상위**

| 필드 | 뜻 |
|---|---|
| `spec_id` | 검사한 전략서 |
| `hardcap_version` | 적용한 시스템 상한(하드캡) 버전 |
| `as_of` | 검사 기준일 = 가격 데이터의 마지막 거래일. 참조 검사 구간은 2023-01-01 ~ 이 날 |
| `passed` | 전부 통과했는가 |
| `blocked_at` | 막힌 단계 번호(1~4). 통과면 `null` |
| `regeneration.required` | 전략서를 다시 만들어야 하는가. 스키마(1단)·참조(2단) 실패일 때만 true |
| `regeneration.reasons` | 다시 만들어야 하는 이유. 위반 항목에 `stage` 가 붙은 모양 |
| `cash_target` | 보정 후 현금 목표. 상한을 다 늘려도 모자라면 `cash_min` 보다 커진다. 3단 전에 막히면 `null` |
| `stages` | 단계 4개. 항상 1~4 순서로 온다 |
| `universe` | 종목별 원래 범위와 확정 범위. 1단(형식)에서 막히면 빈 목록 |

**`stages[]`**

| 필드 | 뜻 |
|---|---|
| `stage` · `name` | 1 `schema` · 2 `reference` · 3 `logic` · 4 `hardcap` |
| `status` | `passed` · `failed` · `not_run`(앞 단계에서 막혀 안 돌았다) |
| `violations[]` | 위반 목록. 아래 표 |
| `adjusted_bounds` | 3단에만 온다. 범위 보정 내역 |
| `clamped_fields` | 4단에만 온다. 하드캡으로 깎인 값 목록 |

**`violations[]`**

| 필드 | 뜻 |
|---|---|
| `code` | 화면 분기용 코드. 아래 표 |
| `message` | 사용자에게 그대로 보여 줄 한 문장 |
| `ticker` | 걸린 종목. 전략서 전체에 걸리면 `null` |
| `field` | 걸린 칸. 없으면 `null` |
| `detail` | 판정에 쓴 수치 |

| `code` | 단계 | 뜻 |
|---|---|---|
| `SCHEMA` | 1 | 형식 오류 |
| `UNKNOWN_TICKER` | 2 | 종목 원장에 없는 종목 |
| `DELISTED` | 2 | 상장폐지 종목 |
| `NO_PRICE_DATA` | 2 | 검사 구간의 가격이 모자람 |
| `MIN_GT_MAX` | 3 | 하한이 상한보다 큼 |
| `RISK_TAG_UNKNOWN` | 3 | 종목 위험등급이 비어 있음 |
| `PRESET_FORBIDDEN` | 3 | 이 성향이 담을 수 없는 위험등급 |
| `PRESET_EXCEEDED` · `PRESET_BELOW` | 3 | 성향별 허용범위를 벗어남 |
| `DRAWDOWN_INFEASIBLE` | 3 | 낙폭 목표가 유니버스 변동성으로는 지킬 수 없음 |
| `UNIVERSE_TOO_LARGE` | 3 | 최소 비중을 다 줄여도 넘침 → 종목 수를 줄여야 함(현재 기준표에서는 나오지 않음) |
| `LEVERAGE_NOT_ALLOWED` | 4 | 하드캡이 레버리지·인버스를 허용하지 않음 |

**`adjusted_bounds` (3단)**

| 필드 | 뜻 |
|---|---|
| `case` | `"A"` 하한 합이 넘쳐 줄였다 · `"B"` 상한 합이 모자라 늘렸다 · `null` 보정 불필요 |
| `cash_min` | 보정에 쓴 현금 하한 = max(요청, 하드캡) |
| `cash_target` | 보정 후 현금 목표 |
| `scale` | case A 의 축소 계수. 아니면 `null` |
| `sum_min_before` · `sum_max_before` | 보정 전 하한 합·상한 합 (종목 상한을 씌운 뒤) |
| `sum_min_after` · `sum_max_after` | 보정 후 하한 합·상한 합 |
| `reduce_universe` | 종목 수를 줄여야 하는가 |
| `items[].ceiling` | 그 종목의 상한 = min(성향별 허용 상한, 하드캡 종목 상한) |
| `items[].min_before` → `min_after` | 하한의 보정 전 → 후 |
| `items[].max_before` → `max_after` | 상한의 보정 전 → 후 |

**`clamped_fields[]` (4단)**

| 필드 | 뜻 |
|---|---|
| `field` | 깎인 칸. `universe.<종목>.weight_min|weight_max` · `constraint.<칸>` · `rebalance.min_interval_days` |
| `requested` | 사용자가 요청한 값 |
| `applied` | 하드캡이 적용한 값 |
| `limit` | 근거가 된 하드캡 칸 이름 |

`cash_min` 과 `rebalance.min_interval_days` 는 하한이라 **올라간다.** 나머지는 상한이라 내려간다.
리밸런싱을 안 하는 전략(`trigger.type` 이 `none`)은 간격을 깎지 않는다.

**`universe[]`**

| 필드 | 뜻 |
|---|---|
| `weight_min_raw` · `weight_max_raw` | AI 가 낸 원래 범위. 절대 바뀌지 않는다 |
| `weight_min` · `weight_max` | 보정·클램프 후 확정 범위. 2·3단에서 막히면 원래 범위와 같다 |
| `was_adjusted` | 확정 범위가 원래 범위와 다른가 |

## 오류

| 코드 | 언제 | `detail` |
|---|---|---|
| 401 | 토큰 없음·만료 | — |
| 404 | 전략서가 없거나 남의 것 | `전략서를 찾을 수 없다` |
| 409 | `draft` 가 아님(승인·운용·종료) | `draft 전략서만 검증할 수 있다` |
| 409 | 가격 데이터가 한 행도 없음 | `가격 데이터가 없어 검증할 수 없다` |

검사에서 막히는 것은 오류가 아니다. **200 에 `passed: false`** 로 온다.

## 화면에서 보여 주면 좋을 것

1. **단계별 통과 여부** — `stages` 4개를 순서대로. `failed` 면 `violations[].message` 를 그 아래에,
   `not_run` 은 회색으로. `regeneration.required` 가 true 면 "전략서를 다시 만들어야 합니다"
2. **보정 전후 범위** — `adjusted_bounds.case` 가 있으면 종목별 `min_before~max_before → min_after~max_after`.
   `cash_target` 이 `cash_min` 보다 크면 "상한을 다 늘려도 모자라 현금을 늘렸습니다"
3. **클램프 전후 값** — `clamped_fields` 를 `requested → applied` 로. `limit` 이 근거가 된 하드캡 이름이다
