# 📈 무한매수법 자동 가이드 (Infinite Buying Guide)

라오어의 무한매수법(v2.2 / v3.0)을 **API 없는 증권사**(토스증권, 한국투자증권)에서
실행할 수 있도록 도와주는 도구입니다.

- 🤖 **GitHub Actions 봇**: 매일 미국장 마감 후 종가를 수집 → 체결 여부 자동 판정
  → 오늘 밤 걸 주문을 계산 → **텔레그램으로 전송**
- 🖥 **웹앱 (GitHub Pages)**: 사이클 현황판 + 수동 계산기 + 설정 생성기
- 📊 상태는 `data/state.json` 파일 하나로 관리 (봇이 자동 커밋)

> ⚠️ 이 도구는 매수/매도 **가이드만** 제공합니다. 실제 주문은 본인이 직접 넣습니다.
> 시작 전에 [docs/STRATEGY.md](docs/STRATEGY.md)의 리스크와 대비책을 꼭 읽으세요.

---

## 1. 설치 (1회, 약 10분)

### 1-1. 저장소 만들기

1. GitHub 로그인 → 우측 상단 **+** → **New repository**
2. 이름 예: `infinite-buying`, **Private 권장** (투자 기록이 담기므로)
3. 이 폴더의 모든 파일을 업로드:
   - 방법 A (웹): 저장소 페이지에서 **Add file → Upload files**로 전체 폴더 드래그
   - 방법 B (git):
     ```bash
     cd infinite-buying
     git init && git add . && git commit -m "init"
     git branch -M main
     git remote add origin https://github.com/<내아이디>/infinite-buying.git
     git push -u origin main
     ```
   ⚠️ `.github` 폴더는 숨김 폴더입니다. 웹 업로드 시 누락되기 쉬우니
   업로드 후 저장소에 `.github/workflows/daily.yml`이 있는지 확인하세요.

### 1-2. 텔레그램 봇 만들기 + 시크릿 등록

**새 봇 만들기 (BotFather):**

1. 텔레그램에서 `@BotFather` 검색 → 대화 시작
2. `/newbot` 입력 → 봇 이름 입력 (예: `내 무한매수 알리미`)
3. 봇 아이디 입력 — 반드시 `bot`으로 끝나야 함 (예: `my_infinite_buy_bot`)
4. BotFather가 주는 **토큰**(`123456:ABC-DEF...`) 복사
5. 만들어진 봇과의 대화방을 열고 **/start를 꼭 누르기**
   (이걸 안 하면 봇이 메시지를 보낼 수 없습니다)

**등록 (2가지 방법):**

- 방법 A (웹앱, 편함): 웹앱 → **"📱 텔레그램 설정"** → 봇 토큰 입력 →
  봇에게 아무 메시지 전송 → **chat_id 자동 감지** → **Secrets에 저장** →
  **봇 실행 테스트**. (GitHub 토큰에 Secrets 권한 필요, §2-B 참고)
- 방법 B (GitHub 웹): Settings → Secrets and variables → Actions →
  `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 두 개 등록.
  chat_id는 봇에게 메시지를 보낸 뒤
  `https://api.telegram.org/bot<토큰>/getUpdates`에서 `"chat":{"id":숫자}` 확인.

> chat_id가 바뀌었을 때(새 계정, 새 봇 등)도 방법 A로 언제든 갱신하면 됩니다.

### 1-3. GitHub Pages 켜기 (웹앱)

저장소 → **Settings → Pages → Source: Deploy from a branch →
Branch: main / (root) → Save**

몇 분 후 `https://<내아이디>.github.io/infinite-buying/` 에서 웹앱이 열립니다.
(Private 저장소의 Pages는 유료 플랜이 필요할 수 있습니다. 무료로 쓰려면
저장소를 Public으로 하되, 시드 금액이 노출된다는 점만 감안하세요.)

### 1-4. Actions 권한 확인

저장소 → **Settings → Actions → General → Workflow permissions →
"Read and write permissions"** 선택 후 Save.
(봇이 state.json을 커밋하려면 필요합니다)

---

## 2. 투자 시작/제어 — 3가지 방법

### 방법 A. 텔레그램 명령 (가장 편함) ⭐

봇 대화방에 명령을 보내면 **다음 봇 실행 때** 자동 반영됩니다.
(즉시 반영하려면 명령 후 Actions에서 Run workflow 한 번)

```
/start TQQQ 3350        ← 시드 $3,350로 시작 (v2.2 기본)
/start SOXL 3350 v3.0   ← 버전 지정도 가능
/stop TQQQ              ← 일시정지 (보유분 유지)
/resume TQQQ            ← 재개
/seed TQQQ 4000         ← 시드 변경
/fix TQQQ 12 45.67      ← 실제 잔고로 보정 (수량, 평단)
/status                 ← 상태 확인
/help                   ← 명령 목록
```

### 방법 B. 웹앱 원격 명령 ⭐

웹앱 상단 **"🕹 원격 명령"** 카드에서 작업/종목/금액 선택 → 실행.
즉시 적용되고 텔레그램으로 결과가 옵니다.

최초 1회 설정: Fine-grained 토큰(이 저장소만 선택)을 만들어 카드 하단
"🔑 GitHub 토큰 설정"에 저장. 권한 3개:

| 권한 | 용도 |
|---|---|
| **Actions: Read and write** | 원격 명령 실행, 봇 테스트 |
| **Contents: Read-only** | Private 저장소/로컬에서도 현황판 표시 |
| **Secrets: Read and write** | 웹에서 텔레그램 토큰/chat_id 변경 |

토큰은 해당 브라우저에만 저장되며 저장소에는 올라가지 않습니다.
공용 PC에서는 저장하지 마세요.

### 방법 B-2. Actions 버튼

저장소 → **Actions** → **"관리 (시작·중지·시드·보정)"** → **Run workflow** →
드롭다운에서 작업/종목 선택, 금액 입력 → 실행.

### 방법 C. JSON 직접 편집 (백업용)

웹앱의 "⚙️ 시작 설정 생성기"로 만든 내용을 `data/state.json`에 붙여넣고 커밋.

## 3. 매일 루틴 (3분)

1. **아침 7시 10분경** 텔레그램으로 가이드 도착
   (미국장 마감 직후, 화~토요일)
2. 메시지의 "어젯밤 체결(추정)"과 증권사 체결 내역이 맞는지 확인
3. "오늘 밤 걸어둘 주문"을 그대로 증권사 앱에 입력
   - 한국투자증권: 주문 유형에서 **LOC** 선택
   - 토스증권: 주문 방식에서 **LOC(종가 지정가)** 선택
4. 끝. 판단하지 말 것 — 규칙 이탈이 가장 큰 리스크입니다.

## 4. 체결이 다르거나 주문을 빼먹었을 때

봇의 체결 추정은 종가/고가 기반 시뮬레이션이라 실제와 다를 수 있습니다.
증권사 앱에서 실제 보유수량과 평단을 확인한 뒤 텔레그램에서:

```
/fix TQQQ 12 45.67   (실제 수량, 실제 평단)
```

**매주 토요일 잔고 대조를 습관화하세요.**

## 5. 일시정지 / 종료

- 일시정지: `/stop TQQQ` → 재개: `/resume TQQQ`
- v2.2 ↔ v3.0 전환: 사이클이 **끝난 뒤(보유 0주)** `/start TQQQ 3350 v3.0`

## 6. 폴더 구조

```
infinite-buying/
├── index.html              # 웹앱 (현황판 + 수동 계산기 + 설정 생성기)
├── data/state.json         # 모든 상태 (봇이 자동 갱신)
├── bot/daily.py            # 데일리 봇 (표준 라이브러리만 사용)
├── .github/workflows/daily.yml  # 스케줄: 화~토 07:10 KST
└── docs/STRATEGY.md        # 전략 정리 + 보완점/리스크 대비
```

## 면책

본 저장소는 개인 학습·기록용 도구이며 투자 권유가 아닙니다.
3배 레버리지 ETF는 원금 전액 손실이 가능한 초고위험 상품이며,
모든 투자 판단과 결과의 책임은 사용자 본인에게 있습니다.
무한매수법의 저작권은 라오어님에게 있으며, 정확한 규칙은
책과 공식 카페를 확인하세요.
