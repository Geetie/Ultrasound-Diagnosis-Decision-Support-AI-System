import type { ConsentStatus } from '@/types/api';

export function isConsentGranted(consentStatus: ConsentStatus['consent_status'] | null): boolean {
  return consentStatus === 'granted';
}

export function isPureReportMode(consentStatus: ConsentStatus['consent_status'] | null): boolean {
  return consentStatus === 'denied' || consentStatus === 'withdrawn' || consentStatus === null;
}

export function getConsentDisplayText(consentStatus: ConsentStatus['consent_status'] | null): string {
  switch (consentStatus) {
    case 'granted':
      return '已签署AI辅助诊断知情同意';
    case 'denied':
      return '患者未签署AI辅助诊断知情同意，系统运行于纯报告模式';
    case 'withdrawn':
      return '患者已撤回AI辅助诊断知情同意，系统运行于纯报告模式';
    case 'pending':
      return '知情同意审批中';
    default:
      return '未获取知情同意状态';
  }
}
