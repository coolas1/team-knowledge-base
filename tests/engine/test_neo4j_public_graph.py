"""公共图谱视图的过滤 / 邻域连线 / 关系去重（design D5/D10/D11）。

用 fake driver 记录 Cypher：断言查询包含公共来源过滤（排除会话转录）、
hops 透传、邻域内部关系边返回、实体关系按 (type, direction, endpoint)
去重 —— 无需真实 Neo4j。
"""

from src.engine.components.store.neo4j import Neo4jClient


class _FakeResult:
    def __init__(self, records=None, single=None):
        self._records = records or []
        self._single = single

    async def data(self):
        return self._records

    async def single(self):
        return self._single


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.queries: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def run(self, query, **params):
        self.queries.append((query, params))
        if self.responses:
            return self.responses.pop(0)
        return _FakeResult()


class _FakeDriver:
    def __init__(self, session):
        self._session = session

    def session(self):
        return self._session


def _client_with(session) -> Neo4jClient:
    client = Neo4jClient.__new__(Neo4jClient)  # 跳过 __init__，不连真实服务
    client._driver = _FakeDriver(session)
    return client


async def test_query_neighbors_threads_hops_and_returns_links():
    session = _FakeSession(
        [
            _FakeResult(
                [
                    {
                        "neighbor": {"name": "Acme", "description": "园区公司"},
                        "labels": ["Company"],
                    }
                ]
            ),
            _FakeResult(
                [
                    {
                        "source": "Acme",
                        "target": "Bob",
                        "type": "EMPLOYS",
                        "description": "",
                    }
                ]
            ),
        ]
    )
    client = _client_with(session)

    results, links = await client.query_neighbors("Acme", hops=1)

    assert [r.name for r in results] == ["Acme"]
    assert [r.entity_type for r in results] == ["Company"]
    assert links == [
        {"source": "Acme", "target": "Bob", "type": "EMPLOYS", "description": ""}
    ]

    node_query = session.queries[0][0]
    assert "[*1..1]" in node_query  # hops 真实透传，而非硬编码 2
    link_query = session.queries[1][0]
    assert "MATCH (a)-[r]->(b)" in link_query  # 邻域内部关系边


async def test_query_neighbors_excludes_conversation_only_neighborhood():
    session = _FakeSession([_FakeResult([]), _FakeResult([])])
    client = _client_with(session)

    await client.query_neighbors("Acme", hops=2)

    for query, _params in session.queries:
        assert "neighbor.sources IS NOT NULL" in query
        assert "'conversation'" in query


async def test_get_entity_details_dedupes_parallel_relations():
    relation = {
        "type": "SAME_AS",
        "direction": "OUT",
        "other_name": "koji-kin",
        "other_labels": ["Entity"],
    }
    record = {
        "n": {"name": "koji", "sources": "[]"},
        "labels": ["Entity"],
        "relations": [
            {**relation, "description": "第一次抽取"},
            {**relation, "description": "平行边第二次抽取"},
            {
                "type": "RELATED_TO",
                "direction": "IN",
                "other_name": "koji-kin",
                "other_labels": ["Entity"],
                "description": "",
            },
            {
                "type": "SAME_AS",
                "direction": "OUT",
                "other_name": "other-entity",
                "other_labels": ["Entity"],
                "description": "",
            },
        ],
    }
    session = _FakeSession([_FakeResult(single=record)])
    client = _client_with(session)

    details = await client.get_entity_details("koji")

    assert details is not None
    # 平行边折叠为一条；方向/端点不同则保留
    assert [(r["type"], r["direction"], r["other_name"]) for r in details.relations] == [
        ("SAME_AS", "OUT", "koji-kin"),
        ("RELATED_TO", "IN", "koji-kin"),
        ("SAME_AS", "OUT", "other-entity"),
    ]
    query = session.queries[0][0]
    assert "n.sources IS NOT NULL" in query
    assert "'conversation'" in query


async def test_get_full_graph_excludes_conversation_sources():
    session = _FakeSession([_FakeResult([]), _FakeResult([])])
    client = _client_with(session)

    await client.get_full_graph()

    assert len(session.queries) == 2
    for query, _params in session.queries:
        assert "'conversation'" in query


async def test_get_related_docs_projects_relation_type_with_edge_fallback():
    session = _FakeSession(
        [
            _FakeResult(
                [
                    {
                        "doc_id": "d2",
                        "title": "相关.md",
                        "rel_type": "RELATED_TO",
                        "relation_type": "REFERENCES",
                        "reason": "引用",
                    }
                ]
            )
        ]
    )
    client = _client_with(session)

    docs = await client.get_related_docs(["d1"])

    assert docs == [
        {
            "doc_id": "d2",
            "title": "相关.md",
            "relation_type": "REFERENCES",
            "reason": "引用",
        }
    ]
    query = session.queries[0][0]
    assert "coalesce(r.relation_type, type(r))" in query  # 边类型兜底
    assert "d2.file_type" in query  # 会话转录文档不作为关联文档


async def test_get_related_docs_empty_input_skips_the_query():
    session = _FakeSession([])
    client = _client_with(session)

    assert await client.get_related_docs([]) == []
    assert session.queries == []
