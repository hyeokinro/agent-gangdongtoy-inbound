import os
import json
import re
import time
import math
import requests
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL     = "https://www.gdkids.or.kr:8443"
SCHEDULE_URL = f"{BASE_URL}/imom/05/schedule/schedule.do"
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "")

TABS = [
    {"id": "online",   "label": "온라인방문", "emoji": "🏠"},
    {"id": "delivery", "label": "택배수령",   "emoji": "📦"},
    {"id": "wait",     "label": "대기신청",   "emoji": "⏳"},
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/x-www-form-urlencoded",
}

DATA_FILE = "data/previous.json"
TIMEOUT   = 15
KST       = timezone(timedelta(hours=9))


def today_kst():
    return datetime.now(KST).strftime("%Y%m%d")


def now_kst_str():
    now = datetime.now(KST)
    wd  = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]
    return now.strftime(f"%m/%d({wd}) %H:%M")


# ---------------------------------------------------------------------------
# Crawling
# ---------------------------------------------------------------------------

def _base_form(tab_id):
    return {
        "miv_pageNo":    "1",
        "miv_pageSize":  "100",
        "LISTOP":        "",
        "from":          "",
        "date":          today_kst(),
        "pick_cd":       "",
        "co_cd":         "",
        "toy_gbn":       "1",
        "tab_id":        tab_id,
        "itemcode":      "",
        "deliSch_idx":   "",
        "deliSch_EndDt": "",
        "area":          "",
        "ccode":         "",
        "toy_age":       "",
        "searchkey":     "1",
        "searchtxt":     "",
    }


def _get_delivery_extra():
    """GET으로 delivery 탭 전용 파라미터 파싱."""
    try:
        resp = requests.get(SCHEDULE_URL, headers={**HEADERS, "Content-Type": "text/html"},
                            verify=False, timeout=TIMEOUT)
        soup = BeautifulSoup(resp.text, "html.parser")

        def val(name):
            el = soup.select_one(f"input[name='{name}']")
            return el["value"] if el and el.get("value") else ""

        return {
            "deliSch_idx":   val("deliSch_idx"),
            "deliSch_EndDt": val("deliSch_EndDt"),
            "area":          val("area"),
        }
    except Exception as exc:
        print(f"  delivery 파라미터 GET 실패: {exc}")
        return {}


def _parse_toys(soup):
    toys = {}
    for item in soup.select("ul.album_list > li"):
        img_el = item.select_one(".album_img_area img")
        if not img_el:
            continue
        code_match = re.search(r"goToyView\('([^']+)'\)", img_el.get("onclick", ""))
        if not code_match:
            continue
        code = code_match.group(1)

        name_el = item.select_one("span.album_name")
        name    = name_el.text.strip() if name_el else ""

        age_el = item.select_one("span.album_title")
        age    = age_el.text.strip().replace("◎ 연령 : ", "").strip() if age_el else ""

        img_src   = img_el.get("src", "")
        image_url = f"{BASE_URL}{img_src}" if img_src.startswith("/") else img_src

        toys[code] = {"name": name, "age": age, "image": image_url}
    return toys


def _total_count(soup):
    el = soup.select_one("strong#tot_cnt")
    if not el:
        return 0
    text = el.text.strip()
    return int(text) if text.isdigit() else 0


def get_tab_toys(tab_id):
    form = _base_form(tab_id)

    resp = requests.post(SCHEDULE_URL, data=form, headers=HEADERS,
                         verify=False, timeout=TIMEOUT)
    resp.raise_for_status()
    soup  = BeautifulSoup(resp.text, "html.parser")
    total = _total_count(soup)

    # delivery 탭이 0건이면 GET에서 추가 파라미터 파싱 후 재시도
    if total == 0 and tab_id == "delivery":
        extra = _get_delivery_extra()
        if extra:
            form.update(extra)
            resp  = requests.post(SCHEDULE_URL, data=form, headers=HEADERS,
                                  verify=False, timeout=TIMEOUT)
            resp.raise_for_status()
            soup  = BeautifulSoup(resp.text, "html.parser")
            total = _total_count(soup)

    if total == 0:
        print(f"  [{tab_id}] 응답 미리보기: {resp.text[:300]!r}")
    pages = max(1, math.ceil(total / 100))
    print(f"  [{tab_id}] 총 {total}건 / {pages}페이지")

    all_toys = _parse_toys(soup)

    for page in range(2, pages + 1):
        form["miv_pageNo"] = str(page)
        resp = requests.post(SCHEDULE_URL, data=form, headers=HEADERS,
                             verify=False, timeout=TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        if not soup.select("ul.album_list > li"):
            break
        all_toys.update(_parse_toys(soup))
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
        resp = requests.get(url, headers=HEADERS, verify=False, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        print(f"  이미지 다운로드 실패: {exc}")
        return None


def send_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    return requests.post(url, json={
        "chat_id":    CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
    }, timeout=TIMEOUT)


def send_photo(photo_url, caption):
    tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    img    = _download_image(photo_url)
    if img:
        return requests.post(tg_url, data={
            "chat_id":    CHAT_ID,
            "caption":    caption[:1024],
            "parse_mode": "HTML",
        }, files={"photo": ("photo.jpg", img, "image/jpeg")}, timeout=TIMEOUT)
    return requests.post(tg_url, json={
        "chat_id":    CHAT_ID,
        "photo":      photo_url,
        "caption":    caption[:1024],
        "parse_mode": "HTML",
    }, timeout=TIMEOUT)


def send_media_group(toys_batch, header):
    tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMediaGroup"
    files  = {}
    media  = []
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
        "chat_id": CHAT_ID,
        "media":   json.dumps(media),
    }, files=files or None, timeout=TIMEOUT)


def _toy_line(toy):
    return f"• {toy['name']} ({toy['age']})"


def send_tab_images(tab, new_toys):
    """탭별 이미지 알림 (sendPhoto / sendMediaGroup)."""
    header = f"{tab['emoji']} <b>{tab['label']}</b> 새 등록 ({len(new_toys)}건)"

    if len(new_toys) == 1:
        code, toy = new_toys[0]
        result = send_photo(toy["image"], f"{header}\n{_toy_line(toy)}")
        if not result.ok:
            send_message(f"{header}\n{_toy_line(toy)}")

    elif len(new_toys) <= 10:
        result = send_media_group(new_toys, header)
        if not result.ok:
            send_message("\n".join([header] + [_toy_line(t) for _, t in new_toys]))

    else:
        result = send_media_group(new_toys[:10], header)
        if not result.ok:
            send_message("\n".join([header] + [_toy_line(t) for _, t in new_toys[:50]]))
        extra_lines = [f"{tab['emoji']} <b>{tab['label']}</b> 추가 {len(new_toys)-10}건"]
        extra_lines += [_toy_line(t) for _, t in new_toys[10:]]
        send_message("\n".join(extra_lines))


def send_summary(changes):
    """전체 변경사항 요약 텍스트 + 링크."""
    lines = [f"📢 <b>장난감도서관 예약 알림</b>\n🕐 {now_kst_str()}\n"]
    for tab, new_toys in changes:
        lines.append(f"{tab['emoji']} <b>{tab['label']}</b> 새 등록 ({len(new_toys)}건)")
        for _, toy in new_toys[:20]:
            lines.append(f"  {_toy_line(toy)}")
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
    is_first  = not prev_data
    curr_data = {}

    if is_first:
        print("첫 실행: 현재 상태 저장만 하고 알림은 보내지 않습니다.")

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
