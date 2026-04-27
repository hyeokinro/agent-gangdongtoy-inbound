import os
import json
import re
import html
import time
import math
import requests
from datetime import datetime, timezone, timedelta
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL       = "https://www.gdkids.or.kr:8443"
SCHEDULE_URL   = f"{BASE_URL}/imom/04/schedule/schedule.do"
API_URL        = f"{BASE_URL}/front/schedule/getToyList.do"
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "")

ITEMCODE_BRANCH = {
    "C": "천호점",
    "A": "암사점",
    "K": "고덕점",
    "G": "상일2동점",
    "D": "길동점",
    "H": "천호2동점",
}

# gbn=1: 장난감, gbn=3: 천호2동점 특수용품 (gbn=2 도서는 제외)
CATEGORIES = [
    {"gbn": "1", "label": "장난감"},
    {"gbn": "3", "label": "천호2동점 특수용품"},
]

TABS = [
    {"id": "online",   "label": "온라인방문", "emoji": "🏠"},
    {"id": "delivery", "label": "택배수령",   "emoji": "📦"},
    {"id": "wait",     "label": "대기신청",   "emoji": "⏳"},
]

CHUNK_SIZE = 10  # sendMediaGroup 최대 10장 단위로 청크

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

GET_HEADERS = {
    "User-Agent": _UA,
    "Accept":     "text/html,application/xhtml+xml,*/*;q=0.8",
    "Referer":    SCHEDULE_URL,
    "Origin":     BASE_URL,
}

API_HEADERS = {
    "User-Agent":       _UA,
    "Accept":           "application/json, text/javascript, */*; q=0.01",
    "Content-Type":     "application/x-www-form-urlencoded",
    "X-Requested-With": "XMLHttpRequest",
    "Referer":          SCHEDULE_URL,
    "Origin":           BASE_URL,
}

DATA_FILE = "data/previous.json"
TIMEOUT   = 45  # 서버 응답이 느린 경우 대비 (이전 15초에서 상향)
KST       = timezone(timedelta(hours=9))

SESSION = None


def now_kst_str():
    now = datetime.now(KST)
    wd  = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]
    return now.strftime(f"%m/%d({wd}) %H:%M")


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

def get_session():
    """JSESSIONID 획득. 실패해도 쿠키 없이 진행 (API는 별도 접근 가능한 경우 있음)."""
    global SESSION
    SESSION = requests.Session()
    try:
        resp = SESSION.get(
            SCHEDULE_URL,
            headers={**GET_HEADERS, "Expect": None},  # Expect 헤더 완전 제거
            verify=False,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        print(f"  세션 초기화 완료 (쿠키: {list(SESSION.cookies.keys())})")
    except Exception as exc:
        print(f"  ⚠️ 세션 초기화 실패 (쿠키 없이 진행): {exc}")


# ---------------------------------------------------------------------------
# Crawling
# ---------------------------------------------------------------------------

def _api_form(tab_id, page=1, toy_gbn="1"):
    return {
        "co_cd":        "",
        "toy_age":      "",
        "tab_id":       tab_id,
        "ccode":        "",
        "toy_gbn":      toy_gbn,
        "searchkey":    "1",
        "searchtxt":    "",
        "miv_pageNo":   str(page),
        "miv_pageSize": "100",
    }


def _parse_toy(item):
    img = item.get("fileimg_nm", "")
    return {
        "name":  item.get("name", ""),
        "age":   item.get("agenm", ""),
        "image": f"{BASE_URL}/upload/toy/{img}" if img else "",
    }


def fetch_toys(tab_id, gbn, retries=2):
    """탭 + gbn 조합 전체 페이지 조회. 반환: (toys_dict, total_count)"""
    for attempt in range(retries + 1):
        try:
            return _fetch_toys(tab_id, gbn)
        except Exception as exc:
            if attempt < retries:
                wait = 5 * (attempt + 1)
                print(f"    [{tab_id}] 재시도 {attempt+1}/{retries} ({wait}s 후): {exc}")
                time.sleep(wait)
            else:
                raise


def _fetch_toys(tab_id, gbn):
    """실제 페이지 조회 로직."""
    form = _api_form(tab_id, page=1, toy_gbn=gbn)
    resp = SESSION.post(API_URL, data=form, headers=API_HEADERS, verify=False, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    if data.get("success") != "true":
        return {}, 0

    total = int(data.get("totalcnt") or 0)
    pages = max(1, math.ceil(total / 100))
    toys  = {t["itemcode"]: _parse_toy(t) for t in data.get("toyList", [])}

    for page in range(2, pages + 1):
        form     = _api_form(tab_id, page=page, toy_gbn=gbn)
        resp     = SESSION.post(API_URL, data=form, headers=API_HEADERS, verify=False, timeout=TIMEOUT)
        resp.raise_for_status()
        toy_list = resp.json().get("toyList", [])
        if not toy_list:
            break
        for t in toy_list:
            toys[t["itemcode"]] = _parse_toy(t)
        time.sleep(0.5)

    return toys, total


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------

def detect_new(prev_cat, curr_cat, tab_id):
    prev = prev_cat.get(tab_id, {})
    curr = curr_cat.get(tab_id, {})
    return [(code, toy) for code, toy in curr.items() if code not in prev]


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def _esc(s):
    return html.escape(str(s or ""), quote=False)


def _download_image(url, retries=1):
    if not url:
        return None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=GET_HEADERS, verify=False, timeout=TIMEOUT)
            resp.raise_for_status()
            return resp.content
        except Exception as exc:
            if attempt < retries:
                time.sleep(2)
            else:
                print(f"    이미지 다운로드 실패: {exc}")
    return None


def send_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    return requests.post(url, json={
        "chat_id":                  CHAT_ID,
        "text":                     text,
        "parse_mode":               "HTML",
        "disable_web_page_preview": True,
    }, timeout=TIMEOUT)


def send_photo(image_bytes, caption):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    return requests.post(url, data={
        "chat_id":    CHAT_ID,
        "caption":    caption[:1024],
        "parse_mode": "HTML",
    }, files={"photo": ("photo.jpg", image_bytes, "image/jpeg")}, timeout=TIMEOUT)


def send_media_group(images, first_caption):
    """images: [bytes, ...] 2개 이상. 첫 사진 캡션에 해당 청크 정보 전체 포함."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMediaGroup"
    files, media = {}, []
    for i, img in enumerate(images):
        key   = f"photo{i}"
        files[key] = (f"{key}.jpg", img, "image/jpeg")
        item  = {"type": "photo", "media": f"attach://{key}"}
        if i == 0:
            item["caption"]    = first_caption[:1024]
            item["parse_mode"] = "HTML"
        media.append(item)
    return requests.post(url, data={
        "chat_id": CHAT_ID,
        "media":   json.dumps(media),
    }, files=files, timeout=TIMEOUT)


# ---------------------------------------------------------------------------
# Branch helpers
# ---------------------------------------------------------------------------

def _branch_from_code(itemcode):
    return ITEMCODE_BRANCH.get(itemcode[0] if itemcode else "", "기타")


def _group_by_branch(toys):
    from collections import defaultdict
    groups = defaultdict(list)
    for code, toy in toys:
        groups[_branch_from_code(code)].append((code, toy))
    return dict(groups)


def _toy_line(toy):
    """장난감 이름에서 지점 접두사([고덕점] 등) 제거 후 포맷."""
    name = re.sub(r'^\[.+?\]\s*', '', toy["name"])
    return f"• {_esc(name)} ({_esc(toy['age'])})"


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

def _send_chunk(category, tab, branch, chunk):
    """청크(최대 10건) 단위 알림.
    - 이미지 있는 것만 앨범으로 발송 (다운로드 실패는 스킵, 재시도 1회)
    - 텍스트(이름+연령)는 청크 전체를 첫 번째 사진 캡션에 포함
    - 이미지가 하나도 없으면 텍스트 메시지로 fallback
    """
    header = (
        f"📢 <b>장난감도서관 예약 알림</b>  🕐 {now_kst_str()}\n"
        f"<b>[{_esc(category['label'])}]</b> "
        f"{tab['emoji']} <b>{_esc(tab['label'])}</b> · "
        f"📍 <b>{_esc(branch)}</b> ({len(chunk)}건)"
    )
    lines   = [_toy_line(toy) for _, toy in chunk]
    caption = header + "\n" + "\n".join(lines)

    # 이미지 다운로드 (실패 시 1회 재시도, 그래도 실패면 스킵)
    images = [
        img for _, toy in chunk
        if (img := _download_image(toy.get("image"))) is not None
    ]

    if not images:
        send_message(caption)
    elif len(images) == 1:
        result = send_photo(images[0], caption)
        if not result.ok:
            send_message(caption)
    else:
        result = send_media_group(images, caption)
        if not result.ok:
            send_message(caption)

    time.sleep(0.5)  # 텔레그램 레이트리밋 대비


def notify_category(category, changes):
    """카테고리의 신규 장난감을 탭별 → 지점별 → 10개 청크 순서로 발송."""
    notified = False
    for tab in TABS:
        new_toys = changes.get(tab["id"], [])
        if not new_toys:
            continue
        for branch, toys in _group_by_branch(new_toys).items():
            for i in range(0, len(toys), CHUNK_SIZE):
                _send_chunk(category, tab, branch, toys[i:i + CHUNK_SIZE])
        notified = True

    if notified:
        send_message(f'👉 <a href="{SCHEDULE_URL}">예약 페이지 바로가기</a>')


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

_TAB_IDS = {t["id"] for t in TABS}


def load_previous():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    # 구버전 마이그레이션: top-level 키가 tab_id 인 경우 → gbn=1 로 흡수
    if data and _TAB_IDS.issuperset(data.keys()):
        print("  구버전 데이터 감지 → gbn=1 로 마이그레이션")
        return {"1": data, "3": {}}
    return data


def save_data(data):
    os.makedirs("data", exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    prev_data = load_previous()
    curr_data = {c["gbn"]: {} for c in CATEGORIES}

    print("\n세션 초기화 중...")
    get_session()

    # 전체 크롤링
    for category in CATEGORIES:
        gbn = category["gbn"]
        print(f"\n[{category['label']}] (gbn={gbn}) 크롤링 중...")
        for tab in TABS:
            try:
                toys, total = fetch_toys(tab["id"], gbn)
                curr_data[gbn][tab["id"]] = toys
                print(f"  [{tab['id']}] {len(toys)}/{total}건 수집")
            except Exception as exc:
                print(f"  [{tab['id']}] 실패: {exc}")
                # 실패한 탭은 이전 데이터 유지 (다음 실행에서 재시도)
                curr_data[gbn][tab["id"]] = prev_data.get(gbn, {}).get(tab["id"], {})
                if prev_data and TELEGRAM_TOKEN:
                    send_message(
                        f"⚠️ <b>[{_esc(category['label'])}] {_esc(tab['label'])}</b> 크롤링 실패\n"
                        f"{_esc(str(exc)[:200])}"
                    )
            time.sleep(1)

    # 카테고리별 신규 감지 + 알림
    for category in CATEGORIES:
        gbn      = category["gbn"]
        prev_cat = prev_data.get(gbn, {})

        # 이 카테고리 데이터가 없으면 첫 실행 → 알림 없이 상태만 저장
        if not any(prev_cat.get(t["id"]) for t in TABS):
            print(f"\n[{category['label']}] 첫 실행 → 알림 없이 상태 저장")
            continue

        changes = {}
        for tab in TABS:
            new_toys = detect_new(prev_cat, curr_data[gbn], tab["id"])
            if new_toys:
                changes[tab["id"]] = new_toys
                print(f"  [{category['label']}/{tab['id']}] 신규 {len(new_toys)}건")

        if changes:
            notify_category(category, changes)
        else:
            print(f"  [{category['label']}] 변경 없음")

    save_data(curr_data)
    print("\n완료: data/previous.json 저장됨")


if __name__ == "__main__":
    main()
