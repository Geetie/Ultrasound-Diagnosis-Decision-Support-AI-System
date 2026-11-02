import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

/**
 * A.109 / N8：多器官首判必须带上医生这次的临床事实（源码级接线守卫）。
 *
 * 缺口实测：后端 `MultiOrganDiagnosisRequest` 早已支持
 * clinical_text / patient_context / image_ids（schemas.py:846-849），但前端
 * 三个文件里都没声明、没转换、没发送 → 本会话若还没有单器官请求当 donor，
 * 医生填的临床信息与勾选的禁忌条件进不了 SG 判据（缺数据走保守分支，
 * 或反过来过度拦截）。这条链断掉时页面只表现为"勾了没反应"。
 *
 * 与 PatientConditionsWiring.test.ts 分工：那边钉单器官链路，这边钉多器官。
 */
function readSource(relative: string): string {
  const candidates = [
    resolve(process.cwd(), relative),
    resolve(process.cwd(), 'frontend', relative),
  ];
  const found = candidates.find((path) => existsSync(path));
  if (!found) throw new Error(`未找到 ${relative}`);
  return readFileSync(found, 'utf-8');
}

describe('多器官请求体携带临床事实（N8）', () => {
  it('service 层类型声明了三字段', () => {
    const svc = readSource('src/services/diagnosisService.ts');
    expect(svc).toMatch(/requestMultiOrganDiagnosis\(data:\s*\{[\s\S]*?clinical_text\?:\s*string/);
    expect(svc).toMatch(/requestMultiOrganDiagnosis\(data:\s*\{[\s\S]*?patient_context\?:\s*\{/);
    expect(svc).toMatch(/requestMultiOrganDiagnosis\(data:\s*\{[\s\S]*?image_ids\?:\s*string\[\]/);
  });

  it('hook 的多器官入参接受三字段', () => {
    const hook = readSource('src/hooks/useWorkspaceFlow.ts');
    expect(hook).toMatch(/startMultiOrgan:\s*\(input:\s*\{[\s\S]*?clinicalText\?:\s*string/);
    expect(hook).toMatch(/startMultiOrgan:\s*\(input:\s*\{[\s\S]*?patientContext\?:\s*\{/);
    expect(hook).toMatch(/startMultiOrgan:\s*\(input:\s*\{[\s\S]*?imageIds\?:\s*string\[\]/);
  });

  it('hook 真的把它们放进请求体（只加类型没接线即红）', () => {
    const hook = readSource('src/hooks/useWorkspaceFlow.ts');
    expect(hook).toContain('clinical_text: input.clinicalText || undefined');
    expect(hook).toContain('patient_context: input.patientContext');
    expect(hook).toContain('image_ids: input.imageIds');
  });

  it('路由层多器官分支转换禁忌码后上送（与单器官同源）', () => {
    const routes = readSource('src/app/routes.tsx');
    const block = routes.slice(
      routes.indexOf('await startMultiOrganFlow({'),
      routes.indexOf('return;', routes.indexOf('await startMultiOrganFlow({'))
    );
    expect(block).toContain('conditions: toConditionCodes(clinical.conditions)');
    expect(block).toContain('clinicalText: toClinicalText(clinical)');
    expect(block).toContain('imageIds,');
  });
});
