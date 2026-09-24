
"""
导出验证配置为JSON文件的脚本
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.inference.verification_config import (
    get_default_boundary_config,
    get_default_forbidden_terms_config,
    get_default_verification_config,
    save_config_to_json,
)


def main():
    data_dir = project_root / "app" / "inference" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # 保存完整配置
    full_config = get_default_verification_config()
    full_config_path = data_dir / "verification_config.json"
    save_config_to_json(full_config, str(full_config_path))
    print(f"✅ 完整配置已保存到: {full_config_path}")

    # 保存边界配置
    boundary_config = get_default_boundary_config()
    boundary_config_path = data_dir / "boundary_config.json"
    save_config_to_json(boundary_config, str(boundary_config_path))
    print(f"✅ 边界配置已保存到: {boundary_config_path}")

    # 保存禁忌词配置
    forbidden_terms_config = get_default_forbidden_terms_config()
    forbidden_terms_path = data_dir / "forbidden_terms.json"
    save_config_to_json(forbidden_terms_config, str(forbidden_terms_path))
    print(f"✅ 禁忌词配置已保存到: {forbidden_terms_path}")

    print("\n🎉 所有配置文件导出成功！")


if __name__ == "__main__":
    main()
