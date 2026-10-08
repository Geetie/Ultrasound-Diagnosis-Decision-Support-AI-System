import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

// 前端测试基建（T-GB-FE-01/02/04/05）：
// 与 Vite 同源，沿用 @ 别名，避免重复维护一套构建配置。
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    // 组件测试依赖 antd 的 CSS-in-JS，串行更稳定且体量很小
    pool: 'threads',
    // 上面的注释只说对了意图、没落成配置：满并行时（35 文件同时起）jsdom +
    // cornerstone 的环境准备会吃满 CPU（实测 setup 累计 500s+），大量用例在
    // 默认 5s testTimeout 边缘被打死 —— 失败集**每次不同**、单跑全绿，纯负载假红。
    // 收敛并发 + 放宽超时：用一点时间换确定性。
    maxWorkers: 4,
    minWorkers: 1,
    testTimeout: 20000,
    hookTimeout: 30000,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.d.ts',
        'src/test/**',
        'src/main.tsx',
        'src/**/__tests__/**',
        'src/components/dicom/cornerstone.ts',
      ],
    },
  },
});
