import { Routes, Route } from 'react-router-dom'
import { Layout } from './components/Layout'
import { DocumentListPage } from './pages/DocumentListPage'
import { DocumentDetailPage } from './pages/DocumentDetailPage'
import { GraphPage } from './pages/GraphPage'
import { SearchPage } from './pages/SearchPage'
import { AskPage } from './pages/AskPage'
import { MemoryPage } from './pages/MemoryPage'
import { PPTPage } from './pages/PPTPage'
import { NotFoundPage } from './pages/NotFoundPage'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<DocumentListPage />} />
        <Route path="/documents/:id" element={<DocumentDetailPage />} />
        <Route path="/search" element={<SearchPage />} />
        <Route path="/ask" element={<AskPage />} />
        <Route path="/graph" element={<GraphPage />} />
        <Route path="/memory" element={<MemoryPage />} />
        <Route path="/ppt" element={<PPTPage />} />
        <Route path="/ppt/:id" element={<PPTPage />} />
        {/* 未匹配的客户端路由渲染 404 页面，而不是空白屏 */}
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}
