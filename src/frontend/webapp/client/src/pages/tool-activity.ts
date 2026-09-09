import type { PiAgentEvent } from '../api/client'

export interface ActivityRecord { key: string; label: string; failed: boolean }
const labels: Record<string, string> = { execute: '执行程序', discover: '查找可复用工具', test: '测试工具', save: '工具已保存', reuse: '复用工具', retire: '工具已停用', failure: '工具失败', repair: '修复程序' }
export function appendActivity(current: ActivityRecord[], event: PiAgentEvent): ActivityRecord[] {
  if (event.type !== 'tool.start' && event.type !== 'tool.result') return current
  if (!event.activity) return current
  const key = `${event.toolCallId}:${event.type}`
  if (current.some(item => item.key === key)) return current
  let label = labels[event.activity] ?? '工具活动'
  if (event.type === 'tool.start' && event.activity === 'execute' && current.some(item => item.failed)) label = labels.repair
  if (event.artifactId) label += ` · ${event.artifactId}${event.version ? ` v${event.version}` : ''}`
  return [...current, { key, label, failed: event.type === 'tool.result' && event.isError }]
}
