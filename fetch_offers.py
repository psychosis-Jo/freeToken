#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 福利追踪 · 抓取与抽取主程序
================================
数据源：社区聚合仓库的 raw Markdown（官方活动页多为前端渲染，不在这里硬抓）
抽取：OpenAI 兼容 LLM（默认 StepFun step-router-v1；无 key 且本地没有旧数据时才启发式兜底）
合并：按「厂商别名 + 链接 + 类型 + 模型」并回旧数据。来源或 LLM 失败时保留该来源的旧记录，不推送。
输出：offers.json + index.html
推送：飞书群机器人（仅真正新增的优惠，富文本，可带看板链接）

LLM 厂商由 config.json 的 llm.provider 决定：stepfun / deepseek / qwen / siliconflow / custom。
各厂商优先用自己的密钥（STEPFUN_API_KEY 等），没有时才用通用 LLM_API_KEY。
本地临时换一家：LLM_PROVIDER=deepseek DEEPSEEK_API_KEY=sk-xxx python fetch_offers.py
    python fetch_offers.py --rebuild          # 只归一化现有 offers.json，不访问网络
"""
import os
import re
import json
import sys
import datetime
import email.utils
import xml.etree.ElementTree as ET
from collections import defaultdict
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.abspath(__file__))
OFFERS_FILE = os.path.join(ROOT, "offers.json")
SITE_FILE = os.path.join(ROOT, "index.html")
CONFIG_FILE = os.path.join(ROOT, "config.json")
SOURCES_FILE = os.path.join(ROOT, "sources.json")

LLM_PRESETS = {
    "stepfun": {
        "label": "阶跃星辰 StepFun",
        "base_url": "https://api.stepfun.com/step_plan/v1",
        "model": "step-router-v1",
        "use_json_mode": False,
        "key_env": "STEPFUN_API_KEY",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "use_json_mode": True,
        "key_env": "DEEPSEEK_API_KEY",
    },
    "qwen": {
        "label": "通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "use_json_mode": True,
        "key_env": "QWEN_API_KEY",
    },
    "siliconflow": {
        "label": "硅基流动",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V3",
        "use_json_mode": True,
        "key_env": "SILICONFLOW_API_KEY",
    },
}

ALLOWED_TYPES = ("免费token", "折扣", "试用", "永久免费", "新用户赠送", "动态")
TYPE_ALIASES = {
    "免费token": "免费token",
    "免费tokens": "免费token",
    "freetoken": "免费token",
    "免费额度": "免费token",
    "免费": "免费token",
    "折扣": "折扣",
    "优惠": "折扣",
    "试用": "试用",
    "永久免费": "永久免费",
    "永久": "永久免费",
    "新用户赠送": "新用户赠送",
    "新用户": "新用户赠送",
    "赠送": "新用户赠送",
    "动态": "动态",
}

# 厂商别名：折叠后的键 → 看板展示名。只合并确定是同一家的写法。
ALIASES = {
    "ai21": "ai21",
    "ai21labs": "ai21",
    "baseten": "baseten",
    "mistral": "mistral",
    "mistralai": "mistral",
    "nvidia": "nvidia",
    "nvidianim": "nvidia",
    "google": "googleaistudio",
    "googleaistudio": "googleaistudio",
    "huggingface": "huggingface",
    "hugging face": "huggingface",
    "智谱": "zhipu",
    "智谱ai": "zhipu",
    "zhipu": "zhipu",
    "zhipuai": "zhipu",
    "阿里": "aliyun",
    "阿里云": "aliyun",
    "阿里云百炼": "aliyun",
    "通义": "aliyun",
    "火山": "volc",
    "火山引擎": "volc",
    "火山方舟": "volc",
    "豆包": "volc",
    "硅基": "siliconflow",
    "硅基流动": "siliconflow",
    "siliconflow": "siliconflow",
    "讯飞": "xfyun",
    "科大讯飞": "xfyun",
    "百度": "baidu",
    "百度智能云": "baidu",
    "文心": "baidu",
    "千帆": "baidu",
    "腾讯": "tencent",
    "腾讯云": "tencent",
    "deepseek": "deepseek",
    "minimax": "minimax",
    "月之暗面": "moonshot",
    "kimi": "moonshot",
    "moonshot": "moonshot",
    "ppio": "ppio",
    "派欧": "ppio",
    "派欧云": "ppio",
    "streamlake": "streamlake",
    "通义千问": "aliyun",
    "通义千问qwen": "aliyun",
    "智谱glm": "zhipu",
    "月之暗面kimi": "moonshot",
    "kimi月之暗面": "moonshot",
    "腾讯混元": "tencent",
    "小米": "xiaomi",
    "小米mimo": "xiaomi",
}
DISPLAY = {
    "ai21": "AI21",
    "baseten": "Baseten",
    "mistral": "Mistral",
    "nvidia": "NVIDIA",
    "googleaistudio": "Google AI Studio",
    "huggingface": "Hugging Face",
    "zhipu": "智谱AI",
    "aliyun": "阿里云",
    "volc": "火山引擎",
    "siliconflow": "硅基流动",
    "xfyun": "科大讯飞",
    "baidu": "百度智能云",
    "tencent": "腾讯云",
    "deepseek": "DeepSeek",
    "minimax": "MiniMax",
    "moonshot": "月之暗面",
    "ppio": "PPIO",
    "streamlake": "StreamLake",
    "xiaomi": "小米",
}

CN_SUBSTR = (
    "智谱", "阿里", "通义", "百度", "文心", "腾讯", "混元", "字节", "豆包", "火山", "方舟",
    "月之暗面", "讯飞", "星火", "百川", "昆仑", "天工", "零一", "阶跃", "硅基", "美团",
    "商汤", "百炼", "魔搭", "华为", "盘古", "派欧", "快手", "金山", "数眼", "白山",
    "天翼", "云知声", "智脑", "钉钉", "星辰",
)
CN_WORDS = (
    "glm", "qwen", "ernie", "doubao", "hunyuan", "spark", "baichuan", "deepseek",
    "siliconflow", "longcat", "kimi", "moonshot", "minimax", "ppio", "streamlake",
    "zhipu", "stepfun", "dmxapi", "tiangong", "unisound",
)
FOREIGN_WORDS = (
    "openai", "anthropic", "claude", "google", "gemini", "huggingface", "together",
    "groq", "mistral", "ai21", "cohere", "replicate", "perplexity", "xai", "nvidia",
    "fireworks", "anyscale", "deepinfra", "openrouter", "cerebras", "cloudflare",
    "modal", "nebius", "sambanova", "upstage", "vercel", "baseten", "hyperbolic",
    "novita", "github", "aws", "azure", "bedrock",
)
CN_HOST_SUFFIXES = (
    "siliconflow.cn", "bigmodel.cn", "aliyun.com", "alibabacloud.com", "volcengine.com",
    "volces.com", "modelscope.cn", "baidu.com", "tencent.com", "qq.com", "moonshot.cn",
    "minimax.io", "minimax.chat", "baichuan-ai.com", "deepseek.com", "stepfun.com",
    "360.com", "360.cn", "01.ai", "pp.io", "streamlake.com", "dmxapi.cn", "dmxapi.com",
    "ctyun.cn", "unisound.com", "sensecore.cn", "tiangong.cn", "baishan.com",
    "longcat.chat", "xfyun.cn", "shuyanai.com", "lingyiwanwu.com", "10086.cn",
    "huaweicloud.com", "sensetime.com",
)
FOREIGN_HOST_SUFFIXES = (
    "openai.com", "anthropic.com", "google.com", "huggingface.co", "together.xyz",
    "together.ai", "groq.com", "mistral.ai", "ai21.com", "cohere.com", "replicate.com",
    "perplexity.ai", "x.ai", "fireworks.ai", "anyscale.com", "deepinfra.com",
    "openrouter.ai", "nvidia.com", "cerebras.ai", "cloudflare.com", "modal.com",
    "nebius.com", "sambanova.ai", "upstage.ai", "vercel.com", "aws.amazon.com",
    "azure.com", "microsoft.com", "github.com", "novita.ai", "baseten.co",
    "hyperbolic.xyz", "jina.ai", "inference.net", "digitalocean.com",
)
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "ref", "referrer", "affiliate", "aff",
}
CONTENT_FIELDS = (
    "vendor", "model", "offer_type", "amount", "deadline", "conditions",
    "url", "summary", "source", "region",
)

# 官网控制台是前端渲染，不直接抓。变动和动态用 RSS，规则页用能抽出正文的文档。
# 社区长文只在有 LLM_API_KEY 时抽取；RSS 和文档每次都会更新。
PROMO_HINTS = ("免费", "额度", "赠", "代金券", "试用", "下线", "价格", "计费", "资源包", "邀请", "限时", "折扣")
SOURCES = [
    {
        "name": "额度变动（free-llm-intel）",
        "type": "rss",
        "url": "https://free-llm-intel.aishort.top/feeds/llm-intel-changes.xml",
        "limit": 40,
    },
    {
        "name": "DeepSeek 动态",
        "type": "rss",
        "vendor": "DeepSeek",
        "url": "https://free-llm-intel.aishort.top/feeds/llm-news-deepseek.xml",
        "limit": 8,
        "promo_only": True,
    },
    {
        "name": "通义千问动态",
        "type": "rss",
        "vendor": "阿里云",
        "url": "https://free-llm-intel.aishort.top/feeds/llm-news-aliyun_qwen.xml",
        "limit": 8,
        "promo_only": True,
    },
    {
        "name": "智谱动态",
        "type": "rss",
        "vendor": "智谱AI",
        "url": "https://free-llm-intel.aishort.top/feeds/llm-news-zhipu_glm.xml",
        "limit": 8,
        "promo_only": True,
    },
    {
        "name": "硅基流动动态",
        "type": "rss",
        "vendor": "硅基流动",
        "url": "https://free-llm-intel.aishort.top/feeds/llm-news-siliconflow.xml",
        "limit": 8,
        "promo_only": True,
    },
    {
        "name": "Kimi 动态",
        "type": "rss",
        "vendor": "月之暗面",
        "url": "https://free-llm-intel.aishort.top/feeds/llm-news-moonshot_kimi.xml",
        "limit": 8,
        "promo_only": True,
    },
    {
        "name": "腾讯混元动态",
        "type": "rss",
        "vendor": "腾讯云",
        "url": "https://free-llm-intel.aishort.top/feeds/llm-news-tencent_hunyuan.xml",
        "limit": 8,
        "promo_only": True,
    },
    {
        "name": "MiniMax 动态",
        "type": "rss",
        "vendor": "MiniMax",
        "url": "https://free-llm-intel.aishort.top/feeds/llm-news-minimax.xml",
        "limit": 8,
        "promo_only": True,
    },
    {
        "name": "美团 LongCat 动态",
        "type": "rss",
        "vendor": "美团",
        "url": "https://free-llm-intel.aishort.top/feeds/llm-news-longcat_meituan.xml",
        "limit": 8,
        "promo_only": True,
    },
    {
        "name": "小米 MiMo 动态",
        "type": "rss",
        "vendor": "小米",
        "url": "https://free-llm-intel.aishort.top/feeds/llm-news-xiaomi_mimo.xml",
        "limit": 8,
        "promo_only": True,
    },
    {
        "name": "商汤动态",
        "type": "rss",
        "vendor": "商汤",
        "url": "https://free-llm-intel.aishort.top/feeds/llm-news-sensetime_sensenova.xml",
        "limit": 8,
        "promo_only": True,
    },
    {
        "name": "StreamLake 动态",
        "type": "rss",
        "vendor": "StreamLake",
        "url": "https://free-llm-intel.aishort.top/feeds/llm-news-streamlake.xml",
        "limit": 8,
        "promo_only": True,
    },
    {
        "name": "阿里云百炼 · 新人额度说明",
        "type": "html",
        "vendor": "阿里云",
        "url": "https://help.aliyun.com/zh/model-studio/new-free-quota",
    },
    {
        "name": "千问 · 免费额度说明",
        "type": "html",
        "vendor": "千问",
        "url": "https://platform.qianwenai.com/docs/resources/free-quota",
    },
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
    {
        "name": "cn-freellm（国内平台目录）",
        "type": "markdown",
        "url": "https://raw.githubusercontent.com/dawn0731/cn-freellm/main/README.md",
    },
]


def shanghai_now() -> datetime.datetime:
    return datetime.datetime.now(ZoneInfo("Asia/Shanghai"))


def shanghai_today() -> str:
    return shanghai_now().strftime("%Y-%m-%d")


def env_text(name: str) -> str:
    """空字符串视为未设置，避免 GitHub 未配置的 Secret 盖掉 config.json。"""
    return (os.environ.get(name) or "").strip()


def load_config() -> dict:
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[warn] 读取 config.json 失败: {e}", file=sys.stderr)
        return {}


def model_override(cfg: dict, provider: str) -> dict:
    models = cfg.get("models")
    if not isinstance(models, dict):
        return {}
    ov = models.get(provider)
    return ov if isinstance(ov, dict) else {}


def prefer(env_name: str, override, fallback: str) -> str:
    env = env_text(env_name)
    if env:
        return env
    text = str(override or "").strip()
    return text or fallback


def resolve_llm() -> dict:
    """选中的厂商决定地址、模型名和该用哪一把密钥。

    已知厂商默认走内置预设。后台写在 config.json 的 llm.models 可以改某一家的模型名和地址。
    环境变量仍可临时盖掉这一次。优先级：环境变量 > llm.models > 内置预设。
    """
    cfg = load_config().get("llm") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    provider = (env_text("LLM_PROVIDER") or str(cfg.get("provider") or "stepfun")).strip().lower()
    preset = LLM_PRESETS.get(provider)
    custom = cfg.get("custom") if isinstance(cfg.get("custom"), dict) else {}

    if preset:
        ov = model_override(cfg, provider)
        base_url = prefer("LLM_BASE_URL", ov.get("base_url"), preset["base_url"])
        model = prefer("LLM_MODEL", ov.get("model"), preset["model"])
        label = preset["label"]
        key_env = preset["key_env"]
        default_json = bool(ov["use_json_mode"]) if "use_json_mode" in ov else bool(preset["use_json_mode"])
    else:
        provider = "custom"
        ov = model_override(cfg, "custom")
        base_url = prefer("LLM_BASE_URL", ov.get("base_url") or custom.get("base_url"), "")
        model = prefer("LLM_MODEL", ov.get("model") or custom.get("model"), "")
        label = str(ov.get("label") or custom.get("label") or "自定义").strip() or "自定义"
        key_env = "CUSTOM_API_KEY"
        if "use_json_mode" in ov:
            default_json = bool(ov.get("use_json_mode"))
        else:
            default_json = bool(custom.get("use_json_mode", cfg.get("use_json_mode", False)))

    use_json = env_text("LLM_USE_JSON_MODE")
    if use_json:
        use_json_mode = use_json.lower() in ("1", "true", "yes", "on")
    else:
        use_json_mode = default_json

    dedicated = env_text(key_env)
    generic = env_text("LLM_API_KEY")
    if dedicated:
        api_key, key_from = dedicated, key_env
    elif generic:
        api_key, key_from = generic, "LLM_API_KEY"
    else:
        api_key, key_from = "", ""

    return {
        "provider": provider,
        "label": label,
        "base_url": base_url,
        "model": model,
        "use_json_mode": use_json_mode,
        "api_key": api_key,
        "key_from": key_from,
        "key_env": key_env,
    }


def llm_banner(conf: dict) -> str:
    model = conf.get("model") or "未填模型名"
    return f"{conf.get('label') or conf.get('provider')} · {model}"


def dashboard_url() -> str:
    return env_text("DASHBOARD_URL") or str(load_config().get("dashboard_url") or "").strip()


def retain_days() -> int:
    raw = env_text("RETAIN_DAYS") or str(load_config().get("retain_days") or "21")
    try:
        return max(1, int(raw))
    except ValueError:
        return 21


def sanitize_source(raw):
    """后台写入的信源。enabled 为 false 的条目不抓。无效条目返回 None。"""
    if not isinstance(raw, dict):
        return None
    if raw.get("enabled") is False:
        return None
    name = str(raw.get("name") or "").strip()
    kind = str(raw.get("type") or "").strip().lower()
    url = str(raw.get("url") or "").strip()
    if not name or kind not in ("rss", "html", "markdown") or not url.startswith("https://"):
        print(f"[warn] 忽略无效信源: {name or url or kind}", file=sys.stderr)
        return None
    src = {"name": name[:80], "type": kind, "url": url[:500]}
    vendor = str(raw.get("vendor") or "").strip()
    if vendor:
        src["vendor"] = vendor[:40]
    if kind == "rss":
        try:
            limit = int(raw.get("limit") or 8)
        except (TypeError, ValueError):
            limit = 8
        src["limit"] = max(1, min(limit, 80))
        src["promo_only"] = bool(raw.get("promo_only"))
    return src


def load_sources() -> list:
    if not os.path.exists(SOURCES_FILE):
        return [dict(s) for s in SOURCES]
    try:
        with open(SOURCES_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[warn] 读取 sources.json 失败，改用内置信源: {e}", file=sys.stderr)
        return [dict(s) for s in SOURCES]
    if not isinstance(data, list):
        print("[warn] sources.json 不是数组，改用内置信源", file=sys.stderr)
        return [dict(s) for s in SOURCES]
    return [src for src in (sanitize_source(raw) for raw in data) if src]


def squash(name: str) -> str:
    return re.sub(r"[\s._\-+（）()]+", "", (name or "").lower())


def vendor_key(name: str) -> str:
    key = squash(name)
    return ALIASES.get(key, key or "unknown")


def canonical_vendor(name: str) -> str:
    raw = (name or "").strip()
    mapped = ALIASES.get(squash(raw))
    if mapped:
        return DISPLAY.get(mapped, raw)
    return raw


def has_word(text: str, words: tuple) -> bool:
    low = (text or "").lower()
    for w in words:
        if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", low):
            return True
    return False


def host_of(url: str) -> str:
    try:
        host = urlparse(url or "").netloc.lower().strip(".")
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def host_is(host: str, suffixes: tuple) -> bool:
    for s in suffixes:
        if host == s or host.endswith("." + s):
            return True
    return False


def is_cn_host(host: str) -> bool:
    return bool(host) and (host.endswith(".cn") or host_is(host, CN_HOST_SUFFIXES))


def is_foreign_zh(vendor: str) -> bool:
    return "谷歌" in (vendor or "")


def strong_cn(vendor: str, url: str) -> bool:
    if is_cn_host(host_of(url)):
        return True
    if is_foreign_zh(vendor):
        return False
    if re.search(r"[一-鿿]", vendor or ""):
        return True
    if has_word(vendor, CN_WORDS) or any(s in (vendor or "") for s in CN_SUBSTR):
        return True
    return False


def infer_region(vendor: str, summary: str, url: str) -> str:
    """国内 / 国外。只看厂商名和域名。摘要几乎都是中文，不能用来判断要不要翻墙。"""
    host = host_of(url)
    vendor = vendor or ""
    if is_cn_host(host) or strong_cn(vendor, url):
        return "国内"
    if is_foreign_zh(vendor) or has_word(vendor, FOREIGN_WORDS) or host_is(host, FOREIGN_HOST_SUFFIXES):
        return "国外"
    return "国外"


def reconcile_region(vendor: str, summary: str, url: str, current: str) -> str:
    inferred = infer_region(vendor, summary, url)
    if inferred == "国内" and strong_cn(vendor, url):
        return "国内"
    if current in ("国内", "国外"):
        return current
    return inferred


def norm_type(value: str) -> str:
    raw = (value or "").strip()
    if raw in ALLOWED_TYPES:
        return raw
    key = raw.lower().replace(" ", "")
    return TYPE_ALIASES.get(key, TYPE_ALIASES.get(raw, "其他"))


def norm_model(value: str) -> str:
    s = (value or "").strip().lower()
    s = s.replace("系列", "")
    s = re.sub(r"\s+", "", s)
    s = s.strip("、,，等")
    return s or "通用"


def norm_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw.lower().startswith(("http://", "https://")):
        return ""
    if re.search(r"[\s<>\"']", raw):
        return ""
    try:
        p = urlparse(raw)
    except Exception:
        return ""
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host:
        return ""
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if k.lower() not in TRACKING_PARAMS]
    path = p.path.rstrip("/")
    return urlunparse((p.scheme.lower(), host, path, "", urlencode(query), ""))


def strip_tags(value: str) -> str:
    return re.sub(r"<[^>]*>", "", value or "").strip()


def amount_fp(amount: str) -> str:
    """额度指纹：同样的数字视为同一条，忽略「$10」和「$10免费试用额度」这种措辞差。"""
    nums = []
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*(亿|万|千|[kKmMwW])?(?!\d)", amount or ""):
        try:
            n = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        unit = (m.group(2) or "").lower()
        mult = {"": 1, "千": 1e3, "k": 1e3, "万": 1e4, "w": 1e4, "m": 1e6, "亿": 1e8}.get(unit, 1)
        nums.append(int(n * mult) if float(n * mult).is_integer() else n * mult)
    if nums:
        return "n:" + ",".join(str(n) for n in sorted(nums)[:6])
    slug = re.sub(r"\s+", "", (amount or "").lower())[:24]
    return "t:" + slug


def compute_expires(deadline: str) -> str:
    """只认明确日期。领取后 N 天是用户侧有效期，不算活动截止。"""
    dl = deadline or ""
    m = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", dl)
    if not m:
        m = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", dl)
    if not m:
        return ""
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return ""


def _date_or_blank(value: str) -> str:
    s = (value or "").strip()
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", s):
        return s
    return ""


def norm(o: dict, today="") -> dict:
    vendor = canonical_vendor(strip_tags(o.get("vendor") or ""))
    url = norm_url(strip_tags(o.get("url") or ""))
    summary = strip_tags(o.get("summary") or "")[:300]
    d = {
        "vendor": vendor,
        "model": strip_tags(o.get("model") or "").strip() or "通用",
        "offer_type": norm_type(strip_tags(o.get("offer_type") or "")),
        "amount": strip_tags(o.get("amount") or "")[:200],
        "deadline": strip_tags(o.get("deadline") or "")[:80],
        "conditions": strip_tags(o.get("conditions") or "")[:200],
        "url": url,
        "summary": summary,
        "source": strip_tags(o.get("source") or "")[:80],
        "region": (o.get("region") or "").strip(),
        "external_id": strip_tags(o.get("external_id") or "")[:160],
        "first_seen": _date_or_blank(o.get("first_seen") or ""),
        "last_seen": _date_or_blank(o.get("last_seen") or ""),
    }
    d["expires_on"] = compute_expires(d["deadline"])
    d["missing_since"] = _date_or_blank(o.get("missing_since") or "")
    d["region"] = reconcile_region(d["vendor"], d["summary"], d["url"], d["region"])
    if o.get("_born"):
        d["_born"] = True
    if o.get("_seen"):
        d["_seen"] = True
    if today and not d["first_seen"] and o.get("_born"):
        d["first_seen"] = today
    return d


def struct_key(o: dict) -> tuple:
    if o.get("external_id"):
        return ("id", o["external_id"])
    url = o.get("url") or ""
    base = (
        vendor_key(o.get("vendor") or ""),
        url or "nourl",
        o.get("offer_type") or "",
        norm_model(o.get("model") or ""),
    )
    if not url:
        slug = re.sub(r"\s+", "", o.get("summary") or "")[:40]
        return base + (amount_fp(o.get("amount") or ""), slug)
    return base


def _earlier(a: str, b: str) -> str:
    vals = [x for x in (a, b) if x]
    return min(vals) if vals else ""


def _later(a: str, b: str) -> str:
    vals = [x for x in (a, b) if x]
    return max(vals) if vals else ""


def _merge_record(old: dict, new: dict, today: str, refresh_seen: bool) -> dict:
    out = dict(old)
    for k in CONTENT_FIELDS:
        nv = (new.get(k) or "").strip()
        ov = (out.get(k) or "").strip()
        if not nv:
            continue
        if refresh_seen or not ov or len(nv) > len(ov):
            out[k] = nv
    out["vendor"] = canonical_vendor(out.get("vendor") or "")
    out["url"] = norm_url(out.get("url") or "")
    out["offer_type"] = norm_type(out.get("offer_type") or "")
    if refresh_seen:
        out["first_seen"] = old.get("first_seen") or new.get("first_seen") or today
        out["last_seen"] = today
        out["missing_since"] = ""
        out["_seen"] = True
    else:
        out["first_seen"] = _earlier(old.get("first_seen") or "", new.get("first_seen") or "")
        out["last_seen"] = _later(old.get("last_seen") or "", new.get("last_seen") or "")
        a = old.get("missing_since") or ""
        b = new.get("missing_since") or ""
        out["missing_since"] = min(a, b) if a and b else ""
    if old.get("_born") or new.get("_born"):
        out["_born"] = True
    if old.get("_seen") or new.get("_seen") or refresh_seen:
        out["_seen"] = True
    out["expires_on"] = compute_expires(out.get("deadline") or "")
    out["region"] = reconcile_region(out["vendor"], out.get("summary") or "", out.get("url") or "", out.get("region") or "")
    return out


def collapse(offers: list) -> list:
    """同一次快照内：结构键和额度指纹都相同才合并。不同额度（如 AWS 两个计划）保留。"""
    groups = defaultdict(list)
    order = []
    for o in offers:
        key = struct_key(o)
        if key not in groups:
            order.append(key)
        groups[key].append(o)
    out = []
    merged = 0
    for key in order:
        by_fp = {}
        fp_order = []
        for o in groups[key]:
            fp = amount_fp(o.get("amount") or "")
            if fp not in by_fp:
                fp_order.append(fp)
                by_fp[fp] = o
            else:
                by_fp[fp] = _merge_record(by_fp[fp], o, "", refresh_seen=False)
                merged += 1
        out.extend(by_fp[fp] for fp in fp_order)
    if merged:
        print(f"  · 合并重复 {merged} 条")
    return out


def upsert(prev: list, fresh: list, today: str):
    """把新抽取并入旧数据。一组里旧新各只剩 1 条时，额度措辞不同也视为同一条更新。"""
    groups = defaultdict(list)
    order = []
    for o in prev:
        key = struct_key(o)
        if key not in groups:
            order.append(key)
        groups[key].append(o)

    born = []
    fresh_groups = defaultdict(list)
    for o in fresh:
        fresh_groups[struct_key(o)].append(o)

    for key, news in fresh_groups.items():
        if key not in groups:
            order.append(key)
            groups[key] = []
        olds = groups[key]
        used_old, used_new = set(), set()
        for i, item in enumerate(news):
            fp = amount_fp(item.get("amount") or "")
            for j, old in enumerate(olds):
                if j in used_old:
                    continue
                if amount_fp(old.get("amount") or "") == fp:
                    olds[j] = _merge_record(old, item, today, refresh_seen=True)
                    used_old.add(j)
                    used_new.add(i)
                    break
        left_new = [i for i in range(len(news)) if i not in used_new]
        left_old = [j for j in range(len(olds)) if j not in used_old]
        if len(left_new) == 1 and len(left_old) == 1:
            i, j = left_new[0], left_old[0]
            olds[j] = _merge_record(olds[j], news[i], today, refresh_seen=True)
            used_new.add(i)
        for i, item in enumerate(news):
            if i in used_new:
                continue
            item = dict(item)
            item["first_seen"] = item.get("first_seen") or today
            item["last_seen"] = today
            item["_born"] = True
            item["_seen"] = True
            item["expires_on"] = compute_expires(item.get("deadline") or "")
            olds.append(item)
            born.append(item)
        groups[key] = olds

    flat = []
    for key in order:
        flat.extend(groups[key])
    return flat, born


def drop_stale(offers: list, successful_sources: set, today: str, days: int) -> list:
    """成功抽过的来源里，超过保留天数仍未再出现的条目才删除。失败来源的旧数据原样留下。"""
    today_d = datetime.date.fromisoformat(today)
    kept = []
    dropped = 0
    for o in offers:
        if o.get("_seen") or o.get("_born"):
            o["missing_since"] = ""
            kept.append(o)
            continue
        if o.get("source") not in successful_sources:
            kept.append(o)
            continue
        started = o.get("missing_since") or ""
        if not started:
            o["missing_since"] = today
            kept.append(o)
            continue
        age = (today_d - datetime.date.fromisoformat(started)).days
        if age <= days:
            kept.append(o)
        else:
            dropped += 1
    if dropped:
        print(f"  · 超过 {days} 天未再确认，移除 {dropped} 条")
    return kept


def public_offer(o: dict) -> dict:
    optional = {"missing_since", "first_seen", "last_seen", "expires_on", "external_id"}
    out = {}
    for k, v in o.items():
        if k.startswith("_"):
            continue
        if k in optional and not v:
            continue
        out[k] = v
    return out


def _rss_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = value.replace("**", "")
    return re.sub(r"\s+", " ", value).strip()


def _rss_day(value: str) -> str:
    raw = value or ""
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    except Exception:
        m = re.search(r"(20\d{2}-\d{2}-\d{2})", raw)
        return m.group(1) if m else ""


def _vendor_from_rss_title(title: str) -> str:
    m = re.match(r"^\[([^\]]{1,40})\]", title)
    if m:
        return m.group(1).strip()
    m = re.match(r"^额度变化\s*[·\-|]\s*([^：:]{1,40})", title)
    if m:
        return m.group(1).strip()
    return ""


def _summary_from_rss(title: str, desc: str, with_desc: bool) -> str:
    title = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", "", title).strip()
    parts = re.split(r"[：:]", title, maxsplit=1)
    body = parts[1].strip() if len(parts) == 2 and len(parts[1].strip()) > 8 else title
    if not with_desc:
        return body[:300]
    extra = _rss_text(desc)
    extra = re.sub(r"(?:^|\s)[a-z_]{3,24}：", " ", extra)
    extra = re.sub(r"\s+", " ", extra).strip()[:160]
    if extra and extra[:24] not in body:
        body = body + " " + extra
    return body[:300]


def offers_from_rss(xml_text: str, src: dict):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"[warn] RSS 解析失败 {src['name']}: {e}", file=sys.stderr)
        return None
    limit = int(src.get("limit") or 20)
    promo_only = bool(src.get("promo_only"))
    out = []
    for it in root.findall("./channel/item"):
        title = _rss_text(it.findtext("title") or "")
        if not title:
            continue
        desc = it.findtext("description") or ""
        blob = (title + " " + _rss_text(desc)[:400]).lower()
        if promo_only and not any(k.lower() in blob for k in PROMO_HINTS):
            continue
        guid = _rss_text(it.findtext("guid") or "") or (it.findtext("link") or "") or title
        day = _rss_day(it.findtext("pubDate") or "")
        deadline = ""
        if re.search(r"结束|下线|截止|到期", title):
            found = re.search(r"(20\d{2}-\d{2}-\d{2})", title)
            if found:
                deadline = found.group(1)
        out.append({
            "vendor": _vendor_from_rss_title(title) or src.get("vendor") or "",
            "model": "通用",
            "offer_type": "动态",
            "amount": "",
            "deadline": deadline,
            "conditions": "",
            "url": (it.findtext("link") or "").strip(),
            "summary": _summary_from_rss(title, desc, with_desc=not promo_only),
            "source": src["name"],
            "external_id": guid[:160],
            "first_seen": day,
        })
        if len(out) >= limit:
            break
    return out


def digest_html(text: str, src: dict) -> list:
    picked = []
    for line in text.splitlines():
        s = re.sub(r"\s+", " ", line).strip()
        if len(s) < 12 or len(s) > 180:
            continue
        if not any(k in s for k in ("免费", "额度", "Token", "token", "实名", "代金券")):
            continue
        if s not in picked:
            picked.append(s)
        if len(picked) >= 5:
            break
    if not picked:
        return []
    return [{
        "vendor": src.get("vendor") or "",
        "model": "通用",
        "offer_type": "新用户赠送",
        "amount": "",
        "deadline": "",
        "conditions": "以官方页面为准",
        "url": src["url"],
        "summary": "；".join(picked)[:300],
        "source": src["name"],
        "external_id": ("doc:" + src["url"])[:80],
    }]


def fetch_text(src: dict):
    try:
        r = requests.get(src["url"], timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        if src["type"] == "markdown":
            text = r.text
        else:
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = soup.get_text("\n", strip=True)
        text = (text or "").strip()
        if len(text) < 80:
            print(f"[warn] 正文过短，视为抓取失败 {src['name']}", file=sys.stderr)
            return None
        return text
    except Exception as e:
        print(f"[warn] 抓取失败 {src['name']}: {e}", file=sys.stderr)
        return None


def chunk_text(text: str, size: int = 10000, overlap: int = 400, max_chunks: int = 4) -> list:
    text = text.strip()
    if len(text) <= size:
        return [text]
    parts = re.split(r"\n(?=#{1,3} )", text)
    chunks = []
    buf = ""

    def push(piece: str):
        chunks.append(piece)

    for part in parts:
        if len(part) > size:
            if buf:
                push(buf)
                buf = ""
            step = max(1, size - overlap)
            for i in range(0, len(part), step):
                push(part[i:i + size])
            continue
        if buf and len(buf) + len(part) + 1 > size:
            push(buf)
            buf = buf[-overlap:] + "\n" + part
        else:
            buf = (buf + "\n" + part) if buf else part
    if buf:
        push(buf)
    if len(chunks) > max_chunks:
        print(f"[warn] 正文过长，只抽取前 {max_chunks} 段（共 {len(chunks)} 段）", file=sys.stderr)
        return chunks[:max_chunks]
    return chunks


def _parse_json_safely(content: str):
    if not content:
        return {}
    s = content.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    decoder = json.JSONDecoder()
    for i, ch in enumerate(s):
        if ch in "{[":
            try:
                obj, _ = decoder.raw_decode(s[i:])
                return obj
            except Exception:
                continue
    return {}


def _offers_from_parsed(data) -> list:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("offers", "data", "items", "result"):
            arr = data.get(key)
            if isinstance(arr, list):
                return [x for x in arr if isinstance(x, dict)]
    return []


def _llm_one(client, conf: dict, text: str, source_name: str, today: str, index: int, total: int):
    prompt = f"""你是 AI 大模型优惠信息抽取器。从下方文本中抽取所有「AI 大模型 / API 的免费额度、折扣、试用、赠送 Token、限时活动」。
今天是 {today}。这是同一篇来源的第 {index}/{total} 段，只抽取本段出现的优惠，不要编造。
只输出 JSON，格式：{{"offers":[...]}}，不要多余文字。每条字段：
- vendor: 厂商/平台名（中文优先，用官方常用名，不要把同一家拆成两种写法）
- model: 模型名，无则写"通用"
- offer_type: 只能是 免费token、折扣、试用、永久免费、新用户赠送 之一
- amount: 额度描述（如"500万 token"、"每日100次"），无则 ""
- deadline: 能确定公历日期就写 YYYY-MM-DD；滚动有效期写"领取后N天"；永久写"永久"；不确定留空。不要写"依活动而定"
- conditions: 领取条件，无则 ""
- url: 文本中的 http(s) 链接，没有则 ""
- summary: 一句话中文摘要（厂商+模型+额度+条件），不要 HTML
- region: "国内"或"国外"。国内=通常无需翻墙即可注册使用；国外=国内访问通常需要翻墙
忽略导航、目录、纯标题、以及与优惠无关的句子。
文本来源：{source_name}

文本：
{text}
"""
    kwargs = dict(
        model=conf["model"],
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    if conf["use_json_mode"]:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    content = resp.choices[0].message.content or ""
    return _offers_from_parsed(_parse_json_safely(content))


def extract_with_llm(text: str, source_name: str, today: str):
    """成功返回 list（可以是空列表）；请求或解析全失败返回 None。"""
    conf = resolve_llm()
    api_key = conf.get("api_key") or ""
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        print("[warn] 未安装 openai，跳过 LLM 抽取", file=sys.stderr)
        return None

    client = OpenAI(api_key=api_key, base_url=conf["base_url"], timeout=45)
    chunks = chunk_text(text)
    found = []
    ok_chunks = 0
    for i, piece in enumerate(chunks, 1):
        part = None
        for attempt in (1, 2):
            try:
                part = _llm_one(client, conf, piece, source_name, today, i, len(chunks))
                break
            except Exception as e:
                print(f"[warn] LLM 抽取失败 {source_name} 第{i}段 尝试{attempt}: {e}", file=sys.stderr)
        if part is None:
            continue
        ok_chunks += 1
        for item in part:
            item["source"] = source_name
        found.extend(part)
    if ok_chunks == 0:
        return None
    if not found and len(text) > 800:
        print(f"[warn] {source_name} 抽到 0 条，保留该来源旧数据", file=sys.stderr)
        return None
    return found


def extract_heuristic(text: str, source_name: str) -> list:
    free_sig = ("免费", "free", "额度", "赠送", "折扣", "试用", "羊毛", "福利", "限量", "领取")
    amt_pat = re.compile(r"\d+\s*(万|亿|w|k|m)?\s*(token|tokens|次|条|点|元)", re.I)
    url_pat = re.compile(r"https?://[^\s)>\]]+")
    out = []
    for line in text.splitlines():
        s = strip_tags(line)
        if len(s) < 8 or len(s) > 160:
            continue
        low = s.lower()
        if not any(k in low for k in free_sig):
            continue
        if not (any(k in low for k in ("gpt", "glm", "qwen", "deepseek", "kimi", "claude", "llama")) or amt_pat.search(s)):
            continue
        if "折扣" in s:
            offer_type = "折扣"
        elif "试用" in s:
            offer_type = "试用"
        elif "永久" in s:
            offer_type = "永久免费"
        elif "新用户" in s or "赠送" in s:
            offer_type = "新用户赠送"
        else:
            offer_type = "免费token"
        found = url_pat.search(s)
        out.append({
            "vendor": "",
            "model": "通用",
            "offer_type": offer_type,
            "amount": "",
            "deadline": "",
            "conditions": "",
            "url": found.group(0) if found else "",
            "summary": s[:120],
            "source": source_name,
        })
    return collapse([norm(o) for o in out])[:60]


def build_feishu_payload(new_offers: list, board: str) -> dict:
    lines = []
    for o in new_offers[:20]:
        summary = o.get("summary") or f"{o.get('vendor', '')} {o.get('model', '')}".strip()
        extra = f"（截止 {o['deadline']}）" if o.get("deadline") else ""
        row = [{"tag": "text", "text": f"· {summary}{extra}"}]
        href = o.get("url") or ""
        if href.startswith(("http://", "https://")):
            row.append({"tag": "a", "text": " 领取", "href": href})
        lines.append(row)
    if len(new_offers) > 20:
        lines.append([{"tag": "text", "text": f"· 另有 {len(new_offers) - 20} 条"}])
    if board.startswith(("http://", "https://")):
        lines.append([{"tag": "a", "text": "打开羊毛看板", "href": board}])
    return {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": f"🐑 AI 羊毛播报 · 新增 {len(new_offers)} 条",
                    "content": lines or [[{"tag": "text", "text": "本轮没有可展示的新增条目"}]],
                }
            }
        },
    }


def push_feishu(new_offers: list):
    webhook = env_text("FEISHU_WEBHOOK")
    if not webhook or not new_offers:
        return
    payload = build_feishu_payload(new_offers, dashboard_url())
    try:
        r = requests.post(webhook, json=payload, timeout=15)
    except Exception as e:
        print(f"[warn] 飞书推送失败: {type(e).__name__}", file=sys.stderr)
        return
    if r.status_code != 200:
        print(f"[warn] 飞书 HTTP {r.status_code}", file=sys.stderr)
        return
    try:
        body = r.json()
    except Exception:
        body = {}
    code = body.get("StatusCode", body.get("code", 0))
    if code not in (0, None):
        print(f"[warn] 飞书返回错误码 {code}", file=sys.stderr)
        return
    print(f"[ok] 飞书推送 {len(new_offers)} 条")


def build_site(offers: list, path: str = SITE_FILE):
    data = json.dumps([public_offer(o) for o in offers], ensure_ascii=False).replace("<", "\\u003c")
    now = shanghai_now()
    conf = resolve_llm()
    banner = llm_banner(conf).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = SITE_TEMPLATE.replace("__UPDATED__", now.strftime("%Y-%m-%d %H:%M"))
    html = html.replace("__LLM__", banner)
    html = html.replace("__TODAY__", now.strftime("%Y-%m-%d"))
    html = html.replace("__DATA__", data)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[ok] 看板已生成 {path}")


def load_offers() -> list:
    if not os.path.exists(OFFERS_FILE):
        return []
    try:
        with open(OFFERS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[error] 读取 offers.json 失败，已停止以免覆盖: {e}", file=sys.stderr)
        raise SystemExit(1)
    if not isinstance(data, list):
        print("[error] offers.json 不是数组，已停止以免覆盖", file=sys.stderr)
        raise SystemExit(1)
    return data


def save_offers(offers: list):
    clean = [public_offer(o) for o in offers]
    clean.sort(key=lambda o: (o.get("vendor") or "zzz", o.get("model") or "", o.get("offer_type") or "", o.get("amount") or ""))
    with open(OFFERS_FILE, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return clean


def rebuild_local() -> int:
    today = shanghai_today()
    prev = [norm(o, today) for o in load_offers()]
    offers = collapse(prev)
    offers = save_offers(offers)
    build_site(offers, SITE_FILE)
    print(f"== 已归一化 {len(offers)} 条（未访问网络）==")
    return 0


def _absorb(working, fresh, today):
    fresh = collapse([
        norm(item, today) for item in fresh
        if item.get("summary") or item.get("vendor")
    ])
    if not fresh:
        return working, []
    return upsert(working, fresh, today)


def main() -> int:
    today = shanghai_today()
    prev_raw = load_offers()
    conf = resolve_llm()
    print(f"== 当前抽取厂商：{llm_banner(conf)} ==")
    has_key = bool(conf.get("api_key"))
    if conf.get("key_from") == "LLM_API_KEY":
        print(f"== 使用通用密钥 LLM_API_KEY。请确认它属于 {conf['label']}，或改填 {conf['key_env']} ==")
    elif has_key:
        print(f"== 使用密钥 {conf['key_from']} ==")
    else:
        print(f"== 未配置 {conf['key_env']} 或 LLM_API_KEY，社区长文保持原样；仍会更新 RSS 和官方文档 ==")

    print("== 开始抓取 ==")
    working = collapse([norm(o, today) for o in prev_raw])
    successful = set()
    failures = 0

    sources = load_sources()
    if not sources:
        print("== sources.json 里没有开启的信源，未改写数据 ==")
        return 1

    for src in sources:
        kind = src.get("type")
        if kind == "markdown" and not has_key:
            print(f"  · {src['name']}: 跳过长文抽取")
            continue
        if kind == "rss":
            text = fetch_text({"name": src["name"], "type": "markdown", "url": src["url"]})
            parsed = offers_from_rss(text, src) if text else None
            if parsed is None:
                failures += 1
                print(f"  · {src['name']}: 抓取失败，保留旧数据")
                continue
            fresh = parsed
            print(f"  · {src['name']}: RSS {len(fresh)} 条")
        elif kind == "html":
            text = fetch_text(src)
            if text is None:
                failures += 1
                print(f"  · {src['name']}: 抓取失败，保留旧数据")
                continue
            fresh = digest_html(text, src)
            print(f"  · {src['name']}: 文档摘要 {len(fresh)} 条")
        else:
            text = fetch_text(src)
            if text is None:
                failures += 1
                print(f"  · {src['name']}: 抓取失败，保留旧数据")
                continue
            extracted = extract_with_llm(text, src["name"], today)
            if extracted is None:
                failures += 1
                print(f"  · {src['name']}: LLM 失败，保留旧数据")
                continue
            fresh = extracted
            print(f"  · {src['name']}: LLM 抽取 {len(fresh)} 条")
        if not fresh:
            print(f"  · {src['name']}: 没有可用条目")
            continue
        for item in fresh:
            item["source"] = src["name"]
        working, _born = _absorb(working, fresh, today)
        successful.add(src["name"])

    if not successful:
        print("== 全部来源失败，未改写数据 ==")
        return 1

    working = drop_stale(working, successful, today, retain_days())
    new_offers = [public_offer(o) for o in working if o.get("_born")]
    new_offers.sort(key=lambda o: (o.get("first_seen") or "", o.get("vendor") or ""), reverse=True)
    offers = save_offers(working)
    build_site(offers, SITE_FILE)
    print(f"== 合并后共 {len(offers)} 条，失败来源 {failures} 个 ==")
    if new_offers:
        print(f"== 发现新增 {len(new_offers)} 条 ==")
        push_feishu(new_offers)
    else:
        print("== 无新增推送 ==")
    return 0


SITE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>freetoken</title>
<style>
  @font-face{
    font-family:"Bricolage Grotesque";
    src:url("fonts/bricolage-grotesque-latin.woff2") format("woff2");
    font-weight:600;
    font-style:normal;
    font-display:swap;
  }
  :root{--bg:#0f1115;--card:#1a1d24;--line:#2a2f3a;--green:#3ddc84;--orange:#ff9d4d;--blue:#8eb4ff;--text:#e8eaed;--muted:#9aa0aa;}
  *{margin:0;padding:0;box-sizing:border-box;}
  body{background:var(--bg);color:var(--text);font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;line-height:1.6;padding:24px 16px 60px;}
  .wrap{max-width:960px;margin:0 auto;}
  header{display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:18px;}
  h1{font-family:"Bricolage Grotesque","Avenir Next",sans-serif;font-size:40px;font-weight:600;letter-spacing:-0.045em;line-height:1;color:var(--green);}
  .manage{margin-left:auto;color:var(--muted);font-size:13px;text-decoration:none;}
  .manage:hover{color:var(--green);}
  .meta{color:var(--muted);font-size:13px;flex:1 1 100%;}
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
  .card.expired{border-color:rgba(255,157,77,.45);}
  .card.stale{opacity:.62;}
  .card .top{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px;}
  .tag{font-size:11px;padding:2px 9px;border-radius:20px;background:rgba(61,220,132,.12);color:var(--green);border:1px solid rgba(61,220,132,.3);}
  .tag.orange{background:rgba(255,157,77,.12);color:var(--orange);border-color:rgba(255,157,77,.3);}
  .tag.blue{background:rgba(91,141,239,.12);color:var(--blue);border-color:rgba(91,141,239,.35);}
  .tag.gray{background:rgba(154,160,170,.12);color:var(--muted);border-color:rgba(154,160,170,.3);}
  .vendor{font-weight:600;font-size:15px;}
  .model{color:var(--muted);font-size:13px;}
  .summary{font-size:14px;margin:4px 0 8px;overflow-wrap:anywhere;}
  .row{display:flex;gap:14px;flex-wrap:wrap;font-size:12.5px;color:var(--muted);overflow-wrap:anywhere;}
  .row b{color:var(--text);font-weight:500;}
  .src{margin-top:6px;font-size:12px;color:#6b7280;overflow-wrap:anywhere;}
  .src a{color:#5b8def;text-decoration:none;}
  .empty{text-align:center;color:var(--muted);padding:40px;}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>freetoken</h1>
    <a class="manage" href="admin.html">管理</a>
    <div class="meta">更新于 __UPDATED__（北京时间） · 抽取 __LLM__</div>
  </header>
  <div class="filters">
    <input id="q" placeholder="搜索厂商 / 模型 / 条件 / 关键词">
    <select id="fType"><option value="">全部类型</option></select>
    <select id="fVendor"><option value="">全部厂商</option></select>
    <select id="fStatus">
      <option value="">全部状态</option>
      <option value="active">可领取</option>
      <option value="soon">7天内到期</option>
      <option value="expired">已过期</option>
      <option value="new">近7日新增</option>
    </select>
    <select id="fSort">
      <option value="new" selected>最近发现</option>
      <option value="vendor">按厂商</option>
      <option value="soon">即将到期</option>
    </select>
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
const TODAY = "__TODAY__";
const listEl = document.getElementById('list');
const qEl = document.getElementById('q');
const fType = document.getElementById('fType');
const fVendor = document.getElementById('fVendor');
const fStatus = document.getElementById('fStatus');
const fSort = document.getElementById('fSort');
const stat = document.getElementById('stat');
const fRegion = document.getElementById('fRegion');
let regionVal = "";
fRegion.querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
  fRegion.querySelectorAll('button').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  regionVal = b.dataset.v;
  render();
}));

function esc(s){
  return String(s == null ? '' : s).replace(/[&<>"']/g, ch => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  })[ch]);
}
function safeHref(u){
  const s = String(u || '').trim();
  if (!/^https?:\/\//i.test(s)) return '';
  if (/[\s<>"']/.test(s)) return '';
  return esc(s);
}
function uniq(arr){return [...new Set(arr)].filter(Boolean);}
function daysBetween(a, b){
  const da = Date.parse(a + 'T00:00:00Z');
  const db = Date.parse(b + 'T00:00:00Z');
  if (Number.isNaN(da) || Number.isNaN(db)) return null;
  return Math.round((db - da) / 86400000);
}
function flags(o){
  const ageSeen = o.last_seen ? daysBetween(o.last_seen, TODAY) : null;
  const ageNew = o.first_seen ? daysBetween(o.first_seen, TODAY) : null;
  const until = o.expires_on ? daysBetween(TODAY, o.expires_on) : null;
  return {
    expired: until != null && until < 0,
    soon: until != null && until >= 0 && until <= 7,
    fresh: ageNew != null && ageNew >= 0 && ageNew <= 7,
    stale: (o.missing_since && daysBetween(o.missing_since, TODAY) > 14) || (ageSeen != null && ageSeen > 14),
    until: until
  };
}
uniq(OFFERS.map(o => o.offer_type)).sort((a, b) => a.localeCompare(b, 'zh')).forEach(t => {
  const o = document.createElement('option');
  o.value = t; o.textContent = t; fType.appendChild(o);
});
uniq(OFFERS.map(o => o.vendor)).sort((a, b) => a.localeCompare(b, 'zh')).forEach(v => {
  const o = document.createElement('option');
  o.value = v; o.textContent = v; fVendor.appendChild(o);
});

function render(){
  const q = qEl.value.trim().toLowerCase();
  const t = fType.value, v = fVendor.value, status = fStatus.value, sort = fSort.value;
  let filtered = OFFERS.filter(o => {
    const f = flags(o);
    const blob = (o.vendor + o.model + o.summary + o.amount + (o.conditions || '')).toLowerCase();
    if (q && !blob.includes(q)) return false;
    if (t && o.offer_type !== t) return false;
    if (v && o.vendor !== v) return false;
    if (regionVal && o.region !== regionVal) return false;
    if (status === 'expired' && !f.expired) return false;
    if (status === 'soon' && !f.soon) return false;
    if (status === 'new' && !f.fresh) return false;
    if (status === 'active' && f.expired) return false;
    return true;
  });
  filtered = filtered.slice().sort((a, b) => {
    if (sort === 'new') {
      const af = a.first_seen || '', bf = b.first_seen || '';
      if (af !== bf) {
        if (!af) return 1;
        if (!bf) return -1;
        return bf.localeCompare(af);
      }
      return a.vendor.localeCompare(b.vendor, 'zh');
    }
    if (sort === 'soon') {
      const ae = a.expires_on || '9999-99-99', be = b.expires_on || '9999-99-99';
      return ae.localeCompare(be) || a.vendor.localeCompare(b.vendor, 'zh');
    }
    return a.vendor.localeCompare(b.vendor, 'zh') || (a.model || '').localeCompare(b.model || '', 'zh');
  });
  const cn = OFFERS.filter(o => o.region === '国内').length;
  const foreign = OFFERS.filter(o => o.region === '国外').length;
  const expiredN = OFFERS.filter(o => flags(o).expired).length;
  const freshN = OFFERS.filter(o => flags(o).fresh).length;
  stat.textContent = `共 ${OFFERS.length} 条（国内 ${cn} · 国外 ${foreign} · 已过期 ${expiredN} · 近7日 ${freshN}），当前显示 ${filtered.length} 条`;
  if (!filtered.length){ listEl.innerHTML = '<div class="empty">没有匹配的优惠</div>'; return; }
  listEl.innerHTML = filtered.map(o => {
    const f = flags(o);
    const typeCls = (o.offer_type === '折扣' || f.expired) ? 'tag orange' : (o.offer_type === '动态' ? 'tag blue' : 'tag');
    const regionCls = o.region === '国外' ? 'tag orange' : 'tag';
    const badges = [
      f.expired ? '<span class="tag orange">已过期</span>' : '',
      f.soon ? '<span class="tag orange">即将到期</span>' : '',
      f.fresh ? '<span class="tag blue">新</span>' : '',
      f.stale ? '<span class="tag gray">待复核</span>' : ''
    ].join('');
    const bits = [];
    if (o.amount) bits.push(`<span><b>额度</b> ${esc(o.amount)}</span>`);
    if (o.deadline) bits.push(`<span><b>截止</b> ${esc(o.deadline)}</span>`);
    if (o.expires_on && o.expires_on !== o.deadline) bits.push(`<span><b>日期</b> ${esc(o.expires_on)}</span>`);
    if (o.conditions) bits.push(`<span><b>条件</b> ${esc(o.conditions)}</span>`);
    const href = safeHref(o.url);
    const url = href ? ` · <a href="${href}" target="_blank" rel="noopener noreferrer">来源</a>` : '';
    const seen = o.first_seen ? ` · 发现于 ${esc(o.first_seen)}` : '';
    const confirmed = o.last_seen && o.last_seen !== o.first_seen ? ` · 确认于 ${esc(o.last_seen)}` : '';
    const missing = o.missing_since ? ` · ${esc(o.missing_since)} 起未再出现` : '';
    const cls = 'card' + (f.expired ? ' expired' : '') + (f.stale ? ' stale' : '');
    return `<div class="${cls}">
      <div class="top"><span class="${typeCls}">${esc(o.offer_type)}</span>
        <span class="${regionCls}">${esc(o.region || '未知')}</span>
        ${badges}
        <span class="vendor">${esc(o.vendor || '—')}</span>
        <span class="model">${esc(o.model || '')}</span></div>
      <div class="summary">${esc(o.summary || '')}</div>
      <div class="row">${bits.join('')}</div>
      <div class="src">📡 ${esc(o.source || '')}${seen}${confirmed}${missing}${url}</div>
    </div>`;
  }).join('');
}
[qEl, fType, fVendor, fStatus, fSort].forEach(el => el.addEventListener('input', render));
fStatus.addEventListener('change', render);
fSort.addEventListener('change', render);
render();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    if "--rebuild" in sys.argv:
        sys.exit(rebuild_local())
    sys.exit(main())
