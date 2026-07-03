
from datetime import datetime, time
import uuid

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
from supabase import create_client, Client

try:
    import akshare as ak
except Exception:
    ak = None


APP_TITLE = "基金盘中估算工具 v4.2"

DEFAULT_STOCKS = [
    {"name": "中际旭创", "code": "300308"},
    {"name": "新易盛", "code": "300502"},
    {"name": "天孚通信", "code": "300394"},
    {"name": "源杰科技", "code": "688498"},
    {"name": "长飞光纤", "code": "601869"},
    {"name": "麦格米特", "code": "002851"},
    {"name": "永鼎股份", "code": "600105"},
    {"name": "德科立", "code": "688205"},
    {"name": "东山精密", "code": "002384"},
    {"name": "凯格精机", "code": "301338"},
    {"name": "腾景科技", "code": "688195"},
    {"name": "亨通光电", "code": "600487"},
]

DEFAULT_INDUSTRY_ETFS = [
    {"industry": "科技", "etf_code": "515000"},
    {"industry": "制造", "etf_code": "159997"},
    {"industry": "消费", "etf_code": "159928"},
    {"industry": "金融", "etf_code": "510230"},
    {"industry": "医药", "etf_code": "512010"},
    {"industry": "资源", "etf_code": "510410"},
]


def get_supabase() -> Client:
    url = st.secrets.get("SUPABASE_URL", "")
    key = st.secrets.get("SUPABASE_KEY", "")
    if not url or not key:
        st.error("缺少 Supabase Secrets：请在 Streamlit Cloud → Settings → Secrets 添加 SUPABASE_URL 和 SUPABASE_KEY。")
        st.stop()
    return create_client(url, key)


@st.cache_resource
def supabase_client():
    return get_supabase()


def sb():
    return supabase_client()


def safe_execute(desc, func):
    try:
        return func()
    except Exception as e:
        st.error(f"{desc}失败：{e}")
        return None


def fetch_table(table, order_col=None):
    q = sb().table(table).select("*")
    if order_col:
        q = q.order(order_col)
    return q.execute().data or []


def insert_row(table, row):
    return sb().table(table).insert(row).execute()


def update_row(table, row_id, values):
    return sb().table(table).update(values).eq("id", row_id).execute()


def delete_row(table, row_id):
    return sb().table(table).delete().eq("id", row_id).execute()


def ensure_default_data():
    # 仅在数据库为空时初始化默认股票和ETF
    stocks = fetch_table("stocks")
    if not stocks:
        for s in DEFAULT_STOCKS:
            insert_row("stocks", {"name": s["name"], "code": s["code"]})

    etfs = fetch_table("industry_etfs")
    if not etfs:
        for e in DEFAULT_INDUSTRY_ETFS:
            insert_row("industry_etfs", {"industry": e["industry"], "etf_code": e["etf_code"]})


def china_yahoo_symbol(code: str) -> str:
    code = str(code).strip().zfill(6)
    if code.startswith(("6", "5", "688")):
        return f"{code}.SS"
    return f"{code}.SZ"


def to_china_time(series):
    dt = pd.to_datetime(series, errors="coerce")
    try:
        if getattr(dt.dt, "tz", None) is not None:
            dt = dt.dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
    except Exception:
        pass
    return dt


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

    df["datetime"] = to_china_time(df["datetime"])
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(subset=["datetime", "open", "high", "low", "close"])
    return df.sort_values("datetime").reset_index(drop=True)


def filter_cn_trading_hours(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    t = df["datetime"].dt.time
    morning = (t >= time(9, 30)) & (t <= time(11, 30))
    afternoon = (t >= time(13, 0)) & (t <= time(15, 0))
    return df[morning | afternoon].copy()


@st.cache_data(ttl=90)
def fetch_intraday_5m(code: str) -> pd.DataFrame:
    symbol = china_yahoo_symbol(code)
    df = yf.download(symbol, period="5d", interval="5m", progress=False, auto_adjust=False, threads=False)
    df = normalize_yahoo_df(df)
    if df.empty:
        raise RuntimeError(f"Yahoo无5分钟数据：{symbol}")
    df = filter_cn_trading_hours(df)
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
    last_time = today_df["datetime"].max()
    return today_df, today_pct, latest, prev_close, str(today), last_time


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


def signed_html(value, suffix="%", digits=2, big=False):
    try:
        v = float(value)
    except Exception:
        v = 0.0
    color = "#d32f2f" if v >= 0 else "#2e7d32"
    sign = "+" if v >= 0 else ""
    size = "30px" if big else "20px"
    return f"<span style='color:{color};font-weight:800;font-size:{size}'>{sign}{v:.{digits}f}{suffix}</span>"


def signed_money_html(value, digits=2, big=False):
    try:
        v = float(value)
    except Exception:
        v = 0.0
    color = "#d32f2f" if v >= 0 else "#2e7d32"
    sign = "+" if v >= 0 else ""
    size = "30px" if big else "20px"
    return f"<span style='color:{color};font-weight:800;font-size:{size}'>{sign}{v:,.{digits}f}</span>"


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


def make_fund_line_figure(df, title):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["datetime"], y=df["保守估算涨跌幅%"], mode="lines", name="保守估算"))
    if "行业补充估算涨跌幅%" in df.columns:
        fig.add_trace(go.Scatter(x=df["datetime"], y=df["行业补充估算涨跌幅%"], mode="lines", name="行业补充估算"))
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


def get_industry_etf_map():
    rows = fetch_table("industry_etfs", "industry")
    return {r["industry"]: r["etf_code"] for r in rows}


def get_industry_returns():
    etfs = get_industry_etf_map()
    rows = []
    ret_map = {}
    for industry, code in etfs.items():
        if not code:
            rows.append({"行业": industry, "ETF代码": code, "今日涨跌幅%": None, "状态": "缺少ETF代码"})
            continue
        try:
            df, pct, latest, prev_close, trade_date, last_time = get_latest_day_data(code)
            ret_map[industry] = pct
            rows.append({
                "行业": industry,
                "ETF代码": code,
                "今日涨跌幅%": pct,
                "最新价": latest,
                "前收": prev_close,
                "最后时间": last_time,
                "状态": "OK",
            })
        except Exception as e:
            rows.append({"行业": industry, "ETF代码": code, "今日涨跌幅%": None, "状态": str(e)})
    return ret_map, rows


def get_fund_positions(fund_id):
    return sb().table("fund_positions").select("*").eq("fund_id", fund_id).order("created_at").execute().data or []


def get_fund_industry(fund_id):
    """Backward compatible old fixed industry table."""
    rows = sb().table("fund_industry").select("*").eq("fund_id", fund_id).execute().data or []
    if not rows:
        return {
            "tech": 0, "manufacturing": 0, "consumption": 0, "finance": 0, "medical": 0
        }
    return rows[0]


def get_fund_industry_allocations(fund_id):
    """Dynamic industries shown in fund configuration panel."""
    try:
        rows = sb().table("fund_industry_allocations").select("*").eq("fund_id", fund_id).order("industry").execute().data or []
        return rows
    except Exception:
        # If user hasn't run v4.1 schema yet, fall back to old table.
        old = get_fund_industry(fund_id)
        mapping = {
            "科技": old.get("tech", 0),
            "制造": old.get("manufacturing", 0),
            "消费": old.get("consumption", 0),
            "金融": old.get("finance", 0),
            "医药": old.get("medical", 0),
        }
        return [{"fund_id": fund_id, "industry": k, "weight": float(v or 0)} for k, v in mapping.items()]


def save_fund_industry_allocations(fund_id, allocations):
    """Replace dynamic industry allocations for a fund."""
    try:
        sb().table("fund_industry_allocations").delete().eq("fund_id", fund_id).execute()
        data = []
        for item in allocations:
            industry = str(item.get("industry", "")).strip()
            if not industry:
                continue
            try:
                weight = float(item.get("weight", 0) or 0)
            except Exception:
                weight = 0
            data.append({"fund_id": fund_id, "industry": industry, "weight": weight})
        if data:
            sb().table("fund_industry_allocations").insert(data).execute()
        return True, ""
    except Exception as e:
        return False, str(e)


def upsert_fund_industry(fund_id, values):
    rows = sb().table("fund_industry").select("id").eq("fund_id", fund_id).execute().data or []
    payload = {
        "fund_id": fund_id,
        "tech": values.get("tech", 0),
        "manufacturing": values.get("manufacturing", 0),
        "consumption": values.get("consumption", 0),
        "finance": values.get("finance", 0),
        "medical": values.get("medical", 0),
    }
    if rows:
        return sb().table("fund_industry").update(payload).eq("id", rows[0]["id"]).execute()
    return sb().table("fund_industry").insert(payload).execute()


def calc_industry_weighted_return(fund_id):
    allocations = get_fund_industry_allocations(fund_id)
    weights = {}
    for row in allocations:
        industry = str(row.get("industry", "")).strip()
        if not industry:
            continue
        try:
            weights[industry] = float(row.get("weight", 0) or 0)
        except Exception:
            weights[industry] = 0

    total = sum(weights.values())
    ret_map, industry_rows = get_industry_returns()

    if total <= 0:
        return 0, industry_rows, "未填写行业分布，行业补充贡献为0。"

    weighted = 0
    valid_weight = 0
    missing = []
    for ind, w in weights.items():
        if w <= 0:
            continue
        if ind in ret_map:
            weighted += ret_map[ind] * w
            valid_weight += w
        else:
            missing.append(ind)

    if valid_weight <= 0:
        return 0, industry_rows, "行业ETF均未成功获取，行业补充贡献为0。"

    industry_return = weighted / valid_weight
    note = f"行业分布输入合计 {total:.2f}%，有效行业权重 {valid_weight:.2f}%，已归一化估算未知股票走势。"
    if missing:
        note += " 未匹配ETF行业：" + "、".join(missing)
    return industry_return, industry_rows, note


def estimate_fund_now(fund):
    fund_id = fund["id"]
    positions = get_fund_positions(fund_id)

    rows = []
    conservative_return = 0.0
    amount = float(fund.get("amount", 0) or 0)

    cash_weight = float(fund.get("cash_weight", 0) or 0)
    bond_weight = float(fund.get("bond_weight", 0) or 0)
    bond_return = float(fund.get("bond_return", 0) or 0)
    other_weight = float(fund.get("other_weight", 0) or 0)
    other_return = float(fund.get("other_return", 0) or 0)

    known_stock_weight = sum(float(p.get("weight", 0) or 0) for p in positions)
    unknown_weight = max(0, 100 - known_stock_weight - cash_weight - bond_weight - other_weight)

    last_times = []
    for pos in positions:
        name = pos["stock_name"]
        code = pos["stock_code"]
        weight = float(pos.get("weight", 0) or 0)
        try:
            day_df, pct, latest, prev_close, trade_date, last_time = get_latest_day_data(code)
            last_times.append(last_time)
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
                "最后时间": last_time,
                "状态": "OK",
            })
        except Exception as e:
            rows.append({"类型": "已知重仓股票", "名称": name, "代码": code, "占比%": weight, "状态": str(e)})

    rows.append({"类型": "现金", "名称": "现金", "占比%": cash_weight, "涨跌幅%": 0, "基金贡献%": 0, "估算盈亏": 0, "状态": "现金默认0涨跌"})

    bond_contribution = bond_return * bond_weight / 100
    conservative_return += bond_contribution
    rows.append({"类型": "债券", "名称": "债券", "占比%": bond_weight, "涨跌幅%": bond_return, "基金贡献%": bond_contribution, "估算盈亏": amount * bond_contribution / 100, "状态": "手动估算"})

    other_contribution = other_return * other_weight / 100
    conservative_return += other_contribution
    rows.append({"类型": "其他资产", "名称": "其他资产", "占比%": other_weight, "涨跌幅%": other_return, "基金贡献%": other_contribution, "估算盈亏": amount * other_contribution / 100, "状态": "手动估算"})

    rows.append({"类型": "未知仓位", "名称": "未知仓位", "占比%": unknown_weight, "涨跌幅%": 0, "基金贡献%": 0, "估算盈亏": 0, "状态": "保守估算按0处理"})

    conservative_pnl = amount * conservative_return / 100
    conservative_value = amount + conservative_pnl

    industry_return, industry_rows, industry_note = calc_industry_weighted_return(fund_id)
    industry_unknown_contribution = industry_return * unknown_weight / 100
    industry_supplement_return = conservative_return + industry_unknown_contribution
    industry_supplement_pnl = amount * industry_supplement_return / 100
    industry_supplement_value = amount + industry_supplement_pnl

    summary = {
        "known_stock_weight": known_stock_weight,
        "cash_weight": cash_weight,
        "bond_weight": bond_weight,
        "other_weight": other_weight,
        "unknown_weight": unknown_weight,
        "conservative_return": conservative_return,
        "conservative_pnl": conservative_pnl,
        "conservative_value": conservative_value,
        "industry_return": industry_return,
        "industry_unknown_contribution": industry_unknown_contribution,
        "industry_supplement_return": industry_supplement_return,
        "industry_supplement_pnl": industry_supplement_pnl,
        "industry_supplement_value": industry_supplement_value,
        "last_time": max(last_times) if last_times else "",
        "industry_note": industry_note,
    }
    return summary, rows, industry_rows


def make_fund_intraday_line(fund):
    positions = get_fund_positions(fund["id"])
    amount = float(fund.get("amount", 0) or 0)
    bond_weight = float(fund.get("bond_weight", 0) or 0)
    bond_return = float(fund.get("bond_return", 0) or 0)
    other_weight = float(fund.get("other_weight", 0) or 0)
    other_return = float(fund.get("other_return", 0) or 0)
    cash_weight = float(fund.get("cash_weight", 0) or 0)
    known_stock_weight = sum(float(p.get("weight", 0) or 0) for p in positions)
    unknown_weight = max(0, 100 - known_stock_weight - cash_weight - bond_weight - other_weight)

    parts = []
    errors = []
    for pos in positions:
        name = pos.get("stock_name", "")
        code = pos.get("stock_code", "")
        weight = float(pos.get("weight", 0) or 0)
        try:
            df, pct, latest, prev_close, trade_date, last_time = get_latest_day_data(code)
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
        merged = pd.merge_asof(merged.sort_values("datetime"), part.sort_values("datetime"), on="datetime", direction="nearest", tolerance=pd.Timedelta(minutes=7))

    stock_cols = [c for c in merged.columns if c != "datetime"]
    for c in stock_cols:
        merged[c] = pd.to_numeric(merged[c], errors="coerce").fillna(0)

    stock_contribution = merged[stock_cols].sum(axis=1)
    bond_contribution = bond_return * bond_weight / 100
    other_contribution = other_return * other_weight / 100
    merged["保守估算涨跌幅%"] = stock_contribution + bond_contribution + other_contribution

    industry_return, industry_rows, industry_note = calc_industry_weighted_return(fund["id"])
    industry_unknown_contribution = industry_return * unknown_weight / 100
    merged["行业补充估算涨跌幅%"] = merged["保守估算涨跌幅%"] + industry_unknown_contribution
    merged["保守估算盈亏"] = amount * merged["保守估算涨跌幅%"] / 100
    merged["行业补充估算盈亏"] = amount * merged["行业补充估算涨跌幅%"] / 100
    return merged[["datetime", "保守估算涨跌幅%", "行业补充估算涨跌幅%", "保守估算盈亏", "行业补充估算盈亏"]], errors


def display_estimate_card(title, ret, pnl, value):
    st.markdown(
        f"""
        <div style="padding:14px;border:1px solid #eee;border-radius:12px;background:#fafafa;margin-bottom:10px;">
          <div style="font-size:18px;font-weight:800;margin-bottom:8px;">{title}</div>
          <div>估算涨跌幅：{signed_html(ret, "%", big=True)}</div>
          <div>估算盈亏：{signed_money_html(pnl, big=True)}</div>
          <div style="font-size:18px;font-weight:700;">估算当前金额：{value:,.2f}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def get_funds():
    return fetch_table("funds", "created_at")


def get_stocks():
    return fetch_table("stocks", "name")


def main():
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.title(APP_TITLE)
    st.caption("V4.2 Supabase 数据库版：刷新、关闭网页、重新部署后数据仍保存在数据库中。")

    with st.sidebar:
        st.header("操作")
        if st.button("初始化默认数据"):
            ensure_default_data()
            st.success("已初始化默认股票和行业ETF。")
        if st.button("刷新行情缓存"):
            st.cache_data.clear()
            st.rerun()

    tab_dashboard, tab_fund, tab_stock, tab_manage = st.tabs(["Dashboard", "基金估算", "股票5分钟K", "数据管理"])

    with tab_dashboard:
        st.subheader("Dashboard")
        funds = get_funds()
        stocks = get_stocks()
        etfs = fetch_table("industry_etfs", "industry")

        c1, c2, c3 = st.columns(3)
        c1.metric("基金数量", len(funds))
        c2.metric("股票数量", len(stocks))
        c3.metric("行业ETF数量", len(etfs))

        if funds and st.button("一键估算全部基金"):
            for fund in funds:
                with st.expander(fund["name"], expanded=True):
                    summary, rows, industry_rows = estimate_fund_now(fund)
                    a, b = st.columns(2)
                    with a:
                        display_estimate_card("保守估算", summary["conservative_return"], summary["conservative_pnl"], summary["conservative_value"])
                    with b:
                        display_estimate_card("行业补充估算", summary["industry_supplement_return"], summary["industry_supplement_pnl"], summary["industry_supplement_value"])

    with tab_fund:
        st.subheader("基金估算")
        funds = get_funds()
        if not funds:
            st.info("请先到“数据管理”创建基金记录。")
        else:
            fund_map = {f["id"]: f for f in funds}
            fund_id = st.selectbox("选择基金", list(fund_map.keys()), format_func=lambda fid: fund_map[fid]["name"])
            fund = fund_map[fund_id]

            if st.button("计算当前基金估算", type="primary"):
                summary, rows, industry_rows = estimate_fund_now(fund)

                st.write("### 仓位覆盖")
                c1, c2, c3, c4, c5 = st.columns(5)
                c1.metric("已知股票", f"{summary['known_stock_weight']:.2f}%")
                c2.metric("现金", f"{summary['cash_weight']:.2f}%")
                c3.metric("债券", f"{summary['bond_weight']:.2f}%")
                c4.metric("其他资产", f"{summary['other_weight']:.2f}%")
                c5.metric("未知仓位", f"{summary['unknown_weight']:.2f}%")
                if summary["last_time"]:
                    st.caption(f"行情最新时间（中国时间）：{summary['last_time']}")

                col_a, col_b = st.columns(2)
                with col_a:
                    display_estimate_card("保守估算", summary["conservative_return"], summary["conservative_pnl"], summary["conservative_value"])
                with col_b:
                    display_estimate_card("行业补充估算", summary["industry_supplement_return"], summary["industry_supplement_pnl"], summary["industry_supplement_value"])
                st.caption(summary["industry_note"])

                st.write("### 明细")
                st.dataframe(pd.DataFrame(rows), use_container_width=True)
                st.write("### 行业ETF数据")
                st.dataframe(pd.DataFrame(industry_rows), use_container_width=True)

                line_df, errors = make_fund_intraday_line(fund)
                if not line_df.empty:
                    st.plotly_chart(make_fund_line_figure(line_df, fund["name"] + " 盘中估算走势"), use_container_width=True)
                if errors:
                    st.warning("部分股票曲线获取失败：" + "；".join(errors[:5]))

            st.write("当前重仓")
            st.dataframe(pd.DataFrame(get_fund_positions(fund_id)), use_container_width=True)

    with tab_stock:
        st.subheader("股票当日5分钟K")
        stocks = get_stocks()
        if not stocks:
            st.info("请先初始化或添加股票。")
        else:
            stock_map = {s["id"]: s for s in stocks}
            sid = st.selectbox("股票", list(stock_map.keys()), format_func=lambda x: stock_map[x]["name"])
            stock = stock_map[sid]
            try:
                day_df, pct, latest, prev_close, trade_date, last_time = get_latest_day_data(stock["code"])
                day_df = add_indicators(day_df)
                c1, c2, c3, c4 = st.columns(4)
                c1.markdown("今日涨跌幅<br>" + signed_html(pct, "%", big=True), unsafe_allow_html=True)
                c2.metric("最新价", f"{latest:.2f}")
                c3.metric("前收", f"{prev_close:.2f}")
                c4.metric("最后时间", str(last_time).split(" ")[-1])
                st.caption(f"交易日：中国时间 {trade_date}")
                st.plotly_chart(make_intraday_figure(day_df, f"{stock['name']} 当日5分钟K（中国时间）"), use_container_width=True)
                st.plotly_chart(make_pct_line(day_df, f"{stock['name']} 当日涨跌幅走势"), use_container_width=True)
            except Exception as e:
                st.error(f"获取行情失败：{e}")

    with tab_manage:
        st.subheader("数据管理")

        with st.expander("股票管理", expanded=False):
            st.write("新增股票")
            c1, c2 = st.columns(2)
            with c1:
                s_name = st.text_input("股票名称")
            with c2:
                s_code = st.text_input("股票代码")
            if st.button("新增股票"):
                if s_name and s_code:
                    insert_row("stocks", {"name": s_name.strip(), "code": s_code.strip().zfill(6)})
                    st.success("已新增。")
                    st.rerun()

            stocks = get_stocks()
            st.dataframe(pd.DataFrame(stocks), use_container_width=True)
            if stocks:
                st.write("编辑/删除股票")
                stock_options = {s["id"]: f"{s['name']} {s['code']}" for s in stocks}
                sid = st.selectbox("选择股票记录", list(stock_options.keys()), format_func=lambda x: stock_options[x])
                cur = next(s for s in stocks if s["id"] == sid)
                en = st.text_input("修改股票名称", value=cur["name"])
                ec = st.text_input("修改股票代码", value=cur["code"])
                cc1, cc2 = st.columns(2)
                with cc1:
                    if st.button("保存股票修改"):
                        update_row("stocks", sid, {"name": en, "code": ec})
                        st.success("已保存。")
                        st.rerun()
                with cc2:
                    if st.button("删除股票"):
                        delete_row("stocks", sid)
                        st.success("已删除。")
                        st.rerun()

        with st.expander("行业ETF管理", expanded=False):
            etfs = fetch_table("industry_etfs", "industry")
            st.dataframe(pd.DataFrame(etfs), use_container_width=True)
            c1, c2 = st.columns(2)
            with c1:
                ind = st.text_input("行业")
            with c2:
                etf = st.text_input("ETF代码")
            if st.button("新增行业ETF"):
                if ind and etf:
                    insert_row("industry_etfs", {"industry": ind, "etf_code": etf})
                    st.success("已新增。")
                    st.rerun()
            if etfs:
                etf_options = {e["id"]: f"{e['industry']} {e['etf_code']}" for e in etfs}
                eid = st.selectbox("选择行业ETF记录", list(etf_options.keys()), format_func=lambda x: etf_options[x])
                cur = next(e for e in etfs if e["id"] == eid)
                ei = st.text_input("修改行业", value=cur["industry"])
                ec = st.text_input("修改ETF代码", value=cur["etf_code"])
                if st.button("保存行业ETF修改"):
                    update_row("industry_etfs", eid, {"industry": ei, "etf_code": ec})
                    st.success("已保存。")
                    st.rerun()

        with st.expander("基金管理", expanded=True):
            st.write("新增基金")
            f_name = st.text_input("基金名称")
            f_amount = st.number_input("持有金额", min_value=0.0, value=0.0, step=1000.0)
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                cash = st.number_input("现金占比%", min_value=0.0, max_value=100.0, value=0.0, step=0.5)
            with c2:
                bond = st.number_input("债券占比%", min_value=0.0, max_value=100.0, value=0.0, step=0.5)
            with c3:
                bond_ret = st.number_input("债券涨跌幅%", min_value=-100.0, max_value=100.0, value=0.0, step=0.1)
            with c4:
                other = st.number_input("其他资产占比%", min_value=0.0, max_value=100.0, value=0.0, step=0.5)
            other_ret = st.number_input("其他资产涨跌幅%", min_value=-100.0, max_value=100.0, value=0.0, step=0.1)

            if st.button("创建基金"):
                if f_name:
                    res = insert_row("funds", {
                        "name": f_name,
                        "amount": f_amount,
                        "cash_weight": cash,
                        "bond_weight": bond,
                        "bond_return": bond_ret,
                        "other_weight": other,
                        "other_return": other_ret,
                    })
                    new_id = res.data[0]["id"]
                    # 动态行业配置在下方“基金行业分布”中添加
                    st.success("基金已创建。")
                    st.rerun()

            funds = get_funds()
            if funds:
                fund_options = {f["id"]: f["name"] for f in funds}
                fid = st.selectbox("编辑基金", list(fund_options.keys()), format_func=lambda x: fund_options[x])
                fund = next(f for f in funds if f["id"] == fid)

                en = st.text_input("修改基金名称", value=fund["name"])
                ea = st.number_input("修改持有金额", min_value=0.0, value=float(fund.get("amount", 0) or 0), step=1000.0)
                ecash = st.number_input("修改现金占比%", min_value=0.0, max_value=100.0, value=float(fund.get("cash_weight", 0) or 0), step=0.5)
                ebond = st.number_input("修改债券占比%", min_value=0.0, max_value=100.0, value=float(fund.get("bond_weight", 0) or 0), step=0.5)
                ebond_ret = st.number_input("修改债券涨跌幅%", min_value=-100.0, max_value=100.0, value=float(fund.get("bond_return", 0) or 0), step=0.1)
                eother = st.number_input("修改其他资产占比%", min_value=0.0, max_value=100.0, value=float(fund.get("other_weight", 0) or 0), step=0.5)
                eother_ret = st.number_input("修改其他资产涨跌幅%", min_value=-100.0, max_value=100.0, value=float(fund.get("other_return", 0) or 0), step=0.1)

                st.write("基金行业分布")
                st.caption("改成下拉添加形式：从行业ETF列表选择行业，输入占比后添加。界面更简洁。")
                etfs_for_alloc = fetch_table("industry_etfs", "industry")
                current_alloc_rows = get_fund_industry_allocations(fid)

                if not etfs_for_alloc:
                    st.warning("还没有行业ETF，请先在“行业ETF管理”新增行业和ETF代码。")
                else:
                    industry_options = [x.get("industry", "") for x in etfs_for_alloc if x.get("industry", "")]
                    existing_industries = {x.get("industry") for x in current_alloc_rows}

                    add_cols = st.columns([2, 1, 1])
                    with add_cols[0]:
                        industry_pick = st.selectbox(
                            "选择行业",
                            industry_options,
                            key=f"industry_pick_{fid}"
                        )
                    with add_cols[1]:
                        industry_weight = st.number_input(
                            "行业占比%",
                            min_value=0.0,
                            max_value=100.0,
                            value=0.0,
                            step=0.5,
                            key=f"industry_weight_{fid}"
                        )
                    with add_cols[2]:
                        st.write("")
                        st.write("")
                        if st.button("添加/更新行业占比", key=f"add_industry_{fid}"):
                            new_allocs = []
                            updated = False
                            for r in current_alloc_rows:
                                if r.get("industry") == industry_pick:
                                    new_allocs.append({"industry": industry_pick, "weight": industry_weight})
                                    updated = True
                                else:
                                    new_allocs.append({"industry": r.get("industry"), "weight": r.get("weight", 0)})
                            if not updated:
                                new_allocs.append({"industry": industry_pick, "weight": industry_weight})
                            ok, err = save_fund_industry_allocations(fid, new_allocs)
                            if ok:
                                st.success("行业占比已添加/更新。")
                                st.rerun()
                            else:
                                st.error(f"保存失败：{err}")

                    if current_alloc_rows:
                        st.write("当前基金行业分布")
                        alloc_df = pd.DataFrame(current_alloc_rows)
                        show_cols = [c for c in ["industry", "weight"] if c in alloc_df.columns]
                        st.dataframe(alloc_df[show_cols], use_container_width=True)

                        edit_options = {
                            r["id"]: f"{r.get('industry','')} {float(r.get('weight',0) or 0):.2f}%"
                            for r in current_alloc_rows if r.get("id")
                        }
                        if edit_options:
                            selected_alloc_id = st.selectbox(
                                "编辑/删除行业占比",
                                list(edit_options.keys()),
                                format_func=lambda x: edit_options[x],
                                key=f"edit_alloc_{fid}"
                            )
                            selected_alloc = next(r for r in current_alloc_rows if r.get("id") == selected_alloc_id)
                            new_weight = st.number_input(
                                "修改行业占比%",
                                min_value=0.0,
                                max_value=100.0,
                                value=float(selected_alloc.get("weight", 0) or 0),
                                step=0.5,
                                key=f"edit_alloc_weight_{fid}"
                            )
                            b1, b2 = st.columns(2)
                            with b1:
                                if st.button("保存行业占比修改", key=f"save_alloc_{fid}"):
                                    new_allocs = []
                                    for r in current_alloc_rows:
                                        if r.get("id") == selected_alloc_id:
                                            new_allocs.append({"industry": r.get("industry"), "weight": new_weight})
                                        else:
                                            new_allocs.append({"industry": r.get("industry"), "weight": r.get("weight", 0)})
                                    ok, err = save_fund_industry_allocations(fid, new_allocs)
                                    if ok:
                                        st.success("已保存。")
                                        st.rerun()
                                    else:
                                        st.error(f"保存失败：{err}")
                            with b2:
                                if st.button("删除该行业占比", key=f"delete_alloc_{fid}"):
                                    try:
                                        sb().table("fund_industry_allocations").delete().eq("id", selected_alloc_id).execute()
                                        st.success("已删除。")
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"删除失败：{e}")

                        alloc_total = sum(float(r.get("weight", 0) or 0) for r in current_alloc_rows)
                        st.info(f"当前行业分布合计：{alloc_total:.2f}%")
                    else:
                        st.info("当前基金还没有添加行业分布。")

                if st.button("保存基金基础信息"):
                    update_row("funds", fid, {
                        "name": en,
                        "amount": ea,
                        "cash_weight": ecash,
                        "bond_weight": ebond,
                        "bond_return": ebond_ret,
                        "other_weight": eother,
                        "other_return": eother_ret,
                    })
                    st.success("基金基础信息已保存。")
                    st.rerun()

                st.write("重仓股票")
                positions = get_fund_positions(fid)
                st.dataframe(pd.DataFrame(positions), use_container_width=True)
                stocks = get_stocks()
                if stocks:
                    s_opts = {s["id"]: f"{s['name']} {s['code']}" for s in stocks}
                    sid = st.selectbox("选择股票加入重仓", list(s_opts.keys()), format_func=lambda x: s_opts[x])
                    weight = st.number_input("重仓占比%", min_value=0.0, max_value=100.0, value=0.0, step=0.5)
                    if st.button("添加重仓"):
                        s = next(x for x in stocks if x["id"] == sid)
                        insert_row("fund_positions", {"fund_id": fid, "stock_name": s["name"], "stock_code": s["code"], "weight": weight})
                        st.success("已添加。")
                        st.rerun()

                if positions:
                    pos_opts = {p["id"]: f"{p['stock_name']} {p['weight']}%" for p in positions}
                    pid = st.selectbox("编辑/删除重仓", list(pos_opts.keys()), format_func=lambda x: pos_opts[x])
                    pos = next(p for p in positions if p["id"] == pid)
                    pn = st.text_input("修改重仓股票名称", value=pos["stock_name"])
                    pc = st.text_input("修改重仓股票代码", value=pos["stock_code"])
                    pw = st.number_input("修改重仓占比%", min_value=0.0, max_value=100.0, value=float(pos.get("weight", 0) or 0), step=0.5)
                    d1, d2 = st.columns(2)
                    with d1:
                        if st.button("保存重仓修改"):
                            update_row("fund_positions", pid, {"stock_name": pn, "stock_code": pc, "weight": pw})
                            st.success("已保存。")
                            st.rerun()
                    with d2:
                        if st.button("删除重仓"):
                            delete_row("fund_positions", pid)
                            st.success("已删除。")
                            st.rerun()

                if st.button("删除当前基金"):
                    sb().table("fund_positions").delete().eq("fund_id", fid).execute()
                    sb().table("fund_industry").delete().eq("fund_id", fid).execute()
                    try:
                        sb().table("fund_industry_allocations").delete().eq("fund_id", fid).execute()
                    except Exception:
                        pass
                    delete_row("funds", fid)
                    st.success("基金已删除。")
                    st.rerun()


if __name__ == "__main__":
    main()
