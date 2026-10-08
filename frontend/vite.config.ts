import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import fs from 'fs';

/**
 * 仅dev 生效：从仓库的 `dicom_storage/` 提供真实超声 DICOM。
 *
 * 为什么要它：设计包的 `impl_note` 写明影像区渐变只是占位，真实实现放DICOM 容器；
 * 而 AGENT_TASKS §九 明确标注「**叠加标注与真实组织纹理的对比度从未验证**」。
 * 要验这一条就必须拿真图跑，不能只在渐变上比对参考图。
 *
 * 安全性：① 只挂在 dev server 上，`vite build` 完全不经过它，产物里没有这个路由；
 *② 路径做 `path.resolve` 归一化后校验必须仍落在 dicom_storage 内，防目录穿越。
 */
function devDicomServer(): Plugin {
  const ROOT = path.resolve(__dirname, '..');
  const STORE = path.resolve(ROOT, 'dicom_storage');

  return {
    name: 'dev-dicom-server',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = (req.url || '').split('?')[0];
        if (!url.startsWith('/dev-dicom/')) return next();

        const rel = decodeURIComponent(url.slice('/dev-dicom/'.length));
        const abs = path.resolve(STORE, rel);

        // 目录穿越防护：解析后必须仍在 dicom_storage 内
        if (!abs.startsWith(STORE + path.sep)) {
          res.statusCode = 403;
          return res.end('forbidden');
        }
        if (!fs.existsSync(abs) || !fs.statSync(abs).isFile()) {
          res.statusCode = 404;
          return res.end('not found');
        }

        const stat = fs.statSync(abs);
        res.setHeader('Content-Type', 'application/dicom');
        res.setHeader('Content-Length', String(stat.size));
        res.setHeader('Accept-Ranges', 'bytes');
        fs.createReadStream(abs).pipe(res);
      });
    },
  };
}

export default defineConfig({
  /**
   * 部署形态（2026-10-04 交付）：产物挂到后端 `/ui`（见 app/main.py 的
   * StaticFiles 挂载），`/ui/` 之外还有运维仪表盘 `/dashboard` 与根 JSON `/`，
   * 因此 base **不能**是 `/`（会与后端路由抢占 `/`）。用环境变量兼顾 dev：
   * 本地 dev 不设 VITE_BASE → base='/'，开发体验不变。
   */
  base: process.env.VITE_BASE ?? '/',
  plugins: [react(), devDicomServer()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        /**
         * 后端地址**必须可配**，写死 localhost:8000 会让所有接口静默失败
         * （fetch 抛错 → 页面落进 catch → 渲染空列表，测试却因为 mock 照样全绿）。
         *
         * 真机后端在学校 GPU 服务器上（技能 remote-gpu-server-devtest §5：
         * 服务只绑 127.0.0.1，不许暴露到校园网），所以本机要用端口转发：
         *   ssh -N -L 8000:127.0.0.1:18000 student03@10.20.28.21
         * 然后 VITE_API_PROXY 不用改，默认值就能命中转发端口。
         * 要指向别处（例如本地 docker）时用环境变量覆盖：
         *   VITE_API_PROXY=http://127.0.0.1:15432 npm run dev
         */
        target: process.env.VITE_API_PROXY ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    target: 'es2020',
    cssCodeSplit: true,
    rollupOptions: {
      output: {
        /**
         * **不要配 manualChunks** —— 两种配法都在真实浏览器里坏过，这是全 history：
         *
         * ① 2026-10-05（A.27 P0-1）：把 cornerstone 拆成 vendor-cornerstone →
         *    `Class extends value undefined`，白屏。根因是 @cornerstonejs/* 内部
         *    循环依赖 + Rolldown 按 manualChunks 切 chunk 后跨 chunk 循环引用
         *    在 ESM 严格求值顺序下基类未初始化。当时修法：去掉 cornerstone 规则
         *    「交给 Rolldown 自动分割」——主 bundle 渲染恢复了，**但只修了一半**。
         * ② 2026-10-08（A.35 P0）：保留的 vendor-react/antd 规则把 cornerstone
         *    依赖图里的共享模块摊进 vendor chunk，动态导入的 cornerstone chunk
         *    求值时**同一个 TypeError** 复发 —— 但这次不白屏：影像层的三个调用方
         *    全部 catch 静默吞掉，表现为「影像区永远停在渐变占位」，且零 console
         *    错误（degradation 全链路静默，零静默守卫抓不到浏览器侧）。
         *    直到医生全流程走查录像才暴露。
         *
         * 结论：在 Rolldown（Vite 8）下，任何 manualChunks 都可能把 @cornerstonejs
         * 的强连通模块图切碎。唯一端到端验证过的配置是**完全不干预** ——
         * Rolldown 按入口/动态边界自动分组，cornerstone 动态导入图自成一个 chunk。
         * 代价：主 chunk 偏大（vendor 分离收益没了）。正确性优先于体积。
         *
         * 守卫：tests/unit/quality/test_frontend_render_guard.py 钉住本文件
         * 不得再出现 manualChunks（两种配法都坏过，别再试第三种）。
         */
      },
    },
  },
});
