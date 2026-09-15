import MDEditor from '@uiw/react-md-editor'
import rehypeKatex from 'rehype-katex'
import remarkMath from 'remark-math'
import 'katex/dist/katex.min.css'

import { normalizeLatexDelimiters } from './markdown-math'

export function MarkdownContent({ source }: { source: string }) {
  return (
    <MDEditor.Markdown
      source={normalizeLatexDelimiters(source)}
      remarkPlugins={[[remarkMath, { singleDollarTextMath: false }]]}
      rehypePlugins={[rehypeKatex]}
    />
  )
}
