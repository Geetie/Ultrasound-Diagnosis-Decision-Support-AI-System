import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import ClinicalInfoPanel from '../ClinicalInfoPanel';
import {
  EMPTY_CLINICAL_INFO,
  isClinicalInfoFilled,
  toClinicalText,
} from '../clinicalInfo';

/** A.36 ⑤：临床信息录入闭环的前端半环。 */
function Stateful(props: { locked?: boolean }) {
  const [v, setV] = useState(EMPTY_CLINICAL_INFO);
  return <ClinicalInfoPanel value={v} onChange={setV} {...props} />;
}

describe('ClinicalInfoPanel', () => {
  it('折叠态徽标：空=未填；展开填写后=已填', async () => {
    const user = userEvent.setup();
    render(<Stateful />);
    expect(screen.getByText(/未填/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /临床信息/ }));
    const box = screen.getByPlaceholderText(/颈部肿物/);
    await user.type(box, '颈部肿物 2 月');
    expect(screen.getByText(/已填/)).toBeInTheDocument();
    expect(screen.queryByText(/未填/)).toBeNull();
  });

  it('结构化勾选映射安全网关 patient_conditions', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ClinicalInfoPanel value={EMPTY_CLINICAL_INFO} onChange={onChange} />);
    await user.click(screen.getByRole('button', { name: /临床信息/ }));

    await user.click(screen.getByRole('checkbox', { name: '妊娠' }));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ conditions: ['妊娠'] }),
    );
  });

  it('locked：折叠头禁用、不展开、徽标显示已锁定', () => {
    render(<ClinicalInfoPanel value={EMPTY_CLINICAL_INFO} onChange={vi.fn()} locked />);
    const head = screen.getByRole('button', { name: /临床信息/ });
    expect(head).toBeDisabled();
    expect(screen.getByText(/已锁定/)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/颈部肿物/)).toBeNull();
  });

  it('toClinicalText 拼接非空段并逐行标注来源', () => {
    expect(
      toClinicalText({ symptoms: '颈部肿物', history: '', labs: 'TSH 正常', conditions: [] }),
    ).toBe('症状：颈部肿物\n化验：TSH 正常');
    expect(isClinicalInfoFilled(EMPTY_CLINICAL_INFO)).toBe(false);
  });
});