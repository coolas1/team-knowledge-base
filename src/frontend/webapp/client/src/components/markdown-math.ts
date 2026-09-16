/**
 * remark-math understands dollar delimiters, while uploaded Markdown often
 * uses LaTeX's \[...\] and \(...\) forms. Normalize those forms outside
 * fenced or inline code so formulas render without changing code samples.
 */
export function normalizeLatexDelimiters(source: string): string {
  let fence: { marker: '`' | '~'; length: number } | null = null
  let inlineCodeLength: number | null = null
  return source
    .split('\n')
    .map((line) => {
      const fenceMatch = line.match(/^\s*(`{3,}|~{3,})/)
      if (fenceMatch) {
        const marker = fenceMatch[1][0] as '`' | '~'
        const length = fenceMatch[1].length
        if (fence?.marker === marker && length >= fence.length) fence = null
        else if (fence === null && inlineCodeLength === null) fence = { marker, length }
        return line
      }
      if (fence) return line

      let output = ''
      for (let index = 0; index < line.length;) {
        if (line[index] === '`') {
          let end = index + 1
          while (line[end] === '`') end++
          const length = end - index
          const escaped = index > 0 && line[index - 1] === '\\'
          output += line.slice(index, end)
          if (!escaped) {
            if (inlineCodeLength === null) inlineCodeLength = length
            else if (inlineCodeLength === length) inlineCodeLength = null
          }
          index = end
          continue
        }
        if (inlineCodeLength === null && line[index] === '\\') {
          const delimiter = line[index + 1]
          if (delimiter === '[' || delimiter === ']') {
            output += '$$'
            index += 2
            continue
          }
          if (delimiter === '(' || delimiter === ')') {
            output += '$'
            index += 2
            continue
          }
        }
        output += line[index]
        index++
      }
      return output
    })
    .join('\n')
}
