import { useState, useRef } from 'react'
import { NavLink, useNavigate, Outlet } from 'react-router-dom'
import { BookOpen, LoaderCircle, MessageSquare, Network, Search, Upload } from 'lucide-react'
import { api } from '../api/client'
import './Layout.css'

export function Layout() {
  const [uploading, setUploading] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    if (files.length === 0) return
    setUploading(true)
    try {
      for (const file of files) {
        await api.uploadFile(file)
      }
      navigate('/')
    } catch (err: any) {
      alert('上传失败: ' + err.message)
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <NavLink to="/" className="app-brand">
          <BookOpen size={20} aria-hidden="true" />
          <span>团队知识库</span>
        </NavLink>
        <nav className="app-nav" aria-label="主导航">
          <NavLink to="/graph" className={({ isActive }) => `app-nav-link${isActive ? ' is-active' : ''}`}>
            <Network size={17} aria-hidden="true" />
            <span>知识图谱</span>
          </NavLink>
          <NavLink to="/search" className={({ isActive }) => `app-nav-link${isActive ? ' is-active' : ''}`}>
            <Search size={17} aria-hidden="true" />
            <span>搜索</span>
          </NavLink>
          <NavLink to="/ask" className={({ isActive }) => `app-nav-link${isActive ? ' is-active' : ''}`}>
            <MessageSquare size={17} aria-hidden="true" />
            <span>提问</span>
          </NavLink>
        </nav>
        <div className="app-header-spacer" />
        <button
          type="button"
          className="app-upload-button"
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          title={uploading ? '上传中' : '上传文件'}
        >
          {uploading ? (
            <LoaderCircle className="app-spin" size={17} aria-hidden="true" />
          ) : (
            <Upload size={17} aria-hidden="true" />
          )}
          <span>{uploading ? '上传中' : '上传文件'}</span>
        </button>
        <input ref={fileRef} type="file" multiple hidden onChange={handleUpload} />
      </header>

      <div className="app-content">
        <Outlet />
      </div>
    </div>
  )
}
