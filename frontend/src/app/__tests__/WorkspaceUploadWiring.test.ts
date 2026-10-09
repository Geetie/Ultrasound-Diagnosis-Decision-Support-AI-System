import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

/**
 * 「影像 → 判读」主链路的接线守卫（源码级）。
 *
 * 为什么需要源码级断言：这个链路由**四个文件**共同构成 ——
 * 工作列表产出 session_id、路由层读会话与部位、hook 上传拿 image_id、
 * 路由层把 image_id 交给 useWorkspaceFlow.start。
 * 每一段单独看都正常，合起来断掉时页面上只表现为"点了没反应"，
 * 渲染级单测抓不到（2026-10-05 真浏览器走查就是被这个坑了一整轮：
 * `flow.start` 从未被任何地方调用过，判读主链根本没有起点）。
 *
 * 这里只钉契约（谁产出、谁消费），不锁具体实现写法 —— 断言写死实现细节就会在
 * 正常重构时误报，那种守卫比没有守卫更坏。
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

describe('影像 → 判读 链路接线', () => {
  it('工作列表的行内动作把 session_id 带进工作区', () => {
    const source = readSource('src/pages/WorklistPage.tsx');

    // 两处（进行中「继续」/ 今日待处理「查看」）都要带。少一处就有一半入口是死的。
    const links = source.match(/session_id=\$\{encodeURIComponent\(item\.session_id\)\}/g) ?? [];
    expect(links.length).toBe(2);
  });

  it('工作区路由读 session_id 并据此取检查部位', () => {
    const source = readSource('src/app/routes.tsx');

    expect(source).toContain("params.get('session_id')");
    expect(source).toContain('getSession(sessionId)');
    // 部位必须来自会话，不能硬编码 —— 判读要按部位走不同管线
    expect(source).toContain('target_organs?.[0]');
  });

  it('上传成功后用后端 image_id 发起判读（flow.start 真的有调用方）', () => {
    const source = readSource('src/app/routes.tsx');

    expect(source).toContain('useDicomUpload');
    expect(source).toMatch(/startFlow\(\{\s*sessionId/);
    // image_ids 必须是上传回调给的 image_id，不是本地 fileManager 的 id
    // 多图分期①：imageIds 数组直传（多文件顺序上传聚合）
    expect(source).toMatch(/imageIds,/);
  });

  it('判读用的 image_id 来自 upload 端点的返回，不是浏览器本地 id', () => {
    const imaging = readSource('src/services/imagingService.ts');
    const hook = readSource('src/hooks/useDicomUpload.ts');

    // 上行端点与字段名对得上后端（app/api/v1/imaging.py 的 upload_dicom）
    expect(imaging).toContain("'/imaging/dicom/upload'");
    // 回调必须吃 upload 端点返回的 image_id，而不是本地 fileManager 的那个
    // 多图：回调聚合整批 ids
    expect(hook).toMatch(/onUploaded\?\.\(ids\)/);
  });

  it('工作区页面把上传插槽与导航出口渲染出来', () => {
    const page = readSource('src/pages/WorkspacePage.tsx');

    expect(page).toContain('uploadSlot');
    expect(page).toContain('navItems');
    // 插槽必须真的传进结论区，否则控件写了但永远不渲染
    expect(page).toMatch(/WorkspaceConclusionBody[\s\S]*uploadSlot=\{uploadSlot\}/);
  });

  it('影像区高度与 900 骨架不受上传控件影响（控件只落在结论区）', () => {
    const page = readSource('src/pages/WorkspacePage.tsx');
    const region = readSource('src/components/workspace/WorkspaceImageRegion.tsx');

    // 上传入口不得进影像区 —— 影像区恒定 600 是硬规则
    expect(region).not.toContain('WorkspaceImageUpload');
    expect(page).not.toMatch(/WorkspaceImageRegion[\s\S]{0,400}uploadSlot/);
  });
});
