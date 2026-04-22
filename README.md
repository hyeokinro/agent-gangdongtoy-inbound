# 강동구 장난감도서관 입고 알림 봇

강동구 아이맘 장난감도서관 6개 지점의 장난감 상태 변화를 30분마다 감지해 텔레그램으로 알려주는 자동화 에이전트입니다.

## 감시 지점

| 지점 | co_cd | toy_gbn |
|------|-------|---------|
| 천호점 | 2 | 1 |
| 암사점 | 5 | 1 |
| 고덕점 | 7 | 1 |
| 상일2동점 | 3 | 1 |
| 길동점 | 6 | 1 |
| 천호2동점 | 4 | 3 |

## 알림 종류

- 🆕 **신규 입고** — 이전에 없던 장난감이 새로 등록됨 (이미지 포함)
- 🔄 **대여가능 전환** — 대여중이던 장난감이 반납되어 대여 가능해짐 (이미지 포함)
- ❌ **삭제/퇴출** — 목록에서 사라진 장난감 (텍스트만)

변경이 없으면 메시지를 보내지 않습니다.

---

## 설정 방법

### 1. 텔레그램 봇 만들기

1. 텔레그램에서 **@BotFather** 검색 후 대화 시작
2. `/newbot` 명령어 입력
3. 봇 이름과 username(@로 끝나는 이름) 입력
4. 발급된 **API 토큰** 복사 (예: `7123456789:AAHxxxxxxxxxxxxxxx`)

### 2. Chat ID 확인하기

1. 방금 만든 봇에게 아무 메시지 전송
2. 브라우저에서 아래 URL 접속 (토큰 교체):

```
https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
```

3. 응답 JSON에서 `"chat": {"id": 123456789}` 값이 Chat ID

그룹 채팅에 추가할 경우 Chat ID는 음수(예: `-1001234567890`)입니다.

### 3. GitHub Secrets 설정

1. GitHub 리포지토리 → **Settings** → **Secrets and variables** → **Actions**
2. **New repository secret** 클릭 후 두 가지 추가:

| Name | Value |
|------|-------|
| `TELEGRAM_TOKEN` | BotFather에서 받은 API 토큰 |
| `CHAT_ID` | 위에서 확인한 Chat ID |

### 4. Workflow 권한 설정 (필수!)

GitHub Actions가 `data/previous.json`을 커밋·푸시하려면 쓰기 권한이 필요합니다.

1. 리포지토리 → **Settings** → **Actions** → **General**
2. 하단 **Workflow permissions** 섹션에서
3. **Read and write permissions** 선택 후 **Save**

### 5. 수동 실행 방법

1. 리포지토리 → **Actions** 탭
2. 좌측 **장난감도서관 변경 감지** 클릭
3. 우측 **Run workflow** 버튼 클릭

---

## 자동 실행 스케줄

평일(월~금) **오전 9시 ~ 오후 6시 (KST)** 사이에 **30분 간격**으로 실행됩니다.

```yaml
cron: '0,30 0-9 * * 1-5'   # UTC 기준
```

---

## 알림 메시지 예시

**신규 입고 (이미지 포함)**
```
🆕 천호점 신규 입고 (2건)
• [브이텍]러닝 엑티비티 에듀볼 (12개월 이상 ~) ✅대여가능 1개
• [씨투엠뉴]랜드로버 붕붕카 (24개월 이상 ~) ✅대여가능 1개

👉 천호점 바로가기
```

**대여가능 전환 (이미지 포함)**
```
🔄 암사점 대여가능 전환 (1건)
• [레고]듀플로 동물 기차 (18개월 이상 ~) ✅대여가능 1개

👉 암사점 바로가기
```

**삭제/퇴출 (텍스트)**
```
❌ 고덕점 장난감 삭제/퇴출 (1건)
• [피셔프라이스]팝업 놀이공원 (6개월 이상 ~)
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

첫 실행 시에는 `data/previous.json`에 현재 상태만 저장되고 알림은 발송되지 않습니다.
두 번째 실행부터 변경사항이 있을 때 알림이 전송됩니다.
