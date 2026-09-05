import { describe, expect, it, vi } from "vitest";
import type { ToolDefinition } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import {
  ExecutionBudget,
  enforceToolBudget,
  SearchFallbackBudget,
  TurnBudgetExhaustedError,
  TurnDeadlineBudget,
} from "../src/budget.js";

describe("ExecutionBudget", () => {
  it("blocks execution after the hard tool-call limit", async () => {
    const execute = vi.fn(async () => ({ content: [{ type: "text" as const, text: "ok" }], details: {} }));
    const tool = {
      name: "test",
      label: "Test",
      description: "test",
      parameters: Type.Object({}),
      execute,
    } as ToolDefinition;
    const budget = new ExecutionBudget(1);
    const wrapped = enforceToolBudget([tool], budget)[0]!;

    await wrapped.execute("1", {}, undefined, undefined, {} as never);
    const rejected = await wrapped.execute("2", {}, undefined, undefined, {} as never);

    expect(execute).toHaveBeenCalledOnce();
    expect(budget.limitReached).toBe(true);
    expect((rejected as { terminate?: boolean }).terminate).toBe(true);
  });

  it("resets for a new user message", () => {
    const budget = new ExecutionBudget(1);
    expect(budget.claim()).toBe(true);
    expect(budget.claim()).toBe(false);
    budget.reset();
    expect(budget.claim()).toBe(true);
    expect(budget.limitReached).toBe(false);
  });
});

describe("turn deadline budgets", () => {
  it("caps a late tool call before the synthesis reserve", () => {
    let now = 1_000;
    const budget = new TurnDeadlineBudget(180, 60, () => now);
    expect(budget.effectiveTimeoutMs(60_000)).toBe(60_000);
    now += 100_000;
    expect(budget.effectiveTimeoutMs(60_000)).toBe(20_000);
    now += 20_000;
    expect(() => budget.effectiveTimeoutMs(1_000)).toThrow(TurnBudgetExhaustedError);
  });

  it("allows one automatic fallback per turn and resets", () => {
    const budget = new SearchFallbackBudget();
    expect(budget.claim()).toBe(true);
    expect(budget.claim()).toBe(false);
    budget.reset();
    expect(budget.claim()).toBe(true);
  });
});
