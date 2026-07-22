# -*- coding: utf-8 -*-
"""
상태 관리 명령 처리기 (다중 계좌 지원)
- 텔레그램 명령어와 GitHub Actions 수동 실행 양쪽에서 사용
- 사용법(CLI): python bot/manage.py "start sub1 TQQQ 3350 v2.2"

명령 형식: 계좌를 생략하면 기본 계좌(main)로 처리됩니다.
  start [계좌] <종목> <시드$> [v2.2|v3.0]
  stop [계좌] <종목> / resume [계좌] <종목>
  seed [계좌] <종목> <시드$>
  fix [계좌] <종목> <보유수량> <평단가>
  addaccount <계좌이름(영문/숫자/-)> [chat_id]
  delaccount <계좌>
  chatid <계좌> <chat_id|clear>
  status / help
"""
import copy
import json
import os
import re
import sys

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "state.json")

TICKER_TEMPLATE = {
    "enabled": True, "version": "v2.2", "seed": 0, "divisions": 40,
    "active": False, "shares": 0, "total_bought": 0.0, "avg_price": 0.0,
    "realized_profit": 0.0, "cycle_no": 1, "cycle_start": None,
    "one_buy_override": None, "last_close": None, "last_date": None,
    "pending_orders": [], "history": [],
}
KNOWN_TICKERS = ("TQQQ", "SOXL")


def load_state():
    with open(STATE_PATH, encoding="utf-8") as f:
        return migrate(json.load(f))


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def migrate(state):
    """구버전(단일 계좌) state.json을 다중 계좌 구조로 변환."""
    if "accounts" not in state:
        state["accounts"] = [{
            "id": "main", "name": "메인 계좌", "chat_id": "",
            "tickers": state.pop("tickers", {}),
        }]
    return state


def find_account(state, key):
    for a in state["accounts"]:
        if a["id"].lower() == str(key).lower() or a["name"] == key:
            return a
    return None


def _resolve(state, parts, default_account):
    """parts[1]이 계좌면 (계좌, 나머지), 아니면 (기본계좌, parts[1:])"""
    acc = find_account(state, parts[1]) if len(parts) > 1 else None
    if acc is not None and (len(parts) < 2 or parts[1].upper() not in KNOWN_TICKERS):
        return acc, parts[2:]
    return find_account(state, default_account), parts[1:]


def apply_command(state, text, default_account="main"):
    """명령 문자열 1개 처리. 반환: (성공 여부, 응답 메시지)"""
    migrate(state)
    parts = text.strip().lstrip("/").split()
    if not parts:
        return False, "빈 명령"
    cmd = parts[0].lower()

    if cmd in ("help", "도움말"):
        return True, (
            "사용 가능한 명령 (계좌 생략 시 main):\n"
            "/start [계좌] TQQQ 3350 v2.2 — 시작\n"
            "/stop [계좌] TQQQ — 일시정지\n"
            "/resume [계좌] TQQQ — 재개\n"
            "/seed [계좌] TQQQ 4000 — 시드 변경\n"
            "/fix [계좌] TQQQ 12 45.67 — 잔고 보정\n"
            "/addaccount sub1 [chat_id] — 계좌 추가\n"
            "/delaccount sub1 — 계좌 삭제\n"
            "/chatid sub1 123456 — 계좌별 텔레그램 지정 (clear로 해제)\n"
            "/status — 전체 상태")

    if cmd == "status":
        lines = []
        for a in state["accounts"]:
            tg = f" (텔레그램: {a['chat_id']})" if a.get("chat_id") else " (텔레그램: 메인)"
            lines.append(f"📁 {a['name']} [{a['id']}]{tg}")
            for k, t in a["tickers"].items():
                lines.append(
                    f"  {k}: {'▶️' if t['active'] else '⏸'} {t['version']} "
                    f"시드${t['seed']:.0f} | {t['shares']}주 평단${t['avg_price']:.2f}")
        return True, "\n".join(lines)

    if cmd == "addaccount":
        if len(parts) < 2:
            return False, "계좌 이름이 필요합니다. 예: /addaccount sub1"
        name = parts[1]
        if not re.fullmatch(r"[A-Za-z0-9가-힣_-]{1,20}", name):
            return False, "계좌 이름은 공백 없이 20자 이내 (영문/숫자/한글/-/_)"
        if find_account(state, name):
            return False, f"이미 존재하는 계좌: {name}"
        acc_id = re.sub(r"[^A-Za-z0-9_-]", "", name.lower()) or f"acc{len(state['accounts'])+1}"
        if find_account(state, acc_id):
            acc_id = f"{acc_id}{len(state['accounts'])+1}"
        chat = parts[2] if len(parts) > 2 else ""
        state["accounts"].append({
            "id": acc_id, "name": name, "chat_id": chat,
            "tickers": {k: copy.deepcopy(TICKER_TEMPLATE) for k in KNOWN_TICKERS},
        })
        tg = f"전용 chat_id {chat}" if chat else "메인 계좌의 텔레그램으로 전송"
        return True, (f"✅ 계좌 추가: {name} [{acc_id}] ({tg})\n"
                      f"시작하려면: /start {acc_id} TQQQ 3350")

    if cmd == "delaccount":
        if len(parts) < 2:
            return False, "예: /delaccount sub1"
        acc = find_account(state, parts[1])
        if not acc:
            return False, f"계좌를 찾을 수 없음: {parts[1]}"
        if acc["id"] == "main":
            return False, "메인 계좌는 삭제할 수 없습니다"
        holding = [k for k, t in acc["tickers"].items() if t["shares"] > 0]
        if holding:
            return False, f"보유 중인 종목({', '.join(holding)})이 있어 삭제 불가. 먼저 정리하세요."
        state["accounts"] = [a for a in state["accounts"] if a["id"] != acc["id"]]
        return True, f"✅ 계좌 삭제됨: {acc['name']}"

    if cmd == "chatid":
        if len(parts) < 3:
            return False, "예: /chatid sub1 123456789 (해제: /chatid sub1 clear)"
        acc = find_account(state, parts[1])
        if not acc:
            return False, f"계좌를 찾을 수 없음: {parts[1]}"
        acc["chat_id"] = "" if parts[2].lower() == "clear" else parts[2]
        return True, (f"✅ {acc['name']} 텔레그램: "
                      f"{acc['chat_id'] or '메인 계좌로 전송'}")

    # ---------- 이하 종목 명령 (계좌 선택적) ----------
    acc, rest = _resolve(state, parts, default_account)
    if acc is None:
        return False, f"계좌를 찾을 수 없음: {parts[1] if len(parts)>1 else default_account}"
    if not rest:
        return False, f"종목이 필요합니다. 예: /{cmd} {acc['id']} TQQQ"
    ticker = rest[0].upper()
    if ticker not in acc["tickers"]:
        return False, f"[{acc['name']}] 알 수 없는 종목: {ticker}"
    t = acc["tickers"][ticker]
    tag = f"[{acc['name']}] {ticker}"

    if cmd == "start":
        if len(rest) < 2:
            return False, f"시드가 필요합니다. 예: /start {acc['id']} {ticker} 3350"
        try:
            seed = float(rest[1])
        except ValueError:
            return False, f"시드가 숫자가 아닙니다: {rest[1]}"
        if seed < 100:
            return False, "시드는 $100 이상이어야 합니다"
        version = rest[2] if len(rest) > 2 else "v2.2"
        if version not in ("v2.2", "v3.0"):
            return False, f"버전은 v2.2 또는 v3.0: {version}"
        if t["shares"] > 0:
            return False, f"{tag}는 이미 {t['shares']}주 보유 중. /seed 또는 /fix 사용."
        t.update({"enabled": True, "active": True, "version": version,
                  "divisions": 40 if version == "v2.2" else 20,
                  "seed": seed, "shares": 0, "total_bought": 0.0,
                  "avg_price": 0.0, "realized_profit": 0.0,
                  "one_buy_override": None, "cycle_start": None,
                  "pending_orders": []})
        return True, (f"✅ {tag} 시작: 시드 ${seed:.0f}, {version} "
                      f"{t['divisions']}분할 (1회 ${seed/t['divisions']:.2f})")

    if cmd == "stop":
        t["active"] = False
        t["pending_orders"] = []
        note = f" (보유 {t['shares']}주 유지)" if t["shares"] > 0 else ""
        return True, f"⏸ {tag} 일시정지{note}"

    if cmd == "resume":
        t["active"] = True
        return True, f"▶️ {tag} 재개"

    if cmd == "seed":
        if len(rest) < 2:
            return False, f"예: /seed {acc['id']} {ticker} 4000"
        try:
            seed = float(rest[1])
        except ValueError:
            return False, f"숫자가 아닙니다: {rest[1]}"
        t["seed"] = seed
        t["one_buy_override"] = None
        return True, f"✅ {tag} 시드 ${seed:.0f} (1회 ${seed/t['divisions']:.2f})"

    if cmd == "fix":
        if len(rest) < 3:
            return False, f"예: /fix {acc['id']} {ticker} 12 45.67"
        try:
            shares, avg = int(rest[1]), float(rest[2])
        except ValueError:
            return False, "수량은 정수, 평단은 숫자여야 합니다"
        t["shares"] = shares
        t["avg_price"] = avg
        t["total_bought"] = round(shares * avg, 2)
        t["pending_orders"] = []
        return True, (f"✅ {tag} 보정: {shares}주, 평단 ${avg:.2f} "
                      f"(누적 ${t['total_bought']:.2f})")

    return False, f"알 수 없는 명령: {cmd} (/help 참고)"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python bot/manage.py \"start [계좌] TQQQ 3350\"")
        sys.exit(1)
    state = load_state()
    ok, msg = apply_command(state, " ".join(sys.argv[1:]))
    print(msg)
    if ok:
        save_state(state)
    sys.exit(0 if ok else 1)
