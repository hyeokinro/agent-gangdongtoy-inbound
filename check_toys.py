import os
import json
import re
import time
import math
import requests
from bs4 import BeautifulSoup
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://www.gdkids.or.kr:8443"
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

BRANCHES = [
    {"name": "천호점",    "co_cd": "2", "toy_gbn": "1", "path": "02"},
    {"name": "암사점",    "co_cd": "5", "toy_gbn": "1", "path": "05"},
    {"name": "고덕점",    "co_cd": "7", "toy_gbn": "1", "path": "07"},
    {"name": "상일2동점", "co_cd": "3", "toy_gbn": "1", "path": "03"},
    {"name": "길동점",    "co_cd": "6", "toy_gbn": "1", "path": "06"},
    {"name": "천호2동점", "co_cd": "4", "toy_gbn": "3", "path": "04"},
]

BRANCH_URLS = {
    "천호점":    f"{BASE_URL}/imom/02/search/toylist.do?toy_gbn=1&co_cd=2",
    "암사점":    f"{BASE_URL}/imom/05/search/toylist.do?toy_gbn=1&co_cd=5",
    "고덕점":    f"{BASE_URL}/imom/07/search/toylist.do?toy_gbn=1&co_cd=7",
    "상일2동점": f"{BASE_URL}/imom/03/search/toylist.do?toy_gbn=1&co_cd=3",
    "길동점":    f"{BASE_URL}/imom/06/search/toylist.do?toy_gbn=1&co_cd=6",
    "천호2동점": f"{BASE_URL}/imom/04/search/toylist.do?toy_gbn=3&co_cd=4",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/x-www-form-urlencoded",
}

DATA_FILE = "data/previous.json"
TIMEOUT = 15


# ---------------------------------------------------------------------------
# Crawling
# ---------------------------------------------------------------------------

def _base_form(branch):
    return {
        "miv_pageNo":   "",
        "miv_pageSize": "100",
        "total_cnt":    "",
        "LISTOP":       "",
        "co_cd":        branch["co_cd"],
        "itemcode":     "",
        "toy_gbn":      branch["toy_gbn"],
        "ccode":        "",
        "age":          "ALL",
        "toy_status":   "ALL",
        "searchkey":    "2",
        "searchtxt":    "",
    }


def parse_toys(soup):
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
        name = name_el.text.strip() if name_el else ""

        age_el = item.select_one("span.album_title")
        age = age_el.text.strip().replace("◎ 연령 : ", "").strip() if age_el else ""

        status_lis = item.select(".album_register_area ul li")

        def count(li):
            el = li.select_one(".color_2c2c2c")
            return int(re.sub(r"[^0-9]", "", el.text)) if el else 0

        available  = count(status_lis[0]) if len(status_lis) > 0 else 0
        rented     = count(status_lis[1]) if len(status_lis) > 1 else 0
        cleaning   = count(status_lis[2]) if len(status_lis) > 2 else 0
        repairing  = count(status_lis[3]) if len(status_lis) > 3 else 0

        img_src = img_el.get("src", "")
        image_url = f"{BASE_URL}{img_src}" if img_src.startswith("/") else img_src

        toys[code] = {
            "name":      name,
            "age":       age,
            "available": available,
            "rented":    rented,
            "cleaning":  cleaning,
            "repairing": repairing,
            "image":     image_url,
        }
    return toys


def get_toy_list(branch):
    name = branch["name"]
    url  = f"{BASE_URL}/imom/{branch['path']}/search/toylist.do"
    form = _base_form(branch)

    resp = requests.post(url, data=form, headers=HEADERS, verify=False, timeout=TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    tot_el = soup.select_one("strong#tot_cnt")
    total  = int(tot_el.text.strip()) if tot_el else 0
    pages  = max(1, math.ceil(total / 100))
    print(f"  [{name}] 총 {total}건 / {pages}페이지")

    all_toys = parse_toys(soup)

    for page in range(2, pages + 1):
        form["miv_pageNo"] = str(page)
        resp = requests.post(url, data=form, headers=HEADERS, verify=False, timeout=TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        if not soup.select("ul.album_list > li"):
            break
        all_toys.update(parse_toys(soup))
        time.sleep(0.5)

    return all_toys


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------

def detect_changes(branch_name, prev_data, curr_toys):
    prev = prev_data.get(branch_name, {})

    new_toys      = [(c, t) for c, t in curr_toys.items() if c not in prev]
    available_now = [
        (c, t) for c, t in curr_toys.items()
        if c in prev and prev[c]["available"] == 0 and t["available"] >= 1
    ]
    deleted_toys  = [(c, t) for c, t in prev.items() if c not in curr_toys]

    return new_toys, available_now, deleted_toys


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------

def _download_image(url):
    try:
        resp = requests.get(url, headers=HEADERS, verify=False, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        print(f"  이미지 다운로드 실패 ({url}): {exc}")
        return None


def _tg(method, payload):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    return requests.post(url, json=payload, timeout=TIMEOUT)


def send_message(text):
    return _tg("sendMessage", {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"})


def send_photo(photo_url, caption):
    tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    img = _download_image(photo_url)
    if img:
        return requests.post(tg_url, data={
            "chat_id":    CHAT_ID,
            "caption":    caption[:1024],
            "parse_mode": "HTML",
        }, files={"photo": ("photo.jpg", img, "image/jpeg")}, timeout=TIMEOUT)
    # fallback: URL 직접 전달
    return requests.post(tg_url, json={
        "chat_id":    CHAT_ID,
        "photo":      photo_url,
        "caption":    caption[:1024],
        "parse_mode": "HTML",
    }, timeout=TIMEOUT)


def send_media_group(toys_batch, header):
    import json as _json
    tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMediaGroup"
    files = {}
    media = []
    for i, (code, toy) in enumerate(toys_batch):
        cap = (f"{header}\n{_toy_line(toy)}" if i == 0 else _toy_line(toy))
        img = _download_image(toy["image"])
        key = f"photo{i}"
        if img:
            files[key] = (f"{key}.jpg", img, "image/jpeg")
            media.append({
                "type":       "photo",
                "media":      f"attach://{key}",
                "caption":    cap[:1024],
                "parse_mode": "HTML",
            })
        else:
            media.append({
                "type":       "photo",
                "media":      toy["image"],
                "caption":    cap[:1024],
                "parse_mode": "HTML",
            })
    return requests.post(tg_url, data={
        "chat_id": CHAT_ID,
        "media":   _json.dumps(media),
    }, files=files if files else None, timeout=TIMEOUT)


def send_branch_link(branch_name):
    link = BRANCH_URLS[branch_name]
    send_message(f'👉 <a href="{link}">{branch_name} 바로가기</a>')


def _toy_line(toy):
    return f"• {toy['name']} ({toy['age']}) ✅대여가능 {toy['available']}개"


def send_with_images(branch_name, emoji, label, toys):
    if not toys:
        return

    header = f"{emoji} <b>{branch_name}</b> {label} ({len(toys)}건)"

    if len(toys) == 1:
        code, toy = toys[0]
        caption = f"{header}\n{_toy_line(toy)}"
        result = send_photo(toy["image"], caption)
        if not result.ok:
            send_message(caption)
        return

    batch = toys[:10]
    result = send_media_group(batch, header)
    if not result.ok:
        lines = [header] + [_toy_line(t) for _, t in toys]
        send_message("\n".join(lines))
    elif len(toys) > 10:
        extra = [f"{emoji} <b>{branch_name}</b> {label} (추가 {len(toys)-10}건)"]
        extra += [_toy_line(t) for _, t in toys[10:]]
        send_message("\n".join(extra))


def send_deleted(branch_name, toys):
    if not toys:
        return
    lines = [f"❌ <b>{branch_name}</b> 장난감 삭제/퇴출 ({len(toys)}건)"]
    lines += [f"• {t['name']} ({t['age']})" for _, t in toys]
    send_message("\n".join(lines))


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
    prev_data   = load_previous()
    is_first    = not prev_data
    curr_data   = {}

    if is_first:
        print("첫 실행: 현재 상태 저장만 하고 알림은 보내지 않습니다.")

    for branch in BRANCHES:
        name = branch["name"]
        print(f"\n[{name}] 크롤링 중...")
        try:
            toys = get_toy_list(branch)
            curr_data[name] = toys
            print(f"  [{name}] {len(toys)}개 수집 완료")
        except Exception as exc:
            print(f"  [{name}] 실패: {exc}")
            curr_data[name] = prev_data.get(name, {})
            if not is_first and TELEGRAM_TOKEN:
                send_message(f"⚠️ <b>{name}</b> 크롤링 실패\n{str(exc)[:200]}")
        time.sleep(1.5)

    if not is_first:
        for branch in BRANCHES:
            name = branch["name"]
            new_toys, available_now, deleted_toys = detect_changes(
                name, prev_data, curr_data.get(name, {})
            )

            if new_toys or available_now or deleted_toys:
                print(f"\n[{name}] 변경: 신규={len(new_toys)}, 대여가능전환={len(available_now)}, 삭제={len(deleted_toys)}")

            if new_toys:
                send_with_images(name, "🆕", "신규 입고", new_toys)
                send_branch_link(name)

            if available_now:
                send_with_images(name, "🔄", "대여가능 전환", available_now)
                send_branch_link(name)

            if deleted_toys:
                send_deleted(name, deleted_toys)

    save_data(curr_data)
    print("\n완료: data/previous.json 저장됨")


if __name__ == "__main__":
    main()
