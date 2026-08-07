# -*- coding: utf-8 -*-
"""
국면별(상승/하락/횡보) 무한매수법 백테스트 + 대응전략 비교

왜 필요한가
- TQQQ/SOXL 실물은 2010년 이후만 존재하고 그 기간은 대부분 강세장이라,
  "무한매수법이 모든 시장에서 통하는가"를 검증할 수 없다.
- 그래서 나스닥100(^NDX)과 필라델피아반도체(^SOX) 지수로 3배 레버리지 ETF를
  '합성'하여 2000년 닷컴버블 붕괴, 2008년 금융위기까지 소급 검증한다.

합성 방식 (근사치임을 명심할 것)
  3배ETF 일간수익률 ≈ 3 × 지수일간수익률 − 운용보수/252 − 2 × 조달금리/252
  고가/저가도 같은 배율로 근사. 실제 ETF와 완전히 같지 않지만,
  일간 리밸런싱형 레버리지의 변동성 잠식(volatility drag)은 그대로 재현된다.

전략 변형 (대응책 비교)
  base40   : 라오어 v2.2 원본 (40분할)
  v30      : 라오어 v3.0 (20분할, 공격형)
  div80    : 80분할 (1회 매수금 절반 → 하락장 체력 2배)
  ma200    : v2.2 + 200일선 아래에서는 신규 사이클 시작 안 함
  reserve  : v2.2 + 원금 소진 시 예비시드 50% 1회 투입
  ma_res   : ma200 + reserve 동시 적용
  bh3x     : 3배 ETF 매수 후 보유 (벤치마크)
  bh1x     : 지수(1배) 매수 후 보유 (벤치마크)
"""
import json
import math
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import daily      # noqa: E402  (체결 엔진 재사용)
import backtest   # noqa: E402  (일봉 수집기 재사용)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")

# 연도별 대략적인 단기 조달금리 (레버리지 비용 근사)
FUNDING = {
    1999: .050, 2000: .062, 2001: .038, 2002: .017, 2003: .011, 2004: .014,
    2005: .032, 2006: .050, 2007: .050, 2008: .020, 2009: .002, 2010: .002,
    2011: .001, 2012: .001, 2013: .001, 2014: .001, 2015: .002, 2016: .004,
    2017: .010, 2018: .019, 2019: .022, 2020: .004, 2021: .001, 2022: .019,
    2023: .050, 2024: .052, 2025: .043, 2026: .038,
}
EXPENSE = .0095  # 연 운용보수 근사 (TQQQ/SOXL ~0.9%)

REGIMES = [
    ("닷컴버블 붕괴",      "2000-03-10", "2002-10-09", "폭락"),
    ("닷컴 이후 회복",     "2002-10-10", "2007-10-31", "상승"),
    ("금융위기",           "2007-11-01", "2009-03-09", "폭락"),
    ("위기 후 강세장",     "2009-03-10", "2014-12-31", "상승"),
    ("2015-16 횡보",       "2015-01-01", "2016-11-30", "횡보"),
    ("2018 Q4 급락",       "2018-09-01", "2018-12-31", "급락"),
    ("코로나 쇼크",        "2020-02-01", "2020-06-30", "급락+V반등"),
    ("2022 하락장",        "2021-11-19", "2023-01-06", "하락"),
    ("2023- 회복",         "2023-01-07", None,         "상승"),
    ("전체 기간",          None,         None,         "전체"),
]


# ---------------------------------------------------------------
# 데이터
# ---------------------------------------------------------------
def _fetch_one(symbol):
    """일봉 조회. yahoo(period1/period2) → 실패 시 stooq(지수 제외).
    ※ range=max는 야후가 월봉을 돌려주므로 절대 단독 사용 금지."""
    errs = []
    try:
        rows = backtest._hist_yahoo(symbol)
        if backtest.is_daily(rows):
            return rows
        errs.append("yahoo: 월봉 응답")
    except Exception as e:
        errs.append(f"yahoo: {e}")
    if not symbol.startswith("^"):   # stooq는 지수 심볼 형식이 다름
        try:
            rows = backtest._hist_stooq(symbol)
            if backtest.is_daily(rows):
                return rows
            errs.append("stooq: 일봉 아님")
        except Exception as e:
            errs.append(f"stooq: {e}")
    raise RuntimeError(f"{symbol} [{' / '.join(errs)}]")


def fetch_history(symbols):
    """여러 후보 심볼을 순서대로 시도해 첫 성공을 반환. (심볼, 데이터)"""
    if isinstance(symbols, str):
        symbols = [symbols]
    errors = []
    for s in symbols:
        try:
            out = _fetch_one(s)
            print(f"[info] {s}: {out[0]['date']}~{out[-1]['date']} ({len(out)}일)")
            return s, out
        except Exception as e:
            print(f"[warn] {e}")
            errors.append(str(e))
    raise RuntimeError(" / ".join(errors))


def rebase(seg, start_price=60.0):
    """구간 첫 종가를 start_price로 맞춤 (수익률은 그대로).
    합성 3배 시계열은 과거로 갈수록 가격이 폭발하므로 구간별 재기준이 필수."""
    if not seg:
        return seg
    k = start_price / seg[0]["close"]
    return [{"date": p["date"], "open": round(p["open"] * k, 4),
             "high": round(p["high"] * k, 4), "low": round(p["low"] * k, 4),
             "close": round(p["close"] * k, 4)} for p in seg]


def synth_leveraged(index_prices, mult=3.0):
    """지수 일봉 → 3배 레버리지 ETF 합성 일봉."""
    out = []
    price = 10.0  # 임의 시작가
    for i in range(1, len(index_prices)):
        p0, p1 = index_prices[i - 1], index_prices[i]
        year = int(p1["date"][:4])
        cost = (EXPENSE + 2 * FUNDING.get(year, .02)) / 252
        r_c = p1["close"] / p0["close"] - 1
        r_h = p1["high"] / p0["close"] - 1
        r_l = p1["low"] / p0["close"] - 1
        r_o = p1["open"] / p0["close"] - 1
        prev = price
        price = max(prev * (1 + mult * r_c - cost), 0.01)
        out.append({
            "date": p1["date"],
            "open": round(max(prev * (1 + mult * r_o - cost), .01), 4),
            "high": round(max(prev * (1 + mult * r_h - cost), .01), 4),
            "low": round(max(prev * (1 + mult * r_l - cost), .01), 4),
            "close": round(price, 4),
        })
    return out


def slice_period(prices, start, end):
    return [p for p in prices
            if (start is None or p["date"] >= start)
            and (end is None or p["date"] <= end)]


# ---------------------------------------------------------------
# 전략 엔진 (daily.py 규칙과 동일, 분할수/필터만 확장)
# ---------------------------------------------------------------
def new_pos(cfg, seed):
    return {"version": cfg["version"], "seed": seed, "divisions": cfg["divisions"],
            "active": True, "shares": 0, "total_bought": 0.0, "avg_price": 0.0,
            "realized_profit": 0.0, "cycle_no": 1, "cycle_start": None,
            "one_buy_override": None, "last_close": None, "last_date": None,
            "pending_orders": [], "history": []}


def build_orders_ext(t, ticker, cfg, ma200, extra_rounds):
    """daily.build_orders의 확장판.
    - divisions가 40/20이 아니어도 진행률(T/divisions) 기준으로 동일한 스케줄 적용
    - ma200 필터: 200일선 아래면 '신규 사이클' 시작 보류
    - extra_rounds: 예비시드로 늘어난 추가 회차 수"""
    close, avg = t["last_close"], t["avg_price"]
    one_buy = daily.one_buy_amount(t)
    T = daily.calc_T(t)
    div = t["divisions"] + extra_rounds
    orders = []

    # 진행률을 40분할(또는 20분할) 기준으로 환산 → 원본 공식 그대로 사용
    base_div = 40 if t["version"] == "v2.2" else 20
    T_eff = T * base_div / div

    if t["shares"] == 0:
        if cfg.get("ma200") and ma200 is not None and close < ma200:
            return [], T  # 하락 추세에서는 신규 진입 보류
        return ([{"type": "LOC_BUY", "price": round(close * 1.15, 2),
                  "qty": daily.qty_for(one_buy, close),
                  "memo": "사이클 시작"}], T)

    if t["version"] == "v2.2":
        pct = (10 - T_eff / 2) / 100
        max_T, half = base_div - 0.9, base_div / 2
    else:
        pct = (15 - 1.5 * T_eff) / 100
        max_T, half = base_div - 0.9, base_div / 2

    star = round(avg * (1 + pct), 2)
    if T_eff < max_T:
        if T_eff < half:
            orders.append({"type": "LOC_BUY", "price": round(avg, 2),
                           "qty": daily.qty_for(one_buy / 2, close), "memo": "전반전 평단"})
            orders.append({"type": "LOC_BUY", "price": star,
                           "qty": daily.qty_for(one_buy / 2, close), "memo": "전반전 별"})
        else:
            orders.append({"type": "LOC_BUY", "price": star,
                           "qty": daily.qty_for(one_buy, close), "memo": "후반전"})

    q1 = max(t["shares"] // 4, 1)
    q3 = t["shares"] - q1
    if t["version"] == "v2.2":
        limit = round(avg * 1.10, 2)
        orders.append({"type": "LOC_SELL", "price": star, "qty": q1, "memo": "1/4 LOC"})
    else:
        limit = round(avg * (1.15 if ticker == "TQQQ" else 1.20), 2)
        if T_eff <= base_div - 1:
            orders.append({"type": "LOC_SELL", "price": star, "qty": q1, "memo": "1/4 LOC"})
        else:  # 쿼터모드: 매수 중단 + MOC 매도
            orders = [o for o in orders if not o["type"].endswith("BUY")]
            orders.append({"type": "MOC_SELL", "qty": q1, "memo": "쿼터모드 MOC"})
    if q3 > 0:
        orders.append({"type": "LIMIT_SELL", "price": limit, "qty": q3, "memo": "3/4 지정가"})
    return orders, T


def run_strategy(prices, ticker, cfg, seed=10000.0):
    """한 전략을 한 구간에 대해 시뮬레이션."""
    if cfg["kind"] == "hold":
        p0, p1 = prices[0]["close"], prices[-1]["close"]
        peak, mdd = -1e18, 0.0
        for p in prices:
            peak = max(peak, p["close"])
            mdd = min(mdd, (p["close"] - peak) / peak * 100)
        return {"total_pct": round((p1 / p0 - 1) * 100, 1), "mdd_pct": round(mdd, 1),
                "cycles": None, "win_rate": None, "max_cycle_days": None,
                "exhausted_days": None, "invested_peak_pct": 100.0}

    t = new_pos(cfg, seed)
    total_capital = seed * (1.5 if cfg.get("reserve") else 1.0)
    closes = [p["close"] for p in prices]
    equity, cum, extra_rounds, reserve_used = [], 0.0, 0, False
    cycle_days, max_cycle_days, exhausted = 0, 0, 0
    prev_hist = 0
    peak_invested = 0.0

    for i, ohlc in enumerate(prices):
        daily.simulate_fills(t, ohlc)
        if len(t["history"]) > prev_hist:
            cum += t["history"][-1]["profit"]
            prev_hist = len(t["history"])
            max_cycle_days = max(max_cycle_days, cycle_days)
            cycle_days, extra_rounds, reserve_used = 0, 0, False

        t["last_close"] = ohlc["close"]
        t["last_date"] = ohlc["date"]
        w = closes[max(0, i - 199):i + 1]
        ma200 = sum(w) / len(w) if i >= 50 else None

        T = daily.calc_T(t)
        # 예비시드: 원금 소진 시 1회 한정으로 회차 추가
        if cfg.get("reserve") and not reserve_used and T >= t["divisions"] - 0.9:
            extra_rounds = int(t["divisions"] * 0.5)
            reserve_used = True
        if T >= t["divisions"] + extra_rounds - 0.9:
            exhausted += 1

        orders, _ = build_orders_ext(t, ticker, cfg, ma200, extra_rounds)

        # 현금 제약: 실제로 가진 돈 이상은 살 수 없다
        # (누적 실현손익까지 포함한 가용 현금으로 매수 수량을 제한)
        cash = total_capital + cum + t["realized_profit"] - t["total_bought"]
        capped = []
        for od in orders:
            if od["type"] == "LOC_BUY":
                afford = int(max(cash, 0) // max(od["price"], .01))
                od = {**od, "qty": min(od["qty"], afford)}
                cash -= od["qty"] * od["price"]
                if od["qty"] <= 0:
                    continue
            capped.append(od)
        orders = capped
        t["pending_orders"] = orders
        if t["shares"] > 0:
            cycle_days += 1
        peak_invested = max(peak_invested, t["total_bought"])

        mtm = t["shares"] * ohlc["close"] - t["total_bought"] + t["realized_profit"]
        equity.append(total_capital + cum + (mtm if t["shares"] > 0 else 0))

    # 미청산 포지션은 마지막 종가로 평가
    open_pnl = (t["shares"] * prices[-1]["close"] - t["total_bought"]
                + t["realized_profit"]) if t["shares"] > 0 else 0.0
    total = cum + open_pnl
    peak, mdd = -1e18, 0.0
    for eq in equity:
        peak = max(peak, eq)
        if peak > 0:
            mdd = min(mdd, (eq - peak) / peak * 100)
    cycles = t["history"]
    wins = [c for c in cycles if c["profit"] > 0]
    return {
        "total_pct": round(total / total_capital * 100, 1),
        "mdd_pct": round(mdd, 1),
        "cycles": len(cycles),
        "win_rate": round(len(wins) / len(cycles) * 100, 1) if cycles else None,
        "max_cycle_days": max(max_cycle_days, cycle_days),
        "exhausted_days": exhausted,
        "invested_peak_pct": round(peak_invested / seed * 100, 1),
        "open_position": t["shares"] > 0,
    }


STRATEGIES = {
    "base40":  {"kind": "ib", "version": "v2.2", "divisions": 40, "label": "v2.2 원본(40분할)"},
    "v30":     {"kind": "ib", "version": "v3.0", "divisions": 20, "label": "v3.0(20분할)"},
    "div80":   {"kind": "ib", "version": "v2.2", "divisions": 80, "label": "80분할(저속)"},
    "ma200":   {"kind": "ib", "version": "v2.2", "divisions": 40, "ma200": True,
                "label": "v2.2+200일선 필터"},
    "reserve": {"kind": "ib", "version": "v2.2", "divisions": 40, "reserve": True,
                "label": "v2.2+예비시드50%"},
    "ma_res":  {"kind": "ib", "version": "v2.2", "divisions": 40, "ma200": True,
                "reserve": True, "label": "200일선+예비시드"},
    "bh3x":    {"kind": "hold", "label": "3배 ETF 보유"},
}


# ---------------------------------------------------------------
# 실행
# ---------------------------------------------------------------
SKIPPED = []


def analyze(ticker, real, synth, index_prices=None):
    """국면마다 실물(가능하면) 또는 합성 시계열을 골라 재기준화 후 백테스트."""
    result = {"ticker": ticker, "regimes": []}
    real_start = real[0]["date"] if real else "9999"

    for name, start, end, kind in REGIMES:
        # 실물 데이터가 구간 전체를 덮으면 실물, 아니면 합성
        use_real = real and (start is None or start >= real_start) and name != "전체 기간"
        if name == "전체 기간":
            seg_raw, src = (real, "실물") if real else (synth, "합성")
        elif use_real:
            seg_raw, src = slice_period(real, start, end), "실물"
        else:
            seg_raw, src = slice_period(synth, start, end), "합성"
        if len(seg_raw) < 60:
            SKIPPED.append(f"{ticker}/{name}: 데이터 {len(seg_raw)}행뿐 — 제외")
            continue
        seg = rebase(seg_raw)

        row = {"name": name, "kind": kind, "source": src,
               "period": [seg[0]["date"], seg[-1]["date"]], "days": len(seg),
               "index_move_pct": round((seg[-1]["close"] / seg[0]["close"] - 1) * 100, 1),
               "results": {}}
        for key, cfg in STRATEGIES.items():
            try:
                row["results"][key] = run_strategy(seg, ticker, cfg)
            except Exception as e:
                row["results"][key] = {"error": str(e)}
        if index_prices:
            seg_i = slice_period(index_prices, row["period"][0], row["period"][1])
            if seg_i:
                row["index_1x_pct"] = round(
                    (seg_i[-1]["close"] / seg_i[0]["close"] - 1) * 100, 1)
        result["regimes"].append(row)
    return result


def to_markdown(all_results):
    L = ["# 국면별 백테스트 결과", "",
         f"생성: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", "",
         "> 2010년 이전 구간은 지수(^NDX/^SOX)로 합성한 3배 ETF입니다. "
         "운용보수·조달금리를 근사 반영했으나 실제와 오차가 있습니다.", "",
         "각 셀: **총손익%** (MDD%) · 최장사이클일 · 원금소진일수", ""]
    keys = list(STRATEGIES.keys())
    for res in all_results:
        L += [f"## {res['ticker']}", "",
              "| 국면 | 기간 | 데이터 | 3배지수 | " + " | ".join(
                  STRATEGIES[k]["label"] for k in keys) + " |",
              "|---|---|---|---|" + "---|" * len(keys)]
        for r in res["regimes"]:
            cells = []
            for k in keys:
                v = r["results"].get(k, {})
                if "error" in v:
                    cells.append("오류")
                elif v.get("cycles") is None:
                    cells.append(f"**{v['total_pct']}%** ({v['mdd_pct']}%)")
                else:
                    cells.append(f"**{v['total_pct']}%** ({v['mdd_pct']}%) · "
                                 f"{v['max_cycle_days']}일 · {v['exhausted_days']}일")
            L.append(f"| {r['name']} | {r['period'][0]}~{r['period'][1]} | "
                     f"{r.get('source','-')} | {r['index_move_pct']}% | "
                     + " | ".join(cells) + " |")
        L.append("")
    return "\n".join(L)


SOURCES = {
    "TQQQ": {"real": ["TQQQ"], "index": ["^NDX", "QQQ", "^IXIC"]},
    "SOXL": {"real": ["SOXL"], "index": ["^SOX", "SOXX", "^SOXX"]},
}


def main():
    os.makedirs(DOCS_DIR, exist_ok=True)
    all_results, notes = [], []
    for ticker, src in SOURCES.items():
        real, idx, synth = [], [], []
        try:
            _, real = fetch_history(src["real"])
            notes.append(f"{ticker} 실물: {real[0]['date']}~{real[-1]['date']} "
                         f"({len(real)}일)")
        except Exception as e:
            notes.append(f"{ticker} 실물 조회 실패: {e}")
        try:
            used, idx = fetch_history(src["index"])
            synth = synth_leveraged(idx)
            notes.append(f"{ticker} 합성 기준지수: {used} "
                         f"({idx[0]['date']}~{idx[-1]['date']}, {len(idx)}일)")
        except Exception as e:
            notes.append(f"{ticker} 지수 조회 실패: {e}")

        if not real and not synth:
            notes.append(f"{ticker}: 사용 가능한 데이터 없음 — 건너뜀")
            continue
        all_results.append(analyze(ticker, real, synth, idx))

    notes.extend(SKIPPED)
    for n in notes:
        print("[note]", n)
    with open(os.path.join(DATA_DIR, "regime_backtest.json"), "w", encoding="utf-8") as f:
        json.dump({"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "strategies": {k: v["label"] for k, v in STRATEGIES.items()},
                   "notes": notes, "results": all_results}, f,
                  ensure_ascii=False, indent=1)
    with open(os.path.join(DOCS_DIR, "REGIME_BACKTEST.md"), "w", encoding="utf-8") as f:
        f.write(to_markdown(all_results))
    print("완료: data/regime_backtest.json, docs/REGIME_BACKTEST.md")


if __name__ == "__main__":
    main()
