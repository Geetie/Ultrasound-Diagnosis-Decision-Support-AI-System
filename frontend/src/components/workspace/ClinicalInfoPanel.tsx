import { useState } from 'react';
// 值模型与工具函数在 ./clinicalInfo（react-refresh：组件文件只导出组件）
import {
  isClinicalInfoFilled,
  type ClinicalInfoValue,
} from './clinicalInfo';

/**
 * 临床信息录入面板（A.36 ⑤：医生临床信息录入闭环的前端半环）。
 *
 * 后端 DiagnosisRequest 本就有 clinical_text + patient_context 字段，此前无任何
 * 录入入口，结论恒为「信息不足」。
 *
 * 交互契约：
 * - 折叠态显示「已填 / 未填」徽标（默认沉默：没有内容就不展开占屏）；
 * - 结构化勾选直接映射安全网关的 patient_conditions（妊娠/造影剂过敏/抗凝）；
 * - 判读发起后 locked：判读已按当时的信息跑，中途改信息会造成「结论与输入
 *   不一致」—— 如实锁定，下一位患者自然重置；
 * - 不阻塞自动判读：装载即判读是既有口径，空信息由徽标提示而非弹窗拦截。
 */

const CONDITION_OPTIONS = ['妊娠', '造影剂过敏', '抗凝治疗'] as const;

export default function ClinicalInfoPanel({
  value,
  onChange,
  locked = false,
}: {
  value: ClinicalInfoValue;
  onChange: (v: ClinicalInfoValue) => void;
  /** 判读发起后锁定：结论必须与输入一致。 */
  locked?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const filled = isClinicalInfoFilled(value);

  const set = (patch: Partial<ClinicalInfoValue>) => onChange({ ...value, ...patch });

  const toggleCondition = (c: string) => {
    set({
      conditions: value.conditions.includes(c)
        ? value.conditions.filter((x) => x !== c)
        : [...value.conditions, c],
    });
  };

  return (
    <div className="ws-clinical" data-testid="clinical-panel">
      <button
        type="button"
        className="ws-clinical__head"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        disabled={locked}
        title={locked ? '判读已发起，临床信息已锁定' : undefined}
      >
        临床信息
        <span
          className={filled ? 'ws-clinical__badge ws-clinical__badge--ok' : 'ws-clinical__badge'}
        >
          {filled ? '已填' : '未填（建议补充，有助于结论）'}
        </span>
        {locked && <span className="ws-clinical__badge">已锁定</span>}
      </button>

      {open && (
        <div className="ws-clinical__body">
          <label className="ws-clinical__field">
            <span>症状 / 主诉</span>
            <textarea
              rows={2}
              value={value.symptoms}
              onChange={(e) => set({ symptoms: e.target.value })}
              placeholder="例：颈部肿物 2 月，吞咽异物感"
              disabled={locked}
            />
          </label>
          <label className="ws-clinical__field">
            <span>相关病史</span>
            <textarea
              rows={2}
              value={value.history}
              onChange={(e) => set({ history: e.target.value })}
              placeholder="例：高血压 5 年，规律服药"
              disabled={locked}
            />
          </label>
          <label className="ws-clinical__field">
            <span>化验摘要</span>
            <textarea
              rows={2}
              value={value.labs}
              onChange={(e) => set({ labs: e.target.value })}
              placeholder="例：TSH 2.1 mIU/L，正常范围"
              disabled={locked}
            />
          </label>
          <div className="ws-clinical__conditions" role="group" aria-label="安全网关条件">
            {CONDITION_OPTIONS.map((c) => (
              <label key={c} className="ws-clinical__check">
                <input
                  type="checkbox"
                  checked={value.conditions.includes(c)}
                  onChange={() => toggleCondition(c)}
                  disabled={locked}
                />
                {c}
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}