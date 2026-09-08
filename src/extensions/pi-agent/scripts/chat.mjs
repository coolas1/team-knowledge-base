// Terminal chat client for the TKB Pi Agent HTTP/SSE API (default :8010).
//
// Drives POST /v1/sessions then POST /v1/sessions/:id/messages, rendering the
// streamed SSE events (assistant deltas, tool calls, citations) inline.
//
//   node scripts/chat.mjs [base-url]      # base-url defaults to $PI_AGENT_URL
//                                        #   or http://localhost:8010
//
// The two pure helpers (parseSseFrames, renderEvent) are unit-tested in
// tests/chat.test.mjs; everything below them is interactive I/O glue.

import { pathToFileURL } from "node:url";
import * as readline from "node:readline/promises";

const DEFAULT_URL = process.env.PI_AGENT_URL || "http://localhost:8010";

// --- pure: split an SSE byte stream into complete frames -----------------
// Returns { frames: [{ type, data }], rest } where `rest` is the incomplete
// trailing frame to carry into the next chunk.
export function parseSseFrames(buffer) {
  const chunks = buffer.split("\n\n");
  const rest = chunks.pop() ?? "";
  const frames = [];
  for (const raw of chunks) {
    if (!raw || !raw.trim()) continue;
    let type;
    const dataParts = [];
    for (const line of raw.split("\n")) {
      if (line.startsWith("event:")) type = line.slice("event:".length).trim();
      else if (line.startsWith("data:")) dataParts.push(line.slice("data:".length).replace(/^ /, ""));
    }
    const data = dataParts.length === 0 ? undefined : safeParse(dataParts.join("\n"));
    frames.push({ type, data });
  }
  return { frames, rest };
}

function safeParse(s) {
  try {
    return JSON.parse(s);
  } catch {
    return s; // hand the raw payload back when it isn't JSON
  }
}

// --- pure: map an agent event to a printable fragment --------------------
// tone: stream | think | info | ok | warn | error | skip
//   stream/think -> inline text (no newline); the caller places newlines.
//   skip         -> caller handles it (citations + lifecycle are loop-level).
export function renderEvent(event) {
  switch (event?.type) {
    case "assistant.delta":
      return { text: event.delta ?? "", tone: "stream" };
    case "assistant.thinking":
      return { text: event.delta ?? "", tone: "think" };
    case "tool.start":
      return { text: `🔧 ${event.toolName}`, tone: "info" };
    case "tool.result":
      return event.isError
        ? { text: `  ⚠ ${event.toolName} failed`, tone: "error" }
        : { text: `  ✓ ${event.toolName}`, tone: "ok" };
    case "limit.reached":
      return {
        text: `⚠ ${event.limit} limit reached (max ${event.maximum})`,
        tone: "warn",
      };
    case "message.failed":
      return {
        text: `✗ ${event.message ?? event.error ?? "run failed"}`,
        tone: "error",
      };
    default:
      // message.start, citation, message.completed are handled by the loop.
      return { text: "", tone: "skip" };
  }
}

// --- interactive client --------------------------------------------------
const USE_COLOR = process.stdout.isTTY ?? false;
const paint = (code, s) => (USE_COLOR ? `\x1b[${code}m${s}\x1b[0m` : s);
const TONE_STYLE = {
  info: (s) => paint("36", s), // cyan
  ok: (s) => paint("32", s), // green
  warn: (s) => paint("33", s), // yellow
  error: (s) => paint("31", s), // red
  think: (s) => paint("2", s), // dim
};

async function createSession(baseUrl) {
  const res = await fetch(`${baseUrl}/v1/sessions`, { method: "POST" });
  if (!res.ok) {
    throw new Error(`create session failed: ${res.status} ${await res.text()}`);
  }
  return (await res.json()).id;
}

async function sendMessage(baseUrl, sessionId, message) {
  const res = await fetch(`${baseUrl}/v1/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!res.ok || !res.body) {
    const detail = await res.text().catch(() => "");
    throw new Error(`message failed: ${res.status} ${detail}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let citations = [];
  let streaming = false;
  const endStream = () => {
    if (streaming) {
      process.stdout.write("\n");
      streaming = false;
    }
  };

  const printSources = () => {
    if (!citations.length) return;
    process.stdout.write(paint("2", `sources (${citations.length}):`) + "\n");
    citations.forEach((ci, i) => {
      process.stdout.write(`  [${i + 1}] ${ci.title ?? ci.docId}\n`);
    });
    citations = [];
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { frames, rest } = parseSseFrames(buffer);
    buffer = rest;
    for (const frame of frames) {
      const event = frame.data ?? { type: frame.type };
      if (frame.type === "citation" && event) {
        citations.push(event);
        continue;
      }
      if (frame.type === "message.completed") {
        endStream();
        printSources();
        continue;
      }
      const { text, tone } = renderEvent(event);
      if (tone === "stream" || tone === "think") {
        process.stdout.write(text);
        streaming = true;
        continue;
      }
      if (tone === "skip") continue;
      endStream();
      process.stdout.write(`${(TONE_STYLE[tone] ?? String)(text)}\n`);
    }
  }
  decoder.decode(); // flush decoder
  endStream();
  printSources();
}

async function main() {
  const baseUrl = (process.argv[2] || DEFAULT_URL).replace(/\/+$/, "");
  const sessionId = await createSession(baseUrl);
  process.stdout.write(
    `Connected to ${baseUrl}\nsession ${sessionId}\nType /quit (or Ctrl-D) to exit.\n\n`,
  );

  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });
  while (true) {
    let line;
    try {
      line = await rl.question(paint("1", "you> "));
    } catch {
      break; // Ctrl-D / closed stdin
    }
    const input = line.trim();
    if (input === "/quit" || input === "/exit") break;
    if (input === "") continue;
    try {
      process.stdout.write(paint("2", "assistant> "));
      await sendMessage(baseUrl, sessionId, input);
      process.stdout.write("\n");
    } catch (err) {
      process.stdout.write("\n");
      process.stderr.write(paint("31", `✗ ${err.message}\n`));
    }
  }
  rl.close();
}

const invokedDirectly =
  import.meta.url === pathToFileURL(process.argv[1] ?? "").href;
if (invokedDirectly) {
  main().catch((err) => {
    process.stderr.write(`${err.stack ?? String(err)}\n`);
    process.exitCode = 1;
  });
}
