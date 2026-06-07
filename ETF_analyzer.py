# -*- coding: utf-8 -*-
"""
ETF 候选分析工具（集成 akshare 新浪源自动取数）
================================================
只需在下面【配置区】填好：每只 ETF 的「名称 -> 新浪代码」、日期窗口、组合权重，
运行 python etf_analyzer.py 即可自动：
  1) 从新浪源抓取每日收盘价（按日期对齐多只 ETF）
  2) 算出每只的 区间收益率 / 年化波动率 / 最大回撤 / 夏普比率，并给出 核心/卫星/观察 建议
  3) 输出相关性矩阵（判断哪两个卫星是真分散）
  4) 模拟一套组合的整体指标
  5) 把结果导出到 Excel

注意：新浪源返回的是「不复权」价。一个月窗口内多数 ETF 影响很小，但若窗口内有除息，
      算出的回撤会略偏大；若要严格前复权，请用东方财富源(fund_etf_hist_em, adjust='qfq')。
"""

import time
import numpy as np
import pandas as pd
import socket
socket.setdefaulttimeout(10)

try:
    import akshare as ak
except ImportError:
    ak = None  # 未安装 akshare 时给出友好提示

# ============================================================
# 配置区（你只需要改这里）
# ============================================================
USE_AKSHARE = True          # True=联网自动取数；False=用下面的 MANUAL_PRICES 手动数据

# 名称 -> 新浪代码（5开头加 sh，15/16开头加 sz）。改成你真正要分析的候选 ETF：
ETF_LIST = {
    "红利低波ETF南方": "sh515450",   
    "标普500ETF南方": "sh513650",   
    "10年国债ETF国泰": "sh511260",  
    "5年地方债ETF鹏华": "sz159972",
    "国开债ETF华安": "sz159649",
    "通信ETF国泰": "sh515880", 
    "电力ETF广发": "sz159611", 
    "科创芯片ETF嘉实": "sh588200", 
    "能源ETF广发": "sz159945", 
    "道琼斯ETF鹏华": "sh513400",
    "国企红利ETF鹏扬": "sz159515",
}

START = "20260101"          # 取数起始（含）
END   = "20260530"          # 取数结束（含）

# 想模拟的组合权重（名称必须是 ETF_LIST 里的键；不在表里的会被自动忽略）：
PORTFOLIO = {
    "红利低波ETF南方": 0.1,   
    "标普500ETF南方": 0.1,   
    "10年国债ETF国泰": 0.16,  
    "5年地方债ETF鹏华": 0.16,
    "国开债ETF华安": 0.16,
    "通信ETF国泰": 0.05, 
    "电力ETF广发": 0.05, 
    "科创芯片ETF嘉实": 0.05,  
    "能源ETF广发": 0.05, 
    "道琼斯ETF鹏华": 0.05,
    "国企红利ETF鹏扬": 0.05,  
}

TRADING_DAYS = 252          # 年化用的交易日数
RF_ANNUAL    = 0.0          # 无风险年化收益率，简化为0；可改为如 0.018

# 仅当 USE_AKSHARE=False 时使用：名称 -> 收盘价列表（最早在前）
MANUAL_PRICES = {
    # "红利ETF": [1.05, 1.06, ...],
}

# ============================================================
# 取数：新浪源（带轻量重试），返回「按日期索引的收盘价 Series」
# ============================================================
def fetch_etf_sina(code_sina, start=START, end=END, retries=3):
    if ak is None:
        raise RuntimeError("未安装 akshare，请先 pip install akshare")
    last_err = None
    for i in range(retries):
        try:
            df = ak.fund_etf_hist_sina(symbol=code_sina)          # 新浪返回全部历史
            df["date"] = pd.to_datetime(df["date"])               # 日期列转日期类型
            df = df[(df["date"] >= pd.to_datetime(start)) &
                    (df["date"] <= pd.to_datetime(end))]
            s = pd.Series(pd.to_numeric(df["close"]).values,       # 取收盘价
                          index=df["date"].values)
            return s.sort_index()                                  # 按日期排好
        except Exception as e:
            last_err = e
            print(f"  {code_sina} 第{i+1}次失败：{e}")
            time.sleep(2)
    raise RuntimeError(f"取数失败（{code_sina}）：{last_err}")

def load_prices():
    """返回一个价格 DataFrame：列=ETF名称，行=日期（已对齐到各 ETF 共同的交易日）。"""
    if USE_AKSHARE:
        series = {}
        for name, code in ETF_LIST.items():
            print(f"抓取 {name} ({code}) …")
            s = fetch_etf_sina(code)
            if s.empty:
                print(f"  ⚠ {name} 在该日期窗口内无数据，已跳过")
                continue
            series[name] = s
        if not series:
            raise RuntimeError("没有取到任何数据，请检查代码/日期/网络")
        # 用字典构造 DataFrame 会自动按日期索引对齐，dropna 只保留各 ETF 都有的交易日
        df = pd.DataFrame(series).sort_index().dropna()
        return df
    else:
        if not MANUAL_PRICES:
            raise RuntimeError("USE_AKSHARE=False 时请在 MANUAL_PRICES 填入数据")
        n = min(len(v) for v in MANUAL_PRICES.values())           # 按最短长度对齐
        return pd.DataFrame({k: v[-n:] for k, v in MANUAL_PRICES.items()})

# ============================================================
# 指标计算
# ============================================================
def daily_returns(price_series):
    return price_series.pct_change().dropna()

def max_drawdown(price_series):
    p = price_series.dropna()
    return (p / p.cummax() - 1).min()                              # 相对历史高点的最大回撤

def metrics(price_series):
    p = price_series.dropna()
    r = daily_returns(p)
    ann_vol = r.std(ddof=1) * np.sqrt(TRADING_DAYS)
    ann_ret = r.mean() * TRADING_DAYS
    return {
        "区间收益率": p.iloc[-1] / p.iloc[0] - 1,
        "年化波动率": ann_vol,
        "最大回撤":   max_drawdown(p),
        "夏普比率":   (ann_ret - RF_ANNUAL) / ann_vol if ann_vol > 0 else np.nan,
        "样本天数":   len(p),
    }

def classify(m, vol_cap=0.20, mdd_cap=-0.10):
    if m["年化波动率"] < vol_cap and m["最大回撤"] > mdd_cap:
        return "核心"
    return "卫星" if m["区间收益率"] > 0 else "观察"

def simulate_portfolio(returns_df, weights):
    w = pd.Series(weights)
    cols = [c for c in w.index if c in returns_df.columns]         # 只用表里有的 ETF
    if not cols:
        return None
    w = w[cols] / w[cols].sum()                                    # 权重归一化
    port_ret = (returns_df[cols] * w).sum(axis=1)
    port_price = (1 + port_ret).cumprod()
    ann_vol = port_ret.std(ddof=1) * np.sqrt(TRADING_DAYS)
    ann_ret = port_ret.mean() * TRADING_DAYS
    return {
        "组合区间收益率": port_price.iloc[-1] - 1,
        "组合年化波动率": ann_vol,
        "组合最大回撤":   max_drawdown(port_price),
        "组合夏普比率":   (ann_ret - RF_ANNUAL) / ann_vol if ann_vol > 0 else np.nan,
        "实际使用成分":   cols,
    }

# ============================================================
# 主流程
# ============================================================
def pct(x):
    return f"{x*100:6.2f}%" if pd.notna(x) else "  n/a"

def main():
    price_df = load_prices()
    ret_df = price_df.pct_change().dropna()
    print(f"\n数据区间：{str(price_df.index.min())[:10]} ~ {str(price_df.index.max())[:10]}，"
          f"共 {len(price_df)} 个对齐后的交易日\n")

    # 1) 指标表
    rows = []
    for name in price_df.columns:
        m = metrics(price_df[name]); m["ETF"] = name; m["建议分类"] = classify(m)
        rows.append(m)
    table = pd.DataFrame(rows).set_index("ETF")
    show = table.copy()
    for c in ["区间收益率", "年化波动率", "最大回撤"]:
        show[c] = show[c].map(pct)
    show["夏普比率"] = show["夏普比率"].map(lambda x: f"{x:5.2f}")
    print("========== 1) 各 ETF 指标 ==========")
    print(show[["区间收益率", "年化波动率", "最大回撤", "夏普比率", "样本天数", "建议分类"]].to_string())

    # 2) 相关性矩阵
    print("\n========== 2) 相关性矩阵（越接近1越同涨同跌，选低相关的两个卫星）==========")
    print(ret_df.corr().round(2).to_string())

    # 3) 组合模拟
    print("\n========== 3) 组合模拟 ==========")
    port = simulate_portfolio(ret_df, PORTFOLIO)
    if port is None:
        print("  PORTFOLIO 里的名称和 ETF_LIST 对不上，跳过。")
    else:
        print("  权重：", {k: PORTFOLIO[k] for k in port["实际使用成分"]})
        for k in ["组合区间收益率", "组合年化波动率", "组合最大回撤"]:
            print(f"  {k}: {pct(port[k])}")
        print(f"  组合夏普比率: {port['组合夏普比率']:.2f}")

    # 4) 导出 Excel
    out = "ETF分析结果.xlsx"
    with pd.ExcelWriter(out) as xw:
        table.to_excel(xw, sheet_name="指标")
        ret_df.corr().to_excel(xw, sheet_name="相关性矩阵")
        price_df.to_excel(xw, sheet_name="收盘价")
    print(f"\n已导出：{out}")

if __name__ == "__main__":
    main()
