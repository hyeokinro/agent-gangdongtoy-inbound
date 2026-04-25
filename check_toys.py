import os
import json
import re
import time
import math
import requests
from datetime import datetime, timezone, timedelta
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL     = "https://www.gdkids.or.kr:8443"
SCHEDULE_URL = f"{BASE_URL}/imom/04/schedule/schedule.do"
API_URL      = f"{BASE_URL}/front/schedule/getToyList.do"
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "")

ITEMCODE_BRANCH = {
    "C": "천호점",
    "A": "암사점",
    "K": "고덕점",
    "G": "상일2동점",
    "D": "길동점",
}

TABS = [
    {"id": "online",   "label": "온라인방문", "emoji": "🏠"},
    {"id": "delivery", "label": "택배수령",   "emoji": "📦"},
    {"id": "wait",     "label": "대기신청",   "emoji": "⏳"},
]

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

GET_HEADERS = {
    "User-Agent":   _UA,
    "Accept":       "text/html,application/xhtml+xml,*/*;q=0.8",
    "Referer":      SCHEDULE_URL,
    "Origin":       BASE_URL,
}

API_HEADERS = {
    "User-Agent":        _UA,
    "Accept":            "application/json, text/javascript, */*; q=0.01",
    "Content-Type":      "application/x-www-form-urlencoded",
    "X-Requested-With":  "XMLHttpRequest",
    "Referer":           SCHEDULE_URL,
    "Origin":            BASE_URL,
}

DATA_FILE = "data/previous.json"
TIMEOUT   = 15
KST       = timezone(timedelta(hours=9))

SESSION = None


def today_kst():
    return datetime.now(KST).strftime("%Y%m%d")


def now_kst_str():
    now = datetime.now(KST)
    wd  = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]
    return now.strftime(f"%m/%d({wd}) %H:%M")


# ---------------------------------------------------------------------------
# Crawling
# ---------------------------------------------------------------------------

def get_session():
    global SESSION
    SESSION = requests.Session()
    SESSION.headers.update({"Expect": ""})  # 417 Expectation Failed 방지
    resp = SESSION.get(SCHEDULE_URL, headers=GET_HEADERS, verify=False, timeout=TIMEOUT)
    resp.raise_for_status()
    print(f"  세션 초기화 완료 (쿠키: {list(SESSION.cookies.keys())})")


def _api_form(tab_id, page=1):
    return {
        "co_cd":      "",
        "toy_age":    "",
        "tab_id":     tab_id,
        "ccode":      "",
        "toy_gbn":    "1",
        "searchkey":  "1",
        "searchtxt":  "",
        "miv_pageNo": str(page),
        "miv_pageSize": "100",
    }


def _parse_toy(item):
    img = item.get("fileimg_nm", "")
    return {
        "name":  item.get("name", ""),
        "age":   item.get("agenm", ""),
        "image": f"{BASE_URL}/upload/toy/{img}" if img else "",
    }


def get_tab_toys(tab_id):
    form = _api_form(tab_id, page=1)
    resp = SESSION.post(API_URL, data=form, headers=API_HEADERS, verify=False, timeout=TIMEOUT)
    resp.raise_for_status()
    data  = resp.json()

    if data.get("success") != "true":
        print(f"  [{tab_id}] API 실패 응답: {str(data)[:200]}")
        return {}

    total = int(data.get("totalcnt") or 0)
    pages = max(1, math.ceil(total / 100))
    print(f"  [{tab_id}] 총 {total}건 / {pages}페이지")

    all_toys = {t["itemcode"]: _parse_toy(t) for t in data.get("toyList", [])}

    for page in range(2, pages + 1):
        form = _api_form(tab_id, page=page)
        resp = SESSION.post(API_URL, data=form, headers=API_HEADERS, verify=False, timeout=TIMEOUT)
        resp.raise_for_status()
        page_data = resp.json()
        toy_list  = page_data.get("toyList", [])
        if not toy_list:
            break
        for t in toy_list:
            all_toys[t["itemcode"]] = _parse_toy(t)
        time.sleep(0.5)

    return all_toys


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------

def detect_new(prev_data, curr_data, tab_id):
    prev = prev_data.get(tab_id, {})
    curr = curr_data.get(tab_id, {})
    return [(c, t) for c, t in curr.items() if c not in prev]


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------

def _download_image(url):
    try:
        resp = requests.get(url, headers=GET_HEADERS, verify=False, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        print(f"  이미지 다운로드 실패: {exc}")
        return None


def send_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    return requests.post(url, json={
        "chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
    }, timeout=TIMEOUT)


def send_photo(photo_url, caption):
    tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    img    = _download_image(photo_url)
    if img:
        return requests.post(tg_url, data={
            "chat_id": CHAT_ID, "caption": caption[:1024], "parse_mode": "HTML",
        }, files={"photo": ("photo.jpg", img, "image/jpeg")}, timeout=TIMEOUT)
    return requests.post(tg_url, json={
        "chat_id": CHAT_ID, "photo": photo_url,
        "caption": caption[:1024], "parse_mode": "HTML",
    }, timeout=TIMEOUT)


def send_media_group(toys_batch, header):
    tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMediaGroup"
    files, media = {}, []
    for i, (code, toy) in enumerate(toys_batch):
        cap = (f"{header}\n• {toy['name']} ({toy['age']})" if i == 0
               else f"• {toy['name']} ({toy['age']})")
        img = _download_image(toy["image"])
        key = f"photo{i}"
        if img:
            files[key] = (f"{key}.jpg", img, "image/jpeg")
            media.append({"type": "photo", "media": f"attach://{key}",
                          "caption": cap[:1024], "parse_mode": "HTML"})
        else:
            media.append({"type": "photo", "media": toy["image"],
                          "caption": cap[:1024], "parse_mode": "HTML"})
    return requests.post(tg_url, data={
        "chat_id": CHAT_ID, "media": json.dumps(media),
    }, files=files or None, timeout=TIMEOUT)


def _branch_from_code(itemcode):
    """itemcode 첫 글자로 지점 판별. C→천호점, A→암사점, K→고덕점, G→상일2동점, D→길동점"""
    return ITEMCODE_BRANCH.get(itemcode[0] if itemcode else "", "기타")


def _group_by_branch(toys):
    """[(code, toy), ...] → {지점명: [(code, toy), ...]}"""
    from collections import defaultdict
    groups = defaultdict(list)
    for code, toy in toys:
        groups[_branch_from_code(code)].append((code, toy))
    return dict(groups)


def _toy_line(toy):
    return f"• {toy['name']} ({toy['age']})"


def _toy_line_short(toy):
    """지점명 제거한 짧은 버전. '[고덕점] 블록' → '블록 (36개월~)'"""
    name = re.sub(r'^\[.+?\]\s*', '', toy["name"])
    return f"• {name} ({toy['age']})"


def send_tab_images(tab, new_toys):
    """이미지 알림 — 지점별로 묶어서 sendMediaGroup"""
    groups = _group_by_branch(new_toys)
    for branch, toys in groups.items():
        header = f"{tab['emoji']} <b>{tab['label']}</b> · <b>{branch}</b> ({len(toys)}건)"
        if len(toys) == 1:
            code, toy = toys[0]
            result = send_photo(toy["image"], f"{header}\n{_toy_line_short(toy)}")
            if not result.ok:
                send_message(f"{header}\n{_toy_line_short(toy)}")
        elif len(toys) <= 10:
            result = send_media_group(toys, header)
            if not result.ok:
                send_message("\n".join([header] + [_toy_line_short(t) for _, t in toys]))
        else:
            result = send_media_group(toys[:10], header)
            if not result.ok:
                send_message("\n".join([header] + [_toy_line_short(t) for _, t in toys]))
            else:
                extra = [f"{tab['emoji']} <b>{branch}</b> 추가 {len(toys)-10}건"]
                extra += [_toy_line_short(t) for _, t in toys[10:]]
                send_message("\n".join(extra))


def send_summary(changes):
    """텍스트 요약 — 탭 > 지점 2단계 구조"""
    lines = [f"📢 <b>장난감도서관 예약 알림</b>\n🕐 {now_kst_str()}\n"]
    for tab, new_toys in changes:
        lines.append(f"{tab['emoji']} <b>{tab['label']}</b> 새 등록 ({len(new_toys)}건)")
        groups = _group_by_branch(new_toys)
        for branch, toys in groups.items():
            lines.append(f"  📍 <b>{branch}</b> ({len(toys)}건)")
            for _, toy in toys:
                lines.append(f"    {_toy_line_short(toy)}")
        lines.append("")
    lines.append(f'👉 <a href="{SCHEDULE_URL}">예약 페이지 바로가기</a>')
    text = "\n".join(lines)
    for chunk in [text[i:i+4096] for i in range(0, len(text), 4096)]:
        send_message(chunk)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_previous():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_data(data):
    os.makedirs("data", exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    prev_data = load_previous()
    is_first  = not prev_data or not any(prev_data.get(tab["id"]) for tab in TABS)
    curr_data = {}

    if is_first:
        print("첫 실행: 현재 상태 저장만 하고 알림은 보내지 않습니다.")

    print("\n세션 초기화 중...")
    get_session()

    for tab in TABS:
        print(f"\n[{tab['id']}] 크롤링 중...")
        try:
            toys = get_tab_toys(tab["id"])
            curr_data[tab["id"]] = toys
            print(f"  [{tab['id']}] {len(toys)}개 수집 완료")
        except Exception as exc:
            print(f"  [{tab['id']}] 실패: {exc}")
            curr_data[tab["id"]] = prev_data.get(tab["id"], {})
            if not is_first and TELEGRAM_TOKEN:
                send_message(f"⚠️ <b>{tab['label']}</b> 탭 크롤링 실패\n{str(exc)[:200]}")
        time.sleep(1.5)

    if not is_first:
        changes = []
        for tab in TABS:
            new_toys = detect_new(prev_data, curr_data, tab["id"])
            if new_toys:
                print(f"  [{tab['id']}] 신규 {len(new_toys)}건")
                changes.append((tab, new_toys))

        if changes:
            for tab, new_toys in changes:
                send_tab_images(tab, new_toys)
            send_summary(changes)
        else:
            print("  변경사항 없음")

    save_data(curr_data)
    print("\n완료: data/previous.json 저장됨")


if __name__ == "__main__":
    main()
