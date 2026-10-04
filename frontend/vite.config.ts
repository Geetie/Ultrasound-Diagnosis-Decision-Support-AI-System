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
         * **不要**把 cornerstone 单独拆成 chunk。
         *
         * 2026-10-05 真实浏览器走查抓到白屏：`TypeError: Class extends value
         * undefined is not a constructor or null`，堆栈全部落在
         * `vendor-cornerstone-*.js`。根因是 @cornerstonejs/* 5.11.3 内部存在
         * 循环依赖，Vite 8（Rolldown 打包器）按 `manualChunks` 强行把它切成一个
         * 独立 chunk 后，跨 chunk 的循环引用在 ESM 严格求值顺序下**基类尚未
         * 初始化就执行 `extends`**，整个 vendor chunk 抛错 → React 从未挂载，
         * `#root` 子节点为 0，白屏。
         *
         * 交给 Rolldown 自动分割即可（它会把相互依赖的模块放进同一 chunk）。
         * 代价是主 chunk 变大（约 +3.5MB），但正确性优先于体积。
         */
        manualChunks(id) {
          if (id.includes('node_modules')) {
            if (id.includes('react') || id.includes('react-dom') || id.includes('react-router')) {
              return 'vendor-react';
            }
            if (id.includes('antd') || id.includes('@ant-design')) {
              return 'vendor-antd';
            }
            if (id.includes('echarts')) {
              return 'vendor-echarts';
            }
          }
        },
      },
    },
  },
});
