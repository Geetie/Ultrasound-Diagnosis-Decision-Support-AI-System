# session-handoff.md — 会话交接

> 多会话/长任务专用。**Last Updated / Current Objective / Recommended Next Step / Blockers / Files / Next Session** 六项必填。
> 下一位 Agent 先读本节，再读 `progress.md` 与 `feature_list.json`。

## Last Updated

2026-10-13（分支 `main`；**A.59 结论正文可读化 + 零静默补漏三处（含 1 个 P0）**——
从**核对 A.58 登记项的归因**出发：①**更正一条错误归因**（A.58.10① 把专科正文里的
`[cardiac] LVEF=unknown, severity=normal` 误记成安全门问题；安全门那句本来就是中文）；
②**结论正文可读化**：新增 `app/agents/specialist/readable.py`（`graded_content` / `cardiac_content` /
`severity_cn`（ASE 标签 + canonical 短名两套词表）/ 五个降级文案）与 `schemas.ORGAN_CN_LABELS`
+ `organ_cn()`（器官中文名单一事实源），三专科 + `base.py` 三处降级 + `dispatcher` + `pipeline`
兜底全部改中文；机器值只留 `reasoning_trace`；`consistency._extract_lvef` 容许中文右括号
（正文形态「（LVEF）62%」可解析）；③**降级必转人工**（三处降级原先不置 `needs_review` = 静默降级）；
④**`aggregate()` 不再丢 `needs_review`**（`invoke()` 唯一出口）；⑤**P0**：`needs_review` 未算
结果终态 → **医生打开待复核检查永远停在「分析中」**（p28 P28-2 真阳性抓到）→ 抽出
`TERMINAL_RESULT_STATUSES`（completed/needs_review/cancelled/failed/timeout）。
真机：**p26 PASS=35 FAIL=0 · p28 PASS=7 FAIL=0**（新增 P28-4）· **p27 甲状腺/心脏两腿各 PASS=4 FAIL=0**
（新增 P27-5，心脏腿用 `--organ cardiac`）。本地 `tests/unit` **6565 passed / 1 例全量顺序偶发**
（`tests/unit/sec/...::test_decrypt_with_wrong_key_version_raises`，单独跑恒过，已登记待查）。详见 16 号附录 A.59）

### A.59 复跑入口与踩坑（下一位直接用）

- 可读文案自检（**服务器**，秒级）：`PYTHONPATH=~/uai/src ~/uai/venv/bin/python -c "from
  app.agents.specialist.readable import cardiac_content, evidence_insufficient_text; print(...)"`
- 正文可读性真机门禁（**服务器**）：`p27_grade_extraction.py --rounds 8 --organ thyroid`（P27-5 部位名）
  与 `--organ cardiac`（英文键值最早就出在这里）
- 渲染层（**本机**）：`node scripts/audit/p28_degradation_visibility.mjs`（新增 P28-4；反证腿需屏蔽
  `/api/v1/imaging/**`；**挑会话已改为「终态」判定**）
- 端到端（**本机**）：`node scripts/audit/p26_squad_e2e_walkthrough.mjs --dicom1 … --dicom2 …`
- 静态守卫：`pytest tests/unit/agt/test_grade_extraction_contract.py`（`TestNoTechnicalStringInDoctorFacingContent`
  已扩围到器官代码 / `LVEF=` / `severity=` / 三处降级串 + 正常路径 + LLM 异常分支）
- **踩坑（A.59 新增）**：
  ① **同一段 UI 文本可能是几层拼接的**：判"哪个模块吐的串"必须看**该层代码**，别按相邻关系猜
  （A.58.10① 就是这么错的）；② **修好一个缺陷会让下一个缺陷可见**（降级不再静默后，前端"终态集合漏项"
  立刻现形）→ 终态/枚举这类集合**一次穷举完**；③ **`Start-Process cmd.exe /c x.bat` 会静默不跑**
  （第 3 次）→ 一律直连 exe + 绝对路径重定向，且**先确认日志文件存在**；④ **PowerShell 内联多行 python
  必炸** → 写 `.py` 再跑；⑤ `git add` 里 `app/models`（含 `__pycache__`）的 ignore 警告**不代表
  `schemas.py` 被忽略**（用 `git ls-files` / `git cat-file -e HEAD:<path>` 核实）。

**下一批（A.59 批次剩余，按序）**：①存量静默收敛（前端影像层 38 处 + 后端主路径 ~45 处，基线下调；
真机 p26/p20）②Langfuse ≥700 例采集 + 指标门控（容器已在跑）③t2a 公开集 held-out 复验（标「公开集下界」）。

### 上一轮（A.58）Last Updated 原文

2026-10-13（分支 `main`；**A.58「零静默失败」常驻机制 + 结论正文可读化 + 两项口径落地**——
把 20 号研究产出的「零静默」从**出事才清查**升级为**常驻门禁**，三层各带反证：
①**约定层（后端）** `test_no_silent_catch.py` 由「7 文件白名单 + 正则」改为 **`ast` 全仓扫描**
（193 个 git 跟踪 `*.py`）+ 存量基线（**428 处，只许下调**）+ 7 主路径文件**零容忍** +
`contextlib.suppress` 后门一并判；②**约定层（前端）** 本地规则 `local/no-silent-catch`
（catch 必须 `console.error` + 用户可见降级）+ **独立 config/基线棘轮**（44 处/10 文件，不动
`--max-warnings 1`）+ `npm run lint:silent-catch`；③**渲染层** 新探针
`p28_degradation_visibility.mjs`（15s 窗口 + clip 截图 PIL 像素方差 + **`page.route` abort 反证腿**）
—— 反证腿当场暴露产品级缺陷：**影像取回失败原先静默退回设计预览态（假标注框 + 零提示）**，已修
（`data-region="image-load-error"` 可读告警 + `showMock` 不再渲染示意框）；④**语义层** 模型
「反向索要」守卫（`app/agents/specialist/solicitation.py`：强动作 + 影像对象判据，豁免 A.56 转人工
文案）→ `needs_review` + trace，**只在索要真进正文时**才换正文；⑤**结论正文可读化**
（`utils/text.ts#stripMarkdownEmphasis`，仅显示层）；⑥**口径落地**：语音进主流程（工作区页面级
`VoiceControl` + 建会话页口述；**踩坑**：写全局 `sessionStore` 会让 `AppLayout` 的 nav WebSocket
消费者误连，p26 `T0-err` 当场抓到 → 改**显式 `sessionId` 入参**）、设置页「设备与权限」（403 可读）。
真机：**p26 PASS=35 FAIL=0**（上轮 33/0）· **p28 PASS=6 FAIL=0** · **p20 PASS=31 FAIL=0** ·
**p27 PASS=3 FAIL=0（降级 0/8）**。详见 16 号附录 A.58）

### A.58 复跑入口与踩坑（下一位直接用）

- 渲染层降级可见性（**本机**）：`node scripts/audit/p28_degradation_visibility.mjs`
  （先建会话→装真实多帧 DICOM；产出 `C:/Windows/TEMP/uai_p28/` 截图 + `p28_variance*.json`；
  **退出码 0/1/2**；反证腿会用 `page.route` 屏蔽 `/api/v1/imaging/**`（影像字节面））
- 零静默棘轮：`pytest tests/unit/quality/test_no_silent_catch.py test_frontend_silent_catch.py`
  （后端 428 处基线 / 前端 44 处基线；**只许下调**，重算见两文件头注释）；
  `cd frontend && npm run lint:silent-catch`（独立 config，**不要**并进 `npm run lint`）
- 语义层守卫：`pytest tests/unit/agt/test_no_solicitation_guard.py`（21 例，含「请结合图像与病史
  自行判断」不误伤的反证）
- 结论正文归一化：`frontend/src/utils/text.ts`（只做显示层；`__` **只在成对时**去，避免改动
  `grade_unparseable` 这类技术标识符）
- **踩坑（A.58 新增，都会再犯）**：
  ① **往全局 store 写字段 = 触发别人的副作用**：为让语音可用而把会话写进 `sessionStore`，
  直接让 `AppLayout` 的 `useRealtimeConnection` 「以为会话就绪」去连 nav WebSocket（隧道下必失败，
  p26 `T0-err` 抓到）。**页面级组件要什么就显式传什么**。
  ② **登录刚跳完就一次性 `count()` = 假 FAIL**：工作区是懒加载路由，刚落点时 DOM 里还没有工作区
  节点 → 必须**轮询等渲染**（p26 已改 30×500ms，并在 detail 带 `workspace-root=1`）。
  ③ **严格预算的 ESLint 上别加新规则**（`--max-warnings 1`）→ 必须独立 config + 基线棘轮。
  ④ **`Start-Process cmd.exe /c x.bat` 可能静默不跑**（无日志无进程）→ 改
  `Start-Process node.exe -ArgumentList … -RedirectStandardOutput <绝对路径>`，并**先确认日志文件存在**。
  ⑤ **改了包就要跑它的棘轮**：本轮新脚本一处多余 `int()` 让 ruff `RUF046 13 > 12` 立刻红。
  ⑥ 静态产物仍需 `VITE_BASE=/ui/` 清空式重建 + 部署（见 A.55/A.52 踩坑）。

### 上一轮（A.57）Last Updated 原文

2026-10-13（分支 `main`；**A.57 第一/第二梯队收口 —— p26 零 FAIL**——收口 A.56 走查登记的
易做项，并在复跑中又抓到 2 个真缺陷：①**报告模板补「影像分割（模型辅助）」段**（liver/cardiac
原缺 `{{nodule_summary}}` → 心脏/肝脏报告正文看不到分割面积与帧级溯源）→ 真机 **T1-5b 由 FAIL
转 PASS**；②**影像角标去硬编码**（参数条写死「甲状腺 · 线阵 12 MHz · 增益 62 · 深度 10 cm」、
时间写死「2026-10-02」，心脏会话照显甲状腺→改「声明部位 + 影像参数以设备为准/参数待装载」+
会话起始时间）；③**历史对比默认配对修正**（原取 `comparable_organs[0]`（按器官名排序），患者
最新两次是心脏时却默认并排更早的甲状腺对 → 改优先「最近一次检查所在器官」；文案「结节面积」→
「分割面积」）；④**p20 重跑 PASS=31 FAIL=0**（A.55 尾巴 G1.2 复核通过）+ 21 号路线图刷新。
真机 **p26 PASS=33 FAIL=0**（同时修掉 p26 自身的竞态：它原先"先等分割入口 90s 再等结论"，
判读冷启动时会制造假 FAIL）。详见 16 号附录 A.57）

### A.57 复跑入口与踩坑（下一位直接用）

- 第一/第二梯队端到端走查（**本机** Playwright + 隧道；样本在服务器现转，见下）：
  `node scripts/audit/p26_squad_e2e_walkthrough.mjs --dicom1 <w1.dcm> --dicom2 <w2.dcm>`
  （样本：`scripts/camus_frame_to_dicom.py --out /tmp/uai_w1.dcm --count 7 --multiframe
  --keyword 4CH_half`；第二次换 `2CH_half`（同器官 cardiac）；scp 回本机 `C:\Windows\TEMP\uai_e2e\`）
- 前端深度边界（**本机**）：`node scripts/audit/p20_frontend_boundary.mjs --dicom <7 帧样本>`
- 报告模板改动后**必须重启主服务**（`report_service._TEMPLATE_CACHE` 是进程内缓存）：
  `cd ~/uai/src && set -a && source ~/uai/.env && set +a && nohup ~/uai/venv/bin/python -m uvicorn
  app.main:app --host 127.0.0.1 --port 18000 >> ~/uai/logs/api.log 2>&1 &`
- 守卫：`pytest tests/unit/quality`（105 例，含角标数据驱动守卫）、
  `pytest tests/unit/api/test_report_templates.py`（模板渲染）、
  `vitest run src/pages/__tests__/WorkspacePage.test.tsx src/pages/__tests__/PatientHistoryPage.test.tsx`
- **踩坑（都会再犯）**：
  ① **启动期资产有进程内缓存**（报告模板）→ 改完**必须重启主服务**；只同步文件不重启 = "改了没生效"。
  ② **重启后第一次判读明显变慢**（冷启动，实测 ~90s vs 常态 ~10s）→ **探针要先等结果落地再验结果
     里的入口**；反过来（先等入口）会在慢判读时稳定产生**假 FAIL**。
  ③ **页面用例传 `dicomImageId` 会拉进 cornerstone 依赖图 → vitest 长时间不返回**：真实影像分支
     用静态守卫 + 真机 p26 覆盖，页面用例只渲染预览态。
  ④ **`Start-Process -FilePath x.bat` 报 "not a valid application"** → 要
     `Start-Process cmd.exe -ArgumentList '/c','x.bat'`；子进程 `%TEMP%` 可能与当前 shell 不同，
     日志写**绝对路径**。
  ⑤ **cmd 里中文参数会因代码页变形**（`vitest -t "中文"` 恒 0 匹配）→ 过滤词用 ASCII。
  ⑥ **服务器 shell 是 fish**：带 `|`/`$`/引号的 ssh 一行式命令会被双层解释 → **写脚本 + scp 过去跑**。

### 上一轮（A.56）复跑入口与踩坑

- 第一/第二梯队端到端走查（**本机** Playwright + 隧道，被测系统 = 服务器）：
  `node scripts/audit/p26_squad_e2e_walkthrough.mjs --dicom1 <w1.dcm> --dicom2 <w2.dcm>`
  （需 `UAI_DOCTOR_USER/PASSWORD`；样本在服务器现转：`scripts/camus_frame_to_dicom.py
  --count 7 --multiframe --keyword 4CH_half` / 第二次换 `2CH_half`（同器官 cardiac）；
  产出 `C:/Windows/TEMP/uai_p26/` 截图 + `p26_report.json`；退出码 0=全过）
- 分级提取降级率门禁（**服务器**）：`~/uai/venv/bin/python scripts/audit/p27_grade_extraction.py
  --rounds 8`（退出码 0=降级率达标且零技术串）
- 结构化输出能力实测（**服务器**）：`scripts/audit/p27a_structured_output_probe.py`
- 守卫：`pytest tests/unit/quality/test_frontend_render_guard.py`（34 例，含 `useTransitions`
  与器官声明三例）、`tests/unit/agt/test_grade_extraction_contract.py`（27 例）
- **踩坑（都会再犯）**：
  ① **react-router v7 的 `<BrowserRouter>` 默认 `useTransitions`**：location 更新走 transition lane；
  含重依赖（cornerstone）的页面里该 lane 可能**永不提交** → 导航静默失效（URL 变、视图不变、
  无报错）。排查要点：**点击后有没有路由 chunk 请求**（没有 = React 没开始渲染新路由）+
  **同页面普通 lane 的 setState 是否仍生效**（生效 = 不是 React 卡死）。
  ② **本机 vitest 执行 `WorkspaceImageRegion.test.tsx` 会挂**（collect 正常、真执行长时间不返回）
  → 覆盖改由静态守卫 + 真机 p26 承担。
  ③ **cmd 里中文参数会因代码页变形**：`vitest -t "器官错配"` 恒 0 匹配（表现全是 skipped）→ 用 ASCII 过滤词。
  ④ **`Start-Process` 子进程的 `%TEMP%` 与当前 shell 可能不同**：日志一律写**绝对路径**；
  且 **bat 不能直接 `Start-Process -FilePath x.bat`**（要 `cmd.exe /c`）。
  ⑤ 前端产物仍必须走 `scripts/deploy_frontend.sh` 口径（`VITE_BASE=/ui/`）——见上一轮 A.55 踩坑。

### 上一轮（A.55）复跑入口与踩坑

- 对比度真机取证（**本机** Playwright + 隧道；被测系统是服务器）：
  `node scripts/audit/p25_overlay_contrast.mjs`
  （需 `UAI_DOCTOR_USER/PASSWORD`；产出两主题截图 + `overlay_elements.json` DOM 旁证 +
  `contrast_result.json`；退出码 0=通过 / 1=不达标 / 2=输入不合格，可进 CI）
- 纯数学复算（不需要浏览器）：
  `python scripts/contrast.py --sidecar <json> --shot <png> --json-out <out>`
- 守卫：`pytest tests/unit/quality/test_contrast_tokens.py tests/unit/quality/test_frontend_render_guard.py`
- **踩坑（都会再犯）**：
  ① **前端产物必须走 `scripts/deploy_frontend.sh`**（它设了 `VITE_BASE=/ui/`）；直接
  `npm run build` 会用默认 base `/`，产物引用 `/assets/…` → **全站白屏**（A.55 事故）。
  构建后先看 `dist/index.html` 的引用前缀再部署。
  ② **本机 `npm run build` / `vite build` 会被工具当成 watch 命令、10s 后中断**
  （实测 dist 被清空却没写出产物）→ 用批处理 / `Start-Process` 分离进程，输出重定向到文件再读。
  ③ **工作区 URL 是 `/ui/`，不是 `/ui/workspace`**（后者被 `path="*"` 静默回落且**丢 query**）。
  ④ **`chromium.launch` 不接受 `--user-data-dir`**，要指定 profile 用 `launchPersistentContext`。
  ⑤ 改了工具/包要跑它的棘轮：A.53/A.55 的 ruff 增量已在本轮就地收敛（UP031 基线 33→24）。
  ⑥ p20 的 G1.2 同一 URL 陷阱已就地修正但**未重跑**（需 `--dicom` 真样本）——下轮跑 p20 时确认 31/31。

### 上一轮（A.54）Last Updated 原文

2026-10-12（分支 `main`；**A.54 第二梯队③ 历史对比阅片已交付**——后端
`GET /api/v1/patients/{id}/sessions` + 前端 `/ui/history`（时间线配对 / 并排影像 /
面积变化 Δ% / 跨器官不给数字）；真机 p23 **18/18**、p24 **11/11**、同批次回归
p22 **9/9**。途中修掉 2 个真缺陷：**并排影像因容器无高度 → DICOM 层塌成 0×0
（canvas 不渲染）**、**跨器官仍给「面积变化 -23.2%」伪病情变化**；另把报告页入口
从"已生成报告才出现"改为无条件渲染（患者标识两级解析）。详见 16 号附录 A.54）

### A.54 复跑入口与踩坑（下一位直接用）

- 后端真机：`cd ~/uai/src/scripts/audit && ~/uai/venv/bin/python p23_patient_history.py`
  （H1 结构 / H2 patient_exists / H3 倒序 / H5 organ 过滤 / H7 limit 422 / **H9 患者隔离** /
  **H10c 结论采用报告诊断意见** —— H10 会**现造报告 + 补录标记结论**再回查，不需要预置数据）
- 前端真机（**本机**，先起隧道 `ssh -N -L 8000:127.0.0.1:18000 student03@10.20.28.21`）：
  `node scripts/audit/p24_history_compare_ui.mjs`（报告与截图在 `C:/Windows/TEMP/uai_p24/`；
  J7 会现造「甲状腺+心脏」混合患者，需 `UAI_ADMIN_PASSWORD`）
- **踩坑（会再犯）**：
  ① **`WorkspaceDicomLayer` 必须是"有尺寸 + 定位"的容器的子元素**（它自身
  `position:absolute; inset:0`）——放进无高度 div 会**静默不渲染**（页面只显示一句含糊
  的"影像加载中/不可用"）。新页面复用它时务必给 `position:relative + height`。
  ② **跨器官不得给测值变化**：面积/测值 Δ 必须先确认 `organ` 一致，否则是伪造临床趋势
  （曾有 -23.2% 的假变化）。同理 `comparable_organs` 只有 ≥2 次才非空。
  ③ **探针选样不要复用待测字段**（p24 原先用接口的 `comparable_organs` 挑患者 = 与接口
  同错同对）→ 从 `items` 现算「同一器官 ≥2 次」。
  ④ **异步渲染断言要等待**：DICOM 加载是异步链，立即断言 canvas 会假阴性（p24 J5 曾误报）。
  ⑤ 本机 `vitest` 对含 cornerstone 依赖图的重文件（`WorkspaceImageRegion.test.tsx`）多次
  >10min 未完成；该行为由真机 p22 C6 / p24 J5 覆盖，**不要据此宣称该文件通过**。

### 上一轮（A.52）Last Updated 原文

2026-10-12（**A.52 A.51 批次端到端摸排与验证**——后端 6 断面真机探针
`scripts/audit/p19_a51_e2e_audit.py` **49/49 PASS**、浏览器层走查
`scripts/a51_ui_walkthrough.mjs` **11/11 PASS**（本机 Playwright + 隧道，真实 CAMUS
单文件 7 帧）；摸出并修复 **3 个真缺陷**：①知识库版本口径 kb_v1.0.0（会话/报告/结果，
与报告证据行的 kb_v1.3.0 自相矛盾）②前端帧级分割门控拿 Cornerstone 句柄比后端 id
→ **掩膜任何帧都不显示** ③影像加载失败静默黑屏（结论照出，更易误读）；另修多帧样本
奇数字节与产物堆积。详见 16 号附录 A.52）

### A.52 复跑入口与踩坑（下一位直接用）

- 后端摸排：`cd ~/uai/src/scripts/audit && ~/uai/venv/bin/python p19_a51_e2e_audit.py`
  （6 断面：运行期配置 / qdrant+dense 腿 / 设备列表 / 患者列表 / 端到端判读 / 部署产物）
- 浏览器走查（**本机**跑，被测系统是服务器）：先起隧道
  `ssh -N -L 8000:127.0.0.1:18000 student03@10.20.28.21`，再
  `node scripts/a51_ui_walkthrough.mjs --dicom <CAMUS 单文件多帧样本>`；
  样本在服务器现转：`scripts/camus_frame_to_dicom.py --out x.dcm --count 7 --multiframe`
  （**必须带 `--multiframe`** 才是单文件多帧路径）→ scp 回本机。
- **容量压测（第二梯队②）**：`bash scripts/run_bench.sh --levels 1,5,10,20,50 --users 10
  --cooldown 20 --poll-deadline 240 --output reports/prod_capacity_a53.json`（服务器；
  **必须以服务同源环境跑**，启动器已入库 `scripts/run_bench.sh`）。汇总脚本读
  `reports/prod_capacity_a53.json`。
- **cine 走查（第二梯队①）**：`node scripts/audit/p22_cine_playback.mjs --dicom <单文件多帧>`
  （需 `UAI_ADMIN_USER/PASSWORD` 兜底造授权患者；报告 `C:/Windows/TEMP/uai_p22/`）。
- **前端边界摸排（31 项，退出码可进 CI）**：
  `node scripts/audit/p20_frontend_boundary.mjs --dicom <同上>` —— 覆盖认证守卫 / 建会话
  输入边界（阈值·通配·注入·超长·不存在·空值·必填）/ 装载坏文件三类 / 报告页异常入参 /
  帧越界与**归属唯一性** / XSS 与视口 / 前端→后端请求面 0 个 5xx。报告落在
  `C:/Windows/TEMP/uai_p20/p20_report.json`（含请求清单）。
- **踩坑（会再犯）**：
  ①**显示栈里的 imageId 是 Cornerstone 句柄**（`dicomfile:N?frame=k`），不是后端
  `img_…`；凡是要与后端 image_id 比的地方，必须走 `frameOwners` 对齐数组（A.52.3-2）。
  ②**多帧 DICOM 每帧字节数必须为偶**（Rows×Cols 为奇 → `总长/帧数` 不整除 →
  Cornerstone 报 `Invalid frame length`；pydicom 自己却能读，所以"后端能跑通"不代表
  前端能渲染）。
  ③**前端产物必须清空式部署**（覆盖式拷贝会把历次 hash 文件留在仓库里；
  用 `scripts/deploy_frontend.sh`）。
  ④多层 props 透传改签名后 `tsc -b` 才是唯一裁判（测试夹具也要同步补字段）。
  ⑤**新建会话页「设备编号」是必填**（空则「开始检查」禁用，title 提示要填设备编号）——
  别按「后端不校验设备存在性」推断成「可以不填」。
  ⑥**FHIR 读患者必须「先存在性（404）→ 再同意（403）」**：先查同意会让不存在的患者
  变成 403「未签署同意」，把医生引去质控，且前端「404→就地建档」永不可达（A.52.3-5）。
  ⑦**医生可见的错误文案必须是中文**（后端英文原文会原样显示）；有棘轮守卫
  `tests/unit/quality/test_error_messages_are_chinese.py`，机器对机器面（device/alerts/utils）
  在允许清单里显式豁免。
  ⑨**不要随手删 `frontend/node_modules/.vite`**：清了之后本机 vitest 冷缓存重转极慢
    （>10 min 仍不产出摘要，多轮重试同现象），前端全量/单文件验证会直接卡住。
    另：`--reporter=basic` 与 `--reporter=json` **不能同时给**（两个 --reporter 会让 vitest 挂起）。
  ⑧**改了某个包的实现，必须跑那个包的测试**：A.51 改 `qdrant_client` 用 `query_points`
  时没跑 `tests/unit/emb`，5 例红被瞒了整整一轮（AsyncMock 下 `hasattr` 恒真，旧 mock 静默失效）。
- 本轮修复后的门禁：`deploy_acceptance_gate.sh` **PASS=24 FAIL=0**（服务器已部署到
  `429049a` + 后续文档提交）。

## 上一轮（A.51）Last Updated 原文

2026-10-12（**A.51 远期第一梯队四项收口**——①检索口径闭合（服务腿
hit@10 0.9853 = eval 腿，diff +0.00pp；顺带修 qdrant-client `search()` 移除 + kb 向量
未灌 两个真缺陷）；②多帧分期③择优帧 + 帧级归属（p17 15/15、p18 12/12，报告含「第
X/Y 帧」）；③设备/患者列表端点（p16 14/14，零 api_key）；④fusion-validate 降载
（P99 2296→885ms PASS，降级路径实测 `validate_degraded=1` 且结果正常返回）。
详见 16 号附录 A.51。上一轮 A.50.10 E11/E13 真 HAPI 闭环仍成立）

### A.51 复跑入口（下一位直接用）

- 探针（服务器）：`cd ~/uai/src/scripts/audit && ~/uai/venv/bin/python p15_retrieval_parity.py`
  （`p16_list_endpoints.py` / `p17_multiframe_segmentation.py` / `p18_multiframe_singlefile.py`
  / `p14b_validate_bench.py` 同目录，全部可复跑）
- 单测：`tests/unit/services/test_multiframe_frame_selection.py`、
  `tests/unit/api/test_list_endpoint_contracts.py`、`tests/unit/fus/test_validate_budget_degrade.py`
- 真多帧样本：`python scripts/camus_frame_to_dicom.py --out /tmp/x.dcm --count 7 --multiframe`
  （A.51 新增 `--multiframe`：N 帧写进**一个** DICOM，供择优帧复验）
- **踩坑（会再犯）**：用 `replace_in_file` 在类定义处插入新类时，**插入边界会吃掉相邻类的
  尾部字段**（本轮把 `DeviceRemoteRecoveryResponse.expires_at` 吞进了新类 → 运行期
  pydantic ValidationError，静态检查与 `import app.main` 都发现不了）。改 schema 后
  **必须**跑 `tests/unit/api/test_list_endpoint_contracts.py` 这类字段集守卫。
- **踩坑**：`tsc -b` 的增量缓存会掩盖 HEAD 既有类型错误；清缓存（改前端文件即失效）
  才会暴露。前端改动后务必真跑 `tsc -b + vite build`，不要只看 vitest。
- 前端产物部署：`cd frontend && VITE_BASE=/ui/ npm run build` → 拷贝 `dist/*` 到
  **`app/static/ui/`**（该目录入库，随之提交）。

### ✅ 服务器同步状态（2026-10-12 已补齐）

- **服务器 `~/uai/src` 已是 HEAD 镜像**：`git archive HEAD` 铺开 + **12 个关键文件
  sha256 对照 MATCH=12 / DIFF=0**（app 代码 5 个 + 仓储 1 个 + feature_list/progress/
  session-handoff + p15 探针 + 16 号/21 号文档）。
- 端点核验：`health=200`、`/api/v1/patients` 未授权 `401`、`/ui/` `200`。
  **应用代码与门禁通过版本 `4fefdb5` 逐字节相同，故未重启主服务**（门禁 PASS=24 依旧有效）。
- 过程插曲（备查）：同步第一次尝试因 `10.20.28.21` **ping 与 TCP 22 均不通**失败
  （3 次重试 + 60s 等待，ssh `Connection timed out`）——**服务器侧短暂不可达，非本机网络**；
  约 5 分钟后自愈，重试即成功。若再遇到：先 `Test-NetConnection 10.20.28.21 -Port 22`，
  通了再跑同步，不必改代码。
- **踩坑复现（硬规则 #1 又一次）**：把 `while read` + `sha256sum` 内联进 `ssh "..."`
  被 fish 拒绝（`Missing end to balance this while loop`）→ 一律本地落盘 `.sh` → `scp` → `bash x.sh`。
  另：**PowerShell 重定向 `>` 写清单是 UTF-16LE**，服务器 `read` 会全读成乱码（表现为全
  `MISSING`）——跨机传清单用 Python `Path.write_text(..., newline="\n")` 落 UTF-8/LF。

### 常驻服务新增（复跑 p13 前确认在跑）
- HAPI：`cd ~/uai/hapi && nohup java -jar ROOT.war --server.address=127.0.0.1 --server.port=8080 > ~/uai/logs/hapi_war.log 2>&1 &`（30s 就绪，`/fhir/metadata`）
- bridge：`cd ~/uai/src && FHIR_BRIDGE_HAPI_URL=http://127.0.0.1:8080/fhir nohup ~/uai/venv/bin/python scripts/fhir_bridge.py > ~/uai/logs/fhir_bridge.log 2>&1 &`（8091）
- PACS SCP：`cd ~/uai/src && nohup ~/uai/venv/bin/python scripts/pacs_scp.py > ~/uai/logs/pacs_scp.log 2>&1 &`（11112）
- **~/uai/.env 已设 PACS_ADAPTER=http / PACS_ENDPOINT=http://127.0.0.1:8091/reports**（报告 push 真出进程）

## Current Objective

**10-17 交付**：医生主流程**含「开新检查」与「多帧翻阅」已全通**（登录 →
新建会话/工作列表 → 多帧装载 → 帧指示器翻帧 → 判读 → 报告 → 签发 → 打印/导出，
全部真机端到端验证）。判读性能已测量+预热（重启首判分割 59.8ms，端到端 8.4s）。
**NMPA 验收材料：用户拍板留到性能数据提升、远期目标攻克之后**——当前无阻塞性剩余项，
改进方向见 feature_list 未闭合条目（LLM 模型侧提速、真实设备奇数帧长多帧的 loader 兼容、
同意撤回的消费端）。
**第二梯队进度**：①多帧分期② cine 播放（A.52.3b）②生产容量验收（A.53）③历史对比阅片（A.54）
均已交付；**第三梯队**里唯一「数据已就绪、可真机验证」的 `zero-silence-guard` 已机制化（A.58，
约定/渲染/语义三层常驻门禁）；其余为数据/口径前置（t2a 独立集、Langfuse ≥700 例）。剩余候选见
21 号路线图（含 §阶段三-A 的 t2a 数据方案）。

## 医生视角走查结论（2026-10-05 夜，下一位 Agent 先读这段）

此前所有验证只到 HTTP 层（门禁 23 项 curl + 契约对账 18 路径字符串比对），
**从未在真实浏览器里渲染过一行前端代码**。首次真实点击即发现
**临床前端此前完全不可用**，4 个 P0 全部已修并复验（commit `1ac92d5`）：

| # | 缺陷 | 根因 | 修法 |
|---|---|---|---|
| 1 | 白屏（`#root` 子节点 0） | `vendor-cornerstone` 跨 chunk 循环依赖 + Vite 8/Rolldown 求值顺序 | 从 `manualChunks` 移除 cornerstone |
| 2 | 登录永远 422 | 前端发 JSON 打到 `OAuth2PasswordRequestForm` 端点 | 改打 `/auth/login` |
| 3 | 登录后跳后端根 JSON | `window.location.href='/'` | 改用 `import.meta.env.BASE_URL` |
| 4 | 9 条二级路由全打不开 | `BrowserRouter` 缺 `basename` | `basename={BASE_URL 去尾斜杠}` |

**A.28 已闭合这2 个**（2026-10-05 下午，详见 16 号附录 A.28）：DICOM 上传入口（结论区
扫查态「选择 DICOM 影像」，装载后自动发起判读）+ 工作区导航出口（常驻条右侧三项）。
真机 Playwright 20/20 PASS，含**真传一个 DICOM 后界面自动进入「分析中」**——
`useWorkspaceFlow.start()` 第一次真正有调用方，此前判读主链没有起点。

**勘察结论值得记住**：上传端点（`POST /api/v1/imaging/dicom/upload`）与前端封装
（`imagingService.uploadDicom`）**早就写好了，只是从未被调用**。所以这类"功能缺失"
要先查是不是接线问题，而不是急着加接口。

**新增三层守卫**（下一位直接用）：
- 静态：`tests/unit/quality/test_frontend_render_guard.py`（pytest，17 例，含 5 例自测）
- 运行时：`scripts/frontend_browser_smoke.mjs`（Playwright + 真 Chrome，20 项）
  密码只能从环境变量注入：`UAI_DOCTOR_USER` / `UAI_DOCTOR_PASSWORD` / `UAI_BASE_URL`
  加 `--upload <dicom>` 可真传一份并验证自动发起判读（需 GPU 在线）
- 走查样本：`python scripts/make_test_dicom.py <路径>` 现造合规 DICOM
  （**不能取自 `dicom_storage/`**：落盘副本已被 PHI 剥离，回传必然 400）

**A.29（2026-10-06）**：补走「报告签名」这一跳时挖出**两个 P0**，均已修并加守卫 ——
①**判读结果永远等不到**：前端 `pollDiagnosisResult` 的 `maxWaitMs` 默认 30s，
而真实管线 90~300s；`timeout` 又被 `PHASE_TO_STATE` 映射回 `progress`，
于是界面**永久停在「分析中」**。A.24.1 只把服务端阈值抬到 180s，前端没跟着改。
②**报告与签名在 UI 上不可达**：`request_id` 从不交给界面，医生只能手敲 URL。
现两处都通：真机 26/26 PASS，直到**签发成功**（HSM 签名、签发人 smoke-doctor）。

**下一位先读这段**：医生主流程已全通，别再重复走查；直接看
`feature_list.json` 里 `planned` 的 4 项（NMPA 材料最急）。

**A.30（深夜）**：报告缺段落做了**知情确认**（完整性未过 → 签发先弹缺失清单，
确认后才能签；刻意不做硬禁用，口径需项目组定）；**外部字体已移除**（12 条 CSP 违规
清零，视觉零变化；要恢复设计字体走自托管）。真机走查 27/27 PASS。

**A.31（同夜）**：**影像回取已闭合** —— 后端补 GET 端点（列表 + 字节），前端显示源
改服务端副本，刷新 / 「继续检查」自动恢复最近影像（真机 27/27 PASS，审计
`dicom_retrieve` 留痕）。已登记 `ui-no-wado-retrieve-endpoint` 转 done。

**A.32（同夜）**：报告签发门禁 + 补录定稿并落地。口径（AI 拍板、依据记档于
`report_service.SIGN_BLOCKING_SECTIONS`）：缺 **findings / diagnosis**（证据段/结论段）
服务端拒签（400 E1030）；缺 **measurement_summary / clinical_actions** 走知情确认。
补录 PATCH 重渲染 + 出处记录（doctor_supplemented）。真机全闭环 28/28 PASS：
缺阻断段 → 签发被禁 → 补录 → 解禁 → 知情确认 → 已签发。

**A.33（2026-10-08）已闭合新建会话接线**：患者编号 → `GET /fhir/Patient/{id}` 一次校验
存在性 + 同意（**未签署/撤回 → 引导找质控，医生无权补签是事实**；404 → 就地建档后重新
校验，建档≠同意）；设备号录入 + localStorage 记住上次；「开始检查」带 `?session_id=`
直达工作区（旧实现 `navigate('/')` 把新会话 id 丢了）。真机端到端 6/6 PASS。
**关键坑（已写进 fhirService 注释与契约对账脚本）**：FHIR 挂在 `/fhir` 不带 `/api/v1`，
每个请求必须 `baseURL: ''` 覆盖全局前缀，否则永远 404。

**A.34 已收口可修项，剩余全部为「等外部条件」，无代码侧欠账**：
①**NMPA 材料**（项目决策 2026-10-08：延期至性能数据提升、远期目标攻克后；可借鉴 cmde.org.cn
  公开的 AI 三类证技术审评报告，系统证据映射见 A.33/A.34）；②consent 排程 handler（排程系统
  本体不存在，handler 是安全的 no-op 占位）；③UDT/faithfulness/覆盖率/甲状腺数据（语料与
  项目组决策）；④患者/设备列表端点（依赖登记数据，现为校验+录入+记忆补位）。
**医生全流程最终摸排 13/13 PASS**：`scripts/doctor_flow_walkthrough.mjs`（登录→新建会话→
装载→判读→报告→生成草稿→补录→签发闭环；报告草稿需医生点「生成草稿」，无草稿时 404+引导是正常态）。

录像：`C:\Users\ghf\Videos\US-DX-医生视角走查-20261005.webm`（2.71MB）。
走查脚本用 Playwright + 复用 `~\.agent-browser\browsers\chrome-154\chrome.exe`，
录屏需 `npx playwright install ffmpeg`。**下一位做真浏览器验证时直接复用这套**。

## A.49 端到端管线摸排（2026-10-10，下一位先读这段）

**用户指令**：「走端到端摸排，列出要摸排的管线，一一排查过去！」10 条管线
（走查/影像交互/测量标注/认证/同意撤回/设备/告警/报告分发/辅助/FHIR）逐一
真机实测，探针套件**入库 `scripts/audit/`（p0_admin_bootstrap + p4~p10，
可复跑）**：探针 71 PASS / 1 P2 观察。修复 3 真缺陷（详见 16 号 A.49.2），
甄别 4 个探针误判（别再重查：refresh 宽限窗 30s / attributes 中文 503 是
语言护栏 / FHIR 读端点要 consent 前置 / 设备同名注册已被拒绝）。

**本轮踩坑（都会再犯的那种）**：
- **build 丢 `VITE_BASE=/ui/` → 全站白屏**：每条命令都是新 shell，`$env:VITE_BASE`
  必须与 build 同一条命令；build 后先查 `dist/index.html` 资源前缀再部署。
- **登录限流**：探针登录几十次/天必撞 E1007 429——`scripts/audit/_audit_common.py`
  login 已带退避重试；写新探针直接复用它（别再裸 curl login）。
- **探针断言先对齐设计再跑**：8 个 FAIL 里 4 个是误判，先查代码定性。
- 服务器 /tmp 历史探针剩 25 个（清理命令需审批窗口未执行，见硬规则 #11）。
- smoke-admin（SYSTEM_ADMIN）账号已建（密码 ~/uai/.smoke_admin_pw，600），
  管理面探针靠它；p0_admin_bootstrap.py 可重建。

## A.50 远期第一梯队（2026-10-11）

- **语音引擎**：~/uai/models/funasr（fp32+int8 ONNX + tokens.txt 逐行 token 格式
  + test_wavs 真实语音），~/uai/src/models/funasr 是符号链接；适配器
  app/speech/features.py（numpy 特征管线）+ funasr.py（paraformer 路径）；
  功能验证 `scripts/audit/validate_asr.py`。**勿动共享 venv**（torch 被 vLLM 钉死）。
- **钉子**：多文件改动按文件 scp 容易漏（manager.py 漏发 → 修了还 400，探针抓到）；
  `awk f > f` 自截断；pydantic v2 bytes 字段吃 str 按 utf-8 编码非 base64 解码；
  CTC 连续重复折叠是正确语义。
- /tmp 自己的历史 _ 探针已清零（A.50.5）；root 所属 1 个不动（铁律 #3）。
  fish 对无匹配通配符整行中止——含通配符远端命令必须包 bash -c。
- **qdrant 未启动**（RAG 跑 pgvector 降级链路，4526 行正常返回）——设计内降级，
  feature_list `qdrant-deployment` 登记：上线后复测检索质量口径。
- **本机无 make**（WSL 有）——make 系列目标本机用 WSL 或 CI 跑。

## 凭据泄漏：已决定不轮换（A.28.10 更正 A.27.7 的建议）

`smoke-doctor` 密码明文进入过A.27 会话记录。**本轮决定不轮换**，理由与适用边界见
16 号附录 A.28.10：它是测试账号、只访问合成病例、主服务只绑回环、轮换会打断正在进行的
交付走查。**若该账号将来用于任何真实数据，必须立即轮换。**

该文件是纯密码、无 `=` 结构，别再用 `cut -d= -f1`「只看键名」—— 那等于打印密码。
本轮所有脚本已改为从环境变量注入，脚本与命令行均无密码字面值。

## 本机如何做浏览器验证（2026-10-05 新增，下一位 Agent 直接用）

主服务**只绑服务器 loopback**（硬规则 #3），所以本机必须走 SSH 隧道：

- 访问地址：**`http://127.0.0.1:8000/ui/`**
- 隧道已常驻：`~\.uai\keep_tunnel.ps1` + 计划任务 `UaiKeepTunnel`（登录自启；断了 5s 自动重连）
- 手动重连：`ssh -N -L 8000:127.0.0.1:18000 student03@10.20.28.21`
- 日志：`~\.uai\keep_tunnel.log`；删计划任务：`schtasks /Delete /TN UaiKeepTunnel /F`
- 改前端代码时：另起 `cd frontend && npm run dev`（3000 端口，vite 默认代理到 `127.0.0.1:8000`）
- 服务器重启主服务：走权威脚本 `bash ~/uai/src/scripts/start_main.sh`（**别再手写启动命令**）

## Snapshot（交接摘要）

- **交付三大阻塞已修**（A.23）：①8002 缺 `/v1/chat/completions` → LLM 全链路 404，已换回
  官方 OpenAI server（Qwen2.5-7B-Instruct，gpu-mem 0.46）；②临床前端**从未部署**——已构建
  （VITE_BASE=/ui/）并挂载 `/ui` + SPA 回落，契约对账 18 路径全命中；③8006 判官被误改为
  vLLM，已恢复 llama_cpp_server + GGUF（**启动必须串行**，并发互踩 memory probe）。
- **P2-10 订正并修复**：多器官并未"绕开 SG"（逐器官跑完整管线），真实缺口是
  `create_request` 漏传 clinical_text/patient_context/image_ids → SG 无事实运行；
  已透传 + 从同会话继承，5 条单测钉住。
- **新守卫**：`test_deploy_script_refs.py`（部署脚本悬空引用——llama_cpp_server.py 与
  vllm_server.py 先后被当探针删除但脚本仍引用，同款事故两次）；`check_frontend_api_contract.py`
  （前后端契约对账——**不能用 ast.parse**，TSX 必炸且静默 continue 会产出假通过）。
- **验收门禁**：`scripts/deploy_acceptance_gate.sh` 8 组 23 项，当前 PASS=23 FAIL=0；
  助手脚本已正式入库（drill_token/drill_pick_report/drill_tamper_check），/tmp 探针已清零。
- 分割旁路**默认开启**（`nodule_contour_in_pipeline_enabled` 代码默认 `True`，`settings.py:377`；
  A.52 服务器**运行期实测**：`GET /api/v1/health` 的旁路开关与判读结果里的
  `nodule_segmentation.available=True` 一致）。开关按**启动时**读取环境变量
  `NODULE_CONTOUR_IN_PIPELINE_ENABLED`，**改完必须重启主服务**才生效（事后改 `.env` 无效）。
- 多器官分割矩阵：thyroid 0.7961 / cardiac 0.8395（CAMUS，按患者划分）/ liver 无公开数据。
- 未跟踪文件待处置：`scripts/_verify_services.sh`、`scripts/server_restart_api.sh`、`scripts/_smoke_graph.py`。

## 并发与性能审查（2026-10-03，本轮）

- **发现 26 项（P0×4 / P1×12 / P2×10），已修 17 项**，全部零语义风险项（加锁/信号量/上界/
  超时/回收/offload）；9 项未修并写明理由（其中 2 项需产品决策、1 项是 HEAD 既有 INT 失败）。
- 关键 P0：①多器官联合诊断**原先完全无限流**；②**refresh token rotate 的 check-then-act
  竞态**（同一 token 可派生两个有效后继 → reuse 检测被绕过）；③WS 下行**持锁 await
  send_json**；④登录 bcrypt + HSM 签名、onnxruntime、pydicom 解析在事件循环里同步执行。
- 关键 P1：诊断管线**无端到端超时**（`pipeline_dispatch/fusion_budget_s=6.0` 是从未被读取的
  孤儿配置，最坏 ~240s 占死全部 worker 槽位）→ 新增 `pipeline_total_budget_s=180`。
- 报告：`reports/concurrency_perf_audit.md` / `.json`；压测：`reports/concurrency_perf_report.json`
  （脚本 `scripts/bench_concurrency_staircase.py`，1/5/10 走 HTTP 多医生、20/50 走 direct_enqueue）。
- 回归：新增 46 例（4 个 `test_concurrency_audit_fixes.py`）；核心四包覆盖率 **91%**（上轮 90.04%）。
- **真机遗留**：接手时 8002 未监听（被并行工作线开 GPU 窗口时 `pkill VLLM::EngineCore` 连杀），
  本轮以 `--gpu-memory-utilization 0.40` 拉起 Qwen2.5-7B-Instruct（与判官 8006 共卡）。

## Blockers

| 阻塞 | 谁解 | 备注 |
|---|---|---|
| faithfulness 95% 门控口径 | 项目组 | 判官侧已穷尽（双口径 + 思考对照三方验证） |
| 甲状腺注册数据 | 管理决策 + 授权 | 无商用公开集，须自有/合作医院 |
| 核心覆盖率 90% | 真机依赖 | SoftHSM / 真实 Redis 语义 |
| 服务器代码与仓库同步 | 本机可做 | `git archive` 铺 HEAD + 重启主服务 |
| t2a 独立验证集数据 | 项目组 / 数据方 | 现 AUC 0.8751 属开发集内部验证；方案已备（21 号 §阶段三-A），缺**独立集数据** |
| Langfuse 评测管理 ≥700 例采集 | 环境 + 排期 | 机制已就绪，缺采集窗口（依赖性能阶段） |
| SG 禁忌提示中文化 | 本机可做（可回归） | A.58.10① 登记：`[cardiac] LVEF=unknown, severity=normal` 直进医生可见告警 |

## Files（交接涉及的路径）

- Harness：`AGENTS.md`、`progress.md`、`feature_list.json`、`session-handoff.md`、`init.sh`
- Skills：`.codebuddy/skills/remote-gpu-server-devtest/{SKILL.md,reference.md}`、`.codebuddy/skills/uai-harness-maintenance/{SKILL.md,reference.md}`
- 事实源：`docs-for-guiding generating docs/超声诊疗决策支持系统/{00-项目总览与核心指标.md,16-开发进展汇报-20260928.md}`
- 验证：`Makefile`、`pyproject.toml`、`.github/workflows/{ci.yml,security-scan.yml}`

## Next Session

1. 读 `progress.md` → 执行 `Recommended Next Step`（口径决策 / UDT 送审 / 清收未跟踪文件）。
2. 若触服务器：加载 SKILL `remote-gpu-server-devtest`，先读「铁律（先读）」5 条。
3. 收尾按 `AGENTS.md` 的 `End of Session` 走，并跑 harness 审计（分数 ≥70）。

## Restart Markers（clean restart 检查）

- [ ] `./init.sh` 通过（或说明为何跳过）。
- [ ] `progress.md` 的 Last Updated / Current Objective / Recommended Next Step 已刷新。
- [ ] `feature_list.json` 状态迁移已记录（含 evidence）。
- [ ] 新结论已追加 `16` 号附录 A（如涉及实测数字/决策）。
- [ ] 远程 `/tmp/_*` 已清、GPU 进程清单核对（嵌入×3 + vLLM 8002）。
