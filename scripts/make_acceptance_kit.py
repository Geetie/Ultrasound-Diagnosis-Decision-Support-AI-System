
import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

OUT_DEFAULT = Path(os.path.expanduser('~/acceptance_kit'))
TN3K_DIR = Path(os.path.expanduser('~/uai/data/tn3k_dataset/test-image'))
CAMUS_DIR = Path(os.path.expanduser('~/uai/data/camus/Masks'))


def pick_tn3k(n):
    if not TN3K_DIR.is_dir():
        return []
    files = sorted(p for p in TN3K_DIR.iterdir()
                   if p.suffix.lower() in ('.jpg', '.png', '.jpeg'))
    if not files:
        return []
    step = max(1, len(files) // n)
    return [(f'thyroid_{i:02d}', files[i * step]) for i in range(min(n, len(files)))]


def make_synthetic_liver(path, seed=7):
    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(seed)
    h = w = 512
    img = rng.normal(38, 9, (h, w)).astype(np.float32)
    yy, xx = np.mgrid[0:h, 0:w]
    liver = ((xx - 150) ** 2 / 200**2 + (yy - 260) ** 2 / 150**2) < 1
    img[liver] += 22
    for y0 in (150, 300):
        img[np.abs(yy - (y0 + 0.25 * xx)) < 7] += 95
    img[((xx - 330) ** 2 + (yy - 210) ** 2) < 34**2] -= 26
    arr = np.clip(img, 0, 255).astype(np.uint8)
    Image.fromarray(arr).save(path)
    return {'usable_for': '界面 / 链路走通验证',
            'not_usable_for': '任何精度评估（合成纹理与真实超声差异极大）'}


def build(out_dir: Path, n_thyroid: int = 6) -> dict:
    from make_drill_dicom import build as build_dicom

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'dicom').mkdir(exist_ok=True)
    manifest = []

    for name, src in pick_tn3k(n_thyroid):
        dcm = out_dir / 'dicom' / (name + '.dcm')
        info = build_dicom(src, dcm, patient_id='ACC-' + name)
        manifest.append({
            'id': name, 'organ': 'thyroid', 'dicom': str(dcm),
            'source': 'TN3K 官方 test 集：' + src.name,
            'license': 'NC（非商用）',
            'size': str(info['rows']) + 'x' + str(info['cols']),
            'expected_checks': [
                '报告含「三、影像分割」段（面积/占比/边界框/mm²）',
                '诊断意见非空且不含 llm_unavailable',
                '证据来源列出 kb_v1.3.0 的 chunk',
            ],
        })

    liver_png = out_dir / 'liver_synthetic.png'
    liver_info = make_synthetic_liver(liver_png)
    liver_dcm = out_dir / 'dicom' / 'liver_synthetic.dcm'
    build_dicom(liver_png, liver_dcm, patient_id='ACC-liver-synthetic')
    manifest.append({
        'id': 'liver_synthetic', 'organ': 'liver', 'dicom': str(liver_dcm),
        'source': '程序合成（非真实临床数据）', 'license': '自造',
        **liver_info,
        'expected_checks': [
            '链路可走通（诊断意见能生成）',
            '分割旁路明确返回「无公开数据」原因，不是假轮廓',
            '属性提示对中文报告明确拒绝（预期行为，不是 bug）',
        ],
    })

    if CAMUS_DIR.is_dir():
        cams = sorted(CAMUS_DIR.rglob('*.png'))[:2] or sorted(CAMUS_DIR.rglob('*.jpg'))[:2]
        for i, src in enumerate(cams):
            name = f"cardiac_{i:02d}"
            dcm = out_dir / 'dicom' / (name + '.dcm')
            info = build_dicom(src, dcm, patient_id='ACC-' + name)
            manifest.append({
                'id': name, 'organ': 'cardiac', 'dicom': str(dcm),
                'source': 'CAMUS-Lite：' + src.name,
                'license': 'CC-BY-NC-SA-4.0（学术非商用）',
                'size': str(info['rows']) + 'x' + str(info['cols']),
                'expected_checks': ['诊断意见非空',
                                    '开分割旁路时 cardiac（DSC 0.8395）应产出轮廓'],
            })

    scenarios = {
        'thyroid': '患者女，45 岁。体检发现甲状腺右叶低回声结节，约 1.0 cm，边界尚清，形态规则，未见明显钙化，血流信号不明显。自述乏力、怕冷。',
        'liver': '患者男，52 岁。超声示肝实质回声增粗，肝内见低回声区，形态欠规则，边界欠清。既往有乙肝病史 10 年。',
        'cardiac': '患者女，63 岁。心尖四腔心切面，左心室舒张功能减低，室壁运动未见明显异常。主诉活动后气促。',
    }
    (out_dir / 'clinical_scenarios.json').write_text(
        json.dumps(scenarios, ensure_ascii=False, indent=2), encoding='utf-8')
    (out_dir / 'manifest.json').write_text(json.dumps({
        'purpose': '交付交互核验输入包（可复现）',
        'note': '公开数据或自造数据，无真实患者信息；除 TN3K 官方 test 外均不可用于精度评估',
        'items': manifest, 'clinical_scenarios': scenarios,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'out': str(out_dir), 'items': len(manifest),
            'thyroid': sum(1 for m in manifest if m['organ'] == 'thyroid'),
            'cardiac': sum(1 for m in manifest if m['organ'] == 'cardiac'),
            'liver': sum(1 for m in manifest if m['organ'] == 'liver')}


def main() -> int:
    ap = argparse.ArgumentParser(description='生成交互核验测试影像包')
    ap.add_argument('--out', default=str(OUT_DEFAULT))
    ap.add_argument('--n-thyroid', type=int, default=6)
    a = ap.parse_args()
    print(json.dumps(build(Path(a.out), a.n_thyroid), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
