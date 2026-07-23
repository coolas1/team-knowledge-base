import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type AdminTeam, type AdminToken, type TrustedOllamaAccount } from '../api/client'

export function AdminPage() {
  const [team, setTeam] = useState<AdminTeam | null>(null)
  const [tokens, setTokens] = useState<AdminToken[]>([])
  const [ollamaAccounts, setOllamaAccounts] = useState<TrustedOllamaAccount[]>([])
  const [ollamaUsername, setOllamaUsername] = useState('')
  const [ollamaDisplayName, setOllamaDisplayName] = useState('')
  const [ollamaRole, setOllamaRole] = useState('viewer')
  const [name, setName] = useState('')
  const [subject, setSubject] = useState('')
  const [role, setRole] = useState('viewer')
  const [createdToken, setCreatedToken] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const navigate = useNavigate()

  const load = async () => {
    try {
      setError('')
      const identity = await api.getCurrentIdentity()
      if (!identity.roles.includes('admin')) {
        navigate('/', { replace: true })
        return
      }
      const [teamData, tokenData, ollamaData] = await Promise.all([
        api.getAdminTeam(), api.listAdminTokens(), api.listTrustedOllamaAccounts(),
      ])
      setTeam(teamData)
      setTokens(tokenData.items)
      setOllamaAccounts(ollamaData.items)
    } catch (err: any) {
      setError(err.message || '加载权限信息失败')
    }
  }

  useEffect(() => { load() }, [])

  const createToken = async (event: React.FormEvent) => {
    event.preventDefault()
    setSaving(true)
    try {
      const created = await api.createAdminToken({ name, subject, roles: [role] })
      setCreatedToken(created.token || '')
      setName('')
      setSubject('')
      await load()
    } catch (err: any) {
      setError(err.message || '创建 Token 失败')
    } finally {
      setSaving(false)
    }
  }

  const trustOllamaAccount = async (event: React.FormEvent) => {
    event.preventDefault()
    setSaving(true)
    try {
      await api.createTrustedOllamaAccount({
        username: ollamaUsername,
        display_name: ollamaDisplayName,
        roles: [ollamaRole],
      })
      setOllamaUsername('')
      setOllamaDisplayName('')
      await load()
    } catch (err: any) {
      setError(err.message || '授权 Ollama 账号失败')
    } finally {
      setSaving(false)
    }
  }

  const toggleOllamaAccount = async (account: TrustedOllamaAccount) => {
    try {
      await api.updateTrustedOllamaAccount(account.id, { active: !account.active })
      await load()
    } catch (err: any) {
      setError(err.message || '更新 Ollama 账号失败')
    }
  }

  const revokeOllamaAccount = async (account: TrustedOllamaAccount) => {
    if (!confirm(`确定撤销 Ollama 账号 ${account.username}？`)) return
    try {
      await api.revokeTrustedOllamaAccount(account.id)
      await load()
    } catch (err: any) {
      setError(err.message || '撤销 Ollama 账号失败')
    }
  }

  const toggleToken = async (token: AdminToken) => {
    try {
      await api.updateAdminToken(token.id, { active: !token.active })
      await load()
    } catch (err: any) {
      setError(err.message || '更新 Token 失败')
    }
  }

  const revokeToken = async (token: AdminToken) => {
    if (!confirm(`确定撤销 ${token.name}？撤销后不能恢复使用。`)) return
    try {
      await api.revokeAdminToken(token.id)
      await load()
    } catch (err: any) {
      setError(err.message || '撤销 Token 失败')
    }
  }

  return (
    <main style={{ flex: 1, overflow: 'auto', padding: 24, background: '#f7f8fa' }}>
      <div style={{ maxWidth: 1000, margin: '0 auto' }}>
        <h2 style={{ marginTop: 0 }}>团队权限管理</h2>
        {error && <div style={{ padding: 12, color: '#cf1322', background: '#fff2f0', borderRadius: 6, marginBottom: 16 }}>{error}</div>}

        {team && (
          <section style={{ background: '#fff', padding: 18, borderRadius: 8, marginBottom: 16 }}>
            <h3 style={{ marginTop: 0 }}>{team.name}</h3>
            <div style={{ display: 'flex', gap: 30, color: '#555', fontSize: 14 }}>
              <span>team_id：<b>{team.id}</b></span>
              <span>当前身份：{team.current_subject}</span>
              <span>可见文档：{team.document_count}（公共 {team.public_document_count}）</span>
              <span>可信 Ollama 账号：{team.trusted_ollama_account_count}</span>
              <span>受管 Token：{team.managed_token_count}</span>
            </div>
          </section>
        )}

        <section style={{ background: '#fff', padding: 18, borderRadius: 8, marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>可信 Ollama 账号</h3>
          <p style={{ color: '#666', fontSize: 13, lineHeight: 1.6 }}>
            在后台登记用户名后，该账号可从受信本机网络直接访问本团队，不需要 Token；公共文档会自动包含在结果中。
          </p>
          <form onSubmit={trustOllamaAccount} style={{ display: 'flex', gap: 10, alignItems: 'end', flexWrap: 'wrap' }}>
            <label style={{ fontSize: 13 }}>Ollama 用户名<br /><input required value={ollamaUsername} onChange={(e) => setOllamaUsername(e.target.value)} placeholder="例如 t1ngyx" style={{ padding: 7, marginTop: 4 }} /></label>
            <label style={{ fontSize: 13 }}>显示名称<br /><input value={ollamaDisplayName} onChange={(e) => setOllamaDisplayName(e.target.value)} placeholder="可选" style={{ padding: 7, marginTop: 4 }} /></label>
            <label style={{ fontSize: 13 }}>访问级别<br /><select value={ollamaRole} onChange={(e) => setOllamaRole(e.target.value)} style={{ padding: 7, marginTop: 4 }}><option value="viewer">viewer（只读）</option><option value="member">member（可读写）</option><option value="admin">admin（权限管理）</option></select></label>
            <button disabled={saving} style={{ padding: '8px 16px', border: 0, borderRadius: 5, background: '#1677ff', color: '#fff' }}>{saving ? '保存中...' : '信任该账号'}</button>
          </form>
          {team && ollamaUsername.trim() && (
            <div style={{ marginTop: 12, fontSize: 13, color: '#555' }}>
              无 Token MCP 地址：<code>http://127.0.0.1:8001/mcp?ollama_user={encodeURIComponent(ollamaUsername.trim().toLowerCase())}&amp;team_id={encodeURIComponent(team.id)}</code>
            </div>
          )}
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, marginTop: 18 }}>
            <thead><tr>{['用户名', '显示名称', '访问级别', '状态', '最后使用', '无 Token 连接', '操作'].map((h) => <th key={h} style={{ textAlign: 'left', padding: 8, borderBottom: '1px solid #ddd' }}>{h}</th>)}</tr></thead>
            <tbody>{ollamaAccounts.map((account) => (
              <tr key={account.id}>
                <td style={{ padding: 8 }}><b>{account.username}</b></td><td>{account.display_name || '—'}</td><td>{account.roles.join(', ')}</td>
                <td>{account.active ? '启用' : '停用'}</td><td>{account.last_used_at ? new Date(account.last_used_at).toLocaleString() : '从未'}</td>
                <td><code style={{ fontSize: 11 }}>?ollama_user={account.username}&amp;team_id={account.team_id}</code></td>
                <td><button onClick={() => toggleOllamaAccount(account)} style={{ marginRight: 6 }}>{account.active ? '停用' : '启用'}</button><button onClick={() => revokeOllamaAccount(account)} disabled={!account.active}>撤销</button></td>
              </tr>
            ))}</tbody>
          </table>
          {ollamaAccounts.length === 0 && <div style={{ color: '#999', marginTop: 14 }}>尚未登记可信 Ollama 账号。</div>}
        </section>

        <section style={{ background: '#fff', padding: 18, borderRadius: 8, marginBottom: 16 }}>
          <h3 style={{ marginTop: 0 }}>兼容 API Token</h3>
          <p style={{ color: '#666', fontSize: 13 }}>用于远程或不受信网络客户端；本机 Ollama 账号不需要创建 Token。</p>
          <form onSubmit={createToken} style={{ display: 'flex', gap: 10, alignItems: 'end', flexWrap: 'wrap' }}>
            <label style={{ fontSize: 13 }}>名称<br /><input required value={name} onChange={(e) => setName(e.target.value)} style={{ padding: 7, marginTop: 4 }} /></label>
            <label style={{ fontSize: 13 }}>使用者标识<br /><input required value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="例如 remote-agent" style={{ padding: 7, marginTop: 4 }} /></label>
            <label style={{ fontSize: 13 }}>访问级别<br /><select value={role} onChange={(e) => setRole(e.target.value)} style={{ padding: 7, marginTop: 4 }}><option value="viewer">viewer（只读）</option><option value="member">member（可读写）</option><option value="admin">admin（权限管理）</option></select></label>
            <button disabled={saving} style={{ padding: '8px 16px', border: 0, borderRadius: 5, background: '#1677ff', color: '#fff' }}>{saving ? '创建中...' : '授权并生成 Token'}</button>
          </form>
          {createdToken && (
            <div style={{ marginTop: 14, padding: 12, background: '#fffbe6', border: '1px solid #ffe58f', borderRadius: 6 }}>
              <b>Token 只显示一次，请立即保存：</b>
              <code style={{ display: 'block', marginTop: 8, wordBreak: 'break-all' }}>{createdToken}</code>
              <div style={{ marginTop: 10, fontSize: 13, lineHeight: 1.6 }}>
                Ollama / MCP 地址：<code>http://127.0.0.1:8001/mcp</code><br />
                请求头：<code>Authorization: Bearer {createdToken}</code>
              </div>
            </div>
          )}
        </section>

        <section style={{ background: '#fff', padding: 18, borderRadius: 8 }}>
          <h3 style={{ marginTop: 0 }}>兼容 Token 列表</h3>
          {tokens.length === 0 ? <div style={{ color: '#999' }}>尚未创建数据库 Token；当前本地管理员来自启动配置。</div> : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead><tr>{['名称', '用户名', '前缀', '访问级别', '状态', '最后使用', '操作'].map((h) => <th key={h} style={{ textAlign: 'left', padding: 8, borderBottom: '1px solid #ddd' }}>{h}</th>)}</tr></thead>
              <tbody>{tokens.map((token) => (
                <tr key={token.id}>
                  <td style={{ padding: 8 }}>{token.name}</td><td>{token.subject}</td><td><code>{token.token_prefix}…</code></td><td>{token.roles.join(', ')}</td>
                  <td>{token.active ? '启用' : '停用'}</td><td>{token.last_used_at ? new Date(token.last_used_at).toLocaleString() : '从未'}</td>
                  <td><button onClick={() => toggleToken(token)} style={{ marginRight: 6 }}>{token.active ? '停用' : '启用'}</button><button onClick={() => revokeToken(token)} disabled={!token.active}>撤销</button></td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </section>
      </div>
    </main>
  )
}
