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
    "H": "천호2동점",
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
    # Expect 헤더를 None으로 설정해 전송 자체를 막음 (""은 빈값으로 전송돼 417 유발)
    SESSION.headers["Expect"] = None
    for attempt in range(1, 4):
        try:
            resp = SESSION.get(SCHEDULE_URL, headers=GET_HEADERS, verify=False, timeout=TIMEOUT)
            resp.raise_for_status()
            print(f"  세션 초기화 완료 (쿠키: {list(SESSION.cookies.keys())})")
            return
        except Exception as exc:
            print(f"  세션 초기화 실패 ({attempt}/3): {exc}")
            if attempt < 3:
                time.sleep(5)
    raise RuntimeError("세션 초기화 3회 모두 실패")


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


def _fetch_toys_for_gbn(tab_id, toy_gbn):
    """특정 toy_gbn으로 한 탭 전체 페이지 조회."""
    form = _api_form(tab_id, page=1, toy_gbn=toy_gbn)
    resp = SESSION.post(API_URL, data=form, headers=API_HEADERS, verify=False, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    if data.get("success") != "true":
        return {}

    total = int(data.get("totalcnt") or 0)
    pages = max(1, math.ceil(total / 100))
    toys  = {t["itemcode"]: _parse_toy(t) for t in data.get("toyList", [])}

    for page in range(2, pages + 1):
        form = _api_form(tab_id, page=page, toy_gbn=toy_gbn)
        resp = SESSION.post(API_URL, data=form, headers=API_HEADERS, verify=False, timeout=TIMEOUT)
        resp.raise_for_status()
        page_data = resp.json()
        toy_list  = page_data.get("toyList", [])
        if not toy_list:
            break
        for t in toy_list:
            toys[t["itemcode"]] = _parse_toy(t)
        time.sleep(0.5)

    return toys, total


def get_tab_toys(tab_id):
    """toy_gbn=1 (일반) + toy_gbn=3 (천호2동점 특별용품) 합산 조회."""
    toys_gbn1, total1 = _fetch_toys_for_gbn(tab_id, "1")
    toys_gbn3, total3 = _fetch_toys_for_gbn(tab_id, "3")
    print(f"  [{tab_id}] gbn=1: {total1}건, gbn=3(천호2동점): {total3}건")
    return {**toys_gbn1, **toys_gbn3}


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
    if not url:
        return None
    try:
        resp = requests.get(url, headers=GET_HEADERS, verify=False, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        print(f"  이미지 다운로드 실패: {exc}")
        return None


def _tg_ok(resp):
    """Telegram API는 HTTP 오류 시에도 200을 반환하므로 JSON body의 ok 필드로 판별."""
    try:
        return resp.json().get("ok") is True
    except Exception:
        return resp.ok


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


def send_media_group(toys_batch, header=None):
    """이미지 앨범 전송. header가 None이면 caption 없이 이미지만 전송."""
    tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMediaGroup"
    files, media = {}, []
    for idx, (code, toy) in enumerate(toys_batch):
        img = _download_image(toy["image"])
        if not img and not toy["image"]:
            continue  # 이미지 없는 항목은 건너뜀
        # caption: header 있으면 첫 장에만 붙이고, 없으면 모두 생략
        if header and idx == 0:
            cap = f"{header}\n{_toy_line_short(toy)}"
        else:
            cap = None
        key = f"photo{len(media)}"
        entry = {"type": "photo"}
        if img:
            files[key] = (f"{key}.jpg", img, "image/jpeg")
            entry["media"] = f"attach://{key}"
        else:
            entry["media"] = toy["image"]
        if cap:
            entry["caption"]    = cap[:1024]
            entry["parse_mode"] = "HTML"
        media.append(entry)
    if not media:
        return type("R", (), {"ok": False, "text": "no media"})()
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


def _send_branch_block(tab, branch, toys):
    """지점 하나의 이미지+텍스트 메시지 발송."""
    header = (
        f"📢 <b>장난감도서관 예약 알림</b>  🕐 {now_kst_str()}\n"
        f"{tab['emoji']} <b>{tab['label']}</b> · 📍 <b>{branch}</b> ({len(toys)}건)"
    )
    toy_lines = "\n".join(_toy_line_short(t) for _, t in toys)

    # 이미지 있는 것 / 없는 것 분리
    with_img    = [(c, t) for c, t in toys if t.get("image")]
    without_img = [(c, t) for c, t in toys if not t.get("image")]

    if with_img:
        batch = with_img[:10]
        if len(batch) == 1:
            code, toy = batch[0]
            caption = f"{header}\n{_toy_line_short(toy)}"
            result = send_photo(toy["image"], caption)
            if not _tg_ok(result):
                # 이미지 전송 실패 시 텍스트로 폴백
                send_message(caption)
        else:
            # 복수 이미지: 텍스트 헤더 먼저, 이미지 앨범은 별도
            send_message(f"{header}\n{toy_lines}")
            result = send_media_group(batch, None)
            if not _tg_ok(result):
                print(f"  send_media_group 실패: {result.text[:200]}")
            if len(with_img) > 10:
                extra = "\n".join(_toy_line_short(t) for _, t in with_img[10:])
                send_message(f"{tab['emoji']} <b>{branch}</b> 추가 {len(with_img)-10}건\n{extra}")
    else:
        # 이미지가 하나도 없으면 텍스트만
        send_message(f"{header}\n{toy_lines}")

    # 이미지 없는 항목은 텍스트로 추가
    if without_img:
        lines = [f"{tab['emoji']} <b>{branch}</b> (이미지 없음)"]
        lines += [_toy_line_short(t) for _, t in without_img]
        send_message("\n".join(lines))

    # 지점 바로가기 링크
    send_message(f'👉 <a href="{SCHEDULE_URL}">예약 페이지 바로가기</a>')


def send_by_branch(changes):
    """탭별, 지점별로 독립 메시지 발송."""
    for tab, new_toys in changes:
        groups = _group_by_branch(new_toys)
        for branch, toys in groups.items():
            _send_branch_block(tab, branch, toys)


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
            send_by_branch(changes)
        else:
            print("  변경사항 없음")

    save_data(curr_data)
    print("\n완료: data/previous.json 저장됨")


if __name__ == "__main__":
    main()
