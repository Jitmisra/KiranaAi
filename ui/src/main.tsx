import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { ErrorBoundary } from './components/ErrorBoundary';
import './styles/tokens.css';
import './styles/app.css';

const el = document.getElementById('root');
if (!el) throw new Error('no #root — the shell HTML did not load');
createRoot(el).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
