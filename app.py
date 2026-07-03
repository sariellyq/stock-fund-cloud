
from datetime import datetime
import json
import uuid

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

try:
    import akshare as ak
except Exception:
    ak = None


APP_TITLE = "基金盘中估算工具"

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


def init_state():
    if "stock_map" not in st.session_state:
        st.session_state.stock_map = DEFAULT_STOCKS.copy()
    if "funds" not in st.session_state:
        st.session_state.funds = {}
    if "active_fund_id" not in st.session_state:
        st.session_state.active_fund_id = None


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
        low = str(c).lower()
        if low in ("date", "datetime"):
            rename[c] = "datetime"
        elif low == "open":
            rename[c] = "open"
        elif low == "high":
            rename[c] = "high"
        elif low == "low":
            rename[c] = "low"
        elif low == "close":
            rename[c] = "close"
        elif low == "volume":
            rename[c] = "volume"
    df = df.rename(columns=rename)

    required = ["datetime", "open", "high", "low", "close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError("Yahoo字段缺失：" + ", ".join(missing))

    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    try:
        if getattr(df["datetime"].dt, "tz", None) is not None:
            df["datetime"] = df["datetime"].dt.tz_convert(None)
    except Exception:
        pass

    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(subset=["datetime", "open", "high", "low", "close"])
    return df.sort_values("datetime").reset_index(drop=True)


@st.cache_data(ttl=120)
def fetch_intraday_5m(code: str) -> pd.DataFrame:
    symbol = china_yahoo_symbol(code)
    df = yf.download(symbol, period="5d", interval="5m", progress=False, auto_adjust=False, threads=False)
    df = normalize_yahoo_df(df)
    if df.empty:
        raise RuntimeError(f"Yahoo无5分钟数据：{symbol}")
    df["trade_date"] = df["datetime"].dt.date
    return df


def get_latest_day_data(code: str):
    df = fetch_intraday_5m(code)
    dates = sorted(df["trade_date"].dropna().unique())
    if len(dates) < 2:
        raise RuntimeError("不足两个交易日数据，无法计算今日涨跌幅。")
    today = dates[-1]
    prev = dates[-2]
    today_df = df[df["trade_date"] == today].copy()
    prev_df = df[df["trade_date"] == prev].copy()
    if today_df.empty or prev_df.empty:
        raise RuntimeError("当日或前一交易日数据为空。")

    prev_close = prev_df["close"].dropna().iloc[-1]
    latest = today_df["close"].dropna().iloc[-1]
    today_pct = (latest / prev_close - 1) * 100 if prev_close else 0
    today_df["today_pct"] = (today_df["close"] / prev_close - 1) * 100 if prev_close else 0
    return today_df, today_pct, latest, prev_close, str(today)


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


def make_intraday_figure(df, title):
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df["datetime"],
        open=df["open"],
        high=df["high"],
        low=df["low"],
        close=df["close"],
        name="5分钟K"
    ))
    for ma in (5, 10, 20):
        col = f"MA{ma}"
        if col in df.columns:
            fig.add_trace(go.Scatter(x=df["datetime"], y=df[col], mode="lines", name=col))
    fig.update_layout(title=title, height=520, xaxis_rangeslider_visible=False)
    return fig


def make_pct_line(df, title):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["datetime"], y=df["today_pct"], mode="lines", name="今日涨跌幅%"))
    fig.update_layout(title=title, height=280, yaxis_title="涨跌幅%")
    return fig


def make_fund_intraday_line(fund):
    """
    生成基金每5分钟估算曲线。
    只计算已知重仓股票 + 现金 + 其他资产。
    未知股票不计算，按0处理。
    """
    positions = fund.get("positions", [])
    amount = float(fund.get("amount", 0) or 0)
    cash_weight = float(fund.get("cash_weight", 0) or 0)
    other_weight = float(fund.get("other_weight", 0) or 0)
    other_return = float(fund.get("other_return", 0) or 0)

    parts = []
    errors = []
    for pos in positions:
        name = pos.get("stock_name", "")
        code = pos.get("code", "")
        weight = float(pos.get("weight", 0) or 0)
        try:
            df, pct, latest, prev_close, trade_date = get_latest_day_data(code)
            part = df[["datetime", "today_pct"]].copy()
            part = part.rename(columns={"today_pct": name})
            part[name] = part[name] * weight / 100
            parts.append(part)
        except Exception as e:
            errors.append(f"{name}: {e}")

    if not parts:
        return pd.DataFrame(), errors

    merged = parts[0]
    for part in parts[1:]:
        merged = pd.merge_asof(
            merged.sort_values("datetime"),
            part.sort_values("datetime"),
            on="datetime",
            direction="nearest",
            tolerance=pd.Timedelta(minutes=7)
        )

    stock_cols = [c for c in merged.columns if c != "datetime"]
    for c in stock_cols:
        merged[c] = pd.to_numeric(merged[c], errors="coerce").fillna(0)

    stock_contribution = merged[stock_cols].sum(axis=1)
    cash_contribution = 0  # 现金默认0
    other_contribution = other_return * other_weight / 100
    merged["保守估算涨跌幅%"] = stock_contribution + cash_contribution + other_contribution
    merged["估算盈亏"] = amount * merged["保守估算涨跌幅%"] / 100
    return merged[["datetime", "保守估算涨跌幅%", "估算盈亏"]], errors


def make_fund_line_figure(df, title):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["datetime"], y=df["保守估算涨跌幅%"], mode="lines", name="基金估算涨跌幅%"))
    fig.update_layout(title=title, height=360, yaxis_title="估算涨跌幅%")
    return fig


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


def estimate_fund_now(fund):
    rows = []
    conservative_return = 0.0
    amount = float(fund.get("amount", 0) or 0)
    cash_weight = float(fund.get("cash_weight", 0) or 0)
    other_weight = float(fund.get("other_weight", 0) or 0)
    other_return = float(fund.get("other_return", 0) or 0)

    known_stock_weight = sum(float(p.get("weight", 0) or 0) for p in fund.get("positions", []))
    unknown_weight = max(0, 100 - known_stock_weight - cash_weight - other_weight)

    for pos in fund.get("positions", []):
        name = pos["stock_name"]
        code = pos["code"]
        weight = float(pos.get("weight", 0) or 0)
        try:
            day_df, pct, latest, prev_close, trade_date = get_latest_day_data(code)
            contribution = pct * weight / 100
            pnl = amount * contribution / 100
            conservative_return += contribution
            rows.append({
                "类型": "已知重仓股票",
                "名称": name,
                "代码": code,
                "占比%": weight,
                "涨跌幅%": pct,
                "基金贡献%": contribution,
                "估算盈亏": pnl,
                "最新价": latest,
                "前收": prev_close,
                "交易日": trade_date,
                "状态": "OK",
            })
        except Exception as e:
            rows.append({
                "类型": "已知重仓股票",
                "名称": name,
                "代码": code,
                "占比%": weight,
                "涨跌幅%": None,
                "基金贡献%": None,
                "估算盈亏": None,
                "最新价": None,
                "前收": None,
                "交易日": "",
                "状态": str(e),
            })

    # 现金默认0涨跌
    rows.append({
        "类型": "现金",
        "名称": "现金",
        "代码": "",
        "占比%": cash_weight,
        "涨跌幅%": 0,
        "基金贡献%": 0,
        "估算盈亏": 0,
        "最新价": None,
        "前收": None,
        "交易日": "",
        "状态": "现金默认0涨跌",
    })

    # 其他资产可手动输入估算涨跌
    other_contribution = other_return * other_weight / 100
    conservative_return += other_contribution
    rows.append({
        "类型": "其他资产",
        "名称": "其他资产",
        "代码": "",
        "占比%": other_weight,
        "涨跌幅%": other_return,
        "基金贡献%": other_contribution,
        "估算盈亏": amount * other_contribution / 100,
        "最新价": None,
        "前收": None,
        "交易日": "",
        "状态": "按手动输入涨跌幅估算",
    })

    rows.append({
        "类型": "未知股票/未知仓位",
        "名称": "未知仓位",
        "代码": "",
        "占比%": unknown_weight,
        "涨跌幅%": 0,
        "基金贡献%": 0,
        "估算盈亏": 0,
        "最新价": None,
        "前收": None,
        "交易日": "",
        "状态": "保守估算：未知仓位不计算，按0处理",
    })

    conservative_pnl = amount * conservative_return / 100
    conservative_value = amount + conservative_pnl

    known_coverage = min(100, known_stock_weight + cash_weight + other_weight)

    # 推演估算：仅作为参考。假设未知股票与已知股票平均走势相近，现金和其他资产仍按设定。
    if known_stock_weight > 0:
        stock_contribution = conservative_return - other_contribution
        inferred_stock_total = stock_contribution / known_stock_weight * (known_stock_weight + unknown_weight)
        inferred_return = inferred_stock_total + other_contribution
    else:
        inferred_return = conservative_return
    inferred_pnl = amount * inferred_return / 100
    inferred_value = amount + inferred_pnl

    summary = {
        "known_stock_weight": known_stock_weight,
        "cash_weight": cash_weight,
        "other_weight": other_weight,
        "unknown_weight": unknown_weight,
        "known_coverage": known_coverage,
        "conservative_return": conservative_return,
        "conservative_pnl": conservative_pnl,
        "conservative_value": conservative_value,
        "inferred_return": inferred_return,
        "inferred_pnl": inferred_pnl,
        "inferred_value": inferred_value,
    }
    return summary, rows


def download_config_button():
    data = {
        "stock_map": st.session_state.stock_map,
        "funds": st.session_state.funds,
        "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    st.download_button(
        "下载当前配置JSON",
        data=json.dumps(data, ensure_ascii=False, indent=2),
        file_name=f"fund_config_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
        mime="application/json",
    )


def main():
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    init_state()

    st.title(APP_TITLE)
    st.caption("盘中使用：股票看当日5分钟K；基金按已知重仓股票 + 现金 + 其他资产进行保守估算。未知仓位不计算。数据通常有延迟，仅用于估算，不构成投资建议。")

    with st.sidebar:
        st.header("操作")
        if st.button("刷新行情"):
            st.cache_data.clear()
            st.rerun()

        uploaded = st.file_uploader("导入配置JSON", type=["json"])
        if uploaded is not None:
            try:
                cfg = json.load(uploaded)
                if "stock_map" in cfg:
                    st.session_state.stock_map = cfg["stock_map"]
                if "funds" in cfg:
                    st.session_state.funds = cfg["funds"]
                    if st.session_state.funds and not st.session_state.active_fund_id:
                        st.session_state.active_fund_id = next(iter(st.session_state.funds.keys()))
                st.success("配置已导入。")
            except Exception as e:
                st.error(f"导入失败：{e}")

        download_config_button()

    tab_fund, tab_stock, tab_manage = st.tabs(["基金估算", "股票5分钟K", "股票/基金管理"])

    with tab_fund:
        st.subheader("基金估算")

        if not st.session_state.funds:
            st.info("请先到“股票/基金管理”创建基金记录并添加重仓股票。")
        else:
            fund_names = {fid: f"{fund.get('name','未命名')}｜金额 {fund.get('amount',0):,.2f}" for fid, fund in st.session_state.funds.items()}
            selected_fid = st.selectbox(
                "选择基金",
                list(fund_names.keys()),
                format_func=lambda fid: fund_names[fid],
                index=list(fund_names.keys()).index(st.session_state.active_fund_id) if st.session_state.active_fund_id in fund_names else 0
            )
            st.session_state.active_fund_id = selected_fid
            fund = st.session_state.funds[selected_fid]

            if st.button("计算当前基金估算", type="primary"):
                summary, rows = estimate_fund_now(fund)

                st.write("### 仓位覆盖")
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("已知股票", f"{summary['known_stock_weight']:.2f}%")
                c2.metric("现金", f"{summary['cash_weight']:.2f}%")
                c3.metric("其他资产", f"{summary['other_weight']:.2f}%")
                c4.metric("未知仓位", f"{summary['unknown_weight']:.2f}%")

                st.write("### 保守估算")
                m1, m2, m3 = st.columns(3)
                m1.metric("保守估算涨跌幅", f"{summary['conservative_return']:.2f}%")
                m2.metric("估算盈亏", f"{summary['conservative_pnl']:,.2f}")
                m3.metric("估算当前金额", f"{summary['conservative_value']:,.2f}")

                st.write("### 推演估算（仅参考）")
                p1, p2, p3 = st.columns(3)
                p1.metric("推演涨跌幅", f"{summary['inferred_return']:.2f}%")
                p2.metric("推演盈亏", f"{summary['inferred_pnl']:,.2f}")
                p3.metric("推演当前金额", f"{summary['inferred_value']:,.2f}")
                st.caption("推演估算假设未知股票与已知股票平均走势相近，仅供参考；默认应看保守估算。")

                df_rows = pd.DataFrame(rows)
                st.dataframe(df_rows, use_container_width=True)

                line_df, errors = make_fund_intraday_line(fund)
                if not line_df.empty:
                    st.write("### 基金盘中5分钟估算走势")
                    st.plotly_chart(make_fund_line_figure(line_df, fund.get("name", "基金") + " 盘中估算涨跌幅"), use_container_width=True)
                    latest_line = line_df.iloc[-1]
                    st.caption(f"最新5分钟点：{latest_line['datetime']}｜估算涨跌幅 {latest_line['保守估算涨跌幅%']:.2f}%｜估算盈亏 {latest_line['估算盈亏']:,.2f}")
                if errors:
                    st.warning("部分股票盘中曲线获取失败：" + "；".join(errors[:5]))

            st.write("当前重仓：")
            st.dataframe(pd.DataFrame(fund.get("positions", [])), use_container_width=True)

    with tab_stock:
        st.subheader("股票当日5分钟K")
        names = list(st.session_state.stock_map.keys())
        selected_name = st.selectbox("股票", names)
        code = st.text_input("股票代码", value=st.session_state.stock_map.get(selected_name, ""))
        if code:
            st.session_state.stock_map[selected_name] = code

        if not code:
            st.warning("该股票暂无代码，请到管理页添加。")
        else:
            try:
                day_df, pct, latest, prev_close, trade_date = get_latest_day_data(code)
                day_df = add_indicators(day_df)
                c1, c2, c3 = st.columns(3)
                c1.metric("今日涨跌幅", f"{pct:.2f}%")
                c2.metric("最新价", f"{latest:.2f}")
                c3.metric("前收", f"{prev_close:.2f}")
                st.caption(f"交易日：{trade_date}")
                st.plotly_chart(make_intraday_figure(day_df, f"{selected_name} 当日5分钟K"), use_container_width=True)
                st.plotly_chart(make_pct_line(day_df, f"{selected_name} 当日涨跌幅走势"), use_container_width=True)
            except Exception as e:
                st.error(f"获取行情失败：{e}")

    with tab_manage:
        st.subheader("股票管理")

        with st.expander("新增/修改股票", expanded=True):
            c1, c2, c3 = st.columns([1, 1, 1])
            with c1:
                new_name = st.text_input("股票名称")
            with c2:
                new_code = st.text_input("股票代码")
            with c3:
                st.write("")
                st.write("")
                if st.button("保存股票"):
                    if new_name and new_code:
                        st.session_state.stock_map[new_name.strip()] = new_code.strip().zfill(6)
                        st.success("股票已保存。")
                    else:
                        st.warning("请填写股票名称和代码。")

            search_name = st.text_input("按名称搜索代码")
            if st.button("搜索股票代码"):
                found = find_code_by_name(search_name)
                if found:
                    st.success(f"{search_name} = {found}")
                else:
                    st.warning("未搜索到，请手动输入。")

            stock_df = pd.DataFrame([{"股票名称": k, "代码": v} for k, v in st.session_state.stock_map.items()])
            edited_stock = st.data_editor(stock_df, num_rows="dynamic", use_container_width=True)
            if st.button("保存股票列表"):
                new_map = {}
                for _, row in edited_stock.iterrows():
                    name = str(row.get("股票名称", "")).strip()
                    code = str(row.get("代码", "")).strip()
                    if name:
                        new_map[name] = code
                st.session_state.stock_map = new_map
                st.success("股票列表已保存到当前会话。")

        st.subheader("基金管理")

        with st.expander("第一步：创建/选择基金记录", expanded=True):
            fund_name = st.text_input("基金名称")
            fund_amount = st.number_input("基金持有金额", min_value=0.0, value=0.0, step=1000.0)
            cash_weight = st.number_input("现金占比%", min_value=0.0, max_value=100.0, value=0.0, step=0.5)
            other_weight = st.number_input("其他资产占比%", min_value=0.0, max_value=100.0, value=0.0, step=0.5)
            other_return = st.number_input("其他资产今日估算涨跌幅%", min_value=-100.0, max_value=100.0, value=0.0, step=0.1)

            if st.button("创建基金记录"):
                if not fund_name:
                    st.warning("请填写基金名称。")
                else:
                    fid = str(uuid.uuid4())
                    st.session_state.funds[fid] = {
                        "name": fund_name,
                        "amount": fund_amount,
                        "cash_weight": cash_weight,
                        "other_weight": other_weight,
                        "other_return": other_return,
                        "positions": []
                    }
                    st.session_state.active_fund_id = fid
                    st.success("基金记录已创建。")

            if st.session_state.funds:
                fund_options = {fid: fund.get("name", "未命名") for fid, fund in st.session_state.funds.items()}
                active = st.selectbox("当前编辑基金", list(fund_options.keys()), format_func=lambda fid: fund_options[fid])
                st.session_state.active_fund_id = active

                fund = st.session_state.funds[active]
                fund["name"] = st.text_input("修改基金名称", value=fund.get("name", ""))
                fund["amount"] = st.number_input("修改持有金额", min_value=0.0, value=float(fund.get("amount", 0) or 0), step=1000.0)
                fund["cash_weight"] = st.number_input("修改现金占比%", min_value=0.0, max_value=100.0, value=float(fund.get("cash_weight", 0) or 0), step=0.5)
                fund["other_weight"] = st.number_input("修改其他资产占比%", min_value=0.0, max_value=100.0, value=float(fund.get("other_weight", 0) or 0), step=0.5)
                fund["other_return"] = st.number_input("修改其他资产今日估算涨跌幅%", min_value=-100.0, max_value=100.0, value=float(fund.get("other_return", 0) or 0), step=0.1)

        if st.session_state.active_fund_id and st.session_state.active_fund_id in st.session_state.funds:
            fund = st.session_state.funds[st.session_state.active_fund_id]
            with st.expander("第二步：添加重仓股票", expanded=True):
                stock_names = list(st.session_state.stock_map.keys())
                stock_pick = st.selectbox("过滤选择股票", stock_names)
                stock_weight = st.number_input("重仓占比%", min_value=0.0, max_value=100.0, value=0.0, step=0.5)

                if st.button("添加到基金重仓"):
                    code = st.session_state.stock_map.get(stock_pick, "")
                    if not code:
                        st.warning("该股票没有代码，请先补充代码。")
                    elif stock_weight <= 0:
                        st.warning("请输入大于0的占比。")
                    else:
                        fund["positions"].append({"stock_name": stock_pick, "code": code, "weight": stock_weight})
                        st.success("已添加。")

                st.write("当前基金重仓：")
                pos_df = pd.DataFrame(fund.get("positions", []))
                edited_pos = st.data_editor(pos_df, num_rows="dynamic", use_container_width=True)
                if st.button("保存重仓列表"):
                    fund["positions"] = edited_pos.to_dict("records")
                    st.success("重仓列表已保存。")

                stock_total = sum(float(p.get("weight", 0) or 0) for p in fund.get("positions", []))
                total_known = stock_total + float(fund.get("cash_weight", 0) or 0) + float(fund.get("other_weight", 0) or 0)
                unknown = max(0, 100 - total_known)
                st.info(f"已知股票 {stock_total:.2f}%｜现金 {float(fund.get('cash_weight', 0) or 0):.2f}%｜其他资产 {float(fund.get('other_weight', 0) or 0):.2f}%｜未知仓位 {unknown:.2f}%")

            if st.button("删除当前基金记录"):
                del st.session_state.funds[st.session_state.active_fund_id]
                st.session_state.active_fund_id = next(iter(st.session_state.funds.keys()), None)
                st.success("已删除。")
                st.rerun()


if __name__ == "__main__":
    main()
