import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { authenticate } from '../api/client'

export function LoginPage() {
  const [token, setToken] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const navigate = useNavigate()

  const login = async (event: React.FormEvent) => {
    event.preventDefault()
    setSubmitting(true)
    setError('')
    try {
      await authenticate(token)
      navigate('/', { replace: true })
    } catch (err: any) {
      setError(err.message || '认证失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', background: '#f4f6f8' }}>
      <form onSubmit={login} style={{ width: 420, padding: 28, background: '#fff', borderRadius: 10, boxShadow: '0 8px 30px rgba(0,0,0,.08)' }}>
        <h2 style={{ marginTop: 0 }}>登录团队知识库</h2>
        <p style={{ color: '#666', fontSize: 14, lineHeight: 1.6 }}>输入管理员分配的访问 Token。团队和权限由后端验证，无法在浏览器中自行选择。</p>
        <label style={{ display: 'block', fontSize: 13, marginBottom: 8 }}>访问 Token</label>
        <input type="password" autoComplete="off" required value={token} onChange={(e) => setToken(e.target.value)}
          placeholder="tkb_… 或本地 bootstrap token" style={{ width: '100%', boxSizing: 'border-box', padding: 10, border: '1px solid #d9d9d9', borderRadius: 6 }} />
        {error && <div style={{ marginTop: 12, color: '#cf1322', fontSize: 13 }}>{error}</div>}
        <button disabled={submitting} style={{ width: '100%', marginTop: 18, padding: 10, border: 0, borderRadius: 6, background: '#1677ff', color: '#fff' }}>
          {submitting ? '验证中...' : '登录'}
        </button>
      </form>
    </main>
  )
}
