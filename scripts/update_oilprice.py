import json
import os
import re
from datetime import datetime, timezone, timedelta

import requests


OUTPUT_PATH = "data/oilprice.json"
SOURCE_URL = "https://oil-price.consumer.org.hk/tc"


def now_hkt():
    hkt = timezone(timedelta(hours=8))
    return datetime.now(hkt).strftime("%Y-%m-%d %H:%M:%S")


def ensure_data_folder():
    os.makedirs("data", exist_ok=True)


def load_existing():
    if not os.path.exists(OUTPUT_PATH):
        return None

    try:
        with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def get_existing_price(existing, fuel_key):
    try:
        return existing["data"][fuel_key]["price"]
    except Exception:
        return None


def extract_price(text, fuel_keywords):
    clean = re.sub(r"\s+", " ", text)

    patterns = [
        r"加德士.*?" + r".*?".join(fuel_keywords) + r".*?(\d{2}\.\d{2})",
        r"Caltex.*?" + r".*?".join(fuel_keywords) + r".*?(\d{2}\.\d{2})",
    ]

    for pattern in patterns:
        match = re.search(pattern, clean, re.IGNORECASE)
        if match:
            return float(match.group(1))

    return None


def build_payload(gold_price, platinum_price):
    return {
        "source": "香港消費者委員會油價資訊通",
        "source_url": SOURCE_URL,
        "fetched_at_hkt": now_hkt(),
        "data": {
            "brand": "Caltex 加德士",
            "gold": {
                "name": "黃金汽油",
                "price": gold_price
            },
            "platinum": {
                "name": "白金汽油",
                "price": platinum_price
            }
        }
    }


def main():
    ensure_data_folder()
    existing = load_existing()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }

    try:
        response = requests.get(SOURCE_URL, headers=headers, timeout=30)
        response.raise_for_status()

        text = response.text

        gold_price = extract_price(text, ["黃金", "Gold"])
        platinum_price = extract_price(text, ["白金", "Platinum"])

        if gold_price is None or platinum_price is None:
            raise ValueError("未能從消委會網頁抽取加德士黃金 / 白金油價")

        old_gold = get_existing_price(existing, "gold")
        old_platinum = get_existing_price(existing, "platinum")

        if old_gold == gold_price and old_platinum == platinum_price:
            print("Oil prices unchanged. No file update needed.")
            print(f"Existing gold: {old_gold}, latest gold: {gold_price}")
            print(f"Existing platinum: {old_platinum}, latest platinum: {platinum_price}")
            return

        payload = build_payload(gold_price, platinum_price)

        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        print("Oil prices changed. oilprice.json updated.")
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    except Exception as e:
        print("Update failed:", str(e))

        # 如果已有舊資料，失敗時唔覆蓋舊 JSON
        if existing:
            print("Keeping existing oilprice.json.")
            return

        # 如果完全無舊資料，先寫入 error JSON
        payload = {
            "source": "香港消費者委員會油價資訊通",
            "source_url": SOURCE_URL,
            "fetched_at_hkt": now_hkt(),
            "error": str(e),
            "data": {}
        }

        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
