# 강동구 장난감도서관 예약 알림 봇

강동구 아이맘 장난감도서관 예약 페이지의 3개 탭을 20분마다 감시해서
새로 등록된 장난감을 텔레그램으로 알려주는 자동화 에이전트입니다.

## 감시 탭

| 탭 | tab_id | 의미 |
|----|--------|------|
| 🏠 온라인방문 | online | 방문 대여 예약 |
| 📦 택배수령 | delivery | 택배 수령 신청 |
| ⏳ 대기신청 | wait | 입고 알림 신청 |

전 지점 장난감이 하나의 URL에서 조회됩니다.

## 알림 종류

- 🆕 **새 등록** — 이전 실행에 없던 장난감이 새로 나타남 (이미지 포함)

변경이 없으면 메시지를 보내지 않습니다.

---

## 설정 방법

### 1. 텔레그램 봇 만들기

1. 텔레그램에서 **@BotFather** 검색 후 대화 시작
2. `/newbot` 명령어 입력
3. 봇 이름과 username 입력
4. 발급된 **API 토큰** 복사

### 2. Chat ID 확인하기

1. 봇에게 아무 메시지 전송
2. 아래 URL 접속 (토큰 교체):

```
https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
```

3. 응답 JSON의 `"chat": {"id": 123456789}` 값이 Chat ID

그룹에 추가할 경우 Chat ID는 음수(예: `-1001234567890`)입니다.

### 3. GitHub Secrets 설정

Settings → Secrets and variables → Actions → New repository secret:

| Name | Value |
|------|-------|
| `TELEGRAM_TOKEN` | BotFather 토큰 |
| `CHAT_ID` | Chat ID |

### 4. Workflow 권한 설정 (필수)

Settings → Actions → General → Workflow permissions →
**Read and write permissions** 선택 → Save

### 5. Cloudflare Workers 스케줄러

Cloudflare Workers에서 20분 간격으로 GitHub Actions를 트리거합니다.

- Worker URL을 브라우저에서 열면 수동 트리거 가능
- Cron Trigger: `7,27,47 0-9 * * 1-5` (평일 KST 9시~18시47분)

---

## 알림 메시지 예시

```
📢 장난감도서관 예약 알림
🕐 04/23(수) 14:30

🏠 온라인방문 새 등록 (3건)
  • [고덕점] 마그네틱 블록 잠수비행기 (36개월 이상 ~)
  • [디즈니베이비] 클레멘토니 팔로오 (6개월 이상 ~)
  • 뉴 해양꼭지퍼즐 (6개월 이상 ~)

📦 택배수령 새 등록 (1건)
  • [브이텍]스마트 러닝 태블릿 (12개월 이상 ~)

👉 예약 페이지 바로가기
```

---

## 프로젝트 구조

```
├── check_toys.py           # 메인 크롤러
├── requirements.txt
├── data/
│   └── previous.json       # 이전 실행 데이터 (자동 커밋)
└── .github/
    └── workflows/
        └── check.yml
```

## 로컬 테스트

```bash
pip install -r requirements.txt
export TELEGRAM_TOKEN="your_token"
export CHAT_ID="your_chat_id"
python check_toys.py
```

첫 실행 시 현재 상태만 저장되고 알림은 발송되지 않습니다.
두 번째 실행부터 변경사항이 있을 때 알림이 전송됩니다.
