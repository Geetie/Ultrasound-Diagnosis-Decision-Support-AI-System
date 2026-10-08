/**
 * xmlbuilder2 的显式 stub。
 *
 * 为什么存在：@cornerstonejs/core → @kitware/vtk.js → xmlbuilder2@4，而
 * xmlbuilder2 的顶层有循环依赖（`class XMLBuilderImpl extends …` 在模块求值期
 * 撞上未初始化的绑定）。**任何生产打包器（Vite 8/Rolldown、Vite 6/Rollup）下，
 * 包含它的 chunk 求值都直接抛 `Class extends value undefined`** —— 而这个 chunk
 * 恰好是整个 cornerstone，一坏全坏。dev（原生 ESM，按依赖序求值）不触发，所以
 * `?dicom=` 后门一直"看着是好的"。
 *
 * vtk.js 只有 `IO/XML/XMLReader.js` / `XMLWriter.js` 两个模块 import 它的
 * `{ create }`（vtk 的 XML 数据格式读写），cornerstone 的 DICOM 解码路径
 * **完全不经过** vtk XML IO —— 所以 stub 成显式失败即可把它从依赖图里摘掉。
 * 若将来真的需要 vtk XML IO：换用非循环的 XML 库或按需动态加载，别直接解 stub。
 */
export function create(): never {
  throw new Error(
    'xmlbuilder2 已被 stub（生产打包兼容，见 src/stubs/xmlbuilder2-stub.ts 注释）——vtk XML IO 在本应用中未使用',
  );
}

/** 兼容潜在的具名导入（当前依赖图只用到 create）。 */
export const XMLBuilderImpl = undefined;
export const XMLBuilderCBImpl = undefined;
export default { create };
