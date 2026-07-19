# -*- coding: utf-8 -*-
"""
무한매수법 백테스트 + '유지 vs 중단' 확률 분석

핵심 아이디어
- daily.py의 실전 주문/체결 엔진을 그대로 재사용해서 과거 전체 구간을 시뮬레이션
  (실전과 백테스트의 규칙 불일치가 원천적으로 없음)
- 시뮬레이션의 모든 날에 대해 '상태 스냅샷'(T값, 평단 대비 종가 괴리, 200일선
  위/아래)과 '그날 중단했을 때 손익' vs '규칙대로 유지했을 때 사이클 최종 손익'을 기록
- 현재 실전 상태와 유사한 과거 날들만 골라서, 유지가 중단보다 나았던 비율을 계산
  → "유지 유리 확률"

한계 (반드시 인지할 것)
- 과거가 미래를 보장하지 않음. 특히 TQQQ/SOXL 히스토리(2010~)는 대세 상승장이
  대부분이라 '유지 유리' 쪽으로 편향되어 있음.
- 2022년 수준을 넘는 하락장은 표본에 거의 없음.
"""
import json
import math
import os
import urllib.request
from datetime import datetime, timezone

import daily  # 같은 폴더의 실전 엔진 재사용

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


# ---------------------------------------------------------------
# 전체 히스토리 수집
# ---------------------------------------------------------------
def fetch_history(ticker):
    """상장 이후 전체 일봉. yahoo 실패 시 stooq 폴백."""
    try:
        return _hist_yahoo(ticker)
    except Exception as e:
        print(f"[warn] yahoo history 실패({ticker}): {e} -> stooq")
        return _hist_stooq(ticker)


def _hist_yahoo(ticker):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{ticker}?range=max&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    res = data["chart"]["result"][0]
    ts, q = res["timestamp"], res["indicators"]["quote"][0]
    out = []
    for i in range(len(ts)):
        if any(q[k][i] is None for k in ("open", "high", "low", "close")):
            continue
        out.append({
            "date": datetime.fromtimestamp(ts[i], tz=timezone.utc).strftime("%Y-%m-%d"),
            "open": round(q["open"][i], 4), "high": round(q["high"][i], 4),
            "low": round(q["low"][i], 4), "close": round(q["close"][i], 4),
        })
    if len(out) < 300:
        raise RuntimeError("yahoo: 데이터 부족")
    return out


def _hist_stooq(ticker):
    url = f"https://stooq.com/q/d/l/?s={ticker.lower()}.us&i=d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        lines = r.read().decode().strip().splitlines()[1:]
    out = []
    for ln in lines:
        p = ln.split(",")
        try:
            out.append({"date": p[0], "open": float(p[1]), "high": float(p[2]),
                        "low": float(p[3]), "close": float(p[4])})
        except (ValueError, IndexError):
            continue
    if len(out) < 300:
        raise RuntimeError("stooq: 데이터 부족")
    return out


# ---------------------------------------------------------------
# 백테스트 (daily.py 엔진 재사용)
# ---------------------------------------------------------------
def make_state(version, seed):
    return {
        "enabled": True, "version": version, "seed": seed,
        "divisions": 40 if version == "v2.2" else 20,
        "active": True, "shares": 0, "total_bought": 0.0, "avg_price": 0.0,
        "realized_profit": 0.0, "cycle_no": 1, "cycle_start": None,
        "one_buy_override": None, "last_close": None, "last_date": None,
        "pending_orders": [], "history": [],
    }


def run_backtest(ticker, prices, version="v2.2", seed=4000):
    """전체 구간 시뮬레이션.
    반환: summary(성과 요약), snapshots(유사상황 분석용 일별 기록)"""
    t = make_state(version, seed)
    closes = [p["close"] for p in prices]

    snapshots = []          # 일별 상태 기록
    equity_curve = []       # 총자산 곡선 (시드 + 누적실현 + 평가손익)
    cum_realized = 0.0
    cycle_days = 0
    cycle_start_idx = None
    prev_hist_len = 0

    for i, ohlc in enumerate(prices):
        daily.simulate_fills(t, ohlc)
        t["last_close"] = ohlc["close"]
        t["last_date"] = ohlc["date"]

        # 사이클 종료 감지
        if len(t["history"]) > prev_hist_len:
            fin = t["history"][-1]
            fin["days"] = cycle_days
            fin["start"] = prices[cycle_start_idx]["date"] if cycle_start_idx is not None else None
            fin["end"] = ohlc["date"]
            cum_realized += fin["profit"]
            prev_hist_len = len(t["history"])
            cycle_days = 0
            cycle_start_idx = None

        orders, T = daily.build_orders(t, ticker)
        t["pending_orders"] = orders

        if t["shares"] > 0:
            if cycle_start_idx is None:
                cycle_start_idx = i
            cycle_days += 1

        # 200일 이동평균 (최소 50일)
        w = closes[max(0, i - 199):i + 1]
        ma200 = sum(w) / len(w)
        above_ma = ohlc["close"] >= ma200

        mtm = t["shares"] * ohlc["close"] - t["total_bought"] + t["realized_profit"]
        equity_curve.append(seed + cum_realized + (mtm if t["shares"] > 0 else 0))

        if t["shares"] > 0:
            snapshots.append({
                "i": i, "date": ohlc["date"],
                "cycle": t["cycle_no"], "T": T,
                "gap": (ohlc["close"] / t["avg_price"] - 1) * 100 if t["avg_price"] else 0,
                "above_ma": above_ma,
                "mtm_pnl": round(mtm, 2),   # 이날 전량 중단(청산) 시 손익
                "final_pnl": None,          # 규칙대로 유지 시 사이클 최종 손익 (아래서 채움)
            })

    # 각 스냅샷에 소속 사이클의 최종 손익 연결 (미완결 사이클은 제외)
    fin_by_cycle = {h["cycle"]: h["profit"] for h in t["history"]}
    for s in snapshots:
        s["final_pnl"] = fin_by_cycle.get(s["cycle"])

    # 성과 요약
    peak, mdd = -1e18, 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        mdd = min(mdd, (eq - peak) / peak * 100 if peak > 0 else 0)
    cycles = t["history"]
    wins = [c for c in cycles if c["profit"] > 0]
    summary = {
        "ticker": ticker, "version": version, "seed": seed,
        "period": [prices[0]["date"], prices[-1]["date"]],
        "n_days": len(prices),
        "n_cycles": len(cycles),
        "win_cycles": len(wins),
        "win_rate": round(len(wins) / len(cycles) * 100, 1) if cycles else None,
        "total_profit": round(sum(c["profit"] for c in cycles), 2),
        "total_profit_pct": round(sum(c["profit"] for c in cycles) / seed * 100, 1),
        "avg_cycle_days": round(sum(c.get("days", 0) for c in cycles) / len(cycles), 1) if cycles else None,
        "max_cycle_days": max((c.get("days", 0) for c in cycles), default=None),
        "worst_cycle_profit": round(min((c["profit"] for c in cycles), default=0), 2),
        "mdd_pct": round(mdd, 1),
        "open_cycle": t["shares"] > 0,   # 마지막 사이클 미완결 여부
        "recent_cycles": [
            {"start": c.get("start"), "end": c.get("end"),
             "days": c.get("days"), "profit": round(c["profit"], 2)}
            for c in cycles[-10:]
        ],
    }
    return summary, snapshots


# ---------------------------------------------------------------
# '유지 vs 중단' 확률 분석
# ---------------------------------------------------------------
def analyze_stop_or_hold(snapshots, cur_T, cur_gap, cur_above_ma, seed):
    """현재와 유사한 과거 상황에서 '유지(사이클 최종 손익)'가
    '중단(그날 청산 손익)'보다 나았던 비율을 계산."""
    usable = [s for s in snapshots if s["final_pnl"] is not None]

    # 1차: T ±3, 괴리 ±5%p, 200일선 동일
    # 표본이 30 미만이면 조건을 단계적으로 완화
    filters = [
        {"dT": 3, "dGap": 5, "ma": True},
        {"dT": 5, "dGap": 8, "ma": True},
        {"dT": 5, "dGap": 10, "ma": False},
        {"dT": 8, "dGap": 15, "ma": False},
    ]
    sample, used = [], None
    for f in filters:
        sample = [s for s in usable
                  if abs(s["T"] - cur_T) <= f["dT"]
                  and abs(s["gap"] - cur_gap) <= f["dGap"]
                  and (not f["ma"] or s["above_ma"] == cur_above_ma)]
        used = f
        if len(sample) >= 30:
            break

    if not sample:
        return {"available": False, "reason": "유사한 과거 상황 표본 없음"}

    better = [s for s in sample if s["final_pnl"] > s["mtm_pnl"]]
    diffs = [(s["final_pnl"] - s["mtm_pnl"]) / seed * 100 for s in sample]
    diffs.sort()
    n = len(sample)
    return {
        "available": True,
        "n": n,
        "criteria": f"T±{used['dT']}, 괴리±{used['dGap']}%p"
                    + (", 200일선 동일" if used["ma"] else ""),
        "prob_hold_better": round(len(better) / n * 100, 1),
        "avg_extra_pct": round(sum(diffs) / n, 2),      # 유지 시 평균 추가 손익(시드 대비 %p)
        "p10_extra_pct": round(diffs[int(n * 0.1)], 2),  # 하위 10% (최악권)
        "p90_extra_pct": round(diffs[int(n * 0.9) - 1], 2),
    }


def analyze_entry(snapshots, summary, cur_above_ma):
    """포지션이 없을 때: 200일선 위/아래에서 시작한 사이클들의 과거 성과."""
    first_days = {}
    for s in snapshots:
        if s["cycle"] not in first_days:
            first_days[s["cycle"]] = s
    same = [s for s in first_days.values()
            if s["above_ma"] == cur_above_ma and s["final_pnl"] is not None]
    if not same:
        return {"available": False}
    wins = [s for s in same if s["final_pnl"] > 0]
    return {
        "available": True, "n": len(same),
        "regime": "200일선 위" if cur_above_ma else "200일선 아래",
        "win_rate": round(len(wins) / len(same) * 100, 1),
        "avg_pnl_pct": round(sum(s["final_pnl"] for s in same) / len(same)
                             / summary["seed"] * 100, 2),
    }


# ---------------------------------------------------------------
# 통합 실행 (daily.py에서 호출)
# ---------------------------------------------------------------
def run_for_ticker(ticker, live_t):
    """히스토리 수집 → 백테스트 → 현재 상태 기준 분석 → 파일 저장.
    반환: 텔레그램 메시지용 분석 dict (없으면 None)"""
    try:
        prices = fetch_history(ticker)
    except Exception as e:
        print(f"[warn] {ticker} 히스토리 수집 실패: {e}")
        return None

    version = live_t.get("version", "v2.2")
    seed = live_t.get("seed", 4000)
    summary, snapshots = run_backtest(ticker, prices, version, seed)

    closes = [p["close"] for p in prices]
    w = closes[-200:]
    cur_above_ma = closes[-1] >= sum(w) / len(w)

    result = {"summary": summary, "generated": datetime.now(timezone.utc)
              .isoformat(timespec="seconds"), "signal": None, "entry": None}

    if live_t.get("shares", 0) > 0 and live_t.get("avg_price", 0) > 0:
        cur_T = daily.calc_T(live_t)
        cur_gap = (live_t["last_close"] / live_t["avg_price"] - 1) * 100
        sig = analyze_stop_or_hold(snapshots, cur_T, cur_gap, cur_above_ma, seed)
        sig["cur_T"] = cur_T
        sig["cur_gap"] = round(cur_gap, 1)
        sig["above_ma"] = cur_above_ma
        result["signal"] = sig
    else:
        result["entry"] = analyze_entry(snapshots, summary, cur_above_ma)
        result["entry"]["above_ma"] = cur_above_ma

    out = os.path.join(DATA_DIR, f"backtest_{ticker}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"[info] {ticker} 백테스트 저장: 사이클 {summary['n_cycles']}개, "
          f"승률 {summary['win_rate']}%")
    return result
