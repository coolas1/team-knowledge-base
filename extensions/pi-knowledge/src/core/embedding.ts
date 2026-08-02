import type { KnowledgeConfig } from "../config.ts";

/**
 * 火山方舟 ARK embedding 客户端（多模态端点 /embeddings/multimodal）。
 * doubao-embedding-vision 系列：文本与图片嵌入同一向量空间，跨模态可检索。
 * 服务端 dimensions 参数降维（251215 支持 1024/2048）；若返回维度仍超出
 * 配置维度则客户端兜底截取前 N 维 + L2 归一化（MRL）。
 * 该端点单请求只产出一个向量，批量摄取用并发池补偿。
 */

type MultimodalInput =
  | { type: "text"; text: string }
  | { type: "image_url"; image_url: { url: string } };

interface ArkMultimodalEmbeddingResponse {
  data: { embedding: number[] } | Array<{ embedding: number[] }>;
  usage?: Record<string, unknown>;
}

function sliceAndNormalize(embedding: number[], dim: number): number[] {
  if (embedding.length === dim) return embedding;
  if (embedding.length < dim) {
    throw new Error(`embedding 维度不足: 返回 ${embedding.length}, 需要 ${dim}`);
  }
  const sliced = embedding.slice(0, dim);
  let norm = 0;
  for (const value of sliced) norm += value * value;
  norm = Math.sqrt(norm);
  if (norm === 0) return sliced;
  return sliced.map((value) => value / norm);
}

async function embedRequest(config: KnowledgeConfig, input: MultimodalInput[]): Promise<number[]> {
  if (!config.ark.apiKey) {
    throw new Error("缺少 ARK API Key：设置环境变量 ARK_API_KEY 或在 ~/.pi/agent/knowledge.json 配置 ark.apiKey");
  }
  const response = await fetch(`${config.ark.baseUrl}/embeddings/multimodal`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${config.ark.apiKey}`,
    },
    body: JSON.stringify({
      model: config.ark.embeddingModel,
      input,
      dimensions: config.ark.embeddingDim,
    }),
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`ARK embedding 请求失败 (${response.status}): ${body.slice(0, 500)}`);
  }
  const payload = (await response.json()) as ArkMultimodalEmbeddingResponse;
  const item = Array.isArray(payload.data) ? payload.data[0] : payload.data;
  return sliceAndNormalize(item.embedding, config.ark.embeddingDim);
}

/** 批量文本 embedding；端点单请求单向量，内部用并发池 */
export async function embed(config: KnowledgeConfig, texts: string[]): Promise<number[][]> {
  if (texts.length === 0) return [];
  const results: number[][] = new Array(texts.length);
  let cursor = 0;
  const concurrency = Math.min(4, texts.length);
  const worker = async () => {
    while (cursor < texts.length) {
      const index = cursor++;
      results[index] = await embedRequest(config, [{ type: "text", text: texts[index] }]);
    }
  };
  await Promise.all(Array.from({ length: concurrency }, worker));
  return results;
}

export async function embedOne(config: KnowledgeConfig, text: string): Promise<number[]> {
  return embedRequest(config, [{ type: "text", text }]);
}

/** 图片 embedding：url 可为 http(s) 或 data:image/...;base64,... */
export async function embedImage(config: KnowledgeConfig, imageUrl: string): Promise<number[]> {
  return embedRequest(config, [{ type: "image_url", image_url: { url: imageUrl } }]);
}
