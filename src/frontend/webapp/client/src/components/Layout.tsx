import { useState, useRef } from 'react'
import { NavLink, useNavigate, Outlet } from 'react-router-dom'
import {
  AlertCircle,
  BookOpen,
  FileUp,
  LoaderCircle,
  MessageSquare,
  Network,
  RefreshCw,
  Search,
  Upload,
  X,
} from 'lucide-react'
import { ApiError, api } from '../api/client'
import './Layout.css'

interface UploadFailure {
  file: File
  message: string
  suggestion: string
  retryable: boolean
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function Layout() {
  const [uploading, setUploading] = useState(false)
  const [uploadFailure, setUploadFailure] = useState<UploadFailure | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()

  const uploadFile = async (
    file: File,
    keepFailureVisible = false,
    navigateOnSuccess = true,
  ) => {
    setUploading(true)
    if (!keepFailureVisible) setUploadFailure(null)
    try {
      const doc = await api.uploadFile(file)
      setUploadFailure(null)
      if (navigateOnSuccess) navigate(`/documents/${doc.id}`)
      return true
    } catch (error) {
      const apiError = error instanceof ApiError ? error : undefined
      setUploadFailure({
        file,
        message: error instanceof Error ? error.message : '上传未完成',
        suggestion: apiError?.suggestion || '请确认文件可正常打开，或稍后直接重试。',
        retryable: apiError?.retryable ?? true,
      })
      return false
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const handleUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    if (files.length === 0) return
    if (files.length === 1) {
      await uploadFile(files[0])
      return
    }
    for (const file of files) {
      if (!(await uploadFile(file, false, false))) return
    }
    navigate('/')
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
        <input
          ref={fileRef}
          type="file"
          accept=".md,.markdown,.txt,.pdf,.docx,.pptx,.png,.jpg,.jpeg,.tiff,.bmp,.webp"
          multiple
          hidden
          onChange={handleUpload}
        />
      </header>

      {uploadFailure && (
        <section className="app-upload-error" role="alert" aria-live="assertive">
          <AlertCircle size={20} aria-hidden="true" />
          <div className="app-upload-error-content">
            <strong>{uploadFailure.file.name} 上传失败</strong>
            <span>
              {uploadFailure.message} · {uploadFailure.suggestion}
            </span>
            <small>{formatFileSize(uploadFailure.file.size)}</small>
          </div>
          <div className="app-upload-error-actions">
            {uploadFailure.retryable && (
              <button
                type="button"
                onClick={() => void uploadFile(uploadFailure.file, true)}
                disabled={uploading}
              >
                {uploading ? (
                  <LoaderCircle className="app-spin" size={16} aria-hidden="true" />
                ) : (
                  <RefreshCw size={16} aria-hidden="true" />
                )}
                <span>{uploading ? '重试中' : '重试'}</span>
              </button>
            )}
            <button type="button" onClick={() => fileRef.current?.click()} disabled={uploading}>
              <FileUp size={16} aria-hidden="true" />
              <span>重新选择</span>
            </button>
            <button
              type="button"
              className="app-upload-error-close"
              onClick={() => setUploadFailure(null)}
              disabled={uploading}
              aria-label="关闭上传错误"
              title="关闭"
            >
              <X size={17} aria-hidden="true" />
            </button>
          </div>
        </section>
      )}

      <div className="app-content">
        <Outlet />
      </div>
    </div>
  )
}
