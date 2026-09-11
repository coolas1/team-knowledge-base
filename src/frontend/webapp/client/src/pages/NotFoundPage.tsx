import { Link, useLocation } from 'react-router-dom'

export function NotFoundPage() {
  const location = useLocation()

  return (
    <main style={{ flex: 1, overflow: 'auto', padding: 24, textAlign: 'center' }}>
      <h2 style={{ marginBottom: 8 }}>页面不存在</h2>
      <p style={{ color: '#666', marginBottom: 4 }}>
        找不到路径 <code>{location.pathname}</code>。
      </p>
      <p style={{ marginTop: 16 }}>
        <Link to="/" style={{ color: '#1890ff' }}>
          返回文档列表
        </Link>
      </p>
    </main>
  )
}
