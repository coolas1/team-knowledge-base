import { useState } from 'react'
import { api, type SearchResult, type DiagnoseResult, type DiagnoseStage } from '../api/client'

// 单个流程阶段的徽标
function Stage({ label, detail, ms, accent }: { label: string; detail: string; ms?: number; accent?: string }) {
  return (
    <div style={{
      flex: 1, minWidth: 120, padding: '10px 12px', borderRadius: 8,
      background: '#fff', border: `1px solid ${accent || '#e8e8e8'}`,
    }}>
      <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 15, fontWeight: 600, color: accent || '#333' }}>{detail}</div>
      {ms !== undefined && <div style={{ fontSize: 11, color: '#bbb', marginTop: 2 }}>{ms} ms</div>}
    </div>
  )
}

function scoreColor(score: number): string {
  if (score >= 0.1) return '#3fb950'
  if (score >= 0.03) return '#d29922'
  return '#f85149'
}

// ── 诊断面板组件 ──────────────────────────────────────────────

function DiagPanel({ diag }: { diag: DiagnoseResult }) {
  const maxMs = Math.max(...diag.stages.map(s => s.elapsed_ms), 1)
  const verdictColor = diag.verdict.includes('✓') ? '#3fb950'
    : diag.verdict.includes('△') ? '#d29922' : '#f85149'

  return (
    <div style={{ background: '#fff', border: '2px solid #fa8c16', borderRadius: 12, padding: 20, marginBottom: 24 }}>
      {/* 标题行 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <span style={{ fontSize: 16, fontWeight: 700 }}>🔬 检索诊断</span>
        <span style={{
          padding: '2px 12px', borderRadius: 12, fontSize: 13, fontWeight: 600,
          background: verdictColor + '18', color: verdictColor,
        }}>{diag.verdict} | rank={diag.final_rank}</span>
        <span style={{ fontSize: 12, color: '#999' }}>总耗时 {diag.total_ms.toFixed(0)}ms</span>
      </div>

      {/* ① 漏斗追踪 */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: '#666', marginBottom: 8 }}>漏斗追踪</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {diag.stages.map((s, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
              <span style={{
                width: 20, height: 20, borderRadius: '50%', display: 'flex',
                alignItems: 'center', justifyContent: 'center', fontSize: 11,
                background: s.target_hit ? '#f6ffed' : '#fff2f0',
                color: s.target_hit ? '#3fb950' : '#f85149',
                border: `1px solid ${s.target_hit ? '#b7eb8f' : '#ffccc7'}`,
              }}>{s.target_hit ? '✓' : '✗'}</span>
              <span style={{ width: 100, color: '#666' }}>{s.name}</span>
              <span style={{ color: s.target_hit ? '#333' : '#ccc' }}>
                {s.target_hit ? `rank=${s.target_rank}` : 'MISS'}
                {s.target_score > 0 && ` (${s.target_score.toFixed(3)})`}
              </span>
              {/* 路径级详情 */}
              {Object.keys(s.path_hits).length > 0 && (
                <span style={{ fontSize: 11, color: '#4493f8' }}>
                  {Object.entries(s.path_hits).map(([k, v]) => `${k}@${v.rank}`).join(' ')}
                </span>
              )}
              {/* 额外信息 */}
              {s.extra && Object.keys(s.extra).length > 0 && (
                <span style={{ fontSize: 11, color: '#999' }}>
                  {s.extra.vote_count !== undefined && `votes=${s.extra.vote_count} `}
                  {s.extra.mode && `mode=${s.extra.mode} `}
                  {s.extra.target_source && `${s.extra.target_source} `}
                  {s.extra.expand_added !== undefined && `+${s.extra.expand_added} `}
                  {s.extra.graph_rescue !== undefined && `rescue=${s.extra.graph_rescue}`}
                </span>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* ② 耗时分布 */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: '#666', marginBottom: 8 }}>耗时分布</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          {diag.stages.map((s, i) => {
            const pct = diag.total_ms > 0 ? (s.elapsed_ms / diag.total_ms * 100) : 0
            const barW = Math.max((s.elapsed_ms / maxMs) * 100, 2)
            const isBottleneck = s.elapsed_ms === maxMs
            return (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
                <span style={{ width: 100, color: '#666', textAlign: 'right' }}>{s.name}</span>
                <div style={{ flex: 1, height: 16, background: '#f5f5f5', borderRadius: 4, overflow: 'hidden' }}>
                  <div style={{
                    width: `${barW}%`, height: '100%', borderRadius: 4,
                    background: isBottleneck ? '#fa8c16' : '#91caff',
                  }} />
                </div>
                <span style={{ width: 80, color: isBottleneck ? '#fa8c16' : '#999', fontWeight: isBottleneck ? 600 : 400 }}>
                  {s.elapsed_ms.toFixed(0)}ms ({pct.toFixed(0)}%)
                </span>
              </div>
            )
          })}
        </div>
      </div>

      {/* ③ 内容质量 */}
      <div>
        <div style={{ fontSize: 13, fontWeight: 600, color: '#666', marginBottom: 8 }}>内容质量</div>
        <ContentQuality cq={diag.content_quality} />
      </div>
    </div>
  )
}

function ContentQuality({ cq }: { cq: Record<string, any> }) {
  if (!cq || cq.error) {
    return <div style={{ fontSize: 12, color: '#f85149' }}>⚠ {cq?.error || '无数据'}</div>
  }
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, fontSize: 12 }}>
      <div style={{ padding: '6px 12px', background: '#f6f6f6', borderRadius: 6 }}>
        目标 chunks: <b>{cq.target_chunk_count ?? '?'}</b>
      </div>
      {cq.chunk_token_sizes && (
        <div style={{ padding: '6px 12px', background: '#f6f6f6', borderRadius: 6 }}>
          词数: <b>{cq.chunk_token_sizes.join(', ')}</b>
        </div>
      )}
      {cq.overview_languages && (
        <div style={{
          padding: '6px 12px', borderRadius: 6,
          background: cq.overview_lang_mismatch ? '#fff2f0' : '#f6ffed',
          color: cq.overview_lang_mismatch ? '#f85149' : '#3fb950',
        }}>
          overview语言: {cq.overview_languages.join(',')} {cq.overview_lang_mismatch ? '⚠不匹配' : '✓'}
        </div>
      )}
      {cq.overview_faithfulness_issues?.length > 0 && (
        <div style={{ padding: '6px 12px', background: '#fff7e6', borderRadius: 6, color: '#d48806' }}>
          忠实度问题: {cq.overview_faithfulness_issues.length} 处
        </div>
      )}
      {cq.cross_chunk_dependency && (
        <div style={{ padding: '6px 12px', background: '#fff7e6', borderRadius: 6, color: '#d48806' }}>
          跨chunk依赖: YES
        </div>
      )}
    </div>
  )
}

export function SearchPage() {
  const [query, setQuery] = useState('')
  const [gold, setGold] = useState('')
  const [loading, setLoading] = useState(false)
  const [diagLoading, setDiagLoading] = useState(false)
  const [result, setResult] = useState<SearchResult | null>(null)
  const [diag, setDiag] = useState<DiagnoseResult | null>(null)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  const doSearch = async () => {
    if (!query.trim()) return
    setLoading(true)
    setError('')
    setResult(null)
    try {
      const r = await api.search(query.trim())
      setResult(r)
    } catch (e: any) {
      setError(e.message || '检索失败')
    } finally {
      setLoading(false)
    }
  }

  const doDiagnose = async () => {
    if (!query.trim() || !gold.trim()) return
    setDiagLoading(true)
    setError('')
    setDiag(null)
    try {
      const goldFiles = gold.split(',').map(s => s.trim()).filter(Boolean)
      const r = await api.diagnose(query.trim(), goldFiles)
      setDiag(r)
    } catch (e: any) {
      setError(e.message || '诊断失败')
    } finally {
      setDiagLoading(false)
    }
  }

  const toggle = (i: number) => {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(i) ? next.delete(i) : next.add(i)
      return next
    })
  }

  const d = result?.debug

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: 24, background: '#fafafa' }}>
      {/* 搜索框 */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && doSearch()}
          placeholder="输入查询，查看检索全链路召回详情…"
          style={{
            flex: 1, padding: '10px 14px', fontSize: 15, borderRadius: 8,
            border: '1px solid #d9d9d9', outline: 'none',
          }}
        />
        <button
          onClick={doSearch}
          disabled={loading}
          style={{
            padding: '10px 28px', borderRadius: 8, border: 'none',
            background: '#1890ff', color: '#fff', fontSize: 15,
            cursor: loading ? 'wait' : 'pointer', opacity: loading ? 0.6 : 1,
          }}
        >{loading ? '检索中…' : '搜索'}</button>
      </div>

      {/* 诊断输入行 */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 20 }}>
        <input
          value={gold}
          onChange={e => setGold(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && doDiagnose()}
          placeholder="目标文档文件名（逗号分隔，如 2023-11-15.md, coral-paper.md）"
          style={{
            flex: 1, padding: '8px 14px', fontSize: 13, borderRadius: 8,
            border: '1px dashed #d9d9d9', outline: 'none', background: '#fffbe6',
          }}
        />
        <button
          onClick={doDiagnose}
          disabled={diagLoading || !query.trim() || !gold.trim()}
          style={{
            padding: '8px 20px', borderRadius: 8, border: 'none',
            background: '#fa8c16', color: '#fff', fontSize: 13,
            cursor: diagLoading ? 'wait' : 'pointer',
            opacity: (diagLoading || !query.trim() || !gold.trim()) ? 0.5 : 1,
          }}
        >{diagLoading ? '诊断中…' : '🔬 诊断'}</button>
      </div>

      {error && <div style={{ color: '#f85149', marginBottom: 16 }}>错误：{error}</div>}

      {/* ═══ 诊断结果面板 ═══ */}
      {diag && <DiagPanel diag={diag} />}

      {d && (
        <>
          {/* 全链路流程概览 */}
          <div style={{ marginBottom: 20 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#666', marginBottom: 8 }}>
              检索全链路（总耗时 {d.total_ms} ms，最终 {d.final_chunks} 个 chunks）
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <Stage label="① Query 改写" detail={d.rewrite.rewritten ? '已改写' : '原样'} ms={d.rewrite.elapsed_ms} accent="#4493f8" />
              <Stage label="② L1 多路召回" detail={`${d.recall.vector_main}+${d.recall.vector_expanded}+${d.recall.bm25}`} ms={d.recall.elapsed_ms} accent="#4493f8" />
              <Stage label="③ RRF 融合" detail={`${d.recall.rrf_merged} 候选`} accent="#a371f7" />
              <Stage label="④ 迭代扩展" detail={`+${d.iterative_expand.added}`} ms={d.iterative_expand.elapsed_ms} accent="#a371f7" />
              <Stage label="⑤ Reranker" detail={`${d.reranker.input}→${d.reranker.survivors}`} ms={d.reranker.elapsed_ms} accent="#d29922" />
              <Stage label="⑥ 图谱增强" detail={`${d.graph.entities}实体+${d.graph.graph_chunks}chunks`} ms={d.graph.elapsed_ms} accent="#3fb950" />
            </div>
          </div>

          {/* Query 改写详情 */}
          <div style={{ background: '#fff', border: '1px solid #e8e8e8', borderRadius: 8, padding: 16, marginBottom: 20 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#666', marginBottom: 10 }}>Query 改写详情</div>
            <div style={{ fontSize: 13, marginBottom: 6 }}>
              <span style={{ color: '#999' }}>原始：</span>{d.rewrite.original}
            </div>
            <div style={{ fontSize: 13, marginBottom: 6 }}>
              <span style={{ color: '#999' }}>改写：</span>
              <span style={{ color: d.rewrite.rewritten !== d.rewrite.original ? '#4493f8' : '#333' }}>
                {d.rewrite.rewritten}
              </span>
            </div>
            <div style={{ fontSize: 13, marginBottom: 6 }}>
              <span style={{ color: '#999' }}>关键词：</span>
              {d.rewrite.keywords.map((k, i) => (
                <span key={i} style={{
                  display: 'inline-block', margin: '0 4px 4px 0', padding: '1px 8px',
                  background: '#e6f4ff', color: '#1890ff', borderRadius: 10, fontSize: 12,
                }}>{k}</span>
              ))}
            </div>
            {d.rewrite.expanded_queries.length > 0 && (
              <div style={{ fontSize: 13 }}>
                <span style={{ color: '#999' }}>扩展查询：</span>
                {d.rewrite.expanded_queries.map((q, i) => (
                  <span key={i} style={{
                    display: 'inline-block', margin: '0 4px 4px 0', padding: '1px 8px',
                    background: '#f9f0ff', color: '#722ed1', borderRadius: 10, fontSize: 12,
                  }}>{q}</span>
                ))}
              </div>
            )}
          </div>

          {/* 召回结果列表 */}
          <div style={{ fontSize: 13, fontWeight: 600, color: '#666', marginBottom: 8 }}>
            召回结果（{result!.chunks.length} 个，按 reranker 分数排序）
          </div>
          {result!.chunks.map((c, i) => {
            const isOpen = expanded.has(i)
            return (
              <div key={i} style={{
                background: '#fff', border: '1px solid #e8e8e8', borderRadius: 8,
                padding: 14, marginBottom: 8,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer' }} onClick={() => toggle(i)}>
                  <span style={{
                    width: 26, height: 26, borderRadius: '50%', background: '#f0f0f0',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 12, fontWeight: 700, color: '#666', flexShrink: 0,
                  }}>{i + 1}</span>
                  <span style={{ fontWeight: 600, fontSize: 14, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {c.title || '(无标题)'}
                  </span>
                  <span style={{ fontSize: 12, color: '#999' }}>
                    reranker <b style={{ color: scoreColor(c.reranker_score) }}>{c.reranker_score.toFixed(3)}</b>
                  </span>
                  <span style={{ fontSize: 12, color: '#999' }}>
                    vector <b style={{ color: '#4493f8' }}>{c.vector_score.toFixed(3)}</b>
                  </span>
                  <span style={{ fontSize: 11, color: '#bbb' }}>{isOpen ? '▲' : '▼'}</span>
                </div>
                {isOpen && (
                  <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid #f0f0f0' }}>
                    <div style={{ fontSize: 11, color: '#bbb', marginBottom: 4 }}>doc_id: {c.doc_id}</div>
                    <pre style={{
                      fontSize: 12, color: '#444', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                      background: '#fafafa', padding: 10, borderRadius: 6, maxHeight: 300, overflow: 'auto',
                      fontFamily: 'inherit', margin: 0,
                    }}>{c.chunk_text}</pre>
                  </div>
                )}
              </div>
            )
          })}

          {/* 关联实体 & 文档 */}
          {(result!.related_entities.length > 0 || result!.related_docs.length > 0) && (
            <div style={{ display: 'flex', gap: 16, marginTop: 20, flexWrap: 'wrap' }}>
              {result!.related_entities.length > 0 && (
                <div style={{ flex: 1, minWidth: 280, background: '#fff', border: '1px solid #e8e8e8', borderRadius: 8, padding: 16 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: '#666', marginBottom: 8 }}>
                    关联实体（{result!.related_entities.length}）
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {result!.related_entities.map((e, i) => (
                      <span key={i} style={{
                        padding: '2px 10px', background: '#f6ffed', color: '#389e0d',
                        borderRadius: 10, fontSize: 12,
                      }}>{e.name} <span style={{ color: '#bbb' }}>·{e.type}</span></span>
                    ))}
                  </div>
                </div>
              )}
              {result!.related_docs.length > 0 && (
                <div style={{ flex: 1, minWidth: 280, background: '#fff', border: '1px solid #e8e8e8', borderRadius: 8, padding: 16 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: '#666', marginBottom: 8 }}>
                    关联文档（{result!.related_docs.length}）
                  </div>
                  {result!.related_docs.map((rd, i) => (
                    <div key={i} style={{ fontSize: 12, marginBottom: 4 }}>
                      <span style={{ color: '#333' }}>{rd.title}</span>
                      <span style={{ color: '#bbb' }}> ·{rd.relation_type}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}

      {!result && !loading && !error && (
        <div style={{ color: '#999', textAlign: 'center', marginTop: 80 }}>
          <div style={{ fontSize: 48, marginBottom: 16 }}>🔍</div>
          <div style={{ fontSize: 16 }}>输入查询开始检索调试</div>
          <div style={{ fontSize: 13, marginTop: 8, color: '#bbb' }}>
            可查看 Query 改写、各路召回数量、Reranker 分数明细，排查召回问题
          </div>
        </div>
      )}
    </div>
  )
}
