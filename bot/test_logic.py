# -*- coding: utf-8 -*-
"""로직 검증용 오프라인 테스트: 가짜 시세로 여러 날을 시뮬레이션한다.
실행: python bot/test_logic.py"""
import json, copy, sys, os
sys.path.insert(0, os.path.dirname(__file__))
import daily


def make_t(version="v2.2", seed=4000):
    return {
        "enabled": True, "version": version, "seed": seed,
        "divisions": 40 if version == "v2.2" else 20,
        "active": True, "shares": 0, "total_bought": 0.0, "avg_price": 0.0,
        "realized_profit": 0.0, "cycle_no": 1, "cycle_start": None,
        "one_buy_override": None, "last_close": None, "last_date": None,
        "pending_orders": [], "history": [],
    }


def step(t, ticker, ohlc, verbose=True):
    fills = daily.simulate_fills(t, ohlc)
    t["last_close"] = ohlc["close"]
    t["last_date"] = ohlc["date"]
    orders, T = daily.build_orders(t, ticker)
    t["pending_orders"] = orders
    if verbose:
        print(f"\n[{ohlc['date']}] 종가 ${ohlc['close']}  T={T} "
              f"보유 {t['shares']}주 평단 ${t['avg_price']:.2f} "
              f"누적 ${t['total_bought']:.0f}")
        for f in fills:
            print("   체결:", f)
        for o in orders:
            print("   내일 주문:", o["type"], o.get("price"), o["qty"], "-", o["memo"])
    return fills, orders, T


def scenario_downtrend():
    print("=" * 60)
    print("시나리오 1: v2.2 / TQQQ 하락 후 반등")
    t = make_t()
    prices = [50, 48, 45, 42, 40, 38, 41, 44, 47, 50, 53, 56]
    for i, c in enumerate(prices):
        ohlc = {"date": f"D{i:02d}", "open": c, "high": round(c * 1.02, 2),
                "low": round(c * 0.98, 2), "close": float(c)}
        step(t, "TQQQ", ohlc)
    assert t["shares"] >= 0
    print("\n사이클 이력:", t["history"])


def scenario_v3():
    print("=" * 60)
    print("시나리오 2: v3.0 / SOXL 급등 익절")
    t = make_t("v3.0")
    prices = [20, 19, 18, 17, 18, 20, 23, 26]
    for i, c in enumerate(prices):
        # 마지막 날 급등: 고가를 크게
        high = c * 1.25 if i == len(prices) - 1 else c * 1.02
        ohlc = {"date": f"D{i:02d}", "open": c, "high": round(high, 2),
                "low": round(c * 0.97, 2), "close": float(c)}
        step(t, "SOXL", ohlc)
    print("\n사이클 이력:", t["history"], "1회매수금 override:", t["one_buy_override"])


def sanity_checks():
    print("=" * 60)
    print("경계값 점검")
    t = make_t()
    # T=0 첫 매수
    t["last_close"] = 50.0
    orders, T = daily.build_orders(t, "TQQQ")
    assert orders[0]["type"] == "LOC_BUY" and orders[0]["qty"] == 2, orders
    # T=20 후반전 진입
    t.update({"shares": 40, "avg_price": 50.0, "total_bought": 2000.0})
    orders, T = daily.build_orders(t, "TQQQ")
    assert T == 20.0
    buys = [o for o in orders if o["type"] == "LOC_BUY"]
    assert len(buys) == 1, "후반전은 매수 1건"
    assert abs(buys[0]["price"] - 50.0 * (1 + (10 - 10) / 100)) < 0.01
    sells = [o for o in orders if "SELL" in o["type"]]
    assert sum(s["qty"] for s in sells) == 40, "전량 매도 주문"
    # 원금 소진 T>=39.1: 매수 없음
    t.update({"shares": 78, "avg_price": 50.3, "total_bought": 3920.0})
    orders, T = daily.build_orders(t, "TQQQ")
    assert T >= 39.1 and not [o for o in orders if o["type"] == "LOC_BUY"], (T, orders)
    # v3.0 쿼터모드
    t3 = make_t("v3.0")
    t3.update({"shares": 100, "avg_price": 39.0, "total_bought": 3900.0,
               "last_close": 35.0})
    orders, T = daily.build_orders(t3, "TQQQ")
    assert T == 19.5
    assert any(o["type"] == "MOC_SELL" for o in orders), orders
    assert not any(o["type"] == "LOC_BUY" for o in orders), "쿼터모드 매수 금지"
    print("경계값 점검 통과 ✅")


if __name__ == "__main__":
    scenario_downtrend()
    scenario_v3()
    sanity_checks()
    print("\n모든 테스트 통과 ✅")
