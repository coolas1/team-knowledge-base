import { useEffect, useState } from 'react'
import type { CSSProperties, FormEvent } from 'react'
import { Link } from 'react-router-dom'
import {
  api,
  MemoryDirective,
  MemoryFact,
  MemoryOperation,
  MentalModelRecord,
} from '../api/client'
import {
  formatOperationStages,
  memorySourceHref,
  operationActions,
  operationStatusLabel,
  operationSubject,
  parsePolicyText,
  validateDirectiveFields,
  validateModelFields,
} from './memory-page'

const panel: CSSProperties = {
  border: '1px solid #e5e7eb',
  borderRadius: 8,
  padding: 16,
  background: '#fff',
}

export function MemoryPage() {
  const [operations, setOperations] = useState<MemoryOperation[]>([])
  const [facts, setFacts] = useState<MemoryFact[]>([])
  const [models, setModels] = useState<MentalModelRecord[]>([])
  const [directives, setDirectives] = useState<MemoryDirective[]>([])
  const [policy, setPolicy] = useState<{ version: number; policy: Record<string, unknown> }>()
  const [policyText, setPolicyText] = useState('')
  const [selected, setSelected] = useState<any>()
  const [error, setError] = useState('')

  const load = async () => {
    try {
      const [nextOperations, nextFacts, nextModels, nextDirectives, nextPolicy] =
        await Promise.all([
          api.listMemoryOperations(),
          api.listMemoryFacts(),
          api.listMentalModels(),
          api.listMemoryDirectives(),
          api.getMemoryPolicy(),
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
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const createModel = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const id = String(data.get('id') || '').trim()
    const name = String(data.get('name') || '').trim()
    const sourceQuery = String(data.get('source_query') || '').trim()
    const validationError = validateModelFields(id, name, sourceQuery)
    if (validationError) return setError(validationError)
    await api.saveMentalModel(id, {
      name,
      source_query: sourceQuery,
      description: '',
      tags: [],
      refresh_mode: 'full',
      refresh_after_consolidation: true,
    })
    event.currentTarget.reset()
    await load()
  }

  const createDirective = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const id = String(data.get('id') || '').trim()
    const name = String(data.get('name') || '').trim()
    const content = String(data.get('content') || '').trim()
    const validationError = validateDirectiveFields(id, name, content)
    if (validationError) return setError(validationError)
    await api.saveMemoryDirective(id, {
      name,
      content,
      trigger: String(data.get('trigger') || '').trim() || null,
      priority: 0,
      is_active: true,
      tags: [],
    })
    event.currentTarget.reset()
    await load()
  }

  const savePolicy = async () => {
    if (!policy) return
    try {
      const parsed = parsePolicyText(policyText)
      await api.updateMemoryPolicy(policy.version, parsed)
      await load()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '策略格式无效')
    }
  }

  return (
    <main style={{ flex: 1, overflow: 'auto', padding: 24 }}>
      <h1 style={{ marginTop: 0 }}>记忆诊断与管理</h1>
      {error && <p role="alert" style={{ color: '#b91c1c' }}>{error}</p>}

      <section style={{ ...panel, marginBottom: 16 }}>
        <h2>任务诊断</h2>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th>状态</th><th>对象</th><th>阶段</th><th>错误</th><th>操作</th></tr></thead>
          <tbody>
            {operations.map((operation) => {
              const actions = operationActions(operation.status)
              return (
                <tr key={operation.id}>
                  <td>{operationStatusLabel(operation.status)}</td>
                  <td>
                    {operation.kind === 'document' && operation.document_id ? (
                      <Link to={memorySourceHref(operation.document_id)}>{operationSubject(operation)}</Link>
                    ) : operationSubject(operation)}
                  </td>
                  <td>{formatOperationStages(operation.stages)}</td>
                  <td>{operation.error || '—'}</td>
                  <td>
                    {actions.retry && <button onClick={() => void api.retryMemoryOperation(operation.id).then(load)}>重试</button>}{' '}
                    {actions.cancel && <button onClick={() => void api.cancelMemoryOperation(operation.id).then(load)}>取消</button>}
                    {!actions.retry && !actions.cancel && '—'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </section>

      <section style={{ ...panel, marginBottom: 16 }}>
        <h2>事实与证据</h2>
        {facts.map((fact) => (
          <article key={fact.id} style={{ marginBottom: 8, padding: 10, border: '1px solid #e5e7eb' }}>
            <button
              style={{ display: 'block', width: '100%', textAlign: 'left' }}
              onClick={() => fact.type === 'observation' && void api.getObservation(fact.id).then(setSelected)}
            >
              <strong>{fact.type} · {fact.freshness}</strong> {fact.text}
            </button>
            <small>
              来源：<Link to={memorySourceHref(fact.document_id)}>{fact.document_title}</Link> · {fact.mentioned_at}
            </small>
          </article>
        ))}
        {selected && <pre data-testid="observation-detail" style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(selected, null, 2)}</pre>}
      </section>

      <section style={{ ...panel, marginBottom: 16 }}>
        <h2>Mental models</h2>
        {models.map((model) => (
          <article key={model.id} style={{ marginBottom: 12 }}>
            <strong>{model.name} · v{model.version} · {model.freshness}</strong>
            <p>{model.summary || model.source_query}</p>
            <small>证据 {model.source_memory_ids.length} 条 {model.error_msg ? `· ${model.error_msg}` : ''}</small>{' '}
            <button onClick={() => void api.refreshMentalModel(model.id).then(load)}>刷新</button>
          </article>
        ))}
        <form onSubmit={(event) => void createModel(event)} style={{ display: 'flex', gap: 8 }}>
          <input required name="id" placeholder="模型 ID" />
          <input required name="name" placeholder="名称" />
          <input required name="source_query" placeholder="来源问题" />
          <button type="submit">创建模型</button>
        </form>
      </section>

      <section style={{ ...panel, marginBottom: 16 }}>
        <h2>可信指令</h2>
        {directives.map((directive) => (
          <article key={directive.id} style={{ marginBottom: 8 }}>
            <strong>{directive.name}</strong> {directive.content}{' '}
            <button onClick={() => void api.deleteMemoryDirective(directive.id).then(load)}>删除</button>
          </article>
        ))}
        <form onSubmit={(event) => void createDirective(event)} style={{ display: 'flex', gap: 8 }}>
          <input required name="id" placeholder="指令 ID" />
          <input required name="name" placeholder="名称" />
          <input required name="content" placeholder="内容" />
          <input name="trigger" placeholder="触发词（可选）" />
          <button type="submit">创建指令</button>
        </form>
      </section>

      <section style={panel}>
        <h2>范围策略 {policy ? `v${policy.version}` : ''}</h2>
        <textarea
          aria-label="范围策略 JSON"
          value={policyText}
          onChange={(event) => setPolicyText(event.target.value)}
          rows={12}
          style={{ width: '100%', fontFamily: 'monospace' }}
        />
        <button onClick={() => void savePolicy()}>保存策略</button>
      </section>
    </main>
  )
}
