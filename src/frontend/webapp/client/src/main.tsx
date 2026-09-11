import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'

declare global {
  interface Window {
    /** Set by the pre-bootstrap guard injected into index.html. */
    __tkbMarkBooted?: () => void
  }
}

const root = ReactDOM.createRoot(document.getElementById('root')!)

root.render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
)

// The shell's boot guard shows a reload prompt if the bundle never runs.
// Reaching this line means it got far enough to render, so the guard stands
// down — including for the deadline path, which would otherwise fire on an
// app that legitimately renders nothing into #root.
window.__tkbMarkBooted?.()
