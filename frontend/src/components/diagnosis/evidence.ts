/**
 * 证据链的数据模型与收窄工具。
 *
 * 独立于组件文件：组件文件只导出组件（满足 react-refresh 约束），
 * 数据模型与纯函数放这里便于复用与单测。
 */

export interface ReasoningStep {
  step: string;
  description: string;
  confidence?: number;
}

export interface KnowledgeRef {
  source: string;
  version: string;
  relevance: number;
}

export interface EvidenceData {
  reasoning_path?: ReasoningStep[];
  knowledge_refs?: KnowledgeRef[];
  confidence_score?: number;
  cached?: boolean;
}

/**
 * 把接口返回的 `Record<string, unknown>` 收窄为组件所需结构。
 *
 * 后端 evidence 是宽松 JSON：只保留结构正确的字段，避免把任意值直接喂给
 * 渲染逻辑，也避免用 `as any` 绕过类型检查。
 */
export function normalizeEvidence(raw: Record<string, unknown> | undefined): EvidenceData {
  const source = raw ?? {};
  return {
    reasoning_path: Array.isArray(source.reasoning_path)
      ? (source.reasoning_path as ReasoningStep[]).filter(
          (step) => typeof step?.step === 'string'
        )
      : undefined,
    knowledge_refs: Array.isArray(source.knowledge_refs)
      ? (source.knowledge_refs as KnowledgeRef[]).filter(
          (ref) => typeof ref?.source === 'string'
        )
      : undefined,
    confidence_score:
      typeof source.confidence_score === 'number' ? source.confidence_score : undefined,
    cached: typeof source.cached === 'boolean' ? source.cached : undefined,
  };
}
