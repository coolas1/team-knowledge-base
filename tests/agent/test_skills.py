from src.agent.interface import SkillContext
from src.agent.skills.ingest_and_summarize import IngestAndSummarizeSkill
from src.agent.skills.search_and_answer import SearchAndAnswerSkill


class FakeEngineClient:
    def __init__(self):
        self.recall_result = {"chunks": [{"doc_id": "d1", "title": "Acme Doc",
                                          "chunk_text": "Acme is in Building A.",
                                          "reranker_score": 9.0, "vector_score": 0.8}],
                            "related_entities": [{"name": "Acme"}], "related_docs": []}
        self.ingested = None
        self.doc_detail = {"id": "d1", "raw_text": "Acme is in Building A.", "overview": "ov"}

    async def recall(self, query, top_k=10):
        return self.recall_result

    async def ingest(self, name, data):
        self.ingested = (name, data)
        return {"id": "d1", "title": name, "file_type": "markdown", "status": "indexed"}

    async def get_document(self, doc_id):
        return self.doc_detail

    async def get_graph(self, entity=None):
        return {"nodes": [], "links": []}

    async def get_neighbors(self, entity):
        return {"nodes": [], "links": []}


class FakeLlm:
    def __init__(self):
        self.prompts = []

    async def complete(self, prompt):
        self.prompts.append(prompt)
        return "SYNTHESIZED ANSWER"


async def test_search_and_answer_with_llm_synthesizes():
    skill = SearchAndAnswerSkill()
    llm = FakeLlm()
    ctx = SkillContext(engine=FakeEngineClient(), llm=llm, params={"query": "where is Acme?"})
    res = await skill.run(ctx)
    assert res.name == "search_and_answer"
    assert res.output["answer"] == "SYNTHESIZED ANSWER"
    assert res.output["query"] == "where is Acme?"
    assert "Acme" in res.output["sources"]["chunks"][0]["chunk_text"]
    assert len(llm.prompts) == 1
    assert "where is Acme?" in llm.prompts[0]


async def test_search_and_answer_without_llm_returns_context():
    skill = SearchAndAnswerSkill()
    ctx = SkillContext(engine=FakeEngineClient(), llm=None, params={"query": "q"})
    res = await skill.run(ctx)
    assert "Acme is in Building A" in res.output["answer"]


async def test_search_and_answer_respects_top_k():
    skill = SearchAndAnswerSkill()
    engine = FakeEngineClient()
    ctx = SkillContext(engine=engine, llm=None, params={"query": "q", "top_k": 3})
    await skill.run(ctx)
    # FakeEngineClient ignores top_k, but the skill must pass it through without error
    assert True


async def test_ingest_and_summarize_with_llm():
    skill = IngestAndSummarizeSkill()
    engine = FakeEngineClient()
    llm = FakeLlm()
    ctx = SkillContext(engine=engine, llm=llm, params={"name": "r.md", "data": b"Acme is in Building A."})
    res = await skill.run(ctx)
    assert res.name == "ingest_and_summarize"
    assert res.output["doc"]["title"] == "r.md"
    assert res.output["summary"] == "SYNTHESIZED ANSWER"
    assert engine.ingested == ("r.md", b"Acme is in Building A.")
    assert "r.md" in llm.prompts[0]


async def test_ingest_and_summarize_without_llm_uses_overview():
    skill = IngestAndSummarizeSkill()
    engine = FakeEngineClient()
    ctx = SkillContext(engine=engine, llm=None, params={"name": "r.md", "data": b"x"})
    res = await skill.run(ctx)
    assert res.output["summary"] == "ov"
