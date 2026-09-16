import { defineTool, type ToolDefinition } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

export type ProposalType = "decision" | "preference" | "commitment";

export interface ProposalDraft {
  proposalType: ProposalType;
  normalizedContent: string;
}

export interface ProposalDraftState {
  value?: ProposalDraft;
}

export function currentProposalDraft(state: ProposalDraftState): ProposalDraft | undefined {
  return state.value;
}

const PROPOSAL_PARAMS = Type.Object({
  proposal_type: Type.Union([
    Type.Literal("decision"),
    Type.Literal("preference"),
    Type.Literal("commitment"),
  ]),
  normalized_content: Type.String({
    minLength: 1,
    maxLength: 2_000,
    description: "Self-contained normalized proposal, without confirmation wording",
  }),
});

export function buildProposalTool(state: ProposalDraftState): ToolDefinition {
  return defineTool({
    name: "tkb_register_pending_proposal",
    label: "Register pending proposal",
    description:
      "Register a document-grounded proposal before asking the user to confirm it. "
      + "Evidence IDs are bound by the runtime and must not be supplied here.",
    promptSnippet:
      "tkb_register_pending_proposal: structure an evidence-backed proposal before requesting confirmation",
    parameters: PROPOSAL_PARAMS,
    async execute(_toolCallId, params) {
      const normalizedContent = params.normalized_content.replace(/\s+/gu, " ").trim();
      if (!normalizedContent) throw new Error("normalized proposal must not be empty");
      state.value = {
        proposalType: params.proposal_type,
        normalizedContent,
      };
      return {
        content: [{ type: "text" as const, text: "Pending proposal registered for explicit user confirmation." }],
        details: { proposalType: params.proposal_type },
      };
    },
  }) as ToolDefinition;
}
