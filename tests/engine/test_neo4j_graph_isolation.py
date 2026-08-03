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
