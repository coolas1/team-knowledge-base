from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.db.neo4j_client import Neo4jClient
from src.db.postgres import init_db

neo4j_client: Neo4jClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global neo4j_client
    # startup
    await init_db()
    neo4j_client = Neo4jClient()
    yield
    # shutdown
    await neo4j_client.close()


app = FastAPI(title="Team Knowledge Base", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}
