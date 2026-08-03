import { PiAgentRuntime } from "./runtime.js";

const prompt =
  process.argv.slice(2).join(" ").trim() ||
  "利用工具告诉我当前可以查看的知识库文件，只列出文件名。";

const runtime = new PiAgentRuntime();

try {
  await runtime.initialize();
  const session = await runtime.createSession();
  process.stderr.write(`session=${session.id}\n`);
  await runtime.streamMessage(session.id, prompt, (event) => {
    if (event.type === "assistant.delta") {
      process.stdout.write(event.delta);
      return;
    }
    if (event.type === "tool.start") {
      process.stderr.write(`\ntool.start=${event.toolName}\n`);
      return;
    }
    if (event.type === "citation") {
      process.stderr.write(`citation=${event.title} (${event.docId})\n`);
      return;
    }
    if (event.type === "message.completed") {
      process.stderr.write(`\ncompleted toolCalls=${event.toolCalls}\n`);
    }
  });
  process.stdout.write("\n");
} finally {
  await runtime.close();
}
