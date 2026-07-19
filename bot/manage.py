# -*- coding: utf-8 -*-
"""
상태 관리 명령 처리기
- 텔레그램 명령어와 GitHub Actions 수동 실행(workflow_dispatch) 양쪽에서 사용
- 사용법(CLI): python bot/manage.py "start TQQQ 3350 v2.2"

지원 명령
  start <종목> <시드$> [v2.2|v3.0]  : 투자 시작 (예: start TQQQ 3350)
  stop <종목>                       : 일시정지 (보유분은 그대로, 주문만 중단)
  resume <종목>                     : 재개
  seed <종목> <시드$>               : 시드 변경
  fix <종목> <보유수량> <평단가>    : 실제 잔고로 보정 (예: fix TQQQ 12 45.67)
  status                            : 현재 상태 요약
"""
import json
import os
import sys

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "state.json")


def load_state():
    with open(STATE_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def apply_command(state, text):
    """명령 문자열 1개를 처리. 반환: (성공 여부, 응답 메시지)"""
    parts = text.strip().lstrip("/").split()
    if not parts:
        return False, "빈 명령"
    cmd = parts[0].lower()

    if cmd in ("help", "도움말"):
        return True, (
            "사용 가능한 명령:\n"
            "/start TQQQ 3350 v2.2 — 투자 시작\n"
            "/stop TQQQ — 일시정지\n"
            "/resume TQQQ — 재개\n"
            "/seed TQQQ 4000 — 시드 변경\n"
            "/fix TQQQ 12 45.67 — 잔고 보정(수량, 평단)\n"
            "/status — 상태 확인")

    if cmd == "status":
        lines = []
        for k, t in state["tickers"].items():
            lines.append(
                f"{k}: {'▶️진행중' if t['active'] else '⏸중지'} {t['version']} "
                f"시드${t['seed']} | {t['shares']}주 평단${t['avg_price']:.2f}")
        return True, "\n".join(lines)

    if len(parts) < 2:
        return False, f"종목이 필요합니다. 예: /{cmd} TQQQ"
    ticker = parts[1].upper()
    if ticker not in state["tickers"]:
        return False, f"알 수 없는 종목: {ticker} (가능: {', '.join(state['tickers'])})"
    t = state["tickers"][ticker]

    if cmd == "start":
        if len(parts) < 3:
            return False, "시드 금액이 필요합니다. 예: /start TQQQ 3350"
        try:
            seed = float(parts[2])
        except ValueError:
            return False, f"시드 금액이 숫자가 아닙니다: {parts[2]}"
        if seed < 100:
            return False, "시드는 $100 이상이어야 합니다"
        version = parts[3] if len(parts) > 3 else "v2.2"
        if version not in ("v2.2", "v3.0"):
            return False, f"버전은 v2.2 또는 v3.0: {version}"
        if t["shares"] > 0:
            return False, (f"{ticker}는 이미 {t['shares']}주 보유 중입니다. "
                           "시드만 바꾸려면 /seed, 잔고 보정은 /fix를 사용하세요.")
        t.update({"enabled": True, "active": True, "version": version,
                  "divisions": 40 if version == "v2.2" else 20,
                  "seed": seed, "shares": 0, "total_bought": 0.0,
                  "avg_price": 0.0, "realized_profit": 0.0,
                  "one_buy_override": None, "cycle_start": None,
                  "pending_orders": []})
        one = seed / t["divisions"]
        return True, (f"✅ {ticker} 시작: 시드 ${seed:.0f}, {version} "
                      f"{t['divisions']}분할 (1회 매수금 ${one:.2f})\n"
                      "다음 봇 실행 때 첫 매수 가이드가 전송됩니다.")

    if cmd == "stop":
        t["active"] = False
        t["pending_orders"] = []
        note = (f" (보유 {t['shares']}주는 그대로 남아있습니다 — 매도는 직접 결정하세요)"
                if t["shares"] > 0 else "")
        return True, f"⏸ {ticker} 일시정지{note}"

    if cmd == "resume":
        t["active"] = True
        return True, f"▶️ {ticker} 재개. 다음 봇 실행 때 주문 가이드가 나옵니다."

    if cmd == "seed":
        if len(parts) < 3:
            return False, "예: /seed TQQQ 4000"
        try:
            seed = float(parts[2])
        except ValueError:
            return False, f"숫자가 아닙니다: {parts[2]}"
        t["seed"] = seed
        t["one_buy_override"] = None
        return True, f"✅ {ticker} 시드를 ${seed:.0f}로 변경 (1회 매수금 ${seed/t['divisions']:.2f})"

    if cmd == "fix":
        if len(parts) < 4:
            return False, "예: /fix TQQQ 12 45.67 (수량, 평단)"
        try:
            shares, avg = int(parts[2]), float(parts[3])
        except ValueError:
            return False, "수량은 정수, 평단은 숫자여야 합니다"
        t["shares"] = shares
        t["avg_price"] = avg
        t["total_bought"] = round(shares * avg, 2)
        t["pending_orders"] = []
        return True, (f"✅ {ticker} 잔고 보정: {shares}주, 평단 ${avg:.2f} "
                      f"(누적매수액 ${t['total_bought']:.2f})\n"
                      "다음 봇 실행 때 새 주문이 계산됩니다.")

    return False, f"알 수 없는 명령: {cmd} (/help 참고)"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python bot/manage.py \"start TQQQ 3350 v2.2\"")
        sys.exit(1)
    state = load_state()
    ok, msg = apply_command(state, " ".join(sys.argv[1:]))
    print(msg)
    if ok:
        save_state(state)
    sys.exit(0 if ok else 1)
