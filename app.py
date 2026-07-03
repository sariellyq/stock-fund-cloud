
from datetime import datetime, timedelta
import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

try:
    import akshare as ak
except Exception:
    ak = None


APP_TITLE = "基金重仓估算工具"

DEFAULT_STOCKS = {
    "中际旭创": "300308",
    "新易盛": "300502",
    "天孚通信": "300394",
    "源杰科技": "688498",
    "长飞光纤": "601869",
    "麦格米特": "002851",
    "长芯博创": "",
    "永鼎股份": "600105",
    "德科立": "688205",
    "东山精密": "002384",
    "凯格精机": "301338",
    "腾景科技": "688195",
    "亨通光电": "600487",
}


def china_yahoo_symbol(code: str) -> str:
    code = str(code).strip().zfill(6)
    if code.startswith(("6", "688")):
        return f"{code}.SS"
    return f"{code}.SZ"


def normalize_yahoo_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    df = df.reset_index()

    rename = {}
    for c in df.columns:
        name = str(c).lower()
        if name in ("date", "datetime"):
            rename[c] = "datetime"
        elif name == "open":
            rename[c] = "open"
        elif name == "high":
            rename[c] = "high"
        elif name == "low":
            rename[c] = "low"
        elif name == "close":
            rename[c] = "close"
        elif name == "volume":
            rename[c] = "volume"

    df = df.rename(columns=rename)
    required = ["datetime", "open", "high", "low", "close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError("Yahoo字段缺失：" + ", ".join(missing))

    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(subset=["datetime", "open", "high", "low", "close"])
    df["pct_change"] = df["close"].pct_change() * 100
    return df.sort_values("datetime").reset_index(drop=True)


@st.cache_data(ttl=300)
def fetch_yahoo(code: str, frequency: str) -> pd.DataFrame:
    symbol = china_yahoo_symbol(code)

    if frequency == "日线":
        period, interval = "2y", "1d"
    elif frequency == "周线":
        period, interval = "5y", "1wk"
    elif frequency == "60分钟":
        period, interval = "60d", "60m"
    elif frequency == "30分钟":
        period, interval = "60d", "30m"
    else:
        period, interval = "60d", "5m"

    df = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=False, threads=False)
    df = normalize_yahoo_df(df)
    if df.empty:
        raise RuntimeError(f"Yahoo无数据：{symbol}")
    return df


@st.cache_data(ttl=1800)
def get_spot_table():
    if ak is None:
        return pd.DataFrame()
    try:
        return ak.stock_zh_a_spot_em()
    except Exception:
        return pd.DataFrame()


def find_code_by_name(name: str) -> str:
    df = get_spot_table()
    if df.empty or "名称" not in df.columns or "代码" not in df.columns:
        return ""
    exact = df[df["名称"].astype(str) == name]
    if not exact.empty:
        return str(exact.iloc[0]["代码"]).zfill(6)
    fuzzy = df[df["名称"].astype(str).str.contains(name, na=False)]
    if not fuzzy.empty:
        return str(fuzzy.iloc[0]["代码"]).zfill(6)
    return ""


def filter_period(df: pd.DataFrame, period_name: str) -> pd.DataFrame:
    if df.empty:
        return df
    days_map = {"近1周": 7, "近1月": 30, "近3月": 90, "近6月": 180, "近1年": 365}
    if period_name not in days_map:
        return df
    end = df["datetime"].max()
    return df[df["datetime"] >= end - pd.Timedelta(days=days_map[period_name])].copy()


def compute_period_return(df: pd.DataFrame) -> float:
    if df.empty or len(df) < 2:
        return 0.0
    first = df["close"].iloc[0]
    last = df["close"].iloc[-1]
    if first == 0 or pd.isna(first) or pd.isna(last):
        return 0.0
    return (last / first - 1) * 100


def add_indicators(df: pd.DataFrame, ma_list=(5, 10, 20), kdj_n=9) -> pd.DataFrame:
    df = df.copy()
    for ma in ma_list:
        df[f"MA{ma}"] = df["close"].rolling(ma).mean()

    low_n = df["low"].rolling(kdj_n, min_periods=1).min()
    high_n = df["high"].rolling(kdj_n, min_periods=1).max()
    rsv = ((df["close"] - low_n) / (high_n - low_n) * 100).replace([float("inf"), -float("inf")], pd.NA).fillna(50)

    k_values, d_values = [], []
    k = d = 50
    for val in rsv:
        k = 2 / 3 * k + 1 / 3 * val
        d = 2 / 3 * d + 1 / 3 * k
        k_values.append(k)
        d_values.append(d)
    df["K"] = k_values
    df["D"] = d_values
    df["J"] = 3 * pd.Series(k_values) - 2 * pd.Series(d_values)
    return df


def make_kline_figure(df, title, ma_list):
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=df["datetime"], open=df["open"], high=df["high"], low=df["low"], close=df["close"], name="K线"))
    for ma in ma_list:
        col = f"MA{ma}"
        if col in df.columns:
            fig.add_trace(go.Scatter(x=df["datetime"], y=df[col], mode="lines", name=col))
    fig.update_layout(title=title, height=520, xaxis_rangeslider_visible=False)
    return fig


def make_kdj_figure(df, title):
    fig = go.Figure()
    for col in ["K", "D", "J"]:
        fig.add_trace(go.Scatter(x=df["datetime"], y=df[col], mode="lines", name=col))
    fig.update_layout(title=title, height=300)
    return fig


def main():
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.title(APP_TITLE)
    st.caption("云端手机版。当前版本只使用 Yahoo Finance 数据源，避免 AkShare 云端断连问题。仅用于估算，不构成投资建议。")

    if "stock_map" not in st.session_state:
        st.session_state.stock_map = DEFAULT_STOCKS.copy()

    with st.sidebar:
        st.header("行情参数")
        frequency = st.selectbox("周期", ["日线", "周线", "60分钟", "30分钟", "5分钟"], index=0)
        period_range = st.selectbox("区间", ["近1周", "近1月", "近3月", "近6月", "近1年", "全部"], index=0)
        ma_text = st.text_input("MA", value="5,10,20")
        try:
            ma_list = tuple(int(x.strip()) for x in ma_text.split(",") if x.strip())
        except Exception:
            ma_list = (5, 10, 20)
        if st.button("刷新行情缓存"):
            st.cache_data.clear()
            st.rerun()

    tab1, tab2, tab3 = st.tabs(["基金估算", "行情图表", "股票代码"])

    with tab1:
        st.subheader("基金持仓估算")
        c1, c2 = st.columns(2)
        with c1:
            fund_amount = st.number_input("持有基金金额", min_value=0.0, value=0.0, step=1000.0)
        with c2:
            other_weight = st.number_input("其他/现金仓位%", min_value=0.0, max_value=100.0, value=0.0, step=1.0)

        selected, weights = [], {}
        cols = st.columns(2)
        for idx, (name, code) in enumerate(st.session_state.stock_map.items()):
            with cols[idx % 2]:
                checked = st.checkbox(name, value=False, key=f"check_{name}")
                weight = st.number_input(f"{name} 占仓%", min_value=0.0, max_value=100.0, value=0.0, step=0.5, key=f"weight_{name}")
                if checked:
                    selected.append(name)
                    weights[name] = weight

        total_weight = sum(weights.values()) + other_weight
        if total_weight > 100:
            st.error(f"当前合计仓位 {total_weight:.2f}%，超过100%。")
        else:
            st.info(f"股票仓位 {sum(weights.values()):.2f}%｜其他/现金 {other_weight:.2f}%｜合计 {total_weight:.2f}%")

        if st.button("估算基金涨跌和盈亏", type="primary"):
            rows = []
            fund_return = 0.0
            for name in selected:
                code = st.session_state.stock_map.get(name, "")
                if not code:
                    rows.append({"股票": name, "代码": "", "权重%": weights[name], "涨跌幅%": None, "贡献%": None, "估算盈亏": None, "备注": "缺少代码"})
                    continue
                try:
                    df = filter_period(fetch_yahoo(code, frequency), period_range)
                    r = compute_period_return(df)
                    contribution = r * weights[name] / 100
                    pnl = fund_amount * contribution / 100
                    fund_return += contribution
                    rows.append({"股票": name, "代码": code, "权重%": weights[name], "涨跌幅%": r, "贡献%": contribution, "估算盈亏": pnl, "备注": ""})
                except Exception as e:
                    rows.append({"股票": name, "代码": code, "权重%": weights[name], "涨跌幅%": None, "贡献%": None, "估算盈亏": None, "备注": str(e)})

            pnl_total = fund_amount * fund_return / 100
            current_amount = fund_amount + pnl_total
            m1, m2, m3 = st.columns(3)
            m1.metric("估算基金涨跌幅", f"{fund_return:.2f}%")
            m2.metric("估算盈亏", f"{pnl_total:,.2f}")
            m3.metric("估算当前金额", f"{current_amount:,.2f}")
            st.dataframe(pd.DataFrame(rows), use_container_width=True)

    with tab2:
        st.subheader("行情图表")
        names = list(st.session_state.stock_map.keys())
        selected_name = st.selectbox("股票", names)
        code = st.text_input("股票代码", value=st.session_state.stock_map.get(selected_name, ""))
        if code:
            st.session_state.stock_map[selected_name] = code

        if st.button("搜索代码"):
            found = find_code_by_name(selected_name)
            if found:
                st.session_state.stock_map[selected_name] = found
                st.success(f"{selected_name} = {found}")
                st.rerun()
            else:
                st.warning("未搜索到，请手动输入。")

        code = st.session_state.stock_map.get(selected_name, "")
        if not code:
            st.warning("该股票暂无代码。")
        else:
            try:
                df = add_indicators(filter_period(fetch_yahoo(code, frequency), period_range), ma_list)
                st.metric(f"{selected_name} 区间涨跌幅", f"{compute_period_return(df):.2f}%")
                st.plotly_chart(make_kline_figure(df, f"{selected_name} {frequency} K线 + MA", ma_list), use_container_width=True)
                st.plotly_chart(make_kdj_figure(df, f"{selected_name} KDJ"), use_container_width=True)
            except Exception as e:
                st.error(f"获取行情失败：{e}")

    with tab3:
        st.subheader("股票代码维护")
        rows = [{"股票名称": k, "代码": v} for k, v in st.session_state.stock_map.items()]
        edited = st.data_editor(pd.DataFrame(rows), num_rows="dynamic", use_container_width=True)
        if st.button("保存到当前会话"):
            new_map = {}
            for _, row in edited.iterrows():
                name = str(row.get("股票名称", "")).strip()
                code = str(row.get("代码", "")).strip()
                if name:
                    new_map[name] = code
            st.session_state.stock_map = new_map
            st.success("已保存。")


if __name__ == "__main__":
    main()
