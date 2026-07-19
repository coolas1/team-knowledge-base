import { Routes, Route } from 'react-router-dom'
import { Layout } from './components/Layout'
import { DocumentListPage } from './pages/DocumentListPage'
import { DocumentDetailPage } from './pages/DocumentDetailPage'
import { GraphPage } from './pages/GraphPage'
import { LogsPage } from './pages/LogsPage'
import { SearchPage } from './pages/SearchPage'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<DocumentListPage />} />
        <Route path="/search" element={<SearchPage />} />
        <Route path="/documents/:id" element={<DocumentDetailPage />} />
        <Route path="/graph" element={<GraphPage />} />
        <Route path="/logs" element={<LogsPage />} />
      </Route>
    </Routes>
  )
}
