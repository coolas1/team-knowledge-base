"""Diagnose why expenses.csv wasn't recalled for Q29."""
import os
for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
    os.environ.pop(k, None)
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import asyncio
import asyncpg
import httpx

http = httpx.Client(trust_env=False, timeout=60.0)


async def db_check():
    c = await asyncpg.connect(host="localhost", port=5433, database="knowledge_base",
                              user="kb_user", password="kb_pass")
    # 找 expenses.csv 文档
    doc = await c.fetchrow(
        "SELECT id::text, title, overview, index_status FROM documents WHERE title='expenses.csv'"
    )
    if not doc:
        print("expenses.csv 文档不存在！")
        await c.close()
        return
    print(f"=== expenses.csv 文档 ===")
    print(f"doc_id: {doc['id']}")
    print(f"index_status: {doc['index_status']}")
    print(f"overview: {doc['overview'][:300]}")
    # 它的 chunks
    chunks = await c.fetch(
        "SELECT chunk_index, chunk_text, token_count FROM chunks WHERE doc_id=$1 ORDER BY chunk_index",
        doc['id']
    )
    print(f"\n=== expenses.csv 的 chunks ({len(chunks)} 个) ===")
    for ch in chunks[:3]:
        print(f"\n--- chunk {ch['chunk_index']} (token~{ch['token_count']}) ---")
        print(ch['chunk_text'][:500])
    await c.close()


def search_check():
    print("\n\n========== 检索测试 ==========")
    # 测试 1: Q29 原始查询
    q1 = "Compare the total Okinawa-trip spend reported in blog.md with the sum of all receipts in expenses.csv"
    r = http.post("http://127.0.0.1:8000/search", json={"query": q1})
    chunks = r.json().get("chunks", [])
    print(f"\n[Q29 原始查询] 返回 {len(chunks)} chunks，expenses.csv 排名：")
    found = False
    for i, c in enumerate(chunks, 1):
        if "expenses" in c.get("title", ""):
            print(f"  第 {i} 位: {c['title']} (reranker={c['reranker_score']:.3f}, vector={c['vector_score']:.3f})")
            found = True
    if not found:
        print("  ❌ expenses.csv 完全没被召回！")
    print("  top5:", [c['title'] for c in chunks[:5]])

    # 测试 2: 直接搜 expenses 内容
    q2 = "expenses receipt amount JPY food lodging dive"
    r2 = http.post("http://127.0.0.1:8000/search", json={"query": q2})
    chunks2 = r2.json().get("chunks", [])
    print(f"\n[直接搜 expenses 内容] expenses.csv 排名：")
    for i, c in enumerate(chunks2, 1):
        if "expenses" in c.get("title", ""):
            print(f"  第 {i} 位: {c['title']} (reranker={c['reranker_score']:.3f})")
            break
    print("  top5:", [c['title'] for c in chunks2[:5]])


asyncio.run(db_check())
search_check()
