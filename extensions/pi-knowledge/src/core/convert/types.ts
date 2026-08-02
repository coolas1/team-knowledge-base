/** 格式转换器公共类型 */

export interface ShadowResult {
  /** 影子 md 正文（不含 frontmatter，由摄取管线统一添加） */
  markdown: string;
  /** 从内容推断的标题（推断不出则为空，由调用方回退到文件名） */
  title: string;
  /** 图片类源文件的原件相对路径（chunk 回指原图用） */
  assetPath?: string;
}
