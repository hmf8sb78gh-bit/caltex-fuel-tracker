import html as html_lib
import json
import os
import re
from datetime import datetime, timezone, timedelta

import requests


OUTPUT_PATH = "data/oilprice.json"
SOURCE_URL = "https://oil-price.consumer.org.hk/tc"

# 全局油價走勢表（Supabase）。用 service_role key（擺 GitHub Secret，唔會上網頁）
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
PRICE_TREND_TABLE = "fuel_price_global"

# 兩張「每升汽油價格比較」表嘅表頭（可見文字、順序固定：零售牌價 → 折後價 → 門市折扣）
# 用表頭做錨點，先切開兩張表，再喺各自區間搵加德士，就唔會互相踩。
GOLD_TABLE_HEADER = "無鉛汽油 零售牌價 折後價 門市折扣"
PLATINUM_TABLE_HEADER = "特級無鉛汽油 零售牌價 折後價 門市折扣"
TABLE_END_MARKER = "使用須知"

# 加德士一列：加德士 $零售牌價 $折後價 (-門市折扣)
CALTEX_ROW = r"加德士\s*\$?\s*(\d{2}\.\d{2})\s*\$?\s*(\d{2}\.\d{2})"

# 消委會「油價趨勢」官方 JSON：返兩年每日牌價（ECharts 格式），用嚟重建全局走勢
CHART_TREND_URL = "https://oil-price.consumer.org.hk/tc/chart/load-data"
CALTEX_COMPANY_CODE = ":company:14:"
FUEL_KEY_GOLD = "regular-unleaded-gasoline"
FUEL_KEY_PLATINUM = "premium-unleaded-gasoline"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
}


def now_hkt():
    hkt = timezone(timedelta(hours=8))
    return datetime.now(hkt).strftime("%Y-%m-%d %H:%M:%S")


def today_hkt():
    hkt = timezone(timedelta(hours=8))
    return datetime.now(hkt).strftime("%Y-%m-%d")


def _norm_trend_date(raw):
    """官方日期係 '2026/9/7'，標準化做 '2026-09-07'。"""
    y, m, d = str(raw).strip().split("/")
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def _fetch_official_retail_series(fuel_key):
    """向消委會走勢 JSON 拎加德士某燃油（黃金/白金）兩年每日『零售牌價』，回傳 {日期: 牌價}。"""
    params = {
        "shortcut": "prev_two_years",
        "company[]": CALTEX_COMPANY_CODE,
        "retail_price[]": "true",
        "auto_fuel_type": fuel_key,
        "theme": "light",
    }
    r = requests.get(
        CHART_TREND_URL,
        params=params,
        headers={**BROWSER_HEADERS, "X-Requested-With": "XMLHttpRequest"},
        timeout=40,
    )
    r.raise_for_status()
    payload = r.json()

    for series in payload.get("series", []):
        # 要『零售牌價』嗰條線，唔要『扣除門市折扣及燃油稅』
        if "零售牌價" not in str(series.get("name", "")):
            continue
        out = {}
        for item in series.get("data", []):
            try:
                raw_date, raw_price = item["value"][0], item["value"][1]
                out[_norm_trend_date(raw_date)] = float(raw_price)
            except (KeyError, IndexError, TypeError, ValueError):
                continue
        if out:
            return out

    raise RuntimeError(f"官方走勢 JSON 搵唔到零售牌價線（{fuel_key}）")


def reconcile_trend_to_supabase(current_gold=None, current_platinum=None):
    """直接用消委會官方兩年每日牌價重建走勢：淨保留真正變動日，窗口內整張對齊（可自我修正錯日期）。

    current_gold/current_platinum 係今輪喺牌價頁即時爬到嘅價。若官方走勢未出今日、
    但大牌價已同官方最後一點唔同，就用今日日期加一個「臨時點」；下次官方正式數據一出，
    整張對齊會自動用官方日期／價取代（證實冇變就刪走），所以臨時點唔會長期標錯。
    唔設定 service key 就跳過，唔阻主流程。只重建官方窗口（約兩年）內嘅列，更早歷史保留。
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("Supabase 未設定（SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY），跳過走勢寫入。")
        return

    gold_series = _fetch_official_retail_series(FUEL_KEY_GOLD)
    platinum_series = _fetch_official_retail_series(FUEL_KEY_PLATINUM)

    dates = sorted(set(gold_series) & set(platinum_series))
    if len(dates) < 2:
        raise RuntimeError("官方走勢日期過少，停止重建以免清空資料")

    # 逐日對比，淨留窗口起點＋黃金或白金有變嘅日子
    rows = []
    last = None
    for day in dates:
        cur = (gold_series[day], platinum_series[day])
        if last is None or abs(cur[0] - last[0]) > 1e-9 or abs(cur[1] - last[1]) > 1e-9:
            rows.append({"price_date": day, "gold": cur[0], "platinum": cur[1]})
            last = cur

    # 官方走勢通常滯後一日：若今日大牌價已同官方最後一點唔同，先用今日日期補一個臨時點。
    # 下次官方正式數據一出，下面嘅整張對齊會用官方日期／價取代，或證實冇變就刪走。
    official_last_day = dates[-1]
    today = today_hkt()
    if current_gold is not None and current_platinum is not None and today > official_last_day:
        latest_gold = gold_series[official_last_day]
        latest_platinum = platinum_series[official_last_day]
        if abs(float(current_gold) - latest_gold) > 1e-9 or \
           abs(float(current_platinum) - latest_platinum) > 1e-9:
            rows.append({
                "price_date": today,
                "gold": float(current_gold),
                "platinum": float(current_platinum),
            })
            print(f"官方走勢未出 {today}，大牌價已變，暫時加入臨時點（下次官方更新自動修正）。")

    window_start = dates[0]
    base = f"{SUPABASE_URL}/rest/v1/{PRICE_TREND_TABLE}"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }

    # 先刪走官方窗口內所有舊列（包括之前誤記、例如無變動都寫入嘅日子），再整批 upsert 官方變動點
    d = requests.delete(
        base + f"?price_date=gte.{window_start}",
        headers={**headers, "Prefer": "return=minimal"},
        timeout=30,
    )
    d.raise_for_status()

    w = requests.post(
        base,
        headers={**headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
        json=rows,
        timeout=60,
    )
    w.raise_for_status()
    print(f"走勢已按消委會官方資料重建：{window_start} 起共 {len(rows)} 個變動點。")


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
        response = requests.get(SOURCE_URL, headers=BROWSER_HEADERS, timeout=30)
        response.raise_for_status()

        gold_price, platinum_price = extract_caltex_prices(response.text)

        # 雙重校驗：合理區間 + 白金一定貴過黃金
        if not (28 < gold_price < 38 and 30 < platinum_price < 42):
            raise ValueError(f"提取價格超出合理區間：黃金 {gold_price} / 白金 {platinum_price}")
        if gold_price >= platinum_price:
            raise ValueError(f"價格順序錯誤：黃金({gold_price}) 不應高於或等於白金({platinum_price})")

        old_gold = get_existing_price(existing, "gold")
        old_platinum = get_existing_price(existing, "platinum")
        prices_changed = not (old_gold == gold_price and old_platinum == platinum_price)

        # 無論油價有無變，都用「今次檢查時間」重寫 fetched_at_hkt，
        # 前端最底就會顯示同每小時檢查一致嘅時間（而唔係停留喺上次油價變動）。
        payload = build_payload(gold_price, platinum_price)

        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        if prices_changed:
            print("Oil prices changed. oilprice.json updated.")
        else:
            print("Prices unchanged; refreshed last-checked timestamp.")
        print(json.dumps(payload, ensure_ascii=False, indent=2))

        # 走勢直接用消委會官方兩年每日牌價對齊重建（淨留真正變動日、自我修正錯日期）；
        # 大牌價已變但官方未出今日時，補一個今日臨時點
        try:
            reconcile_trend_to_supabase(gold_price, platinum_price)
        except Exception as se:
            print("Supabase trend reconcile failed:", str(se))

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
