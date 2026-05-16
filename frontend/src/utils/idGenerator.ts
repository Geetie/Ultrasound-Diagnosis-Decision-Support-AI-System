function generatePrefixedId(prefix: string): string {
  const uuid = crypto.randomUUID().replace(/-/g, '').substring(0, 12);
  return `${prefix}_${Date.now()}_${uuid}`;
}

export function generateRequestId(organ: string): string {
  return generatePrefixedId(`req_${organ}`);
}

export function generateIdempotencyKey(organ: string): string {
  return generatePrefixedId(`idem_${organ}`);
}

export function generateFeedbackId(): string {
  return generatePrefixedId('fb');
}

export function generateReportId(): string {
  return generatePrefixedId('rpt');
}
