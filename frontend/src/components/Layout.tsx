import { useEffect, useState, useRef } from 'react'
import { Link, useNavigate, Outlet } from 'react-router-dom'
import { api, type CurrentIdentity } from '../api/client'
import { clearSessionToken, hasSessionToken } from '../auth/teamAuth'

export function Layout() {
  const [uploading, setUploading] = useState(false)
  const [identity, setIdentity] = useState<CurrentIdentity | null>(null)
  const [checking, setChecking] = useState(true)
  const [uploadScope, setUploadScope] = useState<'team' | 'public'>('team')
  const [choosingUploadScope, setChoosingUploadScope] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()

  useEffect(() => {
    if (!hasSessionToken()) {
      navigate('/login', { replace: true })
      return
    }
    api.getCurrentIdentity()
      .then(setIdentity)
      .catch(() => navigate('/login', { replace: true }))
      .finally(() => setChecking(false))
  }, [navigate])

  const logout = () => {
    clearSessionToken()
    navigate('/login', { replace: true })
  }

  const handleUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      const doc = await api.uploadFile(file, uploadScope)
      navigate(`/documents/${doc.id}`)
    } catch (err: any) {
      alert('上传失败: ' + err.message)
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const chooseUploadScope = (scope: 'team' | 'public') => {
    setUploadScope(scope)
    setChoosingUploadScope(false)
    fileRef.current?.click()
  }

  if (checking || !identity) {
    return <div style={{ padding: 32, color: '#777' }}>正在验证身份...</div>
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '12px 24px', borderBottom: '1px solid #e8e8e8', background: '#fff' }}>
        <Link to="/" style={{ fontSize: 18, fontWeight: 700, textDecoration: 'none', color: '#333' }}>团队知识库</Link>
        <Link to="/graph" style={{ fontSize: 14, textDecoration: 'none', color: '#666', padding: '4px 8px' }}>知识图谱</Link>
        {identity.roles.includes('admin') && (
          <Link to="/admin" style={{ fontSize: 14, textDecoration: 'none', color: '#666', padding: '4px 8px' }}>权限管理</Link>
        )}
        <div style={{ flex: 1 }} />
        <span title={`身份: ${identity.subject}`} style={{ fontSize: 12, color: '#555' }}>
          团队数据库：<b>{identity.team_id}</b> · {identity.subject} · {identity.roles.join(', ')}
        </span>
        <button onClick={logout} style={{ padding: '6px 12px', border: '1px solid #d9d9d9', borderRadius: 6, background: '#fff' }}>退出</button>
        <button onClick={() => setChoosingUploadScope(true)} disabled={uploading}
          style={{ padding: '6px 16px', borderRadius: 6, border: '1px solid #1890ff', background: '#1890ff', color: '#fff', cursor: uploading ? 'wait' : 'pointer', fontSize: 14 }}>
          {uploading ? '上传中...' : '上传文件'}
        </button>
        <input ref={fileRef} type="file" hidden onChange={handleUpload} />
      </header>
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}><Outlet /></div>
      {choosingUploadScope && (
        <div
          role="presentation"
          onClick={() => setChoosingUploadScope(false)}
          style={{ position: 'fixed', inset: 0, zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0, 0, 0, 0.35)' }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="upload-scope-title"
            onClick={(event) => event.stopPropagation()}
            style={{ width: 420, maxWidth: 'calc(100vw - 32px)', padding: 24, borderRadius: 10, background: '#fff', boxShadow: '0 12px 40px rgba(0, 0, 0, 0.2)' }}
          >
            <h3 id="upload-scope-title" style={{ margin: '0 0 8px', fontSize: 18 }}>选择文档范围</h3>
            <p style={{ margin: '0 0 20px', color: '#666', fontSize: 14 }}>选择后将打开文件选择窗口。</p>
            <div style={{ display: 'grid', gap: 12 }}>
              <button
                type="button"
                onClick={() => chooseUploadScope('team')}
                style={{ padding: '14px 16px', textAlign: 'left', border: '1px solid #91caff', borderRadius: 8, background: '#e6f4ff', cursor: 'pointer' }}
              >
                <strong style={{ display: 'block', marginBottom: 4 }}>团队文档</strong>
                <span style={{ color: '#555', fontSize: 13 }}>仅当前团队可读取，并进入当前团队图谱。</span>
              </button>
              <button
                type="button"
                onClick={() => chooseUploadScope('public')}
                style={{ padding: '14px 16px', textAlign: 'left', border: '1px solid #d9d9d9', borderRadius: 8, background: '#fff', cursor: 'pointer' }}
              >
                <strong style={{ display: 'block', marginBottom: 4 }}>公共文档</strong>
                <span style={{ color: '#555', fontSize: 13 }}>所有已认证团队均可读取，并分别合入各团队图谱。</span>
              </button>
            </div>
            <button
              type="button"
              onClick={() => setChoosingUploadScope(false)}
              style={{ marginTop: 16, width: '100%', padding: '8px 12px', border: '1px solid #d9d9d9', borderRadius: 6, background: '#fff', cursor: 'pointer' }}
            >
              取消
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
