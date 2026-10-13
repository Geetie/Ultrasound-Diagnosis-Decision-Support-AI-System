/**
 * 本地 ESLint 规则：禁止静默 catch（A.58「零静默」约定层，依据 20 号研究设计）。
 *
 * 为什么要这条规则
 * ----------------
 * 20 号把"静默失败"定义为「内部异常被吞掉/降级为占位，而医生界面看起来一切正常」，
 * 事故史里最典型的一次是 **A.35**：影像层三处 catch 链吞掉 chunk 求值失败，
 * 页面**空白活了 3 天**，而所有门禁都是绿的。约定层的目标就是让"新写的 catch"
 * 必须自己交代清楚：出错时**要么留痕、要么继续抛、要么给出用户可见降级**。
 *
 * 判据（"非静默证据"，任一即可）
 * ------------------------------
 *   a) `throw`                        —— 异常继续传播，不静默；
 *   b) **记录**：`console.error` / `console.warn` / `reportError(...)` /
 *      `logError(...)` / `captureException(...)` / `Sentry.captureException(...)`；
 *   c) **用户可见降级**：antd `message.error|warning|info`、`notification.*`、
 *      `toast.*`、`alert(...)`、React state setter（`set` + 大写字母，如
 *      `setError(...)`）、把失败写进名字含 error/warn/fail/issue/problem 的变量；
 *   d) 豁免标记 `// silent-ok: 理由`（理由必填，≥4 字符，便于审计与 grep）。
 *      位置：写在 **`catch` 同行**，或写在 **catch 块内**（含空块里的注释行）。
 *      A.60 放宽：`} catch {` 行常已很长，把理由写进块内更易读；两种写法都认。
 *
 * 与 20 号原文的**有意偏离**：原文写"catch 必须带 `console.error` **+** 用户可见降级"。
 * 实测按"二者皆需"会让 `catch (e) { logger…; throw e }` 这类正当写法判红，
 * 规则会立刻被绕过（历史教训：守卫误报一次，之后就没人再信它）。
 * 本实现要求"**不得两者皆无**"，并把 baseline 锁在存量上——只挡新增。
 */
/**
 * 豁免标记：`// silent-ok: 理由` 或 `/* silent-ok: 理由 *\/`（A.60 起两种都认）。
 *
 * 为什么要两种：单行写法 `try { x(); } catch { /* silent-ok: 幂等 *\/ }` 只能用块注释，
 * 而块注释里的标记此前不被识别 → 那两处明明写了理由却仍判红（A.60 实测：DicomViewer 8 -> 2）。
 */
const WAIVER_RE = /(?:\/\/|\/\*)\s*silent-ok:\s*([^\n]*)/;

const LOG_CALLEES = new Set([
  'reportError',
  'logError',
  'captureException',
  'captureMessage',
]);

const DEGRADE_CALLEES = new Set(['alert']);

const DEGRADE_OBJECTS = new Set(['message', 'notification', 'toast', 'Modal', 'sentry']);

const FAILURE_WORDS = ['error', 'warn', 'fail', 'issue', 'problem'];

function nameMentionsFailure(name) {
  const low = String(name).toLowerCase();
  return FAILURE_WORDS.some((w) => low.includes(w));
}

function calleeName(node) {
  const callee = node.callee;
  if (!callee) return '';
  if (callee.type === 'Identifier') return callee.name;
  if (callee.type === 'MemberExpression' && callee.property) {
    return callee.property.name || callee.property.value || '';
  }
  return '';
}

function objectName(node) {
  const callee = node.callee;
  if (callee && callee.type === 'MemberExpression' && callee.object) {
    if (callee.object.type === 'Identifier') return callee.object.name;
    if (callee.object.type === 'MemberExpression') return callee.object.property.name || '';
  }
  return '';
}

/** 这条语句是否构成"记录"。 */
function isLogCall(node) {
  const name = calleeName(node);
  if (name === 'error' || name === 'warn') {
    const obj = objectName(node);
    if (obj === 'console') return true;
  }
  if (LOG_CALLEES.has(name)) return true;
  // Sentry.captureException(...) / logger.error(...) / log.error(...)
  if (nameMentionsFailure(name) && ['logger', 'log', '_log', 'Sentry'].includes(objectName(node))) {
    return true;
  }
  return false;
}

/** 这条语句是否构成"用户可见降级"。 */
function isDegradeCall(node) {
  const name = calleeName(node);
  const obj = objectName(node);
  if (DEGRADE_CALLEES.has(name)) return true;
  if (DEGRADE_OBJECTS.has(obj)) return true;
  // React state setter：set + 大写字母
  if (/^set[A-Z]/.test(name)) return true;
  if (nameMentionsFailure(name)) return true;
  return false;
}

function isEvidence(node) {
  let found = false;
  const visit = (n) => {
    if (found || !n || typeof n.type !== 'string') return;
    if (n.type === 'ThrowStatement') {
      found = true;
      return;
    }
    if (n.type === 'CallExpression') {
      if (isLogCall(n) || isDegradeCall(n)) {
        found = true;
        return;
      }
    }
    // 把失败写进名字含 error/warn/fail 的变量或属性（errors.push / self.lastError = …）
    if (n.type === 'AssignmentExpression') {
      const target = n.left;
      if (target && target.type === 'Identifier' && nameMentionsFailure(target.name)) {
        found = true;
        return;
      }
      if (target && target.type === 'MemberExpression') {
        const prop = target.property;
        if (prop && nameMentionsFailure(prop.name || prop.value || '')) {
          found = true;
          return;
        }
      }
    }
    for (const key of Object.keys(n)) {
      if (key === 'parent') continue;
      const child = n[key];
      if (Array.isArray(child)) child.forEach(visit);
      else if (child && typeof child.type === 'string') visit(child);
    }
  };
  visit(node);
  return found;
}

/** @type {import('eslint').Rule.RuleModule} */
const noSilentCatch = {
  meta: {
    type: 'problem',
    docs: {
      description: '禁止静默 catch：必须有记录 / 继续抛出 / 用户可见降级（或显式 silent-ok 豁免）',
    },
    messages: {
      silent:
        '静默 catch：处理体里既没有记录（console.error / reportError / logger.error …）、' +
        '没有继续 throw，也没有用户可见降级（setXxx / message.error / toast …）。' +
        '医生界面会「看起来一切正常」（A.35 影像空白活 3 天即此形态）；' +
        '若确属有意的静默分支，请写 `// silent-ok: 理由`。',
      silentPromise:
        '静默 promise catch：`.catch(() => undefined)` 与裸 `catch {}` 语义等价（异常被吞、' +
        '界面照常）。要么记录（console.error）、要么给用户可见降级、要么写 `// silent-ok: 理由`；' +
        'A.58 反证腿实测过：影像字节取回失败被 `.catch(() => null)` 吞掉 → 医生看到的是' +
        '带假标注框的占位底板。',
    },
    schema: [],
  },

  create(context) {
    const sourceCode = context.sourceCode ?? context.getSourceCode();
    const lines = sourceCode.lines ?? sourceCode.getText().split('\n');

    /** 理由是否够实（≥4 字符，且去掉块注释尾部 `*\/`）。 */
    const reasonOk = (raw) => String(raw || '').replace(/\*\/.*$/, '').trim().length >= 4;

    const waived = (line) => {
      const m = WAIVER_RE.exec(lines[line - 1] || '');
      return Boolean(m && reasonOk(m[1]));
    };

    /**
     * 豁免判定（A.60）：标记写在 `catch` 同行，或写在 **catch 块内**（含空块里的注释行）。
     *
     * 只看块内文本而不是逐行扫描——空块 `catch { /* 注释 *\/ }` 没有语句行，
     * 逐行法会漏掉这类（而"空块 + 理由注释"正是最常见的正当静默形态）。
     */
    const waivedInBlock = (blockNode) => {
      if (!blockNode || !blockNode.range) return false;
      const m = WAIVER_RE.exec(sourceCode.getText(blockNode));
      return Boolean(m && reasonOk(m[1]));
    };

    /** 简洁箭头体是否"空"：`() => undefined` / `() => null` / `() => {}`。 */
    const isEmptyArrowBody = (body) => {
      if (!body) return true;
      if (body.type === 'BlockStatement') return body.body.length === 0;
      if (body.type === 'Identifier') return body.name === 'undefined';
      return body.type === 'Literal' && (body.value === null || body.value === undefined);
    };

    return {
      CatchClause(node) {
        // 显式豁免（理由必填）：`catch` 同行，或 catch 块内
        if (waived(node.loc.start.line) || waivedInBlock(node.body)) return;
        const body = node.body && node.body.body ? node.body.body : [];
        if (body.length === 0 || !body.some(isEvidence)) {
          context.report({ node, messageId: 'silent' });
        }
      },

      // promise 链上的 `.catch(...)` 与 `try/catch` 语义等价 —— 只查 `catch` 子句会漏掉
      // 一半静默失败（A.58 实测：影像字节取回失败正是被 `.catch(() => null)` 吞掉的）。
      CallExpression(node) {
        const callee = node.callee;
        if (!callee || callee.type !== 'MemberExpression') return;
        if (!callee.property || callee.property.name !== 'catch') return;
        const handler = node.arguments[0];
        if (!handler || (handler.type !== 'ArrowFunctionExpression' && handler.type !== 'FunctionExpression')) {
          return;
        }
        const body = handler.body;
        // 豁免：`.catch(` 同行，或 handler 块内（A.60 与 try/catch 同口径）
        if (waived(callee.property.loc.start.line)) return;
        if (body.type === 'BlockStatement' && waivedInBlock(body)) return;
        const silent =
          (body.type === 'BlockStatement' && (body.body.length === 0 || !body.body.some(isEvidence))) ||
          (body.type !== 'BlockStatement' && isEmptyArrowBody(body));
        if (silent) context.report({ node: callee.property, messageId: 'silentPromise' });
      },
    };
  },
};

export default noSilentCatch;
