"""
在线选股共振工具 - 后端 (Tushare Pro版)
捕捞季节 + 神龙筹码 双指标共振选股
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
import pandas as pd
import numpy as np
import hashlib
import time

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

def tushare_api(ts_code=None, trade_date=None, start_date=None, end_date=None):
    """调用Tushare Pro API"""
    if trade_date:
        api_name = "daily"
        params = {"trade_date": trade_date}
    elif ts_code and start_date and end_date:
        api_name = "daily"
        params = {"ts_code": ts_code, "start_date": start_date, "end_date": end_date}
    else:
        return None

    payload = {
        "api_name": api_name,
        "token": TUSHARE_TOKEN,
        "params": params,
        "fields": "ts_code,trade_date,open,high,low,close,vol,amount"
    }

    try:
        resp = requests.post("http://api.tushare.pro", json=payload, timeout=30)
        data = resp.json()
        if data["code"] == 0:
            fields = data["data"]["fields"]
            items = data["data"]["items"]
            df = pd.DataFrame(items, columns=fields)
            return df
        else:
            return None
    except:
        return None


def get_stock_data(ts_code, count=60):
    """获取股票K线数据"""
    # 转换股票代码格式
    if "." not in ts_code:
        if ts_code.startswith("6"):
            ts_code = f"{ts_code}.SH"
        else:
            ts_code = f"{ts_code}.SZ"

    # 计算日期
    end_date = time.strftime("%Y%m%d")
    start_ts = time.time() - (count * 2) * 24 * 3600  # 多取一些天数保证够用
    start_date = time.strftime("%Y%m%d", time.localtime(start_ts))

    df = tushare_api(ts_code=ts_code, start_date=start_date, end_date=end_date)
    if df is not None and len(df) > 0:
        df = df.sort_values("trade_date").tail(count)
        return df
    return None


def calculate_pledged_signer(df):
    """
    捕捞季节指标计算
    """
    if df is None or len(df) < 20:
        return None

    closes = df["close"].values.tolist()

    # 计算移动平均线
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

    # 计算能量潮（OBV简化版）
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

    # 计算彩柱数量（近3日能量潮上升）
    color_bars = 0
    if len(obv) >= 3:
        if obv[-1] > obv[-2] > obv[-3]:
            color_bars = 3
        elif obv[-1] > obv[-2] or obv[-2] > obv[-3]:
            color_bars = 2

    # 判断金叉（紫线上穿黄线）
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
    """
    神龙筹码指标计算
    """
    if df is None or len(df) < 20:
        return None

    closes = df["close"].values.tolist()
    volumes = df["vol"].values.tolist()

    def ema(data, period):
        result = []
        k = 2 / (period + 1)
        for i, val in enumerate(data):
            if i == 0:
                result.append(val)
            else:
                result.append(val * k + result[-1] * (1 - k))
        return result

    # 红线：短期筹码均线，橙线：中期，紫线：长期
    red_line = ema(closes, 10)
    orange_line = ema(closes, 20)
    purple_line = ema(closes, 60)

    # 判断红线上穿橙线或紫线
    trend_signal = False
    if len(red_line) >= 2 and len(orange_line) >= 2:
        if red_line[-2] <= orange_line[-2] and red_line[-1] > orange_line[-1]:
            trend_signal = True
        if len(purple_line) >= 2:
            if red_line[-2] <= purple_line[-2] and red_line[-1] > purple_line[-1]:
                trend_signal = True

    # 持股区间（红柱上升）
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

    # 平均套牢比例
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

    # 趋势过滤
    trend_filter = False
    if ma20_val and ma20_prev:
        if closes[-1] > ma20_val and ma20_val > ma20_prev:
            trend_filter = True
        else:
            reasons.append("趋势过滤：未站上20日均线或均线向下")

    # 筹码过滤
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

    # 量能过滤
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


def convert_code(code):
    """股票代码转换"""
    if code.startswith("6"):
        return f"{code}.SH"
    else:
        return f"{code}.SZ"


@app.get("/api/analyze/{code}")
def analyze_stock(code: str, advanced: str = "false"):
    """分析单只股票"""
    ts_code = convert_code(code)
    df = get_stock_data(ts_code, count=60)

    if df is None or len(df) == 0:
        return {"error": f"获取数据失败，请检查股票代码 {code}"}

    pledged = calculate_pledged_signer(df)
    shenlong = calculate_shenlong_chip(df)

    if not pledged or not shenlong:
        return {"error": "数据不足，无法计算指标"}

    basic共振 = pledged["golden_cross"] and shenlong["trend_signal"]

    advanced_result = None
    if advanced.lower() == "true":
        advanced_result = check_advanced_filters(df, pledged)
        basic共振 = basic共振 and advanced_result["passed"]

    return {
        "code": code,
        "date": df.iloc[-1]["trade_date"],
        "close": round(df.iloc[-1]["close"], 2),
        "pledge": pledged,
        "shenlong": shenlong,
        "basic共振": basic共振,
        "advanced_result": advanced_result,
        "recommend": "买入" if basic共振 else "观望"
    }


@app.get("/api/batch")
def batch_analyze(codes: str, advanced: str = "false"):
    """批量选股"""
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    results = []

    for code in code_list:
        try:
            result = analyze_stock(code, advanced)
            if "error" not in result:
                results.append(result)
        except Exception as e:
            results.append({"code": code, "error": str(e)})

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
    """演示股票筛选"""
    demo_codes = "600519,000858,002594,300750,600036,601318,000333,002415,600276,000001"
    return batch_analyze(demo_codes, advanced)


@app.get("/")
def root():
    return {"message": "选股共振工具 API (Tushare Pro版)", "version": "1.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)