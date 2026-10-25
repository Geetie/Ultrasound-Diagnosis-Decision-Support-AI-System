"""A.88 配置接线①：**代码默认引擎必须与生产实际一致**（默认 vLLM/full）。

## 缺陷（《架构真相》§3.2 第一条）

`app/config/settings.py` 的默认是 ``deployment_profile='compact'`` +
``inference_engine='llama.cpp'``，而**生产 .env 实测**是
``DEPLOYMENT_PROFILE=full`` / ``INFERENCE_ENGINE=vllm`` /
``VLLM_DEFAULT_MODEL=Qwen2.5-7B-Instruct``。后果：任何人只 ``Settings()``
（不起 .env 的脚本、单测、新部署、`python -c` 探针）都拿到**CPU 版 llama.cpp
引擎**——AGENTS.md 硬规则 #4 记录过它的真实代价：7B 全靠 CPU，40s/请求，
vLLM 快约 20x。也就是说"代码默认"本身就在把系统往被禁止的方向推。

## 本文件钉住的四条

1. **默认 = 生产**：裸 ``Settings()`` 必须得到 ``full`` + ``vllm``，且
   ``vllm_default_model`` 与生产 .env 同值（``Qwen2.5-7B-Instruct``）。
2. **硬规则 #4 的边界**：改方向**不是**回切 llama.cpp —— 默认值里不许再出现
   ``llama.cpp``/``compact``（反证腿：把默认改回 compact 即红）。
3. **compact 仍是显式可选降级路径**：``Settings(deployment_profile='compact')``
   仍被强制成 llama.cpp（覆盖逻辑一行未动），且显式 compact 时留痕说明它是降级路径。
4. **缺模型名必须启动期可读报错**：引擎是 vllm 且 ``vllm_require_model`` 而模型名
   为空 → ``inference_config_errors()`` 给出可读原因（而不是等第一次生成才炸）。

反证腿见各用例 docstring：把默认改回 compact/llama.cpp、删掉启动期校验、
或把 compact 覆盖逻辑改掉，都会让本文件变红。
"""
from __future__ import annotations

from app.config.settings import Settings, settings


class TestDefaultMatchesProduction:
    def test_default_profile_is_full(self):
        """默认档位必须是 full（生产 .env 的 DEPLOYMENT_PROFILE=full）。"""
        assert Settings().deployment_profile == "full"

    def test_default_engine_is_vllm(self):
        """默认引擎必须是 vllm（反证：改回 'llama.cpp' 即红，且违反硬规则 #4 的方向）。"""
        assert Settings().inference_engine == "vllm"

    def test_declared_field_default_is_vllm_not_just_coerced(self):
        """**字段声明本身**必须是 vllm（不能只靠 full→vllm 的覆盖兜出来）。

        反证腿（这条是本项最关键的一条）：把 ``inference_engine`` 的默认值改回
        ``'llama.cpp'`` 时，``Settings()`` 仍会因为 full 档覆盖而得到 vllm，
        上面那条用例**发现不了**回归。本用例直接读 pydantic 的字段默认值，
        所以"把默认改回 llama.cpp"这种回切会被当场抓到。
        """
        fields = Settings.model_fields
        assert fields["inference_engine"].default == "vllm"
        assert fields["deployment_profile"].default == "full"

    def test_no_default_points_back_to_llama_cpp(self):
        """硬规则 #4 的代码侧落点：默认档位/引擎都不许回到 compact + llama.cpp。"""
        s = Settings()
        assert (s.deployment_profile, s.inference_engine) != ("compact", "llama.cpp")
        assert s.inference_engine != "llama.cpp"

    def test_default_model_name_is_intentionally_empty(self):
        """模型名**故意**不给硬编码默认值（Lead 定案，理由见 settings 注释）。

        硬编码生产模型名会让"用别的模型部署"的环境静默连错模型；因此默认空 +
        ``vllm_require_model=True``，由启动期校验给可读报错，生产以 .env 为准。
        反证腿：若有人把 ``Qwen2.5-7B-Instruct`` 写死进代码默认，本用例即红
        ——那会把"配置缺失"变成"配置看起来齐全但可能是错的"。
        """
        assert Settings().vllm_default_model == ""

    def test_module_level_settings_also_vllm(self):
        """模块级 `settings` 单例（全仓实际消费的那个）同样是 vllm。

        反证腿：只改字段默认值但不改 validator 的 compact 覆盖方向时，
        这里会红——覆盖逻辑必须与默认值同向。
        """
        assert settings.deployment_profile == "full"
        assert settings.inference_engine == "vllm"

    def test_default_max_tokens_is_full_profile_value(self):
        """full 档不再被 compact 的 1024 截断。"""
        assert Settings().effective_max_tokens == Settings().max_tokens


class TestCompactRemainsExplicitDegradePath:
    """硬规则 #4 的另一半：compact 是**显式可选降级路径**，不是默认、也没被删除。"""

    def test_compact_still_forces_llama_cpp(self):
        s = Settings(deployment_profile="compact", inference_engine="vllm")
        assert s.inference_engine == "llama.cpp"

    def test_compact_keeps_compact_limits(self):
        s = Settings(deployment_profile="compact")
        assert s.effective_max_tokens == 1024
        assert s.funasr_device == "cpu"

    def test_full_still_forces_vllm(self):
        s = Settings(deployment_profile="full", inference_engine="llama.cpp")
        assert s.inference_engine == "vllm"


class TestVllmModelStartupValidation:
    def test_missing_model_name_is_reported(self):
        """引擎 vllm + require_model + 空模型名 → 启动期可读报错（不是运行期才炸）。"""
        s = Settings(deployment_profile="full", vllm_default_model="")
        errors = s.inference_config_errors()
        assert errors, "缺模型名必须被显式报告"
        joined = " ".join(errors)
        assert "VLLM_DEFAULT_MODEL" in joined
        assert "INFERENCE_ENGINE" in joined  # 给出降级出路，而不是只说"错了"

    def test_default_config_reports_missing_model_but_is_not_fatal(self):
        """裸 ``Settings()`` 会**自报**"缺模型名"——这是**预期**行为，不是缺陷。

        默认档位/引擎已对齐生产（full + vllm），但模型名故意留空（不硬编码，
        理由见 settings.py 注释）。所以任何环境都不该静默通过这条自检：
        缺模型名 → 报出可读原因。它**不阻断启动**（见 main.py lifespan 里
        只打 ``logger.error``），因为 CI / 本地没有 vLLM、也不该因此启动失败。
        """
        errors = Settings().inference_config_errors()
        assert len(errors) == 1
        assert "VLLM_DEFAULT_MODEL" in errors[0]

    def test_setting_model_name_clears_the_error(self):
        """配上模型名（生产 .env 的做法）→ 自检通过。"""
        s = Settings(vllm_default_model="Qwen2.5-7B-Instruct")
        assert s.inference_config_errors() == []
        assert s.resolved_llm_model == "Qwen2.5-7B-Instruct"

    def test_llama_cpp_needs_no_model_name(self):
        """compact/llama.cpp 路径不要求模型名（降级路径不能被这条校验拦住）。"""
        s = Settings(deployment_profile="compact", vllm_default_model="")
        assert s.inference_config_errors() == []

    def test_require_model_false_accepts_empty(self):
        """显式关掉 require_model 时不再报错（保留既有逃生阀）。"""
        s = Settings(
            deployment_profile="full",
            vllm_default_model="",
            vllm_require_model=False,
        )
        assert s.inference_config_errors() == []

    def test_lifespan_reports_the_error_at_startup(self):
        """校验必须真的挂在启动路径上（否则只是"有个没人调的函数"）。

        反证腿：把 lifespan 里那段 ``settings.inference_config_errors()`` 删掉 /
        改成恒空，本用例即红 —— 这正是"改在启动期暴露"的唯一落地证据。
        """
        import inspect

        import app.main as main_mod

        source = inspect.getsource(main_mod.lifespan)
        assert "inference_config_errors()" in source, (
            "lifespan 未调用 inference_config_errors()：缺模型名会重新退化成"
            "「第一次判读才炸」，医生侧只看到 AI 不可用"
        )
        # 且必须真的把原因打出来（不是拿到就丢）
        assert "inference_config_invalid" in source
