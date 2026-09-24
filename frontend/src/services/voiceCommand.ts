/**
 * 语音指令解析（T-GB-FE-02）。
 *
 * 后端只接受**结构化**指令（`/api/v1/voice/confirm` 的 command_type +
 * organ/grade_target/nav_action），没有自然语言解析端点。因此在"转写 →
 * 结构化指令"之间由前端做一次**保守的关键词解析**，并且：
 *
 * 1. 解析结果**必须经医生确认**才提交（`confirmed: true`）；
 * 2. 解析不确定（缺参数、多义）时置 `needsClarification`，由确认弹层补全，
 *    绝不做"猜测即执行"——这是安全侧的硬约束。
 *
 * 本模块为纯函数，便于单元测试穷举表述。
 */

export type VoiceCommandType = 'diagnosis_trigger' | 'grade_change' | 'navigation_control';
export type TargetOrgan = 'thyroid' | 'liver' | 'cardiac';
export type GradeTarget = 'realtime' | 'standard' | 'full';
export type NavAction = 'start' | 'pause' | 'resume' | 'stop';

export interface ParsedVoiceCommand {
  commandType: VoiceCommandType;
  transcript: string;
  organ?: TargetOrgan;
  gradeTarget?: GradeTarget;
  navAction?: NavAction;
  /** 关键词命中强度（0-1）：用于在确认弹层提示"识别可信度" */
  confidence: number;
  matchedKeywords: string[];
  /** 缺少必填参数（如"开始诊断"未说器官）时需医生在确认层补全 */
  needsClarification: boolean;
}

export const COMMAND_LABELS: Record<VoiceCommandType, string> = {
  diagnosis_trigger: '发起诊断',
  grade_change: '切换响应档位',
  navigation_control: '导航控制',
};

export const ORGAN_LABELS: Record<TargetOrgan, string> = {
  thyroid: '甲状腺',
  liver: '肝脏',
  cardiac: '心脏',
};

export const GRADE_LABELS: Record<GradeTarget, string> = {
  realtime: '实时模式',
  standard: '标准模式',
  full: '全量模式',
};

export const NAV_ACTION_LABELS: Record<NavAction, string> = {
  start: '开始导航',
  pause: '暂停导航',
  resume: '继续导航',
  stop: '停止导航',
};

// 器官同义词（含口语简称与常见错写）
const ORGAN_KEYWORDS: Array<[TargetOrgan, string[]]> = [
  ['thyroid', ['甲状腺', '甲状', '颈部结节', '甲瘤']],
  ['liver', ['肝脏', '肝内', '肝部', '肝']],
  ['cardiac', ['心脏', '心动图', '心超', '心']],
];

const GRADE_KEYWORDS: Array<[GradeTarget, string[]]> = [
  ['full', ['全量', '完整模式', '详细模式', '深度分析']],
  ['realtime', ['实时', '快速模式', '即时']],
  ['standard', ['标准模式', '标准档', '常规模式']],
];

const NAV_KEYWORDS: Array<[NavAction, string[]]> = [
  ['start', ['开始导航', '启动导航', '打开导航']],
  ['pause', ['暂停导航', '暂停叠加', '冻结导航']],
  ['resume', ['继续导航', '恢复导航']],
  ['stop', ['停止导航', '关闭导航', '结束导航']],
];

// 触发动词（用于判定"这是一条指令"）
const ACTION_VERBS = ['开始', '发起', '执行', '进行', '启动', '切换', '改成', '改为', '调到', '暂停', '继续', '停止', '关闭', '打开', '看一下', '做'];
const DIAGNOSIS_NOUNS = ['诊断', '检查', '分析', '评估', '扫查'];

function normalize(transcript: string): string {
  // 去掉空白与常见标点，容忍语音转写的间断
  return transcript.replace(/[\s,，。、；;：:！!？?~·]/g, '');
}

function matchFirst<T>(text: string, table: Array<[T, string[]]>): { value: T; keyword: string } | null {
  for (const [value, keywords] of table) {
    for (const keyword of keywords) {
      if (text.includes(keyword)) {
        return { value, keyword };
      }
    }
  }
  return null;
}

function matchAny(text: string, words: string[]): string | null {
  for (const word of words) {
    if (text.includes(word)) return word;
  }
  return null;
}

/**
 * 解析一段语音转写文本。
 *
 * @returns 结构化指令；完全无法识别时返回 `null`（调用方应提示重说）。
 */
export function parseVoiceCommand(transcript: string): ParsedVoiceCommand | null {
  const raw = transcript ?? '';
  const text = normalize(raw);
  if (!text) return null;

  const matched: string[] = [];
  const organHit = matchFirst(text, ORGAN_KEYWORDS);
  const gradeHit = matchFirst(text, GRADE_KEYWORDS);
  const navHit = matchFirst(text, NAV_KEYWORDS);
  const verbHit = matchAny(text, ACTION_VERBS);
  const diagnosisNounHit = matchAny(text, DIAGNOSIS_NOUNS);

  // 1) 导航优先级最高：出现"导航 + 动作"即视为导航控制
  if (navHit) {
    matched.push(navHit.keyword);
    const confidence = navHit.keyword.includes('导航') ? 0.95 : 0.7;
    return {
      commandType: 'navigation_control',
      transcript: raw,
      navAction: navHit.value,
      confidence,
      matchedKeywords: matched,
      needsClarification: false,
    };
  }

  // 2) 档位切换：需要"切换类动词 + 档位词"
  if (gradeHit && (verbHit || text.includes('模式') || text.includes('档'))) {
    matched.push(gradeHit.keyword);
    if (verbHit) matched.push(verbHit);
    return {
      commandType: 'grade_change',
      transcript: raw,
      gradeTarget: gradeHit.value,
      confidence: 0.85,
      matchedKeywords: matched,
      needsClarification: false,
    };
  }

  // 3) 发起诊断：动词 + 诊断类名词（或只说器官 + 诊断名词）
  if (diagnosisNounHit || (verbHit && organHit)) {
    if (organHit) {
      matched.push(organHit.keyword);
    }
    if (diagnosisNounHit) {
      matched.push(diagnosisNounHit);
    }
    if (verbHit) matched.push(verbHit);

    return {
      commandType: 'diagnosis_trigger',
      transcript: raw,
      organ: organHit?.value,
      confidence: organHit ? 0.9 : 0.6,
      matchedKeywords: matched,
      // 未指明器官时不能猜：由医生在确认层选择
      needsClarification: !organHit,
    };
  }

  return null;
}

/** 供确认弹层在医生手工修正后重新评估"是否可提交"。 */
export function isCommandExecutable(command: ParsedVoiceCommand | null): boolean {
  if (!command) return false;
  if (command.commandType === 'diagnosis_trigger') return Boolean(command.organ);
  if (command.commandType === 'grade_change') return Boolean(command.gradeTarget);
  if (command.commandType === 'navigation_control') return Boolean(command.navAction);
  return false;
}
