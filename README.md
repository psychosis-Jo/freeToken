# 🐑 AI 福利追踪 · 羊毛看板

自动追踪国内 AI 厂商的**免费额度 / 赠送 Token / 折扣 / 试用**活动，每天定时抓取并生成看板，发现新羊毛时推送到飞书。

## 架构

```
数据源（社区聚合仓库的 raw Markdown）
   → 长文分段，LLM 结构化抽取（默认 StepFun step-router-v1）
   → 按厂商别名 + 链接 + 类型 + 模型合并进旧数据
   → 来源或 LLM 失败：保留该来源旧数据，不推送
   → ① 生成静态看板 index.html（GitHub Pages 托管）
   → ② 只有结构上新出现的优惠才推飞书（富文本，可带看板链接）
   → 每天 GitHub Actions 定时跑 + 提交
```

结构化字段：`厂商 / 模型 / 类型 / 额度 / 截止日 / 领取条件 / 来源链接 / 摘要`

## 部署（5 分钟）

1. **建仓库**：把这个目录推到一个 GitHub 仓库（公开，Pages 才能用）。
2. **开 Pages**：仓库 Settings → Pages → Build from `main` 分支、`/ (root)`。
   几分钟后访问 `https://<你的用户名>.github.io/<仓库名>/` 就是看板。
3. **配 Secrets**（Settings → Secrets → Actions）。每家一把钥匙，看板上的「抽取」会写出当前厂商和模型名：
   - `STEPFUN_API_KEY`：阶跃星辰 StepFun（`step-router-v1`）
   - `DEEPSEEK_API_KEY`：DeepSeek（`deepseek-chat`）
   - `QWEN_API_KEY`：通义千问 DashScope（`qwen-plus`）
   - `SILICONFLOW_API_KEY`：硅基流动（`deepseek-ai/DeepSeek-V3`）
   - `CUSTOM_API_KEY`：`provider` 设为 `custom` 时用，地址和模型名写在 `config.json` 的 `llm.custom`
   - `LLM_API_KEY`：通用备用。没填上面的专用钥匙时，当前选中的那一家会用它。换厂商前要确认这把钥匙属于那一家。
   - `FEISHU_WEBHOOK`：飞书**群机器人** webhook 地址（**选填**，填了才推飞书）。⚠️ 注意：这是某个飞书群里添加的「自定义机器人」推送地址，跟 WorkBuddy 里连的飞书不是一回事，需要单独建。
   - `DASHBOARD_URL`：看板地址（选填），例如 `https://<用户名>.github.io/<仓库名>/`。填了才会出现在飞书消息里。
   - `LLM_BASE_URL` / `LLM_MODEL` / `LLM_USE_JSON_MODE`：可选，临时盖掉当前厂商的地址、模型名或 json 模式。留空就用该厂商的预设。
4. **手动跑一次**：Actions → 工作流 → Run workflow，验证看板更新 + 飞书推送。
   之后每天 UTC 1:00（北京 09:00）自动跑。

## 本地运行 / 调试

```bash
pip install -r requirements.txt
STEPFUN_API_KEY=sk-xxx python fetch_offers.py          # 按 config.json 的厂商抓取
LLM_PROVIDER=deepseek DEEPSEEK_API_KEY=sk-xxx python fetch_offers.py   # 只这一次换 DeepSeek
python fetch_offers.py --rebuild                       # 只归一化现有数据，不访问网络
# 打开 index.html 看效果
```

没有对应密钥时，社区长文保持上次结果，RSS 和官方文档照常更新。

## 自定义

### 切换模型厂商
平时改仓库里的 `config.json`，只改 `llm.provider`，然后提交。可选值：

| provider | 厂商 | 默认模型 | 密钥 |
|---|---|---|---|
| `stepfun` | 阶跃星辰 StepFun | step-router-v1 | `STEPFUN_API_KEY` |
| `deepseek` | DeepSeek | deepseek-chat | `DEEPSEEK_API_KEY` |
| `qwen` | 通义千问 | qwen-plus | `QWEN_API_KEY` |
| `siliconflow` | 硅基流动 | deepseek-ai/DeepSeek-V3 | `SILICONFLOW_API_KEY` |
| `custom` | 自定义 OpenAI 兼容端点 | 写在 `llm.custom` | `CUSTOM_API_KEY` |

```json
{"llm":{"provider":"deepseek","custom":{"base_url":"","model":"","use_json_mode":false}}}
```

定时任务读的就是这个字段。Actions 里手动 Run workflow 时，也可以临时选一家，只影响这一次。看板页眉会显示「抽取 阶跃星辰 StepFun · step-router-v1」这样的字样。

阶跃星辰的 `step-router-v1` 不支持 `json_object`，预设里已关闭。DeepSeek、通义、硅基流动默认打开。

- **信源**分三层，都在 `fetch_offers.py` 的 `SOURCES` 里：
  - **额度变动和厂商动态**（RSS，不用 LLM，每次都会更新）：free-llm-intel 的额度变化，以及 DeepSeek、通义、智谱、硅基流动、Kimi、混元、MiniMax、美团、小米、商汤、StreamLake 的官方动态。动态只保留和免费额度、价格、下线、试用相关的条目。
  - **官方规则页**：阿里云百炼新人额度、千问免费额度。控制台那种前端渲染页面仍然不抓。
  - **社区长文**：两个聚合 README，外加 cn-freellm 的国内平台目录。这类要有当前厂商的密钥才会重新抽取；没有 key 时旧目录留着，RSS 和官方文档照样更新。
  看板默认按发现时间排序，新变动会出现在最上面。
- **去重**：同一家厂商的不同写法会折成同一个名字（如 AI21 / AI21 Labs）。同一链接、同一类型、同一模型算同一条；额度只是措辞不同（`$10` 和 `$10免费试用额度`）不会当成新增，也不会再推一次飞书。同一链接下不同模型、不同活动仍然分开。
- **失败与过期**：某个来源抓取失败或模型返回空结果时，这个来源的旧记录留着。连续 21 天（`config.json` 的 `retain_days`）都没再出现，才会删掉。看板上的「已过期」只认明确的公历日期。
- **微信公众号活动**：自动化难抓，建议定期手动补录（直接往 `offers.json` 加一条）。
- **改推送时间**：编辑 `.github/workflows/daily.yml` 里的 `cron`。
- **看板地址**：在 `config.json` 的 `dashboard_url` 或 Secret `DASHBOARD_URL` 里填写。

## 文件说明

| 文件 | 作用 |
|---|---|
| `fetch_offers.py` | 主程序：抓取 + 分段抽取 + 合并 + 推送 + 生成看板 |
| `offers.json` | 当前优惠。每次成功抓取会合并进去，失败不会整份覆盖 |
| `index.html` | 自包含静态看板（内嵌数据，可按厂商/类型/状态筛选；输出已转义） |
| `.github/workflows/daily.yml` | 每日定时任务 |
| `requirements.txt` | 依赖 |
