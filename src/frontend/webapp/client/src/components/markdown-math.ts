/**
 * remark-math understands dollar delimiters, while uploaded Markdown often
 * uses LaTeX's \[...\] and \(...\) forms. Normalize those forms outside
 * fenced code blocks so formulas render without changing code samples.
 */
export function normalizeLatexDelimiters(source: string): string {
  let fence: '`' | '~' | null = null
  return source
    .split('\n')
    .map((line) => {
      const fenceMatch = line.match(/^\s*(`{3,}|~{3,})/)
      if (fenceMatch) {
        const marker = fenceMatch[1][0] as '`' | '~'
        if (fence === marker) fence = null
        else if (fence === null) fence = marker
        return line
      }
      if (fence) return line
      return line
        .replace(/\\\[/g, () => '$$')
        .replace(/\\\]/g, () => '$$')
        .replace(/\\\(/g, () => '$')
        .replace(/\\\)/g, () => '$')
    })
    .join('\n')
}
