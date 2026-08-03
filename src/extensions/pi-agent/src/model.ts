import path from "node:path";
import { ModelRuntime } from "@earendil-works/pi-coding-agent";
import type { PiAgentConfig } from "./config.js";

export type PiAgentModel = NonNullable<ReturnType<ModelRuntime["getModel"]>>;

export interface ModelServices {
  runtime: ModelRuntime;
  model: PiAgentModel;
}

export async function buildModelServices(
  config: PiAgentConfig,
): Promise<ModelServices> {
  if (!config.modelApiKey) {
    throw new Error(`PI_AGENT_API_KEY is required for provider ${config.provider}`);
  }

  const runtime = await ModelRuntime.create({
    authPath: path.join(config.dataDir, "auth.json"),
    modelsPath: null,
    modelsStorePath: path.join(config.dataDir, "models-store.json"),
    allowModelNetwork: false,
  });
  const ollamaCompat =
    config.provider.toLowerCase() === "ollama" &&
    config.modelApi === "openai-completions"
      ? {
          supportsDeveloperRole: false,
          supportsReasoningEffort: false,
        }
      : undefined;

  runtime.registerProvider(config.provider, {
    name: config.provider,
    baseUrl: config.modelBaseUrl,
    api: config.modelApi,
    apiKey: config.modelApiKey,
    models: [
      {
        id: config.model,
        name: config.modelName,
        api: config.modelApi,
        reasoning: config.modelReasoning,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: config.contextWindow,
        maxTokens: config.maxOutputTokens,
        compat: ollamaCompat,
      },
    ],
  });
  await runtime.setRuntimeApiKey(config.provider, config.modelApiKey);
  const model = runtime.getModel(config.provider, config.model);
  if (!model) {
    throw new Error(`Pi model was not registered: ${config.provider}/${config.model}`);
  }
  return { runtime, model };
}
