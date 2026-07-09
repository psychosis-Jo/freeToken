#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 福利追踪 · 抓取与抽取主程序
================================
数据源：社区聚合仓库(markdown) + 官方免费额度页(html)
抽取：OpenAI 兼容 LLM（默认 StepFun step-router-v1，可在 config.json / Secrets 自行切换；无 key 时回退启发式）
输出：offers.json（结构化数据） + index.html（自包含静态看板）
推送：飞书群机器人 webhook（发现新增优惠时）

LLM 配置（优先级：环境变量 > config.json > 内置预设）：
    config.json 示例：
      {"llm":{"provider":"stepfun","base_url":"https://api.stepfun.com/step_plan/v1","model":"step-router-v1","use_json_mode":false}}
    环境变量：LLM_API_KEY(必填) / LLM_PROVIDER / LLM_BASE_URL / LLM_MODEL / LLM_USE_JSON_MODE
    内置预设：stepfun、deepseek、qwen、siliconflow（改 provider 即切换）
本地运行：
    pip install -r requirements.txt
    LLM_API_KEY=sk-xxx python fetch_offers.py        # 用 LLM 抽取
    python fetch_offers.py                            # 无 key，启发式兜底
GitHub Actions 每天自动跑（见 .github/workflows/daily.yml）。
"""
import os
import re
import json
import sys
import datetime
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# 0. LLM 配置（可自行切换厂商，优先级：环境变量 > config.json > 内置预设）
# ---------------------------------------------------------------------------
LLM_PRESETS = {
    "stepfun":    {"base_url": "https://api.stepfun.com/step_plan/v1", "model": "step-router-v1", "use_json_mode": False},
    "deepseek":   {"base_url": "https://api.deepseek.com",             "model": "deepseek-chat",  "use_json_mode": True},
    "qwen":       {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus", "use_json_mode": True},
    "siliconflow":{"base_url": "https://api.siliconflow.cn/v1",        "model": "deepseek-ai/DeepSeek-V3", "use_json_mode": True},
}


def load_config() -> dict:
    try:
        with open("config.json", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[warn] 读取 config.json 失败: {e}", file=sys.stderr)
        return {}


def resolve_llm() -> dict:
    """解析 LLM 配置，优先级：环境变量 > config.json > 内置预设(stepfun)。"""
    cfg = load_config().get("llm", {})
    provider = (os.environ.get("LLM_PROVIDER") or cfg.get("provider") or "stepfun").strip().lower()
    preset = LLM_PRESETS.get(provider, {})
    base_url = (os.environ.get("LLM_BASE_URL") or cfg.get("base_url") or preset.get("base_url")
                or LLM_PRESETS["stepfun"]["base_url"])
    model = (os.environ.get("LLM_MODEL") or cfg.get("model") or preset.get("model")
             or LLM_PRESETS["stepfun"]["model"])
    use_json = os.environ.get("LLM_USE_JSON_MODE")
    if use_json is not None:
        use_json_mode = use_json.strip().lower() in ("1", "true", "yes", "on")
    else:
        use_json_mode = bool(cfg.get("use_json_mode", preset.get("use_json_mode", False)))
    return {"base_url": base_url, "model": model, "use_json_mode": use_json_mode}


# ---------------------------------------------------------------------------
# 1. 数据源配置（可自行增删）
#    type=markdown  -> 直接读原始文本（社区聚合仓库首选）
#    type=html      -> 抓官网页面抽文本（部分动态页需后续上 Playwright）
# ---------------------------------------------------------------------------
SOURCES = [
    {
        "name": "free-llm-api-cn（社区聚合·中文）",
        "type": "markdown",
        "url": "https://raw.githubusercontent.com/bbylw/free-llm-api-cn/main/README.md",
    },
    {
        "name": "FreeLLM-API-KeyHub（社区聚合）",
        "type": "markdown",
        "url": "https://raw.githubusercontent.com/guihuashaoxiang/FreeLLM-API-KeyHub/main/README.md",
    },
    # ---- 官方免费额度页（示例，自行补充；动态页可能需 Playwright）----
    {
        "name": "硅基流动 模型列表",
        "type": "html",
        "url": "https://siliconflow.cn/zh-cn/models",
    },
    {
        "name": "智谱 大模型开放平台",
        "type": "html",
        "url": "https://open.bigmodel.cn/dev/api",
    },
]

OFFERS_FILE = "offers.json"
SITE_FILE = "index.html"
MODEL_KEYWORDS = [
    "gpt", "glm", "ernie", "qwen", "deepseek", "doubao", "kimi", "moonshot",
    "spark", "星火", "minimax", "abab", "step", "阶跃", "hunyuan", "混元",
    "yi", "零一", "baichuan", "百川", "chatglm", "qwq", "llama", "claude",
]


# ---------------------------------------------------------------------------
# 2. 抓取
# ---------------------------------------------------------------------------
def fetch_text(src: dict) -> str:
    try:
        r = requests.get(src["url"], timeout=30,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        if src["type"] == "markdown":
            return r.text
        soup = BeautifulSoup(r.text, "html.parser")
        for s in soup(["script", "style", "noscript"]):
            s.decompose()
        return soup.get_text("\n", strip=True)
    except Exception as e:
        print(f"[warn] 抓取失败 {src['name']}: {e}", file=sys.stderr)
        return ""


# ---------------------------------------------------------------------------
# 3. LLM 结构化抽取
# ---------------------------------------------------------------------------
def _parse_json_safely(content: str) -> dict:
    """从模型输出里尽量抠出 JSON 对象（兼容 ```json 围栏 / 多余文字）。"""
    if not content:
        return {}
    s = content.strip()
    if s.startswith("```"):                      # 去 ```json ... ``` 围栏
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    a, b = s.find("{"), s.rfind("}")             # 退一步：截取第一个 { 到最后一个 }
    if a != -1 and b != -1 and b > a:
        try:
            return json.loads(s[a:b + 1])
        except Exception:
            return {}
    return {}


def extract_with_llm(text: str, source_name: str) -> list:
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        return []
    try:
        from openai import OpenAI
    except ImportError:
        print("[warn] 未安装 openai，跳过 LLM 抽取", file=sys.stderr)
        return []

    conf = resolve_llm()
    client = OpenAI(api_key=api_key, base_url=conf["base_url"])
    model = conf["model"]
    prompt = f"""你是 AI 大模型优惠信息抽取器。从下方文本中抽取所有「AI 大模型 / API 的免费额度、折扣、试用、赠送 Token、限时活动」相关信息。
只输出 JSON，格式：{{"offers":[...]}}，不要多余文字。每条字段：
- vendor: 厂商/平台名（中文优先）
- model: 模型名，无则写"通用"
- offer_type: 从[免费token, 折扣, 试用, 永久免费, 新用户赠送]中选一
- amount: 额度描述（如"500万 token"、"每日100次"），无则 ""
- deadline: 截止日期(YYYY-MM-DD)或周期(如"每日"/"永久"/"限时"），无则 ""
- conditions: 领取条件（如"新用户"/"实名认证"），无则 ""
- url: 来源链接，文本中有则用，否则 ""
- summary: 一句话中文摘要（含厂商+模型+额度+条件）
- region: 从[国内, 国外]选一。"国内"=服务/注册/访问通常无需翻墙（中国厂商及面向中国用户的平台，如硅基流动、阿里云、智谱、百度、腾讯、字节、月之暗面、MiniMax、讯飞、DeepSeek 等）；"国外"=主要面向海外、国内访问通常需翻墙（如 OpenAI、Anthropic、Google、HuggingFace、Together、Groq、Mistral、AI21 等）
文本来源：{source_name}

文本：
{text[:14000]}
"""
    try:
        kwargs = dict(model=model, messages=[{"role": "user", "content": prompt}], temperature=0)
        # 部分模型(如 step-router-v1)不支持 json_object，按配置决定是否带
        if conf["use_json_mode"]:
            kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**kwargs)
        data = _parse_json_safely(resp.choices[0].message.content)
        arr = data.get("offers") or []
        for o in arr:
            o["source"] = source_name
        return arr
    except Exception as e:
        print(f"[warn] LLM 抽取失败 {source_name}: {e}", file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# 4. 启发式兜底（无 LLM key 时）
# ---------------------------------------------------------------------------
def extract_heuristic(text: str, source_name: str) -> list:
    """无 LLM key 时的兜底：只保留「像优惠」的行，降低噪声。"""
    free_sig = ["免费", "free", "额度", "赠送", "折扣", "试用", "羊毛", "福利", "限量", "领取"]
    amt_pat = re.compile(r"\d+\s*(万|亿|w|k|M|万)?\s*(token|tokens|次|条|点|元)", re.I)
    out = []
    for line in text.splitlines():
        s = line.strip()
        if len(s) < 8 or len(s) > 160:
            continue
        low = s.lower()
        hit_model = any(k in low for k in MODEL_KEYWORDS)
        hit_free = any(k in low for k in free_sig)
        hit_amt = bool(amt_pat.search(s))
        # 需同时满足：优惠信号 + (模型信号 或 额度数字)
        if hit_free and (hit_model or hit_amt):
            out.append({
                "vendor": "",
                "model": "",
                "offer_type": "免费token" if hit_free else "其他",
                "amount": "",
                "deadline": "",
                "conditions": "",
                "url": "",
                "summary": s[:120],
                "source": source_name,
            })
    seen, uniq = set(), []
    for o in out:
        if o["summary"] not in seen:
            seen.add(o["summary"])
            uniq.append(o)
    return uniq[:60]


# ---------------------------------------------------------------------------
# 5. 归一化 / 去重 / 新增检测
# ---------------------------------------------------------------------------
CN_HINTS = ["360", "智脑", "智谱", "glm", "阿里", "通义", "qwen", "百度", "文心", "ernie",
            "腾讯", "混元", "hunyuan", "字节", "豆包", "doubao", "月之暗面", "kimi", "moonshot",
            "minimax", "abab", "讯飞", "星火", "spark", "百川", "baichuan", "昆仑", "天工", "零一",
            "yi", "deepseek", "阶跃", "step", "火山", "方舟", "硅基", "siliconflow", "美团",
            "longcat", "钉钉", "商汤", "sense", "百炼", "modelscope", "魔搭", "华为", "盘古", "星辰"]
CN_DOMAINS = ["siliconflow.cn", "bigmodel.cn", "aliyun", "volcengine", "modelscope", "bce.baidu",
              "tencent", "moonshot", "minimax", "zhipu", "baichuan", "deepseek", "stepfun", "360",
              "xverse", "01.ai", "metax", "aibase", "iqiyi", "kunlun", "baidu", "qq.com"]


def infer_region(vendor: str, summary: str, url: str) -> str:
    """无 LLM 或 LLM 未判 region 时的兜底：启发式判断国内/国外。"""
    text = f"{vendor} {summary} {url}".lower()
    u = (url or "").lower()
    if ".cn" in u or any(h in u for h in CN_DOMAINS):
        return "国内"
    if any(h in text for h in ["openai", "anthropic", "claude", "google", "gemini", "huggingface",
                               "together", "groq", "mistral", "ai21", "cohere", "replicate",
                               "perplexity", "meta llama", "llama", "xai", "nvidia", "fireworks",
                               "octoai", "anyscale", "deepinfra", "openrouter", "vertex", "bedrock",
                               "azure", "ollama"]):
        return "国外"
    if re.search(r"[一-鿿]", vendor or ""):
        return "国内"
    if any(h.lower() in text for h in CN_HINTS):
        return "国内"
    return "国外"


def norm(o: dict) -> dict:
    d = {
        "vendor": (o.get("vendor") or "").strip(),
        "model": (o.get("model") or "通用").strip(),
        "offer_type": (o.get("offer_type") or "其他").strip(),
        "amount": (o.get("amount") or "").strip(),
        "deadline": (o.get("deadline") or "").strip(),
        "conditions": (o.get("conditions") or "").strip(),
        "url": (o.get("url") or "").strip(),
        "summary": (o.get("summary") or "").strip(),
        "source": (o.get("source") or "").strip(),
        "region": (o.get("region") or "").strip(),
    }
    if not d["region"]:
        d["region"] = infer_region(d["vendor"], d["summary"], d["url"])
    return d


def sig(o: dict) -> str:
    return f"{o['vendor']}|{o['model']}|{o['offer_type']}|{o['amount']}"


def clean_prev(offers: list) -> list:
    """去除已被新数据覆盖的旧条目（同 signature 只留最新）。"""
    by = {}
    for o in offers:
        by[sig(o)] = o
    return list(by.values())


# ---------------------------------------------------------------------------
# 6. 飞书推送
# ---------------------------------------------------------------------------
def push_feishu(new_offers: list):
    webhook = os.environ.get("FEISHU_WEBHOOK")
    if not webhook or not new_offers:
        return
    lines = [f"🐑 **AI 羊毛播报 · 新增 {len(new_offers)} 条**"]
    for o in new_offers[:20]:
        s = o.get("summary") or f"{o.get('vendor','')} {o.get('model','')}".strip()
        dl = f" · 截止 {o['deadline']}" if o.get("deadline") else ""
        lines.append(f"- {s}{dl}")
    if len(new_offers) > 20:
        lines.append(f"- ……另有 {len(new_offers)-20} 条")
    payload = {"msg_type": "text", "content": {"text": "\n".join(lines)}}
    try:
        requests.post(webhook, json=payload, timeout=15)
        print(f"[ok] 飞书推送 {len(new_offers)} 条")
    except Exception as e:
        print(f"[warn] 飞书推送失败: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# 7. 生成静态看板
# ---------------------------------------------------------------------------
def build_site(offers: list, path: str):
    data = json.dumps(offers, ensure_ascii=False)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    html = SITE_TEMPLATE.replace("__DATA__", data).replace("__UPDATED__", now)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[ok] 看板已生成 {path}")


SITE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 福利追踪 · 羊毛看板</title>
<style>
  :root{--bg:#0f1115;--card:#1a1d24;--line:#2a2f3a;--green:#3ddc84;--orange:#ff9d4d;--text:#e8eaed;--muted:#9aa0aa;}
  *{margin:0;padding:0;box-sizing:border-box;}
  body{background:var(--bg);color:var(--text);font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;line-height:1.6;padding:24px 16px 60px;}
  .wrap{max-width:960px;margin:0 auto;}
  header{display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:18px;}
  h1{font-size:22px;font-weight:700;}
  h1 .em{color:var(--green);}
  .meta{color:var(--muted);font-size:13px;}
  .filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;}
  .filters select,.filters input{background:var(--card);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:7px 10px;font-size:13px;}
  .filters input{flex:1;min-width:160px;}
  .seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;align-self:center;}
  .seg button{background:var(--card);color:var(--muted);border:none;padding:7px 13px;font-size:13px;cursor:pointer;border-right:1px solid var(--line);}
  .seg button:last-child{border-right:none;}
  .seg button.active{background:var(--green);color:#06210f;font-weight:600;}
  .stat{color:var(--muted);font-size:13px;margin-bottom:14px;}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:12px;transition:border-color .2s;}
  .card:hover{border-color:var(--green);}
  .card .top{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px;}
  .tag{font-size:11px;padding:2px 9px;border-radius:20px;background:rgba(61,220,132,.12);color:var(--green);border:1px solid rgba(61,220,132,.3);}
  .tag.orange{background:rgba(255,157,77,.12);color:var(--orange);border-color:rgba(255,157,77,.3);}
  .vendor{font-weight:600;font-size:15px;}
  .model{color:var(--muted);font-size:13px;}
  .summary{font-size:14px;margin:4px 0 8px;}
  .row{display:flex;gap:14px;flex-wrap:wrap;font-size:12.5px;color:var(--muted);}
  .row b{color:var(--text);font-weight:500;}
  .src{margin-top:6px;font-size:12px;color:#6b7280;}
  .src a{color:#5b8def;text-decoration:none;}
  .empty{text-align:center;color:var(--muted);padding:40px;}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>🐑 AI 福利追踪 · <span class="em">羊毛看板</span></h1>
    <div class="meta">更新于 __UPDATED__</div>
  </header>
  <div class="filters">
    <input id="q" placeholder="搜索厂商 / 模型 / 关键词">
    <select id="fType"><option value="">全部类型</option></select>
    <select id="fVendor"><option value="">全部厂商</option></select>
    <div class="seg" id="fRegion">
      <button data-v="" class="active">全部</button>
      <button data-v="国内">国内·免翻墙</button>
      <button data-v="国外">国外·需翻墙</button>
    </div>
  </div>
  <div class="stat" id="stat"></div>
  <div id="list"></div>
</div>
<script>
const OFFERS = __DATA__;
const listEl = document.getElementById('list');
const qEl = document.getElementById('q');
const fType = document.getElementById('fType');
const fVendor = document.getElementById('fVendor');
const stat = document.getElementById('stat');
const fRegion = document.getElementById('fRegion');
let regionVal = "";
fRegion.querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
  fRegion.querySelectorAll('button').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  regionVal = b.dataset.v;
  render();
}));

function uniq(arr){return [...new Set(arr)].filter(Boolean);}
uniq(OFFERS.map(o=>o.offer_type)).forEach(t=>{const o=document.createElement('option');o.value=t;o.textContent=t;fType.appendChild(o);});
uniq(OFFERS.map(o=>o.vendor)).forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v;fVendor.appendChild(o);});

function render(){
  const q=qEl.value.trim().toLowerCase();
  const t=fType.value, v=fVendor.value;
  const filtered=OFFERS.filter(o=>
    (!q || (o.vendor+o.model+o.summary+o.amount).toLowerCase().includes(q)) &&
    (!t || o.offer_type===t) &&
    (!v || o.vendor===v) &&
    (!regionVal || o.region===regionVal)
  );
  const cn=OFFERS.filter(o=>o.region==='国内').length, foreign=OFFERS.filter(o=>o.region==='国外').length;
  stat.textContent=`共 ${OFFERS.length} 条（国内 ${cn} · 国外 ${foreign}），当前显示 ${filtered.length} 条`;
  if(!filtered.length){listEl.innerHTML='<div class="empty">没有匹配的优惠 🤔</div>';return;}
  listEl.innerHTML=filtered.map(o=>{
    const typeCls=o.offer_type==='折扣'?'tag orange':'tag';
    const dl=o.deadline?`<span><b>截止</b> ${o.deadline}</span>`:'';
    const amt=o.amount?`<span><b>额度</b> ${o.amount}</span>`:'';
    const cond=o.conditions?`<span><b>条件</b> ${o.conditions}</span>`:'';
    const url=o.url?` · <a href="${o.url}" target="_blank" rel="noopener">来源</a>`:'';
    const regionCls=o.region==='国外'?'tag orange':'tag';
    const regionText=o.region||'未知';
    return `<div class="card">
      <div class="top"><span class="${typeCls}">${o.offer_type}</span>
        <span class="${regionCls}">${regionText}</span>
        <span class="vendor">${o.vendor||'—'}</span>
        <span class="model">${o.model||''}</span></div>
      <div class="summary">${o.summary||''}</div>
      <div class="row">${amt}${dl}${cond}</div>
      <div class="src">📡 ${o.source||''}${url}</div>
    </div>`;
  }).join('');
}
[qEl,fType,fVendor].forEach(el=>el.addEventListener('input',render));
render();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# 8. 主流程
# ---------------------------------------------------------------------------
def main():
    print("== 开始抓取 ==")
    raw_offers = []
    for src in SOURCES:
        text = fetch_text(src)
        if not text:
            continue
        items = extract_with_llm(text, src["name"])
        if not items:
            items = extract_heuristic(text, src["name"])
            print(f"  · {src['name']}: 启发式抽取 {len(items)} 条（无 LLM key）")
        else:
            print(f"  · {src['name']}: LLM 抽取 {len(items)} 条")
        raw_offers.extend(items)

    offers = [norm(o) for o in raw_offers if (o.get("summary") or o.get("vendor") or o.get("model"))]
    offers = clean_prev(offers)
    # 按厂商排序
    offers.sort(key=lambda o: (o["vendor"] or "zzz", o["model"]))
    print(f"== 去重后共 {len(offers)} 条 ==")

    # 新增检测
    prev = []
    if os.path.exists(OFFERS_FILE):
        try:
            prev = json.load(open(OFFERS_FILE, encoding="utf-8"))
        except Exception:
            prev = []
    prev_sig = {sig(o) for o in prev}
    new_offers = [o for o in offers if sig(o) not in prev_sig]

    # 写数据
    json.dump(offers, open(OFFERS_FILE, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    build_site(offers, SITE_FILE)

    if new_offers:
        print(f"== 发现新增 {len(new_offers)} 条 ==")
        push_feishu(new_offers)
    else:
        print("== 无新增 ==")


if __name__ == "__main__":
    main()
