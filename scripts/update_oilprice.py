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

    # ========== 1. 先提取白金（特級無鉛汽油）零售牌價 ==========
    platinum_pattern = r"特級無鉛汽油.*?加德士.*?(\d{2}\.\d{2})"
    platinum_match = re.search(platinum_pattern, clean, flags=re.DOTALL)
    platinum_retail = float(platinum_match.group(1)) if platinum_match else None

    # ========== 2. 徹底刪除所有特級無鉛相關內容 ==========
    # 把「特級無鉛汽油」開頭到整個文檔結尾全部刪掉，剩下的就只有普通無鉛汽油
    clean_gold_section = re.sub(r"特級無鉛汽油.*", "", clean, flags=re.DOTALL)

    # ========== 3. 在剩下的文本裡提取黃金（普通無鉛）零售牌價 ==========
    gold_pattern = r"無鉛汽油.*?加德士.*?(\d{2}\.\d{2})"
    gold_match = re.search(gold_pattern, clean_gold_section, flags=re.DOTALL)
    gold_retail = float(gold_match.group(1)) if gold_match else None

    print(f"黃金零售牌價: {gold_retail}")
    print(f"白金零售牌價: {platinum_retail}")

    return gold_retail, platinum_retail


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

        # 雙重校驗：價格區間 + 白金一定比黃金貴
        if not (30 < gold_price < 36 and 32 < platinum_price < 38):
            raise ValueError(f"提取價格異常：黃金 {gold_price} / 白金 {platinum_price}")
        if gold_price >= platinum_price:
            raise ValueError(f"價格順序錯誤：黃金({gold_price}) 不應高於白金({platinum_price})")

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
