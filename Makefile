# 常用开发命令（与 CI 保持一致，避免"本地过、CI 挂"）
#
# 注意：pytest 必须带 --dist=loadfile（见 pyproject.toml 注释）——本仓库多个
# 测试类存在链式依赖，按用例随机分发会把链条拆到不同 worker 造成假失败。

PY ?= python
TRACKS_UNIT := tests/unit
TRACKS_INT := tests/integration/int
PYTEST_FLAGS := -q -p no:cacheprovider -n 8 --dist=loadfile --timeout=300

.PHONY: help install lint lint-fix test test-unit test-int test-all test-perf cov bench bench-all frontend fe-test fe-lint fe-build fe-smoke edge compose-up eval-collect eval-gate

help:
	@echo "make install     - 安装后端依赖（含开发依赖）"
	@echo "make lint        - ruff 全量检查（含历史债，仅参考）"
	@echo "make test        - 后端单元 + INT 集成测试"
	@echo "make cov         - 覆盖率（整体 80 / 核心 90 门禁）"
	@echo "make frontend    - 前端 lint + 类型检查 + 测试 + 构建"
	@echo "make fe-smoke    - 真浏览器渲染冒烟（需本机 Chrome + 可达后端 + 凭据环境变量）"
	@echo "make edge        - 本地以开发机模式启动边缘端"

install:
	$(PY) -m pip install -r requirements.txt -r requirements-dev.txt

lint:
	$(PY) -m ruff check app safety_gateway edge tests scripts

lint-fix:
	$(PY) -m ruff check --fix --select RUF100,F401,W291,W292,W293 app tests

test: test-unit test-int

test-unit:
	$(PY) -m pytest $(TRACKS_UNIT) $(PYTEST_FLAGS) --tb=short

test-int:
	$(PY) -m pytest $(TRACKS_INT) $(PYTEST_FLAGS) --tb=short

# perf 标记的用例断言墙钟 P99，必须串行跑（-n0 关闭 xdist 并行；
# 不能用 -p no:xdist，否则 addopts 里的 --dist 会变成未识别参数）
test-perf:
	$(PY) -m pytest -m perf -n0 --tb=short

# 覆盖率门禁：**与 CI 同口径**（整体 82 / 核心 86），实测 整体 82.39% / 核心 86.30%。
#
# 修正记录：此前这里写的是 `--fail-under=75/74` 且注释为"基线 整体76/核心75"，
# 比 CI 的 82/86 **宽松** —— 会出现"`make cov` 绿、CI 红"的假绿灯（本地自测失真）。
# 棘轮历史与当前实测值见 pyproject.toml 的注释块。
cov:
	$(PY) -m pytest $(TRACKS_UNIT) $(TRACKS_INT) $(PYTEST_FLAGS) --tb=short \
		--cov=app --cov-report=term-missing
	$(PY) -m coverage report --fail-under=82
	$(PY) -m coverage report \
		--include='app/security/*,app/services/*,app/fusion/*,app/agents/*' \
		--fail-under=86

# 性能基准统一入口（真机基准需现场硬件）
bench:
	$(PY) scripts/bench_suite.py --suite inline --json bench_report.json

bench-all:
	$(PY) scripts/bench_suite.py --suite all --json bench_report.json

frontend fe-test:
	cd frontend && npm run lint && npx tsc -b && npm run test:run && npm run build

fe-lint:
	cd frontend && npm run lint

fe-build:
	cd frontend && npm run build

# 真浏览器渲染冒烟（运行时层守卫；静态层在 tests/unit/quality/test_frontend_render_guard.py）。
# 为什么需要：门禁 curl 全绿≠浏览器渲染得出来——2026-10-05 首次真浏览器渲染
# 一次抓到 4 个 P0 阻断（白屏/登录 422/二级页全打不开），详见脚本头注释。
# 前置：本机 Playwright + Chrome；UAI_BASE_URL（默认 http://127.0.0.1:8000，可用
# SSH 隧道指向服务器 18000）；凭据只走环境变量（铁律 #10）：
#   UAI_DOCTOR_USER / UAI_DOCTOR_PASSWORD（服务器 ~/uai/.smoke_pw）
# 环境不具备时脚本默认 SKIP（加 --require 强制判失败）。
fe-smoke:
	node scripts/frontend_browser_smoke.mjs

# 评测集（19 号计划）：采集与门控。
#
# 为什么单独成目标：19 号把「双引擎评测集 ≥700 例」定为**可持续运营的资产**，
# 采集/门控必须能一条命令复跑（人肉导出无法去重、无法复现、也无法进 CI）。
#
# 采集源是**业务库**（diagnosis_results + feedback_records + dicom_images，见脚本 docstring）；
# 默认落 ~/uai/data/eval/double_engine_eval_set.jsonl 与 ~/uai/reports/langfuse_eval_set.json
# （服务器路径；本机跑需自行给 --out/--report）。
#
# 门控是**两层**：L1 内容门控（结论非空率/技术串泄漏/降级率/完整性，恒跑、纯 CPU）
# + L2 忠实度（faithfulness ≥0.95 / 相关性 ≥0.90，需 LLM 判官；本仓库暂无判官脚本，
# 缺判官报告时脚本如实记 `not_run`，**不**假装达标也不整体判红）。
eval-collect:
	$(PY) scripts/langfuse/collect_eval_set.py

eval-gate:
	$(PY) scripts/langfuse/eval_gate.py

# 开发机（无 TensorRT）启动边缘端：自动退回空导航后端，链路仍可跑通
edge:
	cd . && $(PY) -m edge

compose-up:
	docker compose -f docker-compose.dev.yml up -d --build
