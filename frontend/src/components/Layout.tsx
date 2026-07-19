import { useState, useRef } from 'react'
import { Link, useNavigate, Outlet } from 'react-router-dom'
import { api } from '../api/client'

export function Layout() {
  const [uploading, setUploading] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      const doc = await api.uploadFile(file)
      navigate(`/documents/${doc.id}`)
    } catch (err: any) {
      alert('上传失败: ' + err.message)
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      {/* 顶部 */}
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 16,
          padding: '12px 24px',
          borderBottom: '1px solid #e8e8e8',
          background: '#fff',
        }}
      >
        <Link to="/" style={{ fontSize: 18, fontWeight: 700, textDecoration: 'none', color: '#333' }}>
          团队知识库
        </Link>
        <Link
          to="/search"
          style={{ fontSize: 14, textDecoration: 'none', color: '#666', padding: '4px 8px' }}
        >
          检索调试
        </Link>
        <Link
          to="/graph"
          style={{ fontSize: 14, textDecoration: 'none', color: '#666', padding: '4px 8px' }}
        >
          知识图谱
        </Link>
        <Link
          to="/logs"
          style={{ fontSize: 14, textDecoration: 'none', color: '#666', padding: '4px 8px' }}
        >
          日志
        </Link>
        <div style={{ flex: 1 }} />
        <button
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          style={{
            padding: '6px 16px',
            borderRadius: 6,
            border: '1px solid #1890ff',
            background: '#1890ff',
            color: '#fff',
            cursor: uploading ? 'wait' : 'pointer',
            fontSize: 14,
          }}
        >
          {uploading ? '上传中...' : '上传文件'}
        </button>
        <input ref={fileRef} type="file" hidden onChange={handleUpload} />
      </header>

      {/* 主体 */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <Outlet />
      </div>
    </div>
  )
}
