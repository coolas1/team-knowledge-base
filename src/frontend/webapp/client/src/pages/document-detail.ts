import type { Document } from '../api/client'

const FALLBACK_FAILURE = '处理任务未完成，服务未返回具体原因。'

export type DetailView = 'loading' | 'error' | 'document'

/** 加载失败时必须进入错误态，而不是永远停在"加载中..."。 */
export function detailView(doc: Document | null, loadError: string): DetailView {
  if (doc) return 'document'
  return loadError ? 'error' : 'loading'
}

export function loadFailureMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : '未知错误'
  return `加载失败：${message}`
}

/** 只有处理中的文档才持续轮询；终态（indexed/failed）停止。 */
export function shouldPoll(status: string | undefined | null): boolean {
  return status === 'pending' || status === 'processing'
}

/** 失败面板是否可见（版本化编辑的后台重索引死亡也落到这里）。 */
export function showsFailurePanel(status: string | undefined | null): boolean {
  return status === 'failed'
}

/**
 * 同一时刻只显示最新一条失败信息：重试错误优先于文档上存储的
 * error_msg，避免两条错误叠加。
 */
export function latestFailureMessage(
  retryError: string,
  storedError: string | null | undefined,
): string {
  return retryError || storedError || FALLBACK_FAILURE
}

/** 开始重试：清掉已展示的旧错误并回到处理中状态（等待轮询新结果）。 */
export function clearedForRetry(doc: Document): Document {
  return { ...doc, status: 'pending', error_msg: undefined }
}
