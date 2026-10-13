/**
 * 统一错误上报（A.60「零静默」约定层 · 前端）。
 *
 * 为什么需要
 * ----------
 * A.58 立的本地规则 `local/no-silent-catch` 要求每个 `catch` 至少具备一条「非静默证据」
 * （记录 / 继续抛出 / 用户可见降级 / `// silent-ok: 理由`）。实测盘点 44 处存量时发现：
 * 全仓**没有任何统一上报出口** —— 事实标准是散落的 `console.error('[模块] 中文说明', err)`
 * （全仓仅 3 个文件命中）。散落写法的代价：
 *   · 没法统一挂采集（将来接 Sentry / 自建端点要改 N 处）；
 *   · 没法定作用域与文案规范，同一条失败在不同文件写法各异；
 *   · 规则只能认 `console.error`（写 `logger.x` 这类自定义封装必须逐处登记）。
 *
 * 边界（刻意保守）
 * ----------------
 * · **只做记录，不做展示**。用户可见降级是各业务自己的事（`setError` / `message.error` /
 *   `data-region="*-error"` 渲染出口），本模块不碰 UI —— 影像层的教训就是"上报有了、
 *   医生说不知道"（A.35 空白活 3 天）。
 * · **上报通道自身异常不得影响业务**：sink 抛错就地吞掉（`silent-ok`，见下）。
 * · 不引第三方依赖（Sentry 之类将来经 `registerErrorSink` 注入即可）。
 */

/** 事故域：用模块名（`cornerstone3d` / `workspace` / `ws-nav` …），便于日志聚合与 grep。 */
export type ErrorScope = string;

export interface ErrorRecord {
  /** `error` = 影响功能；`warn` = 可降级（不影响主流程，但要留痕）。 */
  level: 'error' | 'warn';
  scope: ErrorScope;
  /** 中文短句：**说清"什么没成"**，不写英文技术串（医生可能间接看到日志截图）。 */
  message: string;
  detail?: unknown;
}

type ErrorSink = (record: ErrorRecord) => void;

const sinks: ErrorSink[] = [];

/**
 * 注册上报通道（返回注销函数）。
 *
 * 典型用法：将来接 Sentry / 自建采集端时在应用入口注册一次：
 * `registerErrorSink((r) => Sentry.captureMessage(\`[${r.scope}] ${r.message}\`, r.level));`
 */
export function registerErrorSink(sink: ErrorSink): () => void {
  sinks.push(sink);
  return () => {
    const i = sinks.indexOf(sink);
    if (i >= 0) sinks.splice(i, 1);
  };
}

function emit(record: ErrorRecord): void {
  const line = `[${record.scope}] ${record.message}`;
  if (record.level === 'error') console.error(line, record.detail ?? '');
  else console.warn(line, record.detail ?? '');
  for (const sink of sinks) {
    try {
      sink(record);
    } catch {
      // silent-ok: 上报通道自身异常不得反向影响业务（否则"上报"会变成新的故障源）
    }
  }
}

/** 记录一次**功能失败**（医生可能受影响）。 */
export function reportError(scope: ErrorScope, message: string, detail?: unknown): void {
  emit({ level: 'error', scope, message, detail });
}

/** 记录一次**可降级失败**（功能缺失但有兜底，或属幂等清理/能力探测）。 */
export function reportWarn(scope: ErrorScope, message: string, detail?: unknown): void {
  emit({ level: 'warn', scope, message, detail });
}
