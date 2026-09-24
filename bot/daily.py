# -*- coding: utf-8 -*-
"""
무한매수법 데일리 봇 (v2.2 / v3.0)
- 매일 미국장 마감 후 GitHub Actions에서 실행됩니다.
- 하는 일:
  1. 어제 걸어둔 주문(pending_orders)이 종가/고가 기준으로 체결됐는지 자동 판정
  2. 포지션(평단가, 누적매수액, 보유수량) 업데이트
  3. 오늘 밤 걸어야 할 주문 계산
  4. data/state.json 저장 (웹앱이 이 파일을 읽어서 표시)
  5. 텔레그램으로 주문 가이드 전송

표준 라이브러리만 사용합니다 (pip 설치 불필요).
"""
import json
import math
import os
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timezone

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "state.json")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


# ---------------------------------------------------------------
# 시세 조회
# ---------------------------------------------------------------
def fetch_ohlc(ticker):
    """가장 최근 '완료된' 거래일 1건 (호환용)."""
    return fetch_recent(ticker)[-1]


def fetch_recent(ticker, days=20):
    """최근 거래일들의 OHLC 목록(오래된 → 최신).
    하루라도 실행이 누락되면 그날 체결이 영영 반영되지 않으므로,
    한 건이 아니라 '최근 여러 날'을 받아 미정산분을 모두 따라잡는다."""
    try:
        rows = _fetch_yahoo(ticker)
    except Exception as e:
        print(f"[warn] yahoo 실패({ticker}): {e} -> stooq 폴백")
        rows = _fetch_stooq(ticker)
    if not rows:
        raise RuntimeError(f"{ticker}: 시세 없음")
    return rows[-days:]


def _fetch_yahoo(ticker):
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{ticker}?range=1mo&interval=1d"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    result = data["chart"]["result"][0]
    ts = result["timestamp"]
    q = result["indicators"]["quote"][0]
    out = []
    for i in range(len(ts)):
        if any(q[k][i] is None for k in ("open", "high", "low", "close")):
            continue  # 장중 미완성 캔들 등은 제외
        out.append({
            "date": datetime.fromtimestamp(ts[i], tz=timezone.utc).strftime("%Y-%m-%d"),
            "open": round(q["open"][i], 4),
            "high": round(q["high"][i], 4),
            "low": round(q["low"][i], 4),
            "close": round(q["close"][i], 4),
        })
    if not out:
        raise RuntimeError("yahoo: 유효한 캔들 없음")
    return out


def _fetch_stooq(ticker):
    url = f"https://stooq.com/q/d/l/?s={ticker.lower()}.us&i=d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        lines = r.read().decode().strip().splitlines()[1:]
    out = []
    for ln in lines[-40:]:
        p = ln.split(",")  # Date,Open,High,Low,Close,Volume
        try:
            out.append({"date": p[0], "open": float(p[1]), "high": float(p[2]),
                        "low": float(p[3]), "close": float(p[4])})
        except (ValueError, IndexError):
            continue
    return out


# ---------------------------------------------------------------
# 체결 판정 (LOC / 지정가 / MOC)
# ---------------------------------------------------------------
def simulate_fills(t, ohlc):
    """어제 걸어둔 주문이 오늘 캔들에서 체결됐는지 판정하고 포지션을 갱신한다.
    반환: 체결 내역 리스트"""
    fills = []
    close, high = ohlc["close"], ohlc["high"]

    for od in t.get("pending_orders", []):
        typ, price, qty = od["type"], od.get("price"), od["qty"]
        if qty <= 0:
            continue
        if typ == "LOC_BUY" and close <= price:
            _apply_buy(t, qty, close)
            fills.append(("매수(LOC)", qty, close))
        elif typ == "LOC_SELL" and close >= price:
            _apply_sell(t, qty, close)
            fills.append(("매도(LOC)", qty, close))
        elif typ == "LIMIT_SELL" and high >= price:
            _apply_sell(t, qty, price)
            fills.append(("매도(지정가)", qty, price))
        elif typ == "MOC_SELL":
            _apply_sell(t, qty, close)
            fills.append(("매도(MOC)", qty, close))
    return fills


def _apply_buy(t, qty, price):
    t["total_bought"] = round(t["total_bought"] + qty * price, 2)
    t["shares"] += qty
    t["avg_price"] = round(t["total_bought"] / t["shares"], 4)


def _apply_sell(t, qty, price):
    qty = min(qty, t["shares"])
    if qty == 0:
        return
    profit = round((price - t["avg_price"]) * qty, 2)
    t["realized_profit"] = round(t["realized_profit"] + profit, 2)
    t["shares"] -= qty
    if t["shares"] == 0:
        # 사이클 종료 → 리셋
        t["history"].append({
            "cycle": t["cycle_no"],
            "end": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "profit": t["realized_profit"],
        })
        cycle_profit = t["realized_profit"]
        t["cycle_no"] += 1
        t["total_bought"] = 0.0
        t["avg_price"] = 0.0
        t["cycle_start"] = None
        # v3.0: 수익금을 40분할해 다음 1회 매수금에 복리 반영
        if t["version"] == "v3.0" and cycle_profit > 0:
            base = t["seed"] / t["divisions"]
            t["one_buy_override"] = round(base + cycle_profit / 40, 2)
        t["realized_profit"] = 0.0
    else:
        # 부분(쿼터) 매도: 남은 수량 기준으로 누적매수액 재계산 (평단 유지)
        t["total_bought"] = round(t["avg_price"] * t["shares"], 2)


def compute_ma200(ticker, latest_ohlc=None):
    """200일 이동평균. 히스토리 수집 실패 시 None(=필터 미적용)."""
    try:
        import backtest
        hist = backtest.fetch_history(ticker, latest_ohlc)
        closes = [p["close"] for p in hist[-200:]]
        if len(closes) < 100:
            return None
        return sum(closes) / len(closes)
    except Exception as e:
        print(f"[warn] {ticker} 200일선 계산 실패(필터 미적용): {e}")
        return None


# ---------------------------------------------------------------
# T값과 오늘의 주문 계산
# ---------------------------------------------------------------
def calc_T(t):
    one_buy = one_buy_amount(t)
    if one_buy <= 0:
        return 0.0
    raw = t["total_bought"] / one_buy
    return math.ceil(raw * 10) / 10  # 소수점 둘째 자리에서 올림


def one_buy_amount(t):
    if t.get("one_buy_override"):
        return t["one_buy_override"]
    return t["seed"] / t["divisions"]


def qty_for(amount, price):
    if price <= 0:
        return 0
    q = round(amount / price)
    return max(q, 1) if amount > price * 0.5 else q


def build_orders(t, ticker, ma200=None):
    """내일(오늘 밤) 걸 주문 목록 생성.
    ma200이 주어지고 200일선 필터가 켜져 있으면, 종가가 200일선 아래일 때
    '신규 사이클 시작'을 보류한다 (이미 보유 중이면 규칙대로 계속 진행)."""
    orders = []
    close = t["last_close"]
    one_buy = one_buy_amount(t)
    T = calc_T(t)
    avg = t["avg_price"]
    ver = t["version"]

    if not t["active"]:
        return orders, T

    # ---------- 사이클 첫 매수 ----------
    if t["shares"] == 0:
        if ma200 is not None and close < ma200:
            return [], T   # 하락 추세 — 신규 진입 보류
        # 첫날: 종가 대비 +15% LOC → 사실상 종가에 1회분 체결
        price = round(close * 1.15, 2)
        orders.append({"type": "LOC_BUY", "price": price,
                       "qty": qty_for(one_buy, close),
                       "memo": "사이클 시작: 1회분 매수"})
        return orders, T

    # ---------- 매수 ----------
    # 분할 수를 바꿔도(예: 80분할) 원본 공식을 그대로 쓰기 위해
    # 진행률을 기준 분할(v2.2=40, v3.0=20)로 환산한다.
    #   T_eff = T × 기준분할 / 실제분할  → 사이클 중간에 분할을 바꿔도 진행률 유지
    base_div = 40 if ver == "v2.2" else 20
    T_eff = T * base_div / max(t.get("divisions", base_div), 1)

    if ver == "v2.2":
        pct = (10 - T_eff / 2) / 100
    else:  # v3.0
        pct = (15 - 1.5 * T_eff) / 100
    max_T = base_div - 0.9
    half_line = base_div / 2

    buy_star = round(avg * (1 + pct), 2)  # '별가격'
    if T_eff < max_T:
        half_amt = one_buy / 2
        if T_eff < half_line:  # 전반전
            orders.append({"type": "LOC_BUY", "price": round(avg, 2),
                           "qty": qty_for(half_amt, close), "memo": "전반전: 평단가 LOC"})
            orders.append({"type": "LOC_BUY", "price": buy_star,
                           "qty": qty_for(half_amt, close),
                           "memo": f"전반전: 평단+{pct*100:.1f}% LOC"})
        else:  # 후반전
            orders.append({"type": "LOC_BUY", "price": buy_star,
                           "qty": qty_for(one_buy, close),
                           "memo": f"후반전: 평단{pct*100:+.1f}% LOC"})

    # ---------- 매도 ----------
    q_quarter = max(t["shares"] // 4, 1)
    q_rest = t["shares"] - q_quarter

    if ver == "v2.2":
        sell_limit = round(avg * 1.10, 2)
        orders.append({"type": "LOC_SELL", "price": buy_star, "qty": q_quarter,
                       "memo": f"1/4 매도: 평단{pct*100:+.1f}% LOC"})
        if q_rest > 0:
            orders.append({"type": "LIMIT_SELL", "price": sell_limit, "qty": q_rest,
                           "memo": "3/4 매도: 평단+10% 지정가"})
    else:  # v3.0
        up = 1.15 if ticker == "TQQQ" else 1.20
        sell_limit = round(avg * up, 2)
        if T_eff <= base_div - 1:
            orders.append({"type": "LOC_SELL", "price": buy_star, "qty": q_quarter,
                           "memo": f"1/4 매도: 평단{pct*100:+.1f}% LOC"})
        else:
            # 쿼터모드: 1/4 MOC 매도, 매수 없음
            orders = [o for o in orders if not o["type"].endswith("BUY")]
            orders.append({"type": "MOC_SELL", "qty": q_quarter,
                           "memo": "쿼터모드: 1/4 MOC 매도"})
        if q_rest > 0:
            orders.append({"type": "LIMIT_SELL", "price": sell_limit, "qty": q_rest,
                           "memo": f"3/4 매도: 평단+{(up-1)*100:.0f}% 지정가"})

    return orders, T


# ---------------------------------------------------------------
# 텔레그램
# ---------------------------------------------------------------
def send_telegram(text, chat_id=None):
    chat = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_TOKEN or not chat:
        print("[warn] 텔레그램 시크릿 미설정 — 전송 생략")
        print(text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": chat,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("telegram:", r.status, "->", chat)
    except Exception as e:
        print(f"[warn] 텔레그램 전송 실패({chat}): {e}")


ORDER_LABEL = {
    "LOC_BUY": "🔵 LOC 매수",
    "LOC_SELL": "🔴 LOC 매도",
    "LIMIT_SELL": "🔴 지정가 매도",
    "MOC_SELL": "🔴 MOC 매도",
}


def format_message(account, report):
    lines = [f"<b>📈 무한매수법 데일리 가이드 — {account['name']}</b>",
             f"기준일(미국장): {report['date']}", ""]
    for ticker, r in report["tickers"].items():
        t = account["tickers"][ticker]
        lines.append(f"<b>━━ {ticker} ({t['version']}) ━━</b>")
        lines.append(f"종가 ${r['close']:.2f} | 평단 ${t['avg_price']:.2f} | "
                     f"보유 {t['shares']}주 | T={r['T']}")
        sd = r.get("settled_days") or []
        if len(sd) > 1:
            lines.append(f"<i>⏳ 미정산 {len(sd)}일치를 한 번에 반영: "
                         f"{sd[0]} ~ {sd[-1]}</i>")
        if r["fills"]:
            lines.append("<i>체결(추정):</i>")
            for f in r["fills"]:
                name, qty, price = f[0], f[1], f[2]
                when = f" [{f[3]}]" if len(f) > 3 and len(sd) > 1 else ""
                lines.append(f"  ✅{when} {name} {qty}주 @ ${price:.2f}")
        else:
            lines.append("<i>체결 없음(추정)</i>")
        if r.get("ma200"):
            mark = "위 ✅" if r["close"] >= r["ma200"] else "아래 ⚠️"
            lines.append(f"200일선 ${r['ma200']:.2f} · 종가는 {mark} (필터 ON)")
        if r["orders"]:
            lines.append("<b>오늘 밤 걸어둘 주문:</b>")
            for od in r["orders"]:
                p = f" @ ${od['price']:.2f}" if od.get("price") else ""
                lines.append(f"  {ORDER_LABEL[od['type']]} {od['qty']}주{p}")
                lines.append(f"     └ {od['memo']}")
        elif t["active"] and t["shares"] == 0 and r.get("ma200") \
                and r["close"] < r["ma200"]:
            lines.append("⏸ <b>신규 진입 보류</b> — 종가가 200일선 아래입니다.")
            lines.append("   (200일선 위로 회복하면 자동으로 사이클을 시작합니다)")
        else:
            lines.append("오늘 주문 없음 (비활성 상태)")
        bt = r.get("backtest")
        if bt and bt.get("signal") and bt["signal"].get("available"):
            s = bt["signal"]
            ma = "200일선 위" if s["above_ma"] else "200일선 아래"
            lines.append(f"📊 <b>백테스트 신호</b>: 유지 유리 확률 "
                         f"<b>{s['prob_hold_better']}%</b> "
                         f"(유사상황 {s['n']}일, {ma})")
            lines.append(f"   유지 시 평균 {s['avg_extra_pct']:+.1f}%p, "
                         f"최악권(하위10%) {s['p10_extra_pct']:+.1f}%p")
        elif bt and bt.get("entry") and bt["entry"].get("available"):
            e = bt["entry"]
            lines.append(f"📊 <b>진입 참고</b>: {e['regime']} 시작 사이클 과거 승률 "
                         f"{e['win_rate']}% (표본 {e['n']}개)")
        lines.append("")
    if report.get("stale_days", 0) >= 4:
        lines.append(f"🚨 <b>시세 데이터가 {report['stale_days']}일 지연</b>되고 있습니다. "
                     "Actions 탭에서 워크플로 실패 여부를 확인하세요.")
    lines.append("⚠️ 실제 체결 내역을 증권사 앱에서 꼭 확인하세요.")
    lines.append("체결이 다르면 data/state.json을 수정 후 다시 실행하세요.")
    return "\n".join(lines)


# ---------------------------------------------------------------
# 텔레그램 명령 수신 (/start, /stop, /fix, /seed, /status ...)
# ---------------------------------------------------------------
def poll_telegram_commands(state):
    """봇 대화방에 쌓인 명령을 읽어 상태에 반영하고 확인 메시지를 보낸다.
    계좌별 chat_id가 등록되어 있으면 그 방에서 온 명령은 해당 계좌가 기본값."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    import manage
    # 허용된 대화방 → 기본 계좌 매핑
    chat_map = {str(TELEGRAM_CHAT_ID): "main"}
    for acc in state.get("accounts", []):
        if acc.get("chat_id"):
            chat_map.setdefault(str(acc["chat_id"]), acc["id"])

    offset = state.get("tg_offset", 0)
    url = (f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
           f"?offset={offset + 1}&timeout=0")
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as r:
            updates = json.load(r).get("result", [])
    except Exception as e:
        print(f"[warn] getUpdates 실패: {e}")
        return

    replies = {}  # chat_id -> [text]
    for upd in updates:
        state["tg_offset"] = upd["update_id"]
        msg = upd.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        if chat_id not in chat_map:
            continue  # 등록되지 않은 대화방 무시 (보안)
        if not text.startswith("/") or text.startswith("/start@"):
            continue
        if text == "/start":  # 텔레그램 기본 /start는 도움말로 처리
            text = "/help"
        ok, reply = manage.apply_command(state, text,
                                         default_account=chat_map[chat_id])
        replies.setdefault(chat_id, []).append(
            ("✅ " if ok else "❌ ") + f"<code>{text}</code>\n{reply}")

    for chat_id, msgs in replies.items():
        send_telegram("<b>🛠 명령 처리 결과</b>\n\n" + "\n\n".join(msgs), chat_id)


# ---------------------------------------------------------------
# 메인
# ---------------------------------------------------------------
def main():
    import manage
    with open(STATE_PATH, encoding="utf-8") as f:
        state = manage.migrate(json.load(f))

    # 먼저 텔레그램 명령을 반영 (시작/중지/보정/계좌추가 등)
    poll_telegram_commands(state)

    ohlc_cache = {}  # 종목별 시세는 한 번만 조회
    ma_cache = {}    # 종목별 200일선

    for account in state["accounts"]:
        report = {"date": None, "tickers": {}}

        for ticker, t in account["tickers"].items():
            if not t.get("enabled"):
                continue
            if ticker not in ohlc_cache:
                ohlc_cache[ticker] = fetch_recent(ticker)
            rows = ohlc_cache[ticker]
            ohlc = rows[-1]

            # 200일선 (필터 사용 시에만 계산)
            ma200 = None
            if t.get("ma200_filter"):
                if ticker not in ma_cache:
                    ma_cache[ticker] = compute_ma200(ticker, ohlc)
                ma200 = ma_cache[ticker]

            # ── 미정산 거래일을 날짜순으로 모두 따라잡는다 ──
            # (실행이 하루 걸러뛰거나 시세가 하루 늦게 들어와도 체결이 누락되지 않음)
            last = t.get("last_date")
            pending_days = [r for r in rows if not last or r["date"] > last]
            fills = []
            for day in pending_days:
                for f in simulate_fills(t, day):
                    fills.append((*f, day["date"]))
                t["last_close"] = day["close"]
                t["last_date"] = day["date"]
                if t["active"] and t["shares"] > 0 and not t.get("cycle_start"):
                    t["cycle_start"] = day["date"]
                orders, T = build_orders(t, ticker, ma200)
                t["pending_orders"] = orders

            if not pending_days:      # 새 거래일 없음 → 주문만 최신화
                t["last_close"] = ohlc["close"]
                orders, T = build_orders(t, ticker, ma200)
                t["pending_orders"] = orders
            ohlc = {"date": t["last_date"], "close": t["last_close"]}

            # 백테스트 + 유지/중단 신호 (실패해도 데일리 가이드는 계속)
            bt = None
            try:
                import backtest
                bt = backtest.run_for_ticker(ticker, t, account["id"],
                                             latest_ohlc=ohlc)
            except Exception:
                import traceback
                print(f"[warn] {account['id']}/{ticker} 백테스트 실패:")
                traceback.print_exc()

            t["ma200"] = round(ma200, 2) if ma200 else None
            report["date"] = ohlc["date"]
            report["tickers"][ticker] = {
                "close": ohlc["close"], "T": T, "fills": fills,
                "orders": orders, "backtest": bt, "ma200": ma200,
                "settled_days": [d["date"] for d in pending_days],
            }

        # 활성/보유 종목이 하나라도 있으면 계좌별 메시지 전송
        # (chat_id가 비어있으면 메인 계좌의 텔레그램으로)
        if report["date"]:
            from datetime import date as _date
            y, m, d = map(int, report["date"].split("-"))
            report["stale_days"] = (datetime.now(timezone.utc).date()
                                    - _date(y, m, d)).days
        if any(t["active"] or t["shares"] > 0
               for t in account["tickers"].values()):
            msg = format_message(account, report)
            send_telegram(msg, account.get("chat_id") or None)

    state["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    print("완료")


if __name__ == "__main__":
    main()
