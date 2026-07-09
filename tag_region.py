#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一次性工具：给现有 offers.json 补 region（国内/国外）标签。
- 优先用 LLM 批量判断（更准确）
- LLM 失败或漏判的，回退 fetch_offers.infer_region 启发式
用法：
    LLM_API_KEY=sk-xxx python tag_region.py
跑完会写回 offers.json 并重建 index.html。
"""
import os
import sys
import json
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_offers as f

KEY = os.environ.get("LLM_API_KEY")
if not KEY:
    print("[warn] 未设置 LLM_API_KEY，将全部用启发式兜底标注")

offers = json.load(open("offers.json", encoding="utf-8"))
todo = [i for i, o in enumerate(offers) if not o.get("region")]
print(f"需标注 {len(todo)} 条（共 {len(offers)} 条）")

client = None
if KEY:
    try:
        from openai import OpenAI
        conf = f.resolve_llm()
        client = OpenAI(api_key=KEY, base_url=conf["base_url"])
        MODEL = conf["model"]
        print(f"[ok] LLM 已就绪：{conf['base_url']} / {MODEL}")
    except Exception as e:
        print(f"[warn] LLM 初始化失败，转启发式：{e}")
        client = None


def ask_llm(batch):
    payload = json.dumps(
        [{"id": i, "vendor": offers[i].get("vendor", ""), "summary": offers[i].get("summary", "")}
         for i in batch], ensure_ascii=False)
    prompt = (f"判断以下每个 AI 服务/优惠是「国内（无需翻墙可直接访问/注册）」还是"
              f"「国外（国内访问通常需翻墙）」。\n"
              f"只输出 JSON：{{\"result\":[{{\"id\":0,\"region\":\"国内\"}},...]}}。"
              f"region 只能是\"国内\"或\"国外\"。\n输入：{payload}")
    try:
        r = client.chat.completions.create(
            model=MODEL, messages=[{"role": "user", "content": prompt}], temperature=0)
        data = f._parse_json_safely(r.choices[0].message.content)
        return {x.get("id"): x.get("region", "") for x in data.get("result", [])}
    except Exception as e:
        print(f"  [warn] 批量标注失败：{e}")
        return {}


BATCH = 15
for start in range(0, len(todo), BATCH):
    batch = todo[start:start + BATCH]
    res = ask_llm(batch) if client else {}
    for i in batch:
        reg = res.get(i, "")
        if reg in ("国内", "国外"):
            offers[i]["region"] = reg
        else:
            offers[i]["region"] = f.infer_region(
                offers[i].get("vendor", ""), offers[i].get("summary", ""), offers[i].get("url", ""))
    print(f"  已处理 {min(start + BATCH, len(todo))}/{len(todo)}")

json.dump(offers, open("offers.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
f.build_site(offers, "index.html")
print("region 分布:", dict(Counter(o.get("region", "") for o in offers)))
print("[ok] 已写回 offers.json 并重建 index.html")
