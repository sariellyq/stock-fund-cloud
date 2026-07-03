# 基金盘中估算工具 V4.0 - Supabase 数据库版

## 上传文件

上传到 GitHub：
- app.py
- requirements.txt
- README.md
- supabase_schema.sql
- .gitignore

Streamlit Cloud Main file:
app.py

## Streamlit Secrets

在 Streamlit Cloud → App → Settings → Secrets 添加：

SUPABASE_URL = "你的 Project URL"
SUPABASE_KEY = "你的 Publishable key 或 anon public key"

## Supabase 建表

在 Supabase → SQL Editor 中运行：

supabase_schema.sql

## 说明

V4.0 使用 Supabase 保存：
- 股票
- 行业ETF
- 基金
- 重仓股票
- 行业分布

刷新网页、关闭网页、重新部署后数据不会丢。
