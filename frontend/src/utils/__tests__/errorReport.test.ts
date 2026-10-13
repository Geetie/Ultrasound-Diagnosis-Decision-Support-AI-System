import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { registerErrorSink, reportError, reportWarn } from '../errorReport';

/**
 * 统一上报出口（A.60）。
 *
 * 两条纪律在这里钉住：
 * ① 记录 + 采集双通道（`console` 给现场排查，sink 给将来接 Sentry/自建端点）；
 * ② **上报通道自身异常不得影响业务** —— sink 抛错被吞掉（否则"上报"会变成新的故障源）。
 */
describe('utils/errorReport', () => {
  let errorSpy: ReturnType<typeof vi.spyOn>;
  let warnSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  });

  afterEach(() => {
    errorSpy.mockRestore();
    warnSpy.mockRestore();
  });

  it('reportError 走 console.error 并把作用域与文案带进同一行', () => {
    reportError('cornerstone3d', '影像显示失败', new Error('boom'));

    expect(errorSpy).toHaveBeenCalledTimes(1);
    expect(String(errorSpy.mock.calls[0][0])).toContain('[cornerstone3d]');
    expect(String(errorSpy.mock.calls[0][0])).toContain('影像显示失败');
  });

  it('reportWarn 走 console.warn（可降级失败不得占用 error 级）', () => {
    reportWarn('ws-nav', '报文无法解析');

    expect(warnSpy).toHaveBeenCalledTimes(1);
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('注册的 sink 收到结构化记录（作用域 / 文案 / 级别）', () => {
    const seen: Array<{ level: string; scope: string; message: string }> = [];
    const off = registerErrorSink((r) => seen.push(r));

    reportError('consent', '同意状态获取失败');
    reportWarn('dicom-channel', '已丢弃无效帧');
    off();
    reportError('consent', '注销后不再上报');

    expect(seen).toEqual([
      { level: 'error', scope: 'consent', message: '同意状态获取失败', detail: undefined },
      { level: 'warn', scope: 'dicom-channel', message: '已丢弃无效帧', detail: undefined },
    ]);
  });

  it('sink 抛异常不影响业务（且不影响其它 sink）', () => {
    const ok = vi.fn();
    const offBad = registerErrorSink(() => {
      throw new Error('采集端挂了');
    });
    const offOk = registerErrorSink(ok);

    expect(() => reportError('workspace', '标注未保存')).not.toThrow();
    expect(ok).toHaveBeenCalledTimes(1);

    offBad();
    offOk();
  });
});
