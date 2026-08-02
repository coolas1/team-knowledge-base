DELETE FROM chunks; DELETE FROM documents;
INSERT INTO documents (source_path, kind, content_hash) VALUES ('test.md','doc','x');
INSERT INTO chunks (doc_id, seq, content)
SELECT d.id, s.seq, s.content FROM documents d,
  (VALUES (0,'火山方舟的向量化模型支持中文检索'),(1,'今天天气很好适合散步')) AS s(seq, content)
WHERE d.source_path = 'test.md';
SELECT id, content, pdb.score(id) AS score FROM chunks WHERE content @@@ pdb.match('向量检索') ORDER BY score DESC;
DELETE FROM chunks; DELETE FROM documents;
