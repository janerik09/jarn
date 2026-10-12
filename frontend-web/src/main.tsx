import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { AuthGate } from './components/SignIn'
import { installFetchInterceptor } from './lib/auth'
import './styles/theme.css'

installFetchInterceptor()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AuthGate>
      <App />
    </AuthGate>
  </React.StrictMode>,
)
