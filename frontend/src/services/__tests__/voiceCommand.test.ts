import { describe, expect, it } from 'vitest';
import { isCommandExecutable, parseVoiceCommand } from '../voiceCommand';

describe('parseVoiceCommand', () => {
  describe('发起诊断', () => {
    it('识别器官 + 诊断动词', () => {
      const parsed = parseVoiceCommand('开始甲状腺诊断');
      expect(parsed?.commandType).toBe('diagnosis_trigger');
      expect(parsed?.organ).toBe('thyroid');
      expect(parsed?.needsClarification).toBe(false);
      expect(isCommandExecutable(parsed)).toBe(true);
    });

    it('容忍口语简称与标点（肝脏/心脏）', () => {
      expect(parseVoiceCommand('帮我看一下肝脏')?.organ).toBe('liver');
      expect(parseVoiceCommand('进行心超检查，谢谢！')?.organ).toBe('cardiac');
    });

    it('未指明器官时标记 needsClarification 且不可执行', () => {
      const parsed = parseVoiceCommand('开始诊断');
      expect(parsed?.commandType).toBe('diagnosis_trigger');
      expect(parsed?.organ).toBeUndefined();
      expect(parsed?.needsClarification).toBe(true);
      expect(isCommandExecutable(parsed)).toBe(false);
    });
  });

  describe('档位切换', () => {
    it('切换全量模式', () => {
      const parsed = parseVoiceCommand('切换到全量模式');
      expect(parsed?.commandType).toBe('grade_change');
      expect(parsed?.gradeTarget).toBe('full');
      expect(isCommandExecutable(parsed)).toBe(true);
    });

    it('实时与标准档位', () => {
      expect(parseVoiceCommand('改成实时模式')?.gradeTarget).toBe('realtime');
      expect(parseVoiceCommand('切换到标准模式')?.gradeTarget).toBe('standard');
    });

    it('仅出现档位词但无切换意图时不误判为档位切换', () => {
      const parsed = parseVoiceCommand('这个结节回声不均');
      expect(parsed).toBeNull();
    });
  });

  describe('导航控制', () => {
    it('开始/暂停/继续/停止导航', () => {
      expect(parseVoiceCommand('开始导航')?.navAction).toBe('start');
      expect(parseVoiceCommand('暂停导航')?.navAction).toBe('pause');
      expect(parseVoiceCommand('继续导航')?.navAction).toBe('resume');
      expect(parseVoiceCommand('停止导航')?.navAction).toBe('stop');
    });

    it('导航优先于诊断（避免"开始导航"被当成诊断）', () => {
      const parsed = parseVoiceCommand('开始导航');
      expect(parsed?.commandType).toBe('navigation_control');
    });
  });

  describe('边界', () => {
    it('空文本返回 null', () => {
      expect(parseVoiceCommand('')).toBeNull();
      expect(parseVoiceCommand('   ')).toBeNull();
    });

    it('无法识别时返回 null（不猜测）', () => {
      expect(parseVoiceCommand('今天天气不错')).toBeNull();
    });

    it('保留原始转写文本便于确认层展示与审计', () => {
      const transcript = '开始 甲状腺 诊断。';
      expect(parseVoiceCommand(transcript)?.transcript).toBe(transcript);
    });

    it('记录命中的关键词用于可信度提示', () => {
      const parsed = parseVoiceCommand('开始甲状腺诊断');
      expect(parsed?.matchedKeywords).toContain('甲状腺');
      expect(parsed?.confidence).toBeGreaterThan(0.8);
    });
  });
});
