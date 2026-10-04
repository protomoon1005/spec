# 라이브 판단 결선 (T6 나머지 + T10). Celery daily_judge · update_view_weights 의 본체.
#
# 설명을 docstring 이 아니라 주석에 두는 이유는 app/views/ 와 같다 —
# scripts/check_asof_guard.py 가 app/ 전체의 문자열에서 가드 대상 테이블명을 잡는다.
#
# ── 경계 ─────────────────────────────────────────────────────────────
# M3 는 IntegratedSignal 에서 멈춘다. RiskSizer(리스크 한도 -> 선형매핑 -> 그룹캡 ->
# 주문)는 M2 몫이라 여기서 부르지 않고, 판단 기록의 risk_caps 이하 칸도 비워 둔다.
# 라이브 모드는 portfolio_id 가 실재하므로 지금 스키마로 판단 기록을 쓸 수 있다.
# 백테스트 모드(run_id 축)는 Q3 마이그레이션 대기라 손대지 않는다.
#
# ── 피처 규칙은 bridge 를 그대로 쓴다 ────────────────────────────────
# 워밍업 120 절단 · 종가 전용 결측 처리 · 이력 미달 종목의 중립 · SCORERS 조회가 전부
# BacktestJudge 에 있다. 라이브와 백테스트가 다른 규칙으로 판단하면 백테스트가
# 라이브를 대변하지 못하므로 복제하지 않고, 가중치 출처 한 곳(current_weights)만
# 갈아 끼운다 — 백테스트는 인스턴스 안의 손실 이력, 라이브는 DB 의 as_of 미만 행.
from __future__ import annotations

import bisect
import math
from datetime import date, timedelta

from app.contracts.view_weights import VIEW_TYPES
from app.repositories import decisions, price_daily, specs
from app.repositories.view_weights import get_view_weights, insert_view_weights
from app.views.bridge import BacktestJudge, scorer_source
from app.views.hedge import UPDATE_RULE_VERSION, brier_loss, rolling_weights
from app.views.market.features import CURRENT_FEATURE_SET_VERSION, FEATURE_WARMUP_ROWS
from app.views.market.labels import HORIZON, realized_outcome


def initial_weights() -> dict[str, float]:
    # FN-409 초기 1/3 균등. 손실 이력이 비면 hedge 가 그 값을 낸다 (별도 분기 없음).
    return rolling_weights({view_type: [] for view_type in VIEW_TYPES})


class _LiveJudge(BacktestJudge):
    def __init__(self, tickers: list[str], *, weights: dict[str, float]) -> None:
        super().__init__(tickers)
        self._weights = dict(weights)

    def current_weights(self) -> dict[str, float]:
        return dict(self._weights)

    def model_version(self) -> str:
        # 직전 판단에 실제로 쓴 모델 버전. 시장분석은 evidence 에 cutoff 별 버전을 남기고,
        # 버전이 없는 관점(시장온도 빈도표·중립)은 출처(real/neutral/mock)로 적는다.
        def version(view_type: str) -> str:
            found = {
                s.evidence["model_version"]
                for s in self.last_scores
                if s.view_type == view_type and "model_version" in s.evidence
            }
            return "+".join(sorted(found)) or scorer_source(self._scorers[view_type])

        return ",".join(f"{vt}={version(vt)}" for vt in VIEW_TYPES)


def _portfolio_tickers(portfolio_id: int) -> tuple[str, list[str]]:
    spec_id = decisions.get_portfolio_spec_id(portfolio_id)
    if spec_id is None:
        raise ValueError(f"포트폴리오가 없다: {portfolio_id}")
    return spec_id, [row["ticker"] for row in specs.get_spec_universe(spec_id)]


def daily_judge(portfolio_id: int, *, as_of: date) -> dict:
    spec_id, tickers = _portfolio_tickers(portfolio_id)
    prices = {
        ticker: price_daily.get_price_window(ticker, as_of=as_of, lookback_days=FEATURE_WARMUP_ROWS)
        for ticker in tickers
    }
    # strict < — 오늘 계산된 가중치는 오늘 판단에 못 쓴다. 이력이 없으면 FN-409 균등.
    weights = get_view_weights(portfolio_id, as_of=as_of) or initial_weights()

    judge = _LiveJudge(tickers, weights=weights)
    signal = judge.judge(prices, as_of=as_of)

    # 판단에 실제로 들어간 가장 늦은 거래일. as_of 가 휴장일이면 그보다 앞선다.
    snapshot = max((rows[-1]["trade_date"] for rows in prices.values() if rows), default=None)
    decision_id = decisions.insert_live_decision(
        portfolio_id=portfolio_id,
        spec_id=spec_id,
        as_of=as_of,
        integrated_signal=signal.model_dump(mode="json"),
        view_weights_used=signal.view_weights_used,
        model_version=judge.model_version(),
        feature_set_version=CURRENT_FEATURE_SET_VERSION,
        data_snapshot_asof=snapshot,
        scores=judge.last_scores,
    )
    return {"decision_id": decision_id, "signal": signal.model_dump(mode="json")}


# ── 채점 ─────────────────────────────────────────────────────────────
# 사건 정의는 labels.realized_outcome 하나다: 판단일 t 종가 대비 HORIZON(20)거래일
# 뒤 종가가 오르면 1. 거래일 달력은 가격 저장소 전체의 거래일이고, 실현일은 t 다음
# 거래일부터 센 20번째 거래일이다. 그 날이 as_of '전날까지'(< as_of) 있어야 확정이다.
#
# 관점별 지표 (판단 하나 x 관점 하나 = view_performance 한 행):
#   hit_rate     = raw_score 부호가 실현 방향(+1 상승 / -1 비상승)과 같은 종목 비율.
#                  raw_score = 0 (중립)인 종목은 방향을 말하지 않았으므로 분모에서 뺀다.
#                  전 종목이 중립이면 NULL.
#   contribution = 종목 평균 raw_score x 실현 방향 부호. [-1, 1].
#                  맞힌 쪽으로 강하게 말할수록 크고, 중립은 0 이다.
#   Brier 손실   = 종목 평균 (calibrated_prob - 사건)^2. 가중치 갱신에만 쓰고 저장하지
#                  않는다(컬럼이 없다) — 매번 view_scores 와 가격에서 다시 계산한다.


def _realized(portfolio_id: int, *, as_of: date) -> list[dict]:
    headers = decisions.list_live_decisions(portfolio_id, as_of=as_of)
    if not headers:
        return []
    cutoff = as_of - timedelta(days=1)
    calendar = price_daily.get_trade_dates(start=headers[0]["as_of"], as_of=cutoff)

    ready = []
    for header in headers:
        later = calendar[bisect.bisect_right(calendar, header["as_of"]) :]
        if len(later) < HORIZON:
            break  # 판단일 오름차순이라 뒤도 전부 미확정이다.
        ready.append({**header, "realized_at": later[HORIZON - 1]})
    if not ready:
        return []

    scores = decisions.get_view_scores([row["decision_id"] for row in ready])
    tickers = sorted({s["ticker"] for rows in scores.values() for s in rows})
    closes = price_daily.get_close_history(tickers, as_of=cutoff)

    out = []
    for row in ready:
        outcomes = {}
        for ticker in tickers:
            series = closes.get(ticker, [])
            before = _close_on_or_before(series, row["as_of"])
            after = _close_on_or_before(series, row["realized_at"])
            if before is not None and after is not None and before > 0:
                outcomes[ticker] = realized_outcome(before, after)
        per_view = _grade(scores.get(row["decision_id"], []), outcomes)
        if per_view is not None:
            out.append({**row, "views": per_view})
    return out


def _grade(scores: list[dict], outcomes: dict[str, int]) -> dict[str, dict] | None:
    by_view = {
        view_type: [s for s in scores if s["view_type"] == view_type and s["ticker"] in outcomes]
        for view_type in VIEW_TYPES
    }
    if not all(by_view.values()):
        # 채점할 종목이 없는 관점이 있으면 세 관점의 손실 이력 길이가 어긋난다.
        return None
    graded = {}
    for view_type, rows in by_view.items():
        # raw_score x 실현 방향 부호. 양수면 맞힌 쪽으로 말한 것이다.
        signed = [s["raw_score"] * (1 if outcomes[s["ticker"]] else -1) for s in rows]
        spoken = [value for s, value in zip(rows, signed, strict=True) if s["raw_score"]]
        graded[view_type] = {
            "brier": math.fsum(brier_loss(s["calibrated_prob"], outcomes[s["ticker"]]) for s in rows)
            / len(rows),
            "hit_rate": sum(1 for value in spoken if value > 0) / len(spoken) if spoken else None,
            "contribution": math.fsum(signed) / len(rows),
        }
    return graded


def _close_on_or_before(series: list[tuple[date, float]], day: date) -> float | None:
    index = bisect.bisect_right(series, day, key=lambda item: item[0])
    return series[index - 1][1] if index else None


def grade_decisions(portfolio_id: int, *, as_of: date) -> list[dict]:
    # 확정된 판단을 채점해 아직 기록 안 된 것만 view_performance 에 넣는다 (멱등).
    # 반환은 확정된 판단 전부(판단일 오름차순) — 가중치 갱신이 손실 이력으로 쓴다.
    realized = _realized(portfolio_id, as_of=as_of)
    done = decisions.get_graded_decision_ids(portfolio_id)
    decisions.insert_view_performance(
        portfolio_id=portfolio_id,
        rows=[
            {
                "decision_id": row["decision_id"],
                "view_type": view_type,
                "as_of": row["as_of"],
                "realized_at": row["realized_at"],
                "hit_rate": row["views"][view_type]["hit_rate"],
                "contribution": row["views"][view_type]["contribution"],
            }
            for row in realized
            if row["decision_id"] not in done
            for view_type in VIEW_TYPES
        ],
    )
    return realized


def update_view_weights(portfolio_id: int, *, as_of: date) -> dict:
    # 같은 (portfolio, as_of) 를 두 번 돌리면 append-only 충돌(IntegrityError)이 그대로
    # 올라온다 — 저장소 방침(조용한 덮어쓰기 금지)을 따른다. 채점은 그 전에 멱등으로 끝난다.
    realized = grade_decisions(portfolio_id, as_of=as_of)
    # rolling_weights 가 뒤에서 60개(WINDOW_TRADING_DAYS)만 본다. 판단이 거래일마다
    # 하나라 "최근 60거래일" = "최근 60개 판단"이다.
    losses = {
        view_type: [row["views"][view_type]["brier"] for row in realized] for view_type in VIEW_TYPES
    }
    weights = _save_weights(portfolio_id, as_of=as_of, weights=rolling_weights(losses))
    return {"as_of": as_of.isoformat(), "graded": len(realized), "weights": weights}


def init_view_weights(portfolio_id: int, *, as_of: date) -> dict[str, float]:
    # FN-409: 포트폴리오 활성화 시점에 균등 1/3 을 남긴다. 부르는 자리(활성화 라우터)는
    # M2 경계라 여기서 연결하지 않는다.
    return _save_weights(portfolio_id, as_of=as_of, weights=initial_weights())


def _save_weights(portfolio_id: int, *, as_of: date, weights: dict[str, float]) -> dict[str, float]:
    insert_view_weights(
        portfolio_id=portfolio_id,
        as_of=as_of,
        weights=weights,
        update_rule_version=UPDATE_RULE_VERSION,
    )
    return weights
