import { Routes, Route } from 'react-router-dom'
import { Layout } from './components/Layout'
import { DocumentListPage } from './pages/DocumentListPage'
import { DocumentDetailPage } from './pages/DocumentDetailPage'
import { GraphPage } from './pages/GraphPage'
import { AdminPage } from './pages/AdminPage'
import { LoginPage } from './pages/LoginPage'

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<Layout />}>
        <Route path="/" element={<DocumentListPage />} />
        <Route path="/documents/:id" element={<DocumentDetailPage />} />
        <Route path="/graph" element={<GraphPage />} />
        <Route path="/admin" element={<AdminPage />} />
      </Route>
    </Routes>
  )
}
