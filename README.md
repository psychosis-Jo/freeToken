# 🐑 AI 福利追踪 · 羊毛看板

自动追踪国内 AI 厂商的**免费额度 / 赠送 Token / 折扣 / 试用**活动，每天定时抓取并生成看板，发现新羊毛时推送到飞书。

## 架构

```
数据源(社区聚合仓库 + 官方页)
   → LLM 结构化抽取（无 key 时启发式兜底）
   → 去重 + 新增检测
   → ① 生成静态看板 index.html（GitHub Pages 托管）
   → ② 发现新增优惠 → 飞书群机器人推送
   → 每天 GitHub Actions 定时跑 + 提交
```

结构化字段：`厂商 / 模型 / 类型 / 额度 / 截止日 / 领取条件 / 来源链接 / 摘要`

## 部署（5 分钟）

1. **建仓库**：把这个目录推到一个 GitHub 仓库（公开，Pages 才能用）。
2. **开 Pages**：仓库 Settings → Pages → Build from `main` 分支、`/ (root)`。
   几分钟后访问 `https://<你的用户名>.github.io/<仓库名>/` 就是看板。
3. **配 Secrets**（Settings → Secrets → Actions）：
   - `FEISHU_WEBHOOK`：飞书群机器人 webhook 地址（**选填**，填了才推飞书）
   - `LLM_API_KEY`：OpenAI 兼容 Key（**选填**，不填用启发式兜底；推荐 DeepSeek，便宜）
   - `LLM_BASE_URL`：默认 `https://api.deepseek.com`（可选）
   - `LLM_MODEL`：默认 `deepseek-chat`（可选）
4. **手动跑一次**：Actions → 工作流 → Run workflow，验证看板更新 + 飞书推送。
   之后每天 UTC 1:00（北京 09:00）自动跑。

## 本地运行 / 调试

```bash
pip install -r requirements.txt
LLM_API_KEY=sk-xxx python fetch_offers.py     # 用 LLM 抽取
python fetch_offers.py                        # 无 key，启发式兜底
# 打开 index.html 看效果
```

## 自定义

- **加数据源**：编辑 `fetch_offers.py` 顶部的 `SOURCES`。
  - `type: markdown` 适合社区聚合仓库的 raw README（最稳）。
  - `type: html` 适合官网活动页；纯静态页能抽到文字，动态页（JS 渲染）可能需后续接 Playwright。
- **微信公众号活动**：自动化难抓，建议定期手动补录（直接往 `offers.json` 加一条，或加一个表单页）。
- **改推送时间**：编辑 `.github/workflows/daily.yml` 里的 `cron`。

## 文件说明

| 文件 | 作用 |
|---|---|
| `fetch_offers.py` | 主程序：抓取 + 抽取 + 去重 + 推送 + 生成看板 |
| `offers.json` | 当前所有优惠（每次运行覆盖，GitHub 提交历史即「变更记录」） |
| `index.html` | 自包含静态看板（内嵌数据，可按厂商/类型筛选） |
| `.github/workflows/daily.yml` | 每日定时任务 |
| `requirements.txt` | 依赖 |
