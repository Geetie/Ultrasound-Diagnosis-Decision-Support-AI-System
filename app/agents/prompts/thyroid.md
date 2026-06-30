# Thyroid Specialist Agent — System Prompt (Placeholder)

> 占位文件：T-GA-AGT-04（甲状腺专科智能体）的 system prompt 模板
> 实现位置：`app/agents/specialist/thyroid.py`（Wave 3+ 交付）

本文件作为占位符存在，提示后续轨道交付时本目录应当有：

- `specialist/base.py` — `SpecialistAgent` 抽象基类（继承 `Agent` Protocol）
- `specialist/thyroid.py` — 甲状腺专科实现
- `specialist/liver.py` — 肝脏专科实现
- `specialist/cardiac.py` — 心脏专科实现
- `prompts/thyroid.md` / `liver.md` / `cardiac.md` — 各专科提示词

当前 Phase 0A 阶段（Wave 1+2）的 dispatcher / knowledge / monitor 智能体
**不依赖**上述 specialist 实现，可独立运行与测试。
