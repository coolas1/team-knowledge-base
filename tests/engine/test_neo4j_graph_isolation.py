from src.engine.components.store.neo4j import Neo4jClient


class Result:
    async def data(self):
        return []


class Session:
    def __init__(self):
        self.queries = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def run(self, query, **parameters):
        self.queries.append(query)
        return Result()


class Driver:
    def __init__(self):
        self.value = Session()

    def session(self):
        return self.value


async def test_full_graphrag_graph_excludes_hindsight_projection_edges():
    client = Neo4jClient.__new__(Neo4jClient)
    client._driver = Driver()

    graph = await client.get_full_graph()

    assert graph == {"nodes": [], "links": []}
    link_query = client._driver.value.queries[1]
    assert "a.sources IS NOT NULL" in link_query
    assert "b.sources IS NOT NULL" in link_query


class DetailResult:
    async def single(self):
        return {
            "n": {"name": "Shared", "entity_type": "Concept"},
            "labels": ["Concept"],
            "relations": [],
        }


class DetailSession(Session):
    async def run(self, query, **parameters):
        self.queries.append(query)
        return DetailResult()


class DetailDriver:
    def __init__(self):
        self.value = DetailSession()

    def session(self):
        return self.value


async def test_entity_details_collapses_duplicate_names_deterministically():
    client = Neo4jClient.__new__(Neo4jClient)
    client._driver = DetailDriver()

    result = await client.get_entity_details("Shared")

    assert result is not None
    assert result.name == "Shared"
    query = client._driver.value.queries[0]
    assert "WHERE NOT n:Document" in query
    assert "ORDER BY coalesce(n.entity_type, ''), elementId(n)" in query
    assert "WITH collect(n) AS matches" in query
    assert "WITH head(matches) AS n, relations" in query
