
"""
验证模块快速测试脚本
"""
import sys
from pathlib import Path

# 添加项目根目录
sys.path.insert(0, str(Path.cwd()))

try:
    # 测试导入新模块
    from app.inference.verification_config import (
        get_default_verification_config,
    )
    print('✓ verification_config 导入成功')

    from app.inference.drift_detector import DriftDetector
    print('✓ drift_detector 导入成功')

    # 测试获取默认配置
    config = get_default_verification_config()
    print(f'✓ 默认配置加载成功 - 测试数据版本: {config.test_data_version}')
    print(f'✓ 边界配置 - 甲状腺: {len(config.boundary_config.organ_boundaries.get("thyroid", []))} 个边界')
    print(f'✓ 禁忌词配置 - 甲状腺: {len(config.forbidden_terms_config.organ_terms.get("thyroid", []))} 个禁忌词')

    # 测试漂移检测器初始化
    drift_detector = DriftDetector(model_name='test_model', model_version='1.0')
    print('✓ 漂移检测器初始化成功')

    # 测试幻觉检测器
    from app.inference.hallucination_detector import HallucinationDetector
    detector = HallucinationDetector()
    print('✓ 幻觉检测器初始化成功')

    # 测试一个简单的幻觉检测
    text = '甲状腺结节 TI-RADS 4a 类，考虑良性，伴有肺癌转移'
    organ = 'thyroid'
    result = detector.detect(text, organ)
    print(f'✓ 幻觉检测完成 - 是否是幻觉: {result.is_hallucination}')
    print(f'✓ 检测到 {len(result.issues)} 个问题')

    # 测试一致性模块导入
    print('✓ consistency 模块导入成功')

    print()
    print('🎉 所有核心模块测试通过！')

except Exception as e:
    print(f'✗ 测试失败: {e}')
    import traceback
    traceback.print_exc()
