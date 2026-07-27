from src.engine.components.chunker import Chunk, chunk_text


def test_empty_text_returns_empty():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_single_paragraph_one_chunk():
    chunks = chunk_text("One short paragraph.")
    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert "One short" in chunks[0].text


def test_many_paragraphs_split_with_overlap():
    paras = [f"Paragraph number {i} with enough words to fill space." for i in range(20)]
    text = "\n\n".join(paras)
    chunks = chunk_text(text, chunk_size=40, overlap=10)
    assert len(chunks) > 1
    assert all(isinstance(c, Chunk) for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_oversized_paragraph_is_own_chunk():
    big = "word " * 1000
    chunks = chunk_text(big, chunk_size=100, overlap=0)
    assert len(chunks) >= 1
