import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from '@/app/App';
import '@/styles/global.css';
// 设计 token 为唯一真相源，必须在 global.css 之后加载：
// 两者曾对 --color-text-primary/--color-text-secondary 同名，后加载者胜。
import '@/styles/design-tokens.css';
import '@/styles/workspace.css';
import '@/styles/pages.css';
import '@/styles/animations.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
