import type { ToolDefinition } from "@earendil-works/pi-coding-agent";
import { performance } from "node:perf_hooks";

export class TurnBudgetExhaustedError extends Error {
  constructor() {
    super("No tool time remains before the reserved answer-synthesis window");
    this.name = "TurnBudgetExhaustedError";
  }
}

export class TurnDeadlineBudget {
  private startedAt: number;

  constructor(
    readonly maxRunSeconds: number,
    readonly reserveSeconds: number,
    private readonly clock: () => number = () => performance.now(),
  ) {
    this.startedAt = this.clock();
  }

  reset(): void {
    this.startedAt = this.clock();
  }

  remainingBeforeReserveMs(): number {
    const deadline = this.startedAt + this.maxRunSeconds * 1_000;
    return Math.max(0, deadline - this.reserveSeconds * 1_000 - this.clock());
  }

  effectiveTimeoutMs(configuredMs: number): number {
    const remaining = Math.floor(this.remainingBeforeReserveMs());
    if (remaining <= 0) throw new TurnBudgetExhaustedError();
    return Math.min(configuredMs, remaining);
  }
}

export class SearchFallbackBudget {
  private claimed = false;

  reset(): void {
    this.claimed = false;
  }

  claim(): boolean {
    if (this.claimed) return false;
    this.claimed = true;
    return true;
  }
}

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
