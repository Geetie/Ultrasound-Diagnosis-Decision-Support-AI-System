/**
 * 时间显示工具（单一份实现）。
 *
 * 为什么单独开文件：`toLocalTime` / `fmtTime` 曾在多个页面各写一份（列表页、历史页、
 * 影像区角标各一套），格式一旦要改就得改多处。这里统一成 `formatLocalTimestamp`，
 * 新代码一律用它；旧页面按需逐步替换（不做一次性大改，避免无关回归）。
 */

/** ISO 时间串 → `YYYY-MM-DD HH:mm:ss`（本地时区）；解析失败原样返回，不显示 Invalid Date。 */
export function formatLocalTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, '0');
  return (
    `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  );
}
