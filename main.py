"""
在线选股共振工具 - 后端 (Tushare Pro版)
捕捞季节 + 神龙筹码 双指标共振选股
自动筛选全市场股票
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import requests
import pandas as pd
import numpy as np
import time
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Tushare Pro Token
TUSHARE_TOKEN = "43c0a5c3a4743f32c0769f32dce1318863f8b99a09e881212f7514e1"

# 获取当前脚本所在目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_PATH = os.path.join(os.path.dirname(BASE_DIR), "frontend", "index.html")


def tushare_api(api_name, params=None, fields=None, retries=3):
    """调用Tushare Pro API"""
    payload = {
        "api_name": api_name,
        "token": TUSHARE_TOKEN,
        "params": params or {},
        "fields": fields or ""
    }

    for attempt in range(retries):
        try:
            resp = requests.post("http://api.tushare.pro", json=payload, timeout=30)
            data = resp.json()
            if data["code"] == 0:
                if not data["data"]["items"]:
                    return pd.DataFrame()
                fields_list = data["data"]["fields"]
                items = data["data"]["items"]
                df = pd.DataFrame(items, columns=fields_list)
                return df
            elif "限制" in str(data.get("msg", "")) or "权限" in str(data.get("msg", "")):
                print(f"Tushare API限制: {data.get('msg')}")
                return pd.DataFrame()
            else:
                print(f"Tushare API错误: {data.get('msg')}")
                return pd.DataFrame()
        except Exception as e:
            print(f"请求失败 (尝试 {attempt+1}/{retries}): {e}")
            time.sleep(2)
    return pd.DataFrame()


def get_all_stocks():
    """获取所有股票列表"""
    # 使用基础股票列表API
    df = tushhare_api(
        "stock_basic",
        params={"exchange": "", "list_status": "L"},
        fields="ts_code,symbol,name,area,industry,list_date"
    )

    if df.empty:
        # 如果基础接口受限，使用备用方法
        # 从常用指数成分股获取
        indices = [
            ("000852.SH", "中证1000"),
            ("000905.SH", "中证500"),
            ("000300.SH", "沪深300"),
        ]
        all_stocks = []
        for idx_code, idx_name in indices:
            df_idx = tushare_api(
                "index_weight",
                params={"index_code": idx_code},
                fields="con_code"
            )
            if not df_idx.empty:
                all_stocks.extend(df_idx["con_code"].tolist())

        # 去重
        all_stocks = list(set(all_stocks))
        return all_stocks

    return df["ts_code"].tolist()


def get_stock_data(ts_code, count=60):
    """获取股票K线数据"""
    if "." not in ts_code:
        if ts_code.startswith("6"):
            ts_code = f"{ts_code}.SH"
        else:
            ts_code = f"{ts_code}.SZ"

    end_date = time.strftime("%Y%m%d")
    start_ts = time.time() - (count * 2) * 24 * 3600
    start_date = time.strftime("%Y%m%d", time.localtime(start_ts))

    df = tushare_api(
        "daily",
        params={"ts_code": ts_code, "start_date": start_date, "end_date": end_date},
        fields="ts_code,trade_date,open,high,low,close,vol,amount"
    )

    if df is not None and len(df) > 0:
        df = df.sort_values("trade_date").tail(count)
        return df
    return None


def calculate_pledged_signer(df):
    """捕捞季节指标计算"""
    if df is None or len(df) < 20:
        return None

    closes = df["close"].values.tolist()

    def ma(data, period):
        result = []
        for i in range(len(data)):
            if i < period - 1:
                result.append(None)
            else:
                result.append(sum(data[i-period+1:i+1]) / period)
        return result

    ma5 = ma(closes, 5)
    ma20 = ma(closes, 20)

    volumes = df["vol"].values.tolist()
    obv = []
    cum = 0
    for i, vol in enumerate(volumes):
        if i == 0:
            obv.append(vol)
        else:
            if closes[i] >= closes[i-1]:
                cum += vol
            else:
                cum -= vol
            obv.append(cum)

    color_bars = 0
    if len(obv) >= 3:
        if obv[-1] > obv[-2] > obv[-3]:
            color_bars = 3
        elif obv[-1] > obv[-2] or obv[-2] > obv[-3]:
            color_bars = 2

    golden_cross = False
    red_trend = False
    if len(ma5) >= 2 and len(ma20) >= 2:
        if ma5[-2] is not None and ma20[-2] is not None and ma5[-1] is not None and ma20[-1] is not None:
            if ma5[-2] <= ma20[-2] and ma5[-1] > ma20[-1]:
                golden_cross = True
            if closes[-1] > ma5[-1] and ma5[-1] > ma5[-2]:
                red_trend = True

    return {
        "golden_cross": golden_cross,
        "red_trend": red_trend,
        "color_bars": color_bars,
        "ma5": round(ma5[-1], 2) if ma5[-1] else None,
        "ma20": round(ma20[-1], 2) if ma20[-1] else None
    }


def calculate_shenlong_chip(df):
    """神龙筹码指标计算"""
    if df is None or len(df) < 20:
        return None

    closes = df["close"].values.tolist()

    def ema(data, period):
        result = []
        k = 2 / (period + 1)
        for i, val in enumerate(data):
            if i == 0:
                result.append(val)
            else:
                result.append(val * k + result[-1] * (1 - k))
        return result

    red_line = ema(closes, 10)
    orange_line = ema(closes, 20)
    purple_line = ema(closes, 60)

    trend_signal = False
    if len(red_line) >= 2 and len(orange_line) >= 2:
        if red_line[-2] <= orange_line[-2] and red_line[-1] > orange_line[-1]:
            trend_signal = True
        if len(purple_line) >= 2:
            if red_line[-2] <= purple_line[-2] and red_line[-1] > purple_line[-1]:
                trend_signal = True

    if len(closes) >= 5:
        hold_ratio = []
        for i in range(len(closes)):
            if i < 20:
                hold_ratio.append(0.5)
            else:
                recent_low = min(closes[i-20:i+1])
                recent_high = max(closes[i-20:i+1])
                if recent_high > recent_low:
                    hold_ratio.append((closes[i] - recent_low) / (recent_high - recent_low))
                else:
                    hold_ratio.append(0.5)
    else:
        hold_ratio = [0.5] * len(closes)

    hold_zone = False
    if len(hold_ratio) >= 5:
        if hold_ratio[-1] > hold_ratio[-2] > hold_ratio[-3]:
            hold_zone = True

    avg_trapped = sum(hold_ratio[-20:]) / 20 if len(hold_ratio) >= 20 else 0.5

    return {
        "trend_signal": trend_signal,
        "hold_zone": hold_zone,
        "avg_trapped_rate": round(avg_trapped, 2),
        "red_line": round(red_line[-1], 2) if red_line[-1] else None,
        "orange_line": round(orange_line[-1], 2) if orange_line[-1] else None
    }


def check_advanced_filters(df, pledged):
    """进阶版过滤条件"""
    if df is None or len(df) < 20:
        return {"passed": False, "reasons": []}

    closes = df["close"].values.tolist()

    def ma(data, period):
        result = []
        for i in range(len(data)):
            if i < period - 1:
                result.append(None)
            else:
                result.append(sum(data[i-period+1:i+1]) / period)
        return result

    ma20_arr = ma(closes, 20)
    ma20_val = ma20_arr[-1]
    ma20_prev = ma20_arr[-2]

    reasons = []

    trend_filter = False
    if ma20_val and ma20_prev:
        if closes[-1] > ma20_val and ma20_val > ma20_prev:
            trend_filter = True
        else:
            reasons.append("趋势过滤：未站上20日均线或均线向下")

    chip_filter = False
    if len(closes) >= 20:
        recent_low = min(closes[-20:])
        recent_high = max(closes[-20:])
        if recent_high > recent_low:
            pos = (closes[-1] - recent_low) / (recent_high - recent_low)
            if pos < 0.5:
                chip_filter = True
            else:
                reasons.append("筹码过滤：套牢比例≥50%")
        else:
            chip_filter = True

    volume_filter = False
    if len(df) >= 5:
        vols = df["vol"].values.tolist()
        avg_3 = sum(vols[-3:]) / 3
        avg_5 = sum(vols[-5:]) / 5
        if avg_3 > avg_5 * 0.8:
            volume_filter = True
        else:
            reasons.append("量能过滤：成交量未温和放大")

    return {
        "passed": trend_filter and chip_filter and volume_filter,
        "reasons": reasons,
        "trend_filter": trend_filter,
        "chip_filter": chip_filter,
        "volume_filter": volume_filter
    }


def analyze_single_stock(code, advanced="false"):
    """分析单只股票"""
    # 转换代码格式
    if code.endswith(".SH") or code.endswith(".SZ"):
        ts_code = code
        simple_code = code.replace(".SH", "").replace(".SZ", "")
    else:
        simple_code = code
        if code.startswith("6"):
            ts_code = f"{code}.SH"
        else:
            ts_code = f"{code}.SZ"

    df = get_stock_data(ts_code, count=60)

    if df is None or len(df) == 0:
        return None

    pledged = calculate_pledged_signer(df)
    shenlong = calculate_shenlong_chip(df)

    if not pledged or not shenlong:
        return None

    basic_resonance = pledged["golden_cross"] and shenlong["trend_signal"]

    advanced_result = None
    if advanced.lower() == "true":
        advanced_result = check_advanced_filters(df, pledged)
        basic_resonance = basic_resonance and advanced_result["passed"]

    return {
        "code": simple_code,
        "name": simple_code,  # Tushare基础版没有name字段，用code代替
        "date": df.iloc[-1]["trade_date"],
        "close": round(df.iloc[-1]["close"], 2),
        "pledge": pledged,
        "shenlong": shenlong,
        "basic共振": basic_resonance,
        "advanced_result": advanced_result,
        "recommend": "买入" if basic_resonance else "观望"
    }


def convert_code(code):
    """股票代码转换"""
    if code.startswith("6"):
        return f"{code}.SH"
    else:
        return f"{code}.SZ"


@app.get("/api/autoscreen")
def auto_screen(advanced: str = "false", limit: str = "100"):
    """
    自动筛选全市场股票
    - advanced: 是否使用进阶版筛选
    - limit: 最大筛选数量（默认100只，根据Tushare积分调整）
    """
    try:
        limit_num = min(int(limit), 500)  # 最多500只，防止超时
    except:
        limit_num = 100

    print(f"开始自动筛选，限制: {limit_num}只")

    # 获取股票列表（先用中证500成分股测试）
    stock_list = []

    # 中证500成分股
    df_500 = tushare_api(
        "index_weight",
        params={"index_code": "000905.SH"},
        fields="con_code"
    )
    if not df_500.empty:
        stock_list.extend(df_500["con_code"].tolist())

    # 沪深300成分股
    df_300 = tushare_api(
        "index_weight",
        params={"index_code": "000300.SH"},
        fields="con_code"
    )
    if not df_300.empty:
        stock_list.extend(df_300["con_code"].tolist())

    # 去重
    stock_list = list(set(stock_list))[:limit_num]
    print(f"获取到 {len(stock_list)} 只股票")

    results = []
    共振股票 = []
    checked = 0
    skipped = 0

    for code in stock_list:
        try:
            result = analyze_single_stock(code, advanced)
            checked += 1

            if result is None:
                skipped += 1
                continue

            if result.get("basic共振"):
                共振股票.append(result)

            results.append(result)

            # 每50只打印进度
            if checked % 50 == 0:
                print(f"进度: {checked}/{len(stock_list)}, 共振: {len(共振股票)}")

            # 避免请求过快
            time.sleep(0.3)

        except Exception as e:
            print(f"分析 {code} 时出错: {e}")
            skipped += 1
            continue

    print(f"筛选完成: 检查 {checked} 只, 跳过 {skipped} 只, 共振 {len(共振股票)} 只")

    return {
        "total_checked": checked,
        "total_skipped": skipped,
        "共振数量": len(共振股票),
        "共振股票": 共振股票,
        "all_stocks": results[-20:] if results else [],  # 只返回最后20只作为样本
        "message": f"检查了 {checked} 只股票，找到 {len(共振股票)} 只共振信号"
    }


@app.get("/api/analyze/{code}")
def analyze_stock(code: str, advanced: str = "false"):
    """分析单只股票"""
    result = analyze_single_stock(code, advanced)
    if result is None:
        return {"error": f"获取数据失败，请检查股票代码 {code}"}
    return result


@app.get("/api/batch")
def batch_analyze(codes: str, advanced: str = "false"):
    """批量选股"""
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    results = []

    for code in code_list:
        try:
            result = analyze_single_stock(code, advanced)
            if result:
                results.append(result)
        except Exception as e:
            print(f"分析 {code} 时出错: {e}")

    resonance_stocks = [r for r in results if r.get("basic共振")]
    no_resonance = [r for r in results if not r.get("basic共振")]

    return {
        "total": len(results),
        "共振数量": len(resonance_stocks),
        "共振股票": resonance_stocks,
        "其他股票": no_resonance
    }


@app.get("/api/screener")
def screener(advanced: str = "false"):
    """演示股票筛选（快捷筛选）"""
    demo_codes = "600519,000858,002594,300750,600036,601318,000333,002415,600276,000001"
    return batch_analyze(demo_codes, advanced)


@app.get("/")
def root():
    """返回前端页面"""
    return FileResponse(FRONTEND_PATH)


@app.get("/index.html")
def index_html():
    """返回前端页面"""
    return FileResponse(FRONTEND_PATH)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)