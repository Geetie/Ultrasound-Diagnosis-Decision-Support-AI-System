import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

/**
 * 诊断主链路的接线守卫（源码级）。
 *
 * 起因：`DiagnosisPage` 里 `requestId` 曾被写成 `useState<string | null>(null)` 且**没有 setter**，
 * 于是 `handleRetry` 永远提前返回、轮询入口是死代码；同时提交侧
 * （`ExaminationPage` / `AppLayout`）既不保存 request_id 也不带参数跳转。
 * 结果是**医生提交诊断后永远看不到 AI 结论**——而这类"链路两端各自看都正常、
 * 合起来断掉"的问题，单测渲染很难覆盖，用源码级断言反而稳定。
 *
 * 这里只钉住"链路契约"（谁产出 request_id、谁消费），不锁具体实现写法。
 */
function readSource(relative: string): string {
  const candidates = [
    resolve(process.cwd(), relative),
    resolve(process.cwd(), 'frontend', relative),
  ];
  const found = candidates.find((path) => existsSync(path));
  if (!found) {
    throw new Error(`未找到 ${relative}，已尝试：${candidates.join(', ')}`);
  }
  return readFileSync(found, 'utf-8');
}

describe('诊断主链路接线（防"提交后拿不到结果"回归）', () => {
  it('检查主界面提交成功后把 request_id 带进结果页', () => {
    const source = readSource('src/pages/ExaminationPage.tsx');

    expect(source).toContain('useNavigate');
    expect(source).toMatch(/navigate\(\s*`\/diagnosis\?request_id=/);
  });

  it('语音触发诊断时同样带上 request_id', () => {
    const source = readSource('src/components/layout/AppLayout.tsx');

    expect(source).toMatch(/navigate\(\s*`\/diagnosis\?request_id=/);
  });

  it('结果页从 URL 读取 request_id 并据此轮询', () => {
    const source = readSource('src/pages/DiagnosisPage.tsx');

    expect(source).toContain('useSearchParams');
    expect(source).toContain("searchParams.get('request_id')");
    // 轮询必须由该值驱动（此前是恒为 null 的 useState，轮询入口不可达）
    expect(source).toMatch(/startPolling\(requestId\)/);
    expect(source).not.toContain('const [requestId] = useState<string | null>(null)');
  });

  it('结果页提供报告入口，且报告页与菜单均已接线', () => {
    const diagnosis = readSource('src/pages/DiagnosisPage.tsx');
    const routes = readSource('src/app/routes.tsx');
    const sidebar = readSource('src/components/layout/Sidebar.tsx');

    expect(diagnosis).toMatch(/navigate\(\s*`\/report\?request_id=/);
    expect(routes).toContain("path=\"/report\"");
    expect(routes).toContain("import('@/pages/ReportPage')");
    expect(sidebar).toContain("key: '/report'");
  });
});
