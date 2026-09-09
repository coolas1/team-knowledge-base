import { useEffect, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity, AlertCircle, BrainCircuit, CheckCircle2, Clock3, Database,
  FileText, LoaderCircle, Plus, RefreshCw, RotateCcw, Save, Settings2,
  ShieldCheck, Sparkles, Trash2, XCircle,
} from 'lucide-react'
import {
  api, MemoryDirective, MemoryFact, MemoryOperation, MentalModelRecord,
} from '../api/client'
import {
  memorySourceHref, operationActions, operationStatusLabel, operationSubject,
  parsePolicyText, validateDirectiveFields, validateModelFields,
} from './memory-page'
import './MemoryPage.css'

type MemorySection = 'operations' | 'facts' | 'models' | 'directives' | 'policy'

const sections: Array<{ id: MemorySection; label: string; description: string; icon: typeof Activity }> = [
  { id: 'operations', label: '任务诊断', description: '查看后台处理状态', icon: Activity },
  { id: 'facts', label: '事实与证据', description: '检查可召回记忆', icon: Database },
  { id: 'models', label: 'Mental models', description: '维护长期综合认识', icon: BrainCircuit },
  { id: 'directives', label: '可信指令', description: '管理高优先级规则', icon: ShieldCheck },
  { id: 'policy', label: '范围策略', description: '配置记忆可见范围', icon: Settings2 },
]

const sectionCopy: Record<MemorySection, { title: string; subtitle: string }> = {
  operations: { title: '任务诊断', subtitle: '追踪记忆保存、归纳和模型刷新的每个阶段' },
  facts: { title: '事实与证据', subtitle: '查看当前可被检索和引用的长期记忆' },
  models: { title: 'Mental models', subtitle: '将分散事实持续整理成稳定的项目认识' },
  directives: { title: '可信指令', subtitle: '为记忆推理提供明确、可控的行为约束' },
  policy: { title: '范围策略', subtitle: '控制不同来源和标签下的记忆读写边界' },
}

function statusClass(status: string) {
  if (status === 'completed' || status === 'indexed' || status === 'active' || status === 'success') return 'is-success'
  if (status === 'processing' || status === 'pending' || status === 'queued') return 'is-running'
  if (status === 'failed' || status === 'degraded' || status === 'budget_exhausted') return 'is-danger'
  if (status === 'cancelled' || status === 'stale' || status === 'disabled') return 'is-muted'
  return 'is-neutral'
}

function StatusIcon({ status }: { status: string }) {
  if (['completed', 'indexed', 'active', 'success'].includes(status)) return <CheckCircle2 size={14} />
  if (status === 'processing') return <LoaderCircle className="memory-spin" size={14} />
  if (status === 'pending' || status === 'queued') return <Clock3 size={14} />
  if (status === 'cancelled') return <XCircle size={14} />
  if (['failed', 'degraded', 'budget_exhausted'].includes(status)) return <AlertCircle size={14} />
  return <Activity size={14} />
}

function EmptyState({ icon, title, detail }: { icon: ReactNode; title: string; detail: string }) {
  return <div className="memory-empty"><span className="memory-empty-icon">{icon}</span><strong>{title}</strong><p>{detail}</p></div>
}

function formatDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(date)
}

export function MemoryPage() {
  const [activeSection, setActiveSection] = useState<MemorySection>('operations')
  const [operations, setOperations] = useState<MemoryOperation[]>([])
  const [facts, setFacts] = useState<MemoryFact[]>([])
  const [models, setModels] = useState<MentalModelRecord[]>([])
  const [directives, setDirectives] = useState<MemoryDirective[]>([])
  const [policy, setPolicy] = useState<{ version: number; policy: Record<string, unknown> }>()
  const [policyText, setPolicyText] = useState('')
  const [selected, setSelected] = useState<unknown>()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState('')

  const load = async (showLoading = false) => {
    if (showLoading) setLoading(true)
    try {
      const [nextOperations, nextFacts, nextModels, nextDirectives, nextPolicy] = await Promise.all([
        api.listMemoryOperations(), api.listMemoryFacts(), api.listMentalModels(),
        api.listMemoryDirectives(), api.getMemoryPolicy(),
      ])
      setOperations(nextOperations)
      setFacts(nextFacts)
      setModels(nextModels)
      setDirectives(nextDirectives)
      setPolicy(nextPolicy)
      setPolicyText(JSON.stringify(nextPolicy.policy, null, 2))
      setError('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '记忆管理数据加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])

  const runAction = async (key: string, action: () => Promise<unknown>) => {
    setWorking(key)
    setError('')
    try {
      await action()
      await load()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '操作未完成')
    } finally {
      setWorking('')
    }
  }

  const createModel = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = event.currentTarget
    const data = new FormData(form)
    const id = String(data.get('id') || '').trim()
    const name = String(data.get('name') || '').trim()
    const sourceQuery = String(data.get('source_query') || '').trim()
    const validationError = validateModelFields(id, name, sourceQuery)
    if (validationError) return setError(validationError)
    await runAction('create-model', async () => {
      await api.saveMentalModel(id, { name, source_query: sourceQuery, description: '', tags: [], refresh_mode: 'full', refresh_after_consolidation: true })
      form.reset()
    })
  }

  const createDirective = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = event.currentTarget
    const data = new FormData(form)
    const id = String(data.get('id') || '').trim()
    const name = String(data.get('name') || '').trim()
    const content = String(data.get('content') || '').trim()
    const validationError = validateDirectiveFields(id, name, content)
    if (validationError) return setError(validationError)
    await runAction('create-directive', async () => {
      await api.saveMemoryDirective(id, {
        name, content, trigger: String(data.get('trigger') || '').trim() || null,
        priority: 0, is_active: true, tags: [],
      })
      form.reset()
    })
  }

  const savePolicy = async () => {
    if (!policy) return
    await runAction('save-policy', async () => {
      await api.updateMemoryPolicy(policy.version, parsePolicyText(policyText))
    })
  }

  const activeTasks = operations.filter((item) => ['pending', 'processing'].includes(item.status)).length
  const currentCopy = sectionCopy[activeSection]

  return (
    <div className="memory-shell">
      <aside className="memory-sidebar">
        <div className="memory-sidebar-heading">
          <span className="memory-sidebar-mark"><BrainCircuit size={20} /></span>
          <div><strong>记忆中心</strong><span>诊断与管理</span></div>
        </div>
        <div className="memory-summary">
          <div><strong>{facts.length}</strong><span>记忆事实</span></div>
          <div><strong>{activeTasks}</strong><span>进行中</span></div>
        </div>
        <nav className="memory-navigation" aria-label="记忆管理导航">
          {sections.map((section) => {
            const Icon = section.icon
            return (
              <button key={section.id} type="button" className={activeSection === section.id ? 'is-active' : ''} onClick={() => setActiveSection(section.id)}>
                <Icon size={18} aria-hidden="true" />
                <span><strong>{section.label}</strong><small>{section.description}</small></span>
              </button>
            )
          })}
        </nav>
        <div className="memory-sidebar-note"><Sparkles size={16} /><span>事实保存后会在后台逐步形成 observation 和 Mental model。</span></div>
      </aside>

      <main className="memory-main">
        <header className="memory-header">
          <div><h1>{currentCopy.title}</h1><p>{currentCopy.subtitle}</p></div>
          <button type="button" className="memory-refresh-button" onClick={() => void load(true)} disabled={loading}>
            <RefreshCw className={loading ? 'memory-spin' : ''} size={17} /><span>刷新</span>
          </button>
        </header>

        <div className="memory-content">
          {error && <div className="memory-alert" role="alert"><AlertCircle size={18} /><span>{error}</span></div>}
          {loading ? (
            <div className="memory-loading"><LoaderCircle className="memory-spin" size={20} />正在加载记忆数据</div>
          ) : (
            <>
              {activeSection === 'operations' && (
                <section className="memory-workspace" aria-label="任务诊断">
                  <div className="memory-section-toolbar">
                    <div><strong>{operations.length} 个任务</strong><span>{activeTasks ? `${activeTasks} 个仍在执行` : '当前没有运行中的任务'}</span></div>
                    <div className={`memory-health${activeTasks ? ' is-busy' : ''}`}><span />{activeTasks ? '后台处理中' : '队列空闲'}</div>
                  </div>
                  <div className="memory-operation-list">
                    {operations.map((operation) => {
                      const actions = operationActions(operation.status)
                      return (
                        <article className="memory-operation-card" key={operation.id}>
                          <div className="memory-operation-topline">
                            <span className={`memory-status ${statusClass(operation.status)}`}><StatusIcon status={operation.status} />{operationStatusLabel(operation.status)}</span>
                            <span className="memory-operation-kind">{operation.kind.replace('_', ' ')}</span>
                          </div>
                          <div className="memory-operation-body">
                            <div className="memory-operation-copy">
                              <h2>{operation.kind === 'document' && operation.document_id
                                ? <Link to={memorySourceHref(operation.document_id)}>{operationSubject(operation)}</Link>
                                : operationSubject(operation)}</h2>
                              <div className="memory-stage-list">
                                {Object.entries(operation.stages).map(([stage, status]) => (
                                  <span key={stage} className={statusClass(status)}><b>{stage}</b>{operationStatusLabel(status)}</span>
                                ))}
                                {Object.keys(operation.stages).length === 0 && <span className="is-neutral">暂无阶段信息</span>}
                              </div>
                              {operation.error && <p className="memory-operation-error"><AlertCircle size={14} />{operation.error}</p>}
                            </div>
                            {(actions.retry || actions.cancel) && (
                              <div className="memory-operation-actions">
                                {actions.retry && <button type="button" onClick={() => void runAction(`retry-${operation.id}`, () => api.retryMemoryOperation(operation.id))} disabled={Boolean(working)}><RotateCcw size={15} />重试</button>}
                                {actions.cancel && <button type="button" className="is-danger" onClick={() => void runAction(`cancel-${operation.id}`, () => api.cancelMemoryOperation(operation.id))} disabled={Boolean(working)}><XCircle size={15} />取消</button>}
                              </div>
                            )}
                          </div>
                          <footer>
                            <span>尝试 {operation.attempts} 次</span>
                            {operation.tokens > 0 && <span>{operation.tokens.toLocaleString()} tokens</span>}
                            {operation.duration_ms != null && <span>耗时 {Math.max(1, Math.round(operation.duration_ms / 1000))} 秒</span>}
                          </footer>
                        </article>
                      )
                    })}
                    {operations.length === 0 && <EmptyState icon={<Activity size={24} />} title="暂无任务记录" detail="上传文件或开始对话后，处理阶段会显示在这里。" />}
                  </div>
                </section>
              )}

              {activeSection === 'facts' && (
                <section className="memory-workspace" aria-label="事实与证据">
                  <div className="memory-section-toolbar"><div><strong>{facts.length} 条记忆</strong><span>点击 observation 可查看证据链</span></div></div>
                  <div className="memory-fact-list">
                    {facts.map((fact) => (
                      <article className="memory-fact-card" key={fact.id}>
                        <button className="memory-fact-open" type="button" onClick={() => fact.type === 'observation' && void runAction(`fact-${fact.id}`, async () => setSelected(await api.getObservation(fact.id)))} disabled={fact.type !== 'observation'}>
                          <span className="memory-fact-head"><span className={`memory-status ${statusClass(fact.freshness)}`}>{fact.type}</span><small>{formatDate(fact.mentioned_at)}</small></span>
                          <span className="memory-fact-text">{fact.text}</span>
                        </button>
                        <div className="memory-fact-source"><FileText size={14} />来源：<Link to={memorySourceHref(fact.document_id)}>{fact.document_title}</Link></div>
                      </article>
                    ))}
                    {facts.length === 0 && <EmptyState icon={<Database size={24} />} title="还没有记忆事实" detail="新对话或新文件完成提取后，可召回事实会出现在这里。" />}
                  </div>
                  {selected != null && <div className="memory-evidence-panel"><div><strong>Observation 证据详情</strong><button type="button" onClick={() => setSelected(undefined)}>关闭</button></div><pre data-testid="observation-detail">{JSON.stringify(selected, null, 2)}</pre></div>}
                </section>
              )}

              {activeSection === 'models' && (
                <section className="memory-workspace memory-two-column" aria-label="Mental models">
                  <div className="memory-card-column">
                    {models.map((model) => (
                      <article className="memory-model-card" key={model.id}>
                        <div className="memory-model-heading"><span className="memory-model-icon"><BrainCircuit size={18} /></span><div><h2>{model.name}</h2><span>v{model.version} · {model.refresh_mode}</span></div><span className={`memory-status ${statusClass(model.freshness)}`}>{model.freshness}</span></div>
                        <p>{model.summary || model.source_query}</p>
                        <footer><span>{model.source_memory_ids.length} 条证据</span>{model.error_msg && <span className="is-error">{model.error_msg}</span>}<button type="button" onClick={() => void runAction(`model-${model.id}`, () => api.refreshMentalModel(model.id))} disabled={Boolean(working)}><RefreshCw size={14} />刷新</button></footer>
                      </article>
                    ))}
                    {models.length === 0 && <EmptyState icon={<BrainCircuit size={24} />} title="还没有 Mental model" detail="创建一个常用问题，系统会持续用相关记忆更新综合认识。" />}
                  </div>
                  <form className="memory-form-card" onSubmit={(event) => void createModel(event)}>
                    <div className="memory-form-heading"><Plus size={18} /><div><h2>创建模型</h2><p>定义一个需要持续更新的长期认识</p></div></div>
                    <label>模型 ID<input required name="id" placeholder="例如 project-overview" /></label>
                    <label>显示名称<input required name="name" placeholder="例如 项目概况" /></label>
                    <label>来源问题<textarea required name="source_query" rows={4} placeholder="例如：这个项目当前的目标、架构和主要风险是什么？" /></label>
                    <button className="memory-primary-button" type="submit" disabled={Boolean(working)}>{working === 'create-model' ? <LoaderCircle className="memory-spin" size={16} /> : <Plus size={16} />}创建模型</button>
                  </form>
                </section>
              )}

              {activeSection === 'directives' && (
                <section className="memory-workspace memory-two-column" aria-label="可信指令">
                  <div className="memory-card-column">
                    {directives.map((directive) => (
                      <article className="memory-directive-card" key={directive.id}>
                        <div className="memory-directive-heading"><span className="memory-model-icon"><ShieldCheck size={18} /></span><div><h2>{directive.name}</h2><span>{directive.trigger ? `触发词：${directive.trigger}` : '始终可用'}</span></div><span className={`memory-status ${directive.is_active ? 'is-success' : 'is-muted'}`}>{directive.is_active ? '启用' : '停用'}</span></div>
                        <p>{directive.content}</p>
                        <footer><span>ID · {directive.id}</span><button type="button" className="is-danger" onClick={() => void runAction(`directive-${directive.id}`, () => api.deleteMemoryDirective(directive.id))} disabled={Boolean(working)}><Trash2 size={14} />删除</button></footer>
                      </article>
                    ))}
                    {directives.length === 0 && <EmptyState icon={<ShieldCheck size={24} />} title="还没有可信指令" detail="可信指令会在符合触发条件时参与记忆推理。" />}
                  </div>
                  <form className="memory-form-card" onSubmit={(event) => void createDirective(event)}>
                    <div className="memory-form-heading"><Plus size={18} /><div><h2>创建指令</h2><p>添加需要优先遵守的明确规则</p></div></div>
                    <label>指令 ID<input required name="id" placeholder="例如 source-citation" /></label>
                    <label>显示名称<input required name="name" placeholder="例如 引用来源" /></label>
                    <label>指令内容<textarea required name="content" rows={5} placeholder="描述需要遵守的规则和适用条件" /></label>
                    <label>触发词（可选）<input name="trigger" placeholder="留空表示不限定触发词" /></label>
                    <button className="memory-primary-button" type="submit" disabled={Boolean(working)}>{working === 'create-directive' ? <LoaderCircle className="memory-spin" size={16} /> : <Plus size={16} />}创建指令</button>
                  </form>
                </section>
              )}

              {activeSection === 'policy' && (
                <section className="memory-workspace" aria-label="范围策略">
                  <div className="memory-policy-card">
                    <div className="memory-policy-heading"><span className="memory-model-icon"><Settings2 size={18} /></span><div><h2>范围策略 {policy ? `v${policy.version}` : ''}</h2><p>使用 JSON 配置记忆的读取、写入和功能范围</p></div></div>
                    <textarea aria-label="范围策略 JSON" value={policyText} onChange={(event) => setPolicyText(event.target.value)} rows={18} spellCheck={false} />
                    <div className="memory-policy-footer"><span>保存时会校验 JSON 对象和版本号</span><button className="memory-primary-button" type="button" onClick={() => void savePolicy()} disabled={Boolean(working) || !policy}>{working === 'save-policy' ? <LoaderCircle className="memory-spin" size={16} /> : <Save size={16} />}保存策略</button></div>
                  </div>
                </section>
              )}
            </>
          )}
        </div>
      </main>
    </div>
  )
}
