import html as html_lib
import json
import os
import re
from datetime import datetime, timezone, timedelta

import requests


OUTPUT_PATH = "data/oilprice.json"
SOURCE_URL = "https://oil-price.consumer.org.hk/tc"

# 兩張「每升汽油價格比較」表嘅表頭（可見文字、順序固定：零售牌價 → 折後價 → 門市折扣）
# 用表頭做錨點，先切開兩張表，再喺各自區間搵加德士，就唔會互相踩。
GOLD_TABLE_HEADER = "無鉛汽油 零售牌價 折後價 門市折扣"
PLATINUM_TABLE_HEADER = "特級無鉛汽油 零售牌價 折後價 門市折扣"
TABLE_END_MARKER = "使用須知"

# 加德士一列：加德士 $零售牌價 $折後價 (-門市折扣)
CALTEX_ROW = r"加德士\s*\$?\s*(\d{2}\.\d{2})\s*\$?\s*(\d{2}\.\d{2})"


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


def to_visible_text(raw_html):
    """去掉 script/style 同所有 HTML 標籤、還原 entity，再壓成單行可見文字。"""
    t = re.sub(r"<script.*?</script>", " ", raw_html, flags=re.S | re.I)
    t = re.sub(r"<style.*?</style>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html_lib.unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _caltex_retail(block, label):
    """喺指定表格區間入面搵加德士列，回傳（零售牌價, 折後價）。"""
    m = re.search(CALTEX_ROW, block)
    if not m:
        raise ValueError(f"喺【{label}】表格區間搵唔到加德士嗰列，頁面結構可能已改動")
    retail, discounted = float(m.group(1)), float(m.group(2))
    if not (retail > discounted > 0):
        raise ValueError(f"【{label}】零售牌價({retail})應高於折後價({discounted})，抽取異常")
    return retail, discounted


def extract_caltex_prices(raw_html):
    text = to_visible_text(raw_html)

    # 1. 用表頭定位兩張比較表（必須黃金表喺前、白金表喺後）
    g_idx = text.find(GOLD_TABLE_HEADER)
    p_idx = text.find(PLATINUM_TABLE_HEADER)
    if g_idx == -1 or p_idx == -1 or not (g_idx < p_idx):
        raise ValueError("搵唔到『無鉛 / 特級無鉛』兩張比較表表頭，停止更新以免寫錯價")

    # 2. 切成互不相疊嘅區間：黃金表 = [黃金表頭, 白金表頭)；白金表 = [白金表頭, 使用須知)
    end_idx = text.find(TABLE_END_MARKER, p_idx)
    gold_block = text[g_idx:p_idx]
    platinum_block = text[p_idx:end_idx if end_idx != -1 else len(text)]

    # 3. 每個區間各自搵加德士（group(1) 係零售牌價，先後順序已被表頭鎖死）
    gold_retail, gold_disc = _caltex_retail(gold_block, "黃金(普通無鉛)")
    platinum_retail, platinum_disc = _caltex_retail(platinum_block, "白金(特級無鉛)")

    print(f"黃金 零售牌價: {gold_retail}（折後 {gold_disc}）")
    print(f"白金 零售牌價: {platinum_retail}（折後 {platinum_disc}）")
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

        gold_price, platinum_price = extract_caltex_prices(response.text)

        # 雙重校驗：合理區間 + 白金一定貴過黃金
        if not (28 < gold_price < 38 and 30 < platinum_price < 42):
            raise ValueError(f"提取價格超出合理區間：黃金 {gold_price} / 白金 {platinum_price}")
        if gold_price >= platinum_price:
            raise ValueError(f"價格順序錯誤：黃金({gold_price}) 不應高於或等於白金({platinum_price})")

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
