import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { pptApi, type DeckDraft, type PPTJob, type SlideDraft } from '../api/ppt'
import './PPTPage.css'

const page = (index: number): SlideDraft => ({ title: '', points: [''], layout: index === 0 ? '封面：大标题与核心视觉' : '根据内容选择时间线、对比、流程或证据布局', notes: '', reference_document_ids: [] })
const initial = (): DeckDraft => ({ title: '', style: '清爽专业：白色背景、深蓝标题、青绿强调色，充足留白，清晰中文排版。每页按内容选择不同布局。', context: '', source_document_ids: [], pages: Array.from({ length: 8 }, (_, i) => page(i)) })
const states: Record<string, string> = { awaiting_outline_approval: '等待大纲确认', awaiting_sample_approval: '样张阶段', queued: '等待生成', generating: '正在生图', reviewing: '正在检查', assembling: '正在组装', completed: '已完成', failed: '需要处理', paused_budget: '预算暂停', cancelled: '已取消', pending: '等待开始', generated: '图片已生成', accepted: '检查通过', unknown: '调用结果未知' }

export function PPTPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [job, setJob] = useState<PPTJob | null>(null)
  const [draft, setDraft] = useState<DeckDraft>(initial)
  const [editing, setEditing] = useState(!id)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [documents, setDocuments] = useState<{ id: string; title: string; file_type: string }[]>([])
  useEffect(() => {
    const controller = new AbortController()
    fetch('/api/documents?page_size=100', { signal: controller.signal }).then(r => r.json()).then(d => setDocuments(d.items ?? [])).catch(() => {})
    return () => controller.abort()
  }, [])
  useEffect(() => {
    setJob(null); setEditing(!id)
    if (!id) { setDraft(initial()); return }
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try { const state = await pptApi.get(id, controller.signal); setJob(state) }
      catch (e) { if (!controller.signal.aborted) setError(String(e)) }
      finally { if (!controller.signal.aborted) timer = setTimeout(poll, 2500) }
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [id])
  const act = async (action: string, extra = {}) => {
    if (!job) return
    setBusy(true); setError('')
    try { setJob(await pptApi.control(job.id, job.revision, action, extra)); setEditing(false) }
    catch (e) { setError(String(e)); try { setJob(await pptApi.get(job.id)) } catch { /* preserve error */ } }
    finally { setBusy(false) }
  }
  const updatePage = (i: number, patch: Partial<SlideDraft>) => setDraft(d => ({ ...d, pages: d.pages.map((p, n) => n === i ? { ...p, ...patch } : p) }))
  return <main className="ppt-page">
    <header><div><p className="ppt-eyebrow">图片式演示文稿</p><h1>{job?.spec.title || '创建视觉 PPT'}</h1><p>先确认大纲，再查看样张。每页是一张生成图片，文字与图表不可逐项编辑。</p></div><Link to="/ask">让 Agent 帮我规划内容 →</Link></header>
    {error && <div className="ppt-error" role="alert">{error}</div>}
    {job && <section className="ppt-summary"><strong>{states[job.status] || job.status}</strong><span>第 {job.revision} 版 · {job.pages.length} 页 · {job.backend.model}</span><span>生图请求 {job.accounting.image_attempts ?? 0} / {job.budget.image_attempts} · 已报告 tokens {job.accounting.tokens ?? '未知'} · 套餐抵扣与金额未知</span></section>}
    {editing ? <form onSubmit={async e => { e.preventDefault(); if (job) { await act('revise', { spec: draft }); return } setBusy(true); setError(''); try { const created = await pptApi.create(draft); navigate(`/ppt/${created.id}`) } catch (err) { setError(String(err)) } finally { setBusy(false) } }}>
      <div className="ppt-editor"><label>演示标题<input required maxLength={200} value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })} /></label><label>统一视觉风格<textarea required value={draft.style} onChange={e => setDraft({ ...draft, style: e.target.value })} /></label><label>受众与背景<textarea value={draft.context} onChange={e => setDraft({ ...draft, context: e.target.value })} /></label>
      {!job && <label>页数<input type="number" min={1} max={20} value={draft.pages.length} onChange={e => { const n = Math.max(1, Math.min(20, Number(e.target.value) || 1)); setDraft({ ...draft, pages: Array.from({ length: n }, (_, i) => draft.pages[i] ?? page(i)) }) }} /></label>}
      <label>知识库来源<select multiple value={draft.source_document_ids} onChange={e => setDraft({ ...draft, source_document_ids: Array.from(e.target.selectedOptions, x => x.value) })}>{documents.map(d => <option key={d.id} value={d.id}>{d.title}</option>)}</select></label></div>
      {draft.pages.map((p, i) => <fieldset key={i}><legend>第 {i + 1} 页</legend><label>标题<input required value={p.title} onChange={e => updatePage(i, { title: e.target.value })} /></label><label>要点（每行一项，最多六项）<textarea required value={p.points.join('\n')} onChange={e => updatePage(i, { points: e.target.value.split('\n') })} /></label><label>页面布局<input required value={p.layout} onChange={e => updatePage(i, { layout: e.target.value })} /></label><label>讲稿备注<textarea required value={p.notes} onChange={e => updatePage(i, { notes: e.target.value })} /></label><label>必须保留的原图<select multiple value={p.reference_document_ids} onChange={e => updatePage(i, { reference_document_ids: Array.from(e.target.selectedOptions, x => x.value) })}>{documents.filter(d => d.file_type === 'image').map(d => <option key={d.id} value={d.id}>{d.title}</option>)}</select></label></fieldset>)}
      <button disabled={busy} type="submit">{job ? '保存修改并重新确认' : '保存大纲，进入确认'}</button>
    </form> : job && <>
      <section className="ppt-review"><h2>大纲与风格</h2><p>{job.spec.style}</p>{job.spec.pages.map((p, i) => <article key={i}><h3>{i + 1}. {p.title}</h3><ul>{p.points.map((point, n) => <li key={n}>{point}</li>)}</ul><p>{p.layout}</p>{!!job.pages[i]?.reference_regions?.length && <><p>原图将按下图区域等比嵌入，不裁切、不重绘；标题在上方，要点在下方。确认大纲即确认这些位置。</p><svg viewBox="0 0 2560 1440" role="img" aria-label={`第 ${i + 1} 页原图位置`} style={{ width: '100%', maxWidth: 480, background: '#e9eef4' }}><text x="1280" y="150" textAnchor="middle" fontSize="80">标题</text>{job.pages[i].reference_regions!.map((r, n) => <g key={r.document_id}><rect x={r.box[0]} y={r.box[1]} width={r.box[2]} height={r.box[3]} fill="#d1ece9" stroke="#237c78" strokeWidth="8" /><text x={r.box[0] + r.box[2] / 2} y="740" textAnchor="middle" fontSize="70">原图 {n + 1}</text></g>)}<text x="1280" y="1320" textAnchor="middle" fontSize="80">本页要点</text></svg></>}{p.reference_document_ids.map(doc => <figure key={doc}><img src={`/api/ppt/jobs/${job.id}/references/${doc}`} alt={`第 ${i + 1} 页必需原图`} /><figcaption>请确认这张原图用于本页</figcaption></figure>)}</article>)}</section>
      <div className="ppt-actions">
        {job.status === 'awaiting_outline_approval' && <button disabled={busy} onClick={() => void act('approve_outline')}>确认大纲、风格和模型，生成一张样张</button>}
        {job.status === 'awaiting_sample_approval' && <button disabled={busy || job.pages[0]?.status !== 'accepted'} onClick={() => void act('approve_sample')}>样张满意，批准生成剩余页面</button>}
        {!['generating', 'reviewing', 'assembling'].includes(job.status) && <button disabled={busy} onClick={() => { setDraft(structuredClone(job.spec)); setEditing(true) }}>修改内容或风格</button>}
        {!['completed', 'cancelled'].includes(job.status) && <button disabled={busy} onClick={() => void act('cancel')}>取消任务</button>}
        {job.status === 'paused_budget' && <button disabled={busy} onClick={() => void act('budget', { budget: { image_attempts: 2 * job.pages.length } })}>取消 token / AFP / 金额限制，总生图上限设为页数 × 2</button>}
      </div>
      <section className="ppt-slides">{job.pages.map(p => <article key={p.number}><h3>第 {p.number} 页 · {states[p.status] || p.status}</h3>{p.preview_url && <img src={`${p.preview_url}?revision=${job.revision}&status=${p.status}`} alt={`第 ${p.number} 页预览`} />}<p>{p.error || p.qa?.reason}</p>{['failed', 'unknown'].includes(p.status) && <button disabled={busy} onClick={() => void act('retry', { page: p.number })}>确认重试本页（可能再次扣费）</button>}</article>)}</section>
      {job.status === 'completed' && job.artifact && <a className="ppt-download" href={job.artifact.download_url}>下载已检查的 PPTX（含讲稿备注）</a>}
      <p><Link to="/ppt">创建另一份演示文稿</Link></p>
    </>}
  </main>
}
