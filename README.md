# 基金重仓估算工具 - 云端版 v1.4

## v1.4 修正

上一版仍然可能出现：
name 'china_yahoo_symbol' is not defined

v1.4 直接重写为稳定版本：
- 只默认使用 Yahoo Finance 获取行情
- 不再依赖 AkShare 获取行情
- AkShare 仅用于“搜索代码”，失败不影响行情获取

## 更新方式

把以下文件覆盖到 GitHub：
- app.py
- requirements.txt
- README.md

Streamlit Cloud 会自动重新部署。

## 注意

Yahoo Finance 的 A股数据通常有延迟，仅适合基金重仓涨跌幅粗略估算，不构成投资建议。
