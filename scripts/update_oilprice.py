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


def extract_caltex_prices(text):
    clean = re.sub(r"\s+", " ", text)

    print("===== PAGE TEXT START =====")
    print(clean[:3000])
    print("===== PAGE TEXT END =====")

    caltex_index = clean.find("加德士")
    if caltex_index == -1:
        caltex_index = clean.lower().find("caltex")

    print("Caltex index:", caltex_index)

    if caltex_index != -1:
        nearby = clean[caltex_index:caltex_index + 2000]
        print("===== CALTEX NEARBY TEXT START =====")
        print(nearby)
        print("===== CALTEX NEARBY TEXT END =====")

        nearby_prices = re.findall(r"\b\d{2}\.\d{2}\b", nearby)
        nearby_prices = [float(p) for p in nearby_prices if 15 <= float(p) <= 40]

        unique = []
        for p in nearby_prices:
            if p not in unique:
                unique.append(p)

        print("Nearby prices:", unique)

        if len(unique) >= 2:
            sorted_prices = sorted(unique)
            return sorted_prices[0], sorted_prices[-1]

    all_prices = re.findall(r"\b\d{2}\.\d{2}\b", clean)
    all_prices = [float(p) for p in all_prices if 15 <= float(p) <= 40]

    unique = []
    for p in all_prices:
        if p not in unique:
            unique.append(p)

    print("All prices:", unique)

    if len(unique) >= 2:
        sorted_prices = sorted(unique)
        return sorted_prices[0], sorted_prices[-1]

    return None, None


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

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            ),
            "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
        }

        response = requests.get(SOURCE_URL, headers=headers, timeout=30)
        response.raise_for_status()

        text = response.text
        gold_price, platinum_price = extract_caltex_prices(text)

        if gold_price is None or platinum_price is None:
            raise ValueError("未能從頁面抽取加德士黃金 / 白金油價")

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

        if existing:
            print("Keeping existing oilprice.json.")
            return

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
