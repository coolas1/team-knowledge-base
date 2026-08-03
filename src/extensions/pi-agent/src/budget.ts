import type { ToolDefinition } from "@earendil-works/pi-coding-agent";

export class ExecutionBudget {
  private calls = 0;
  private exceeded = false;

  constructor(readonly maxToolCalls: number) {}

  reset(): void {
    this.calls = 0;
    this.exceeded = false;
  }

  claim(): boolean {
    this.calls += 1;
    if (this.calls > this.maxToolCalls) {
      this.exceeded = true;
      return false;
    }
    return true;
  }

  get toolCalls(): number {
    return this.calls;
  }

  get limitReached(): boolean {
    return this.exceeded;
  }
}

export function enforceToolBudget(
  tools: ToolDefinition[],
  budget: ExecutionBudget,
): ToolDefinition[] {
  return tools.map((tool) => {
    const execute = tool.execute.bind(tool);
    return {
      ...tool,
      async execute(toolCallId, params, signal, onUpdate, ctx) {
        if (!budget.claim()) {
          return {
            content: [
              {
                type: "text" as const,
                text: `Tool call limit reached (${budget.maxToolCalls}). Stop researching and answer from the evidence already collected.`,
              },
            ],
            details: { limit: "tool_calls", max: budget.maxToolCalls },
            isError: true,
            terminate: true,
          };
        }
        return execute(toolCallId, params, signal, onUpdate, ctx);
      },
    } as ToolDefinition;
  });
}
