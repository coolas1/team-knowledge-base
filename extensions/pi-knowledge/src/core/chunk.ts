/**
 * Markdown 感知 chunking：
 * 1. 按标题行切分章节，chunk 记录所属标题（检索时提供上下文）
 * 2. 超长章节按段落（空行分隔，代码围栏内不切）贪心聚合
 * 3. 单段超硬上限时递归降级切分（句子→分句→字符）
 * 4. 相邻 chunk 间有 overlap，防止边界信息丢失
 */

export interface MarkdownChunk {
  seq: number;
  heading: string;
  content: string;
}

/** 目标块大小（字符）；中文场景约等于 400-500 token */
const TARGET_CHARS = 800;
/** 硬上限，超过必须切 */
const MAX_CHARS = 1600;
/** 小于此值的尾块并入前一块 */
const MIN_CHARS = 100;
/** 相邻 chunk 重叠字符数 */
const OVERLAP_CHARS = 150;

interface Section {
  heading: string;
  lines: string[];
}

function splitSections(markdown: string): Section[] {
  const sections: Section[] = [{ heading: "", lines: [] }];
  let inFence = false;
  for (const line of markdown.split("\n")) {
    if (/^\s*(```|~~~)/.test(line)) inFence = !inFence;
    const headingMatch = !inFence && line.match(/^#{1,6}\s+(.+)$/);
    if (headingMatch) {
      sections.push({ heading: headingMatch[1].trim(), lines: [line] });
    } else {
      sections[sections.length - 1].lines.push(line);
    }
  }
  return sections.filter((s) => s.lines.join("\n").trim() !== "");
}

/** 按空行切段落，代码围栏内的空行不算分隔 */
function splitParagraphs(text: string): string[] {
  const paragraphs: string[] = [];
  let current: string[] = [];
  let inFence = false;
  for (const line of text.split("\n")) {
    if (/^\s*(```|~~~)/.test(line)) inFence = !inFence;
    if (!inFence && line.trim() === "") {
      if (current.length > 0) {
        paragraphs.push(current.join("\n"));
        current = [];
      }
    } else {
      current.push(line);
    }
  }
  if (current.length > 0) paragraphs.push(current.join("\n"));
  return paragraphs;
}

/** 递归降级切分：句子边界 → 分句边界 → 空格 → 字符 */
function recursiveSplit(text: string, maxLen: number): string[] {
  if (text.length <= maxLen) return [text];
  // 在 maxLen 附近找最佳切分点
  const searchStart = Math.floor(maxLen * 0.6);
  const searchEnd = maxLen;
  const segment = text.slice(searchStart, searchEnd);

  // 优先级 1：句子结束符（。！？；\n）
  let cut = findLastMatch(segment, /[。！？；\n]/);
  // 优先级 2：分句符（，、：）
  if (cut === -1) cut = findLastMatch(segment, /[，、：,]/);
  // 优先级 3：空格
  if (cut === -1) cut = findLastMatch(segment, /\s/);
  // 优先级 4：硬切
  const cutPos = cut === -1 ? maxLen : searchStart + cut + 1;

  const head = text.slice(0, cutPos);
  const rest = text.slice(cutPos);
  return [head, ...recursiveSplit(rest, maxLen)];
}

function findLastMatch(segment: string, pattern: RegExp): number {
  let last = -1;
  for (let i = 0; i < segment.length; i++) {
    if (pattern.test(segment[i])) last = i;
  }
  return last;
}

export function chunkMarkdown(markdown: string): MarkdownChunk[] {
  const pieces: Array<{ heading: string; content: string }> = [];
  for (const section of splitSections(markdown)) {
    const text = section.lines.join("\n").trim();
    if (text.length <= MAX_CHARS) {
      pieces.push({ heading: section.heading, content: text });
      continue;
    }
    // 段落贪心聚合到 TARGET_CHARS
    let buffer = "";
    const flush = () => {
      if (buffer.trim()) pieces.push({ heading: section.heading, content: buffer.trim() });
      buffer = "";
    };
    for (const paragraph of splitParagraphs(text)) {
      if (paragraph.length > MAX_CHARS) {
        flush();
        for (const part of recursiveSplit(paragraph, MAX_CHARS)) {
          pieces.push({ heading: section.heading, content: part });
        }
        continue;
      }
      if (buffer.length + paragraph.length + 2 > TARGET_CHARS && buffer !== "") flush();
      buffer = buffer === "" ? paragraph : `${buffer}\n\n${paragraph}`;
    }
    flush();
  }
  // 尾部小块并入前块（同标题时）
  const merged: Array<{ heading: string; content: string }> = [];
  for (const piece of pieces) {
    const prev = merged[merged.length - 1];
    if (
      prev &&
      prev.heading === piece.heading &&
      piece.content.length < MIN_CHARS &&
      prev.content.length + piece.content.length <= MAX_CHARS
    ) {
      prev.content = `${prev.content}\n\n${piece.content}`;
    } else {
      merged.push({ ...piece });
    }
  }

  // 添加 overlap：每个 chunk 开头追加前一个 chunk 的尾部文本
  const result: MarkdownChunk[] = [];
  for (let i = 0; i < merged.length; i++) {
    let content = merged[i].content;
    if (i > 0 && merged[i].heading === merged[i - 1].heading) {
      const prevContent = merged[i - 1].content;
      const overlap = prevContent.slice(-OVERLAP_CHARS);
      // 只在 overlap 不为空且当前 chunk 没有已包含它时追加
      if (overlap.length > 20 && !content.startsWith(overlap)) {
        content = `${overlap}…\n\n${content}`;
      }
    }
    result.push({ seq: i, heading: merged[i].heading, content });
  }
  return result;
}
