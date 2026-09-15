import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Archive as ArchiveIcon,
  Check,
  ChevronDown,
  ChevronRight,
  FileText,
  Folder,
  RotateCcw,
  X,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import {
  api,
  type ArchiveJobItem,
  type ArchiveMode,
  type ArchiveOperationItem,
  type ArchivePolicy,
  type ArchiveTreeItem,
  type ArchiveTreeDocument,
  type LegacyArchiveCandidate,
  type LegacyArchivePlan,
} from '../api/client'
import { StatusBadge } from '../components/StatusBadge'
import './ArchivePage.css'

/** 归档分流原因的可读标签。旧标签保留用于展示历史记录。 */
export const ROUTING_REASON_LABELS: Record<string, string> = {
  low_confidence: '置信度较低，请确认推荐目录',
  within_confidence_band: '置信度在审核带内',
  proposed_new_directory: 'AI 提议新建目录',
  review_all_mode: '全量审核模式',
  below_confidence_band: '置信度过低，未自动归档',
  high_confidence: '高置信度自动执行',
  rejected: '已拒绝，保留在收件箱',
  deferred: '暂不归档，保留在收件箱',
  new_directory_requires_confirmation: '当前规则要求确认新目录',
  undone: '已撤销，保留在收件箱',
}

export const DECISION_SOURCE_LABELS: Record<string, string> = {
  auto: '自动',
  review: '人审',
  manual: '手动',
  migration: '存量迁移',
}

export function formatConfidence(confidence: number | null | undefined): string {
  if (confidence == null) return '—'
  return `${Math.round(confidence * 100)}%`
}

/** 需要人注意的原因。 */
export function isWarningReason(reason: string | null): boolean {
  return (
    reason === 'low_confidence' ||
    reason === 'below_confidence_band' ||
    reason === 'proposed_new_directory' ||
    reason === 'rejected' ||
    reason === 'deferred' ||
    reason === 'undone'
  )
}

export interface ArchiveTreeNode extends ArchiveTreeItem {
  name: string
  children: ArchiveTreeNode[]
  synthetic: boolean
}

/** 将 BFF 的扁平相对路径转换成可折叠目录树，并补齐缺失的中间目录。 */
export function buildArchiveTree(items: ArchiveTreeItem[]): ArchiveTreeNode[] {
  const roots: ArchiveTreeNode[] = []
  const byPath = new Map<string, ArchiveTreeNode>()

  for (const item of items) {
    const parts = item.candidate_id.split('/').filter(Boolean)
    let siblings = roots
    let currentPath = ''

    parts.forEach((name, index) => {
      currentPath = currentPath ? `${currentPath}/${name}` : name
      let node = byPath.get(currentPath)
      if (!node) {
        node = {
          candidate_id: currentPath,
          name,
          description: '',
          doc_count: 0,
          documents: [],
          children: [],
          synthetic: true,
        }
        byPath.set(currentPath, node)
        siblings.push(node)
      }
      if (index === parts.length - 1) {
        node.description = item.description
        node.doc_count = item.doc_count
        node.documents = item.documents ?? []
        node.synthetic = false
      }
      siblings = node.children
    })
  }

  const sortNodes = (nodes: ArchiveTreeNode[]) => {
    nodes.sort((left, right) => left.name.localeCompare(right.name, 'zh-CN'))
    nodes.forEach((node) => sortNodes(node.children))
  }
  sortNodes(roots)
  return roots
}

type Tab = 'reviews' | 'skipped' | 'history' | 'tree' | 'management'

const TABS: Array<{ key: Tab; label: string }> = [
  { key: 'reviews', label: '待确认' },
  { key: 'skipped', label: '未归档' },
  { key: 'history', label: '历史' },
  { key: 'tree', label: '目录树' },
  { key: 'management', label: '规则与存量' },
]

export function ArchivePage() {
  const [tab, setTab] = useState<Tab>('reviews')
  const [reviews, setReviews] = useState<ArchiveJobItem[]>([])
  const [skipped, setSkipped] = useState<ArchiveJobItem[]>([])
  const [operations, setOperations] = useState<ArchiveOperationItem[]>([])
  const [tree, setTree] = useState<ArchiveTreeItem[]>([])
  const [expandedTreeNodes, setExpandedTreeNodes] = useState<Set<string>>(new Set())
  const initializedTreeExpansion = useRef(false)
  const [mode, setMode] = useState<ArchiveMode>({ review_all: false })
  const [policy, setPolicy] = useState<ArchivePolicy | null>(null)
  const [policyInstructions, setPolicyInstructions] = useState('')
  const [legacy, setLegacy] = useState<LegacyArchiveCandidate[]>([])
  const [legacySelected, setLegacySelected] = useState<Set<string>>(new Set())
  const [legacyPlan, setLegacyPlan] = useState<LegacyArchivePlan | null>(null)
  const [legacyOverrides, setLegacyOverrides] = useState<
    Record<string, { directory?: string; new_name?: string }>
  >({})
  const [busyId, setBusyId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reassignDirs, setReassignDirs] = useState<Record<string, string>>({})

  const refresh = useCallback(async () => {
    try {
      const [r, s, o, t, m, p, l] = await Promise.all([
        api.listArchiveReviews(),
        api.listArchiveSkipped(),
        api.listArchiveOperations(),
        api.getArchiveTree(),
        api.getArchiveMode(),
        api.getArchivePolicy(),
        api.scanLegacyArchive(),
      ])
      setReviews(r.items)
      setSkipped(s.items)
      setOperations(o.items)
      setTree(t.items)
      setMode(m)
      setPolicy(p)
      setPolicyInstructions((current) => current || p.rules.instructions)
      setLegacy(l.items)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载归档数据失败')
    }
  }, [])

  useEffect(() => {
    void refresh()
    const timer = setInterval(() => void refresh(), 10_000)
    return () => clearInterval(timer)
  }, [refresh])

  const treeNodes = useMemo(() => buildArchiveTree(tree), [tree])

  useEffect(() => {
    if (initializedTreeExpansion.current || treeNodes.length === 0) return
    setExpandedTreeNodes(new Set(treeNodes.map((node) => node.candidate_id)))
    initializedTreeExpansion.current = true
  }, [treeNodes])

  const toggleTreeNode = (candidateId: string) => {
    setExpandedTreeNodes((current) => {
      const next = new Set(current)
      if (next.has(candidateId)) next.delete(candidateId)
      else next.add(candidateId)
      return next
    })
  }

  const act = async (id: string, label: string, fn: () => Promise<unknown>) => {
    setBusyId(id)
    setError(null)
    setNotice(null)
    try {
      await fn()
      setNotice(label)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败')
    } finally {
      setBusyId(null)
    }
  }

  const toggleMode = () =>
    act('mode', mode.review_all ? '已切回自动模式' : '已切换到全量审核模式', () =>
      api.setArchiveMode(!mode.review_all),
    )

  const toggleLegacy = (docId: string) => {
    setLegacySelected((current) => {
      const next = new Set(current)
      if (next.has(docId)) next.delete(docId)
      else next.add(docId)
      return next
    })
  }

  const savePolicy = () =>
    act('policy', '归档规则已保存为新版本', async () => {
      const maxDepth = policy?.rules.max_directory_depth ?? 2
      if (!Number.isInteger(maxDepth) || maxDepth < 1 || maxDepth > 10) {
        throw new Error('新目录最大层级必须是 1 到 10 之间的整数')
      }
      const updated = await api.updateArchivePolicy(policy?.enabled ?? true, {
        ...(policy?.rules ?? {}),
        max_directory_depth: maxDepth,
        strategy: 'project_first',
        instructions: policyInstructions,
      })
      setPolicy(updated)
    })

  const createLegacyPlan = () =>
    act('legacy-plan', '已生成存量归档预览，请确认后执行', async () => {
      const ids = legacySelected.size ? Array.from(legacySelected) : undefined
      setLegacyPlan(await api.planLegacyArchive(ids))
      setLegacyOverrides({})
    })

  const executeLegacyPlan = () => {
    if (!legacyPlan) return
    void act('legacy-execute', '存量归档批次执行完成', async () => {
      await api.executeLegacyArchive(
        legacyPlan.batch_id,
        legacySelected.size ? Array.from(legacySelected) : undefined,
        legacyOverrides,
      )
      setLegacyPlan(null)
      setLegacySelected(new Set())
      setLegacyOverrides({})
    })
  }

  return (
    <div className="archive-page">
      <header className="archive-hero">
        <div className="archive-hero-copy">
          <span className="archive-eyebrow">KNOWLEDGE WORKSPACE</span>
          <h1>自动归档</h1>
          <p>让每份资料都有清晰的归属，也让团队更快找到需要的内容。</p>
        </div>
        <div className="archive-hero-stats" aria-label="归档概览">
          <div className="archive-stat">
            <strong>{reviews.length}</strong>
            <span>待确认</span>
          </div>
          <div className="archive-stat">
            <strong>{tree.length}</strong>
            <span>个目录</span>
          </div>
          <div className="archive-stat">
            <strong>{operations.length}</strong>
            <span>已归档</span>
          </div>
        </div>
      </header>
      <div className="archive-toolbar">
        <div className="archive-tabs" role="tablist" aria-label="归档视图">
          {TABS.map(({ key, label }) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={tab === key}
              className={`archive-tab${tab === key ? ' is-active' : ''}`}
              onClick={() => setTab(key)}
            >
              {label}
              {key === 'reviews' && reviews.length > 0 ? ` (${reviews.length})` : ''}
              {key === 'skipped' && skipped.length > 0 ? ` (${skipped.length})` : ''}
            </button>
          ))}
        </div>
        <div className="archive-mode">
          <ArchiveIcon size={15} aria-hidden="true" />
          <span>模式: {mode.review_all ? '全量审核' : '自动'}</span>
          <button
            type="button"
            className={`archive-mode-toggle${mode.review_all ? ' is-review-all' : ''}`}
            onClick={toggleMode}
            disabled={busyId === 'mode'}
          >
            {mode.review_all ? '切回自动' : '切到全量审核'}
          </button>
        </div>
      </div>

      {notice && (
        <div className="archive-notice" role="status">
          {notice}
        </div>
      )}
      {error && (
        <div className="archive-error" role="alert">
          {error}
        </div>
      )}

      {tab === 'reviews' && (
        <section aria-label="待审队列">
          {reviews.length === 0 && <div className="archive-empty">待审队列为空</div>}
          {reviews.map((job) => (
            <ReviewCard
              key={job.id}
              job={job}
              tree={tree}
              busy={busyId === job.id}
              selectedDir={reassignDirs[job.id] ?? ''}
              onDirChange={(dir) =>
                setReassignDirs((prev) => ({ ...prev, [job.id]: dir }))
              }
              onApprove={() =>
                act(job.id, `已批准 ${job.file_name}`, () =>
                  api.approveArchiveReview(job.id),
                )
              }
              onReject={() =>
                act(job.id, `已暂不归档 ${job.file_name}，文件保留在收件箱`, () =>
                  api.deferArchiveReview(job.id),
                )
              }
              onReassign={() =>
                act(job.id, `已改分类归档 ${job.file_name}`, () =>
                  api.reassignArchiveReview(job.id, reassignDirs[job.id] || ''),
                )
              }
            />
          ))}
        </section>
      )}

      {tab === 'skipped' && (
        <section aria-label="未归档文件">
          {skipped.length === 0 && <div className="archive-empty">没有未归档文件</div>}
          {skipped.map((job) => (
            <ReviewCard
              key={job.id}
              job={job}
              tree={tree}
              busy={busyId === job.id}
              selectedDir={reassignDirs[job.id] ?? ''}
              onDirChange={(dir) =>
                setReassignDirs((prev) => ({ ...prev, [job.id]: dir }))
              }
              onApprove={undefined}
              onReject={undefined}
              onReplan={job.status === 'unarchived' ? () =>
                act(job.id, `已重新规划 ${job.file_name}`, () =>
                  api.replanUnarchived(job.id),
                ) : undefined}
              onReassign={() =>
                act(job.id, `已手动归档 ${job.file_name}`, () =>
                  api.assignArchiveSkipped(job.id, reassignDirs[job.id] || ''),
                )
              }
            />
          ))}
        </section>
      )}

      {tab === 'history' && (
        <section aria-label="历史台账">
          {operations.length === 0 && <div className="archive-empty">暂无归档历史</div>}
          {operations.map((op) => (
            <div key={op.id} className="archive-card">
              <div className="archive-history-row">
                <div>
                  <div className="archive-file-name">
                    {op.destination_path.split('/').pop()}
                  </div>
                  <div className="archive-history-meta">
                    {op.created_at ? new Date(op.created_at).toLocaleString() : ''}
                  </div>
                </div>
                <div>
                  <div className="archive-destination">
                    <code>{op.source_path}</code> → <code>{op.destination_path}</code>
                  </div>
                  <div className="archive-history-meta">
                    来源 {DECISION_SOURCE_LABELS[op.decision_source] ?? op.decision_source}
                    {' · '}置信度 {formatConfidence(op.confidence)}
                    {op.undo_status === 'undone' && ' · 已撤销'}
                    {op.undo_status === 'conflict' && ' · 撤销冲突'}
                    {op.status === 'indexing_failed' && ' · 入库失败'}
                    {op.rationale ? ` · ${op.rationale}` : ''}
                  </div>
                  {op.error_msg && (
                    <div className="archive-history-meta">{op.error_msg}</div>
                  )}
                </div>
                <div className="archive-actions">
                  {op.status === 'indexing_failed' && op.undo_status !== 'undone' && (
                    <button
                      type="button"
                      onClick={() =>
                        act(op.id, `已重试入库 ${op.id}`, () =>
                          api.reindexArchiveOperation(op.id),
                        )
                      }
                      disabled={busyId === op.id}
                    >
                      <RotateCcw size={14} aria-hidden="true" /> 重试入库
                    </button>
                  )}
                  {!op.undo_status && (
                    <button
                      type="button"
                      className="is-danger"
                      onClick={() =>
                        act(op.id, '已撤销，文件回到收件箱', () =>
                          api.undoArchiveOperation(op.id),
                        )
                      }
                      disabled={busyId === op.id}
                    >
                      撤销
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </section>
      )}

      {tab === 'tree' && (
        <section className="archive-tree" aria-label="目录树" role="tree">
          {tree.length === 0 && (
            <div className="archive-empty">归档目录树为空：首批文件归档后自动生成</div>
          )}
          {treeNodes.map((node) => (
            <ArchiveTreeBranch
              key={node.candidate_id}
              node={node}
              depth={0}
              expanded={expandedTreeNodes}
              onToggle={toggleTreeNode}
            />
          ))}
        </section>
      )}

      {tab === 'management' && (
        <section className="archive-management" aria-label="归档规则与存量文件">
          <div className="archive-card">
            <div className="archive-card-head">
              <span className="archive-file-name">默认归档规则</span>
              <span className="archive-history-meta">版本 {policy?.version ?? '—'}</span>
            </div>
            <label className="archive-policy-toggle">
              <input
                type="checkbox"
                checked={policy?.enabled ?? true}
                onChange={(event) =>
                  setPolicy((current) =>
                    current ? { ...current, enabled: event.target.checked } : current,
                  )
                }
              />
              对新文件和存量归档启用此规则
            </label>
            <label className="archive-policy-toggle">
              <input
                type="checkbox"
                checked={policy?.rules.allow_new_directories ?? true}
                onChange={(event) =>
                  setPolicy((current) =>
                    current
                      ? {
                          ...current,
                          rules: {
                            ...current.rules,
                            allow_new_directories: event.target.checked,
                          },
                        }
                      : current,
                  )
                }
              />
              允许高置信度结果自动创建新目录
            </label>
            <label className="archive-policy-toggle">
              新目录最大层级
              <input
                type="number"
                min={1}
                max={10}
                value={policy?.rules.max_directory_depth ?? 2}
                onChange={(event) =>
                  setPolicy((current) =>
                    current
                      ? {
                          ...current,
                          rules: {
                            ...current.rules,
                            max_directory_depth: Number(event.target.value),
                          },
                        }
                      : current,
                  )
                }
              />
            </label>
            <textarea
              className="archive-policy-editor"
              aria-label="归档规则"
              value={policyInstructions}
              onChange={(event) => setPolicyInstructions(event.target.value)}
              rows={4}
            />
            <div className="archive-actions">
              <button
                type="button"
                className="is-primary"
                onClick={savePolicy}
                disabled={busyId === 'policy' || !policyInstructions.trim()}
              >
                保存新版本
              </button>
            </div>
          </div>

          <div className="archive-card">
            <div className="archive-card-head">
              <span className="archive-file-name">已有但未归档的文档</span>
              <span className="archive-history-meta">
                {legacy.length} 个（{legacy.filter((item) => item.file_exists).length} 个可归档）
              </span>
            </div>
            <div className="archive-summary">
              勾选文档后先生成预览；不勾选表示为全部文档生成预览。预览不会移动文件。
            </div>
            <div className="archive-legacy-list">
              {legacy.map((item) => (
                <label key={item.doc_id} className="archive-legacy-item">
                  <input
                    type="checkbox"
                    checked={legacySelected.has(item.doc_id)}
                    onChange={() => toggleLegacy(item.doc_id)}
                    disabled={!item.file_exists}
                  />
                  <span>{item.title}</span>
                  <code>{item.file_path}</code>
                  {!item.file_exists && <span className="archive-reason is-warning">原文件缺失</span>}
                </label>
              ))}
              {legacy.length === 0 && <div className="archive-empty">没有待归档的已有文档</div>}
            </div>
            <div className="archive-actions">
              <button
                type="button"
                onClick={createLegacyPlan}
                disabled={
                  busyId === 'legacy-plan' ||
                  legacy.filter((item) => item.file_exists).length === 0
                }
              >
                生成归档预览
              </button>
            </div>
          </div>

          {legacyPlan && (
            <div className="archive-card">
              <div className="archive-card-head">
                <span className="archive-file-name">批次预览</span>
                <span className="archive-history-meta">规则版本 {legacyPlan.policy_version}</span>
              </div>
              {legacyPlan.items.map((item) => (
                <div key={item.doc_id} className="archive-legacy-plan-item">
                  <strong>{item.title}</strong>
                  {item.error ? (
                    <span className="archive-reason is-warning">{item.error}</span>
                  ) : (
                    <>
                      <code>{item.source_path}</code> → <code>{item.destination_path}</code>
                      <span>置信度 {formatConfidence(item.confidence)}</span>
                      {item.creates_directory && <span>将新建目录</span>}
                      <input
                        aria-label={`修改 ${item.title} 的目标目录`}
                        value={legacyOverrides[item.doc_id]?.directory ?? item.directory ?? ''}
                        onChange={(event) =>
                          setLegacyOverrides((current) => ({
                            ...current,
                            [item.doc_id]: {
                              ...current[item.doc_id],
                              directory: event.target.value,
                            },
                          }))
                        }
                        placeholder="目标目录，例如 项目/知识库"
                      />
                      <input
                        aria-label={`修改 ${item.title} 的文件名`}
                        value={legacyOverrides[item.doc_id]?.new_name ?? item.new_name ?? ''}
                        onChange={(event) =>
                          setLegacyOverrides((current) => ({
                            ...current,
                            [item.doc_id]: {
                              ...current[item.doc_id],
                              new_name: event.target.value,
                            },
                          }))
                        }
                        placeholder="归档后的文件名"
                      />
                    </>
                  )}
                </div>
              ))}
              <div className="archive-actions">
                <button type="button" className="is-primary" onClick={executeLegacyPlan} disabled={busyId === 'legacy-execute'}>
                  确认并执行所选归档
                </button>
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  )
}

interface ArchiveTreeBranchProps {
  node: ArchiveTreeNode
  depth: number
  expanded: Set<string>
  onToggle: (candidateId: string) => void
}

function ArchiveTreeBranch({ node, depth, expanded, onToggle }: ArchiveTreeBranchProps) {
  const hasChildren = node.children.length > 0
  const hasContents = hasChildren || node.documents.length > 0
  const isExpanded = expanded.has(node.candidate_id)
  const totalDocuments = countTreeDocuments(node)
  const rowContent = (
    <>
      <span className="archive-tree-chevron" aria-hidden="true">
        {hasContents ? (
          isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />
        ) : (
          <span className="archive-tree-chevron-placeholder" />
        )}
      </span>
      <Folder size={16} aria-hidden="true" />
      <span className="archive-tree-name">{node.name}</span>
      <span className="archive-tree-count">
        {totalDocuments === 0
          ? '空目录'
          : node.documents.length === totalDocuments
            ? `${totalDocuments} 篇文档`
            : `共 ${totalDocuments} 篇 · 本层 ${node.documents.length} 篇`}
      </span>
    </>
  )

  return (
    <div className="archive-tree-branch" role="treeitem" aria-expanded={hasContents ? isExpanded : undefined}>
      <div className="archive-tree-node" style={{ '--tree-depth': depth } as React.CSSProperties}>
        {hasContents ? (
          <button
            type="button"
            className="archive-tree-node-head"
            aria-label={`${isExpanded ? '收起' : '展开'}目录 ${node.candidate_id}`}
            onClick={() => onToggle(node.candidate_id)}
          >
            {rowContent}
          </button>
        ) : (
          <div className="archive-tree-node-head">{rowContent}</div>
        )}
        {!node.synthetic && node.description && (
          <div className="archive-tree-desc">{node.description}</div>
        )}
      </div>
      {hasContents && isExpanded && (
        <div className="archive-tree-contents" role="group">
          {node.documents.map((document) => (
            <ArchiveDocumentLink
              key={document.id}
              document={document}
              depth={depth + 1}
            />
          ))}
          {node.children.map((child) => (
            <ArchiveTreeBranch
              key={child.candidate_id}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              onToggle={onToggle}
            />
          ))}
        </div>
      )}
    </div>
  )
}

export function countTreeDocuments(node: ArchiveTreeNode): number {
  return (
    node.documents.length +
    node.children.reduce((total, child) => total + countTreeDocuments(child), 0)
  )
}

function ArchiveDocumentLink({
  document,
  depth,
}: {
  document: ArchiveTreeDocument
  depth: number
}) {
  return (
    <Link
      className="archive-tree-document"
      style={{ '--tree-depth': depth } as React.CSSProperties}
      to={`/documents/${encodeURIComponent(document.id)}`}
      aria-label={`打开文档 ${document.title}`}
    >
      <FileText size={17} aria-hidden="true" />
      <span className="archive-tree-document-main">
        <span className="archive-tree-document-title">{document.title}</span>
        <span className="archive-tree-document-meta">{document.file_type}</span>
      </span>
      <StatusBadge status={document.status} />
      <ChevronRight className="archive-tree-document-arrow" size={15} aria-hidden="true" />
    </Link>
  )
}

interface ReviewCardProps {
  job: ArchiveJobItem
  tree: ArchiveTreeItem[]
  busy: boolean
  selectedDir: string
  onDirChange: (dir: string) => void
  onApprove?: () => void
  onReject?: () => void
  onReplan?: () => void
  onReassign: () => void
}

function ReviewCard({
  job,
  tree,
  busy,
  selectedDir,
  onDirChange,
  onApprove,
  onReject,
  onReplan,
  onReassign,
}: ReviewCardProps) {
  const decision = job.plan?.decision
  const destination =
    job.plan?.destination ??
    (decision?.new_subdirectory
      ? `archive/${decision.new_subdirectory}/`
      : decision?.candidate_id
        ? `archive/${decision.candidate_id}/`
        : '')
  const reason = job.routing_reason ?? ''

  return (
    <div className="archive-card">
      <div className="archive-card-head">
        <span className="archive-file-name">{job.file_name}</span>
        <span className="archive-confidence">
          置信度 {formatConfidence(decision?.confidence)}
        </span>
        <span
          className={`archive-reason${isWarningReason(reason) ? ' is-warning' : ''}`}
        >
          {ROUTING_REASON_LABELS[reason] ?? reason}
        </span>
      </div>
      <div className="archive-summary">
        {decision?.rationale || job.error_msg || '（无摘要）'}
      </div>
      <div className="archive-destination">
        建议: <code>{destination}</code>
        {decision?.new_name && decision.new_name !== job.file_name && (
          <> · 重命名为 <code>{decision.new_name}</code></>
        )}
      </div>
      <div className="archive-actions">
        {onApprove && (
          <button type="button" className="is-primary" onClick={onApprove} disabled={busy}>
            <Check size={14} aria-hidden="true" /> 批准
          </button>
        )}
        {onReject && (
          <button type="button" className="is-danger" onClick={onReject} disabled={busy}>
            <X size={14} aria-hidden="true" /> 暂不归档
          </button>
        )}
        {onReplan && (
          <button type="button" onClick={onReplan} disabled={busy}>
            <RotateCcw size={14} aria-hidden="true" /> 使用当前规则重新规划
          </button>
        )}
        <select
          aria-label={`为 ${job.file_name} 选择目录`}
          value={selectedDir}
          onChange={(e) => onDirChange(e.target.value)}
        >
          <option value="">选择目录…</option>
          {tree.map((node) => (
            <option key={node.candidate_id} value={node.candidate_id}>
              {node.candidate_id}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={onReassign}
          disabled={busy || !selectedDir}
          title={selectedDir ? '' : '请先选择目录'}
        >
          {onApprove ? '改分类归档' : '手动归档'}
        </button>
      </div>
    </div>
  )
}
