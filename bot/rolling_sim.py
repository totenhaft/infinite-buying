# -*- coding: utf-8 -*-
"""
롤링 시뮬레이션 — "언제 시작하느냐"에 따른 결과 분포

국면별 백테스트는 국면마다 시작일이 1개뿐이라, 한 달만 어긋나도 숫자가 크게
달라진다는 한계가 있다. 여기서는 **가능한 모든 시작일**(기본 10거래일 간격)에서
전략을 굴려 결과의 확률 분포를 만든다.

산출물
- 승률(총손익 > 0 비율), 중앙값, 평균
- 하위 5%(최악권), 하위 25%, 상위 25%, 최악값  ← 리스크의 실체
- 원금 소진을 경험한 비율, 미청산 종료 비율
- **시작일이 200일선 위/아래일 때의 조건부 성과** ← 진입 타이밍 판단 근거

주의: 겹치는 구간을 여러 번 세므로 표본들은 서로 독립이 아니다.
      "확률"이라기보다 "과거에 이런 시작일들이 어떻게 끝났는가"의 분포로 볼 것.
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import regime_backtest as rb  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

HORIZONS = [(250, "1년"), (500, "2년")]
STEP = 10          # 시작일 간격(거래일). 10 = 약 2주마다
MIN_SAMPLES = 20


def pct(sorted_vals, q):
    if not sorted_vals:
        return None
    i = min(int(len(sorted_vals) * q), len(sorted_vals) - 1)
    return round(sorted_vals[i], 1)


def summarize(rows):
    """rows: [{'total_pct':..,'mdd_pct':..,'exhausted':bool,'open':bool,'above_ma':bool}]"""
    if len(rows) < MIN_SAMPLES:
        return None
    tot = sorted(r["total_pct"] for r in rows)
    mdd = sorted(r["mdd_pct"] for r in rows)
    wins = [r for r in rows if r["total_pct"] > 0]
    out = {
        "n": len(rows),
        "win_rate": round(len(wins) / len(rows) * 100, 1),
        "median": pct(tot, 0.5),
        "mean": round(sum(tot) / len(tot), 1),
        "p05": pct(tot, 0.05),
        "p25": pct(tot, 0.25),
        "p75": pct(tot, 0.75),
        "worst": round(tot[0], 1),
        "best": round(tot[-1], 1),
        "mdd_median": pct(mdd, 0.5),
        "mdd_worst": round(mdd[0], 1),
        "exhausted_rate": round(
            sum(1 for r in rows if r["exhausted"]) / len(rows) * 100, 1),
        "open_rate": round(sum(1 for r in rows if r["open"]) / len(rows) * 100, 1),
    }
    # 조건부: 시작일 200일선 위/아래
    for key, sel in (("above_ma", True), ("below_ma", False)):
        sub = [r for r in rows if r["above_ma"] is sel]
        if len(sub) >= MIN_SAMPLES:
            s = sorted(r["total_pct"] for r in sub)
            out[key] = {
                "n": len(sub),
                "win_rate": round(sum(1 for v in s if v > 0) / len(s) * 100, 1),
                "median": pct(s, 0.5),
                "p05": pct(s, 0.05),
                "worst": round(s[0], 1),
            }
    return out


def run_rolling(ticker, prices, seed=10000.0):
    """모든 시작일 × 전략 × 기간 조합 시뮬레이션."""
    closes = [p["close"] for p in prices]
    ma200 = []
    for i in range(len(closes)):
        w = closes[max(0, i - 199):i + 1]
        ma200.append(sum(w) / len(w) if i >= 199 else None)

    result = {"ticker": ticker,
              "period": [prices[0]["date"], prices[-1]["date"]],
              "horizons": {}}

    for days, label in HORIZONS:
        starts = list(range(200, len(prices) - days, STEP))
        by_strategy = {k: [] for k in rb.STRATEGIES if rb.STRATEGIES[k]["kind"] != "hold"}
        by_strategy["bh3x"] = []

        for s0 in starts:
            seg = rb.rebase(prices[s0:s0 + days])
            above = ma200[s0] is not None and closes[s0] >= ma200[s0]
            for key, cfg in rb.STRATEGIES.items():
                try:
                    r = rb.run_strategy(seg, ticker, cfg, seed)
                except Exception:
                    continue
                by_strategy[key].append({
                    "total_pct": r["total_pct"], "mdd_pct": r["mdd_pct"],
                    "exhausted": bool(r.get("exhausted_days")),
                    "open": bool(r.get("open_position")),
                    "above_ma": above,
                })

        result["horizons"][label] = {
            "days": days, "starts": len(starts),
            "strategies": {k: summarize(v) for k, v in by_strategy.items()},
        }
        print(f"[info] {ticker} {label}: 시작일 {len(starts)}개 × "
              f"{len(by_strategy)}전략 완료")
    return result


def build_series(ticker, src):
    """국면 백테스트와 동일한 소스로 연속 시계열 구성.
    각 시작일 구간은 rebase되므로 절대 가격 수준은 의미 없음."""
    real, synth = [], []
    try:
        _, real = rb.fetch_history(src["real"])
    except Exception as e:
        print(f"[warn] {ticker} 실물 실패: {e}")
    try:
        _, idx = rb.fetch_history(src["index"])
        synth = rb.synth_leveraged(idx)
    except Exception as e:
        print(f"[warn] {ticker} 지수 실패: {e}")

    if real and synth:
        cut = real[0]["date"]
        pre = [p for p in synth if p["date"] < cut]
        if pre:  # 이음매 레벨 맞추기 (구간별 rebase가 있으므로 절대값은 무관)
            k = real[0]["close"] / pre[-1]["close"]
            pre = [{"date": p["date"], "open": p["open"] * k, "high": p["high"] * k,
                    "low": p["low"] * k, "close": p["close"] * k} for p in pre]
        return pre + real
    return real or synth


def main():
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "step": STEP, "seed": 10000,
           "strategies": {k: v["label"] for k, v in rb.STRATEGIES.items()},
           "results": []}
    for ticker, src in rb.SOURCES.items():
        prices = build_series(ticker, src)
        if len(prices) < 800:
            print(f"[warn] {ticker}: 데이터 부족({len(prices)}) — 건너뜀")
            continue
        print(f"[info] {ticker}: {prices[0]['date']}~{prices[-1]['date']} "
              f"({len(prices)}일)")
        out["results"].append(run_rolling(ticker, prices))

    path = os.path.join(DATA_DIR, "rolling_sim.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("완료:", path)


if __name__ == "__main__":
    main()
