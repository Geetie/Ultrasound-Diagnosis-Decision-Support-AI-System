"""临床前端的**运行时可达性**守卫（静态层）。

问题背景
---------
2026-10-05 首次在真实浏览器里渲染前端，一次点击就抓到 **4 个 P0 阻断缺陷**，
而当时**全部自动化门禁都是绿的**：

======== ========================================== ==========================
 现象 根因                                      静态层能否抓到
======== ========================================== ==========================
 白屏  ``@cornerstonejs/*`` 内部循环依赖 +          能（manualChunks 里出现
       ``manualChunks`` 强行切独立 chunk，Vite 8     cornerstone 即回归）
       （Rolldown）下基类未初始化就 ``extends``
 登录  前端发 JSON 到 ``OAuth2PasswordRequestForm`` 能（前端打的是哪个端点 +
 422   的 ``/auth/token``（只收 form-urlencoded）    后端该端点收什么）
 登录后 前端 ``window.location.href = '/'``，而     能（字面量 '/' 出现在跳转里）
 跳后端 ``/`` 是后端根 JSON
 根 JSON
 二级页 ``BrowserRouter`` 缺 ``basename``，产物挂   能（BrowserRouter 无 basename）
 全打不开 ``/ui``，``path="*"`` 全回落成工作区
======== ========================================== ==========================

也就是说：**"契约对账 18 路径全命中"与"页面能渲染"是两件事**。
契约对账原理上抓不到上面任何一条 —— 它们全在"路径字符串正确"之后才发生。

本守卫钉住这四类的**静态可判定形态**，让它们进 CI；
真正"渲染出来了吗"由 ``scripts/frontend_browser_smoke.py``（Playwright + 真
Chrome）负责。两层缺一不可：静态层抓不到运行时，本脚本在无浏览器的机器上跑不了。

另外钉两条 2026-10-05 走查发现、且**极易复发**的缺口：
医生侧的 DICOM 上传入口（实测全站 ``input[type=file]`` = 0）与工作区导航出口
（实测 ``btn=[]``，登录后被困住）。
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND = REPO_ROOT / "frontend"
SRC = FRONTEND / "src"
APP = REPO_ROOT / "app"


def _read(path: Path) -> str:
    assert path.is_file(), f"找不到文件：{path}"
    return path.read_text(encoding="utf-8", errors="replace")


def strip_js_comments(text: str) -> str:
    """去掉 TS/TSX 的注释，保留字符串字面量内容。

    为什么必须剥：本守卫断言的是"代码里不许出现某个字面量"，而
    ``LoginPage.tsx`` 的注释里**故意**留着 ``/auth/token`` 与
    ``window.location.href = '/'`` 作为踩坑记录。第一版直接扫原文，
    立刻被自己的注释判红——这正是本仓库记过的"本地跑一次假红 48 例"同一类陷阱：
    **守卫误报一次，之后就没人再信它了。**

    实现是逐字符状态机而不是正则：正则分不清 ``'https://x'`` 里的 ``//``
    与注释，会把字符串内容也吃掉。
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch in "'\"`":
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                if text[i] == "\\":
                    out.append(text[i : i + 2])
                    i += 2
                    continue
                out.append(text[i])
                i += 1
                if text[i - 1] == quote:
                    break
            continue
        if ch == "/" and nxt == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _code(path: Path) -> str:
    """读 TS/TSX 源码并剥掉注释（Python 文件原样返回）。"""
    text = _read(path)
    return strip_js_comments(text) if path.suffix in {".ts", ".tsx"} else text


class TestGuardItself:
    """守卫的自测。

    不测这个的话，一条"永远通过"的守卫比没有守卫更坏 —— 它会给人虚假的安全感。
    这里只测两件最容易悄悄坏掉的事：剥注释没剥干净（→ 假红）、
    判红条件写得太松（→ 漏报）。
    """

    def test_strip_removes_line_comment_but_keeps_url_in_string(self):
        src = "const a = 1; // window.location.href = '/'\nconst u = 'https://x//y';"
        out = strip_js_comments(src)
        assert "href" not in out
        assert "https://x//y" in out

    def test_strip_removes_block_comment(self):
        src = "/* window.location.href = '/' */\nconst a = 1;"
        assert "href" not in strip_js_comments(src)

    def test_strip_keeps_regex_and_template_literals(self):
        src = "const re = /\\/ui\\/assets/g;\nconst t = `base${x}`;"
        out = strip_js_comments(src)
        assert "/\\/ui\\/assets/g" in out
        assert "`base${x}`" in out

    def test_cornerstone_in_manual_chunks_is_detected(self):
        fake = (
            "export default { build: { rollupOptions: { output: { manualChunks(id) { "
            "if (id.includes('@cornerstonejs/core')) return 'vendor-cornerstone'; "
            "} } } } }"
        )
        body = fake[fake.index("manualChunks(") :]
        offenders = [t for t in ("cornerstone", "dcmjs", "wado") if t in body.lower()]
        assert offenders == ["cornerstone"]

    def test_clean_manual_chunks_is_not_flagged(self):
        vite = _code(FRONTEND / "vite.config.ts")
        body = vite[vite.index("manualChunks(") :]
        assert not [t for t in ("cornerstone", "dcmjs", "wado") if t in body.lower()]


class TestNoBlankScreen:
    """P0-1：白屏。 cornerstone 不得被 manualChunks 切成独立 chunk。"""

    def test_manual_chunks_does_not_split_cornerstone(self):
        vite = _code(FRONTEND / "vite.config.ts")
        assert "manualChunks(" in vite, (
            "vite.config.ts 里找不到 manualChunks —— 若确实已移除，请连同本守卫项一起显式删除"
        )
        body = vite[vite.index("manualChunks(") :]
        offenders = [t for t in ("cornerstone", "dcmjs", "wado") if t in body.lower()]
        assert not offenders, (
            f"manualChunks 里出现了 {offenders} —— @cornerstonejs/* 存在内部循环依赖，"
            "Vite 8（Rolldown）按 manualChunks 强行切独立 chunk 后，跨 chunk 循环引用"
            "在 ESM 严格求值顺序下基类尚未初始化就 extends，整个 vendor chunk 抛错、"
            "React 从未挂载，#root 子节点为 0（2026-10-05 实测白屏）。交给 Rolldown 自动分割。"
        )


class TestLoginReachable:
    """P0-2：登录永远 422（JSON 打到只收 form 的 OAuth2 端点）。"""

    def test_frontend_posts_to_json_login_endpoint(self):
        login_page = _code(SRC / "pages" / "LoginPage.tsx")
        assert "/auth/token" not in login_page, (
            "前端又打回 /auth/token —— 该端点是 OAuth2PasswordRequestForm = Depends()，"
            "只收 application/x-www-form-urlencoded，axios 发 JSON 一律 422（登录永远失败）"
        )
        assert "/auth/login" in login_page

    def test_backend_login_endpoint_really_accepts_json(self):
        auth = _code(APP / "api" / "v1" / "auth.py")
        m = re.search(r'@router\.post\("/login".*?\n(?:async )?def \w+\(([^)]*)\)', auth, re.S)
        assert m, "后端 /auth/login 端点不见了 —— 前端打的路径会 404"
        params = m.group(1)
        assert "OAuth2PasswordRequestForm" not in params, (
            "/login 的入参退化成 OAuth2PasswordRequestForm 了 —— 前端发 JSON 会再次 422"
        )
        assert "LoginRequest" in params, "/login 应收 Pydantic 的 LoginRequest（JSON）"


class TestNoJumpToBackendRoot:
    """P0-3：登录后跳到后端根 JSON；以及 401 回登录页的同族缺陷。"""

    def test_no_literal_root_redirect(self):
        offenders: list[str] = []
        for path in SRC.rglob("*.ts*"):
            text = _code(path)
            for m in re.finditer(r"""window\.location\.href\s*=\s*['"]([^'"]*)['"]""", text):
                target = m.group(1)
                if target in ("/", "/ui", "/login", "/worklist", "/settings", "/report"):
                    offenders.append(f"{path.relative_to(REPO_ROOT)} → {target}")
        assert not offenders, (
            f"这些跳转写死了根路径：{offenders} —— 前端产物挂在 /ui，写死绝对路径会跳到"
            "后端（根 JSON / 404），医生看到的是一串 JSON 或 404，界面直接断掉。"
            "用 import.meta.env.BASE_URL。"
        )

    def test_login_and_401_redirects_use_base_url(self):
        assert "import.meta.env.BASE_URL" in _code(SRC / "pages" / "LoginPage.tsx")
        assert "import.meta.env.BASE_URL" in _code(SRC / "services" / "api.ts"), (
            "401 回登录页的跳转也必须带 BASE_URL —— 写死 '/login' 会把医生甩到后端 404，"
            "表现为「令牌一过期整个界面就打不开」"
        )


class TestRouterBasename:
    """P0-4：所有二级页面打不开（BrowserRouter 缺 basename）。"""

    def test_browser_router_has_basename_from_base_url(self):
        text = _code(SRC / "app" / "providers.tsx")
        assert "<BrowserRouter" in text, "找不到 BrowserRouter"
        m = re.search(r"<BrowserRouter\b([^>]*)>", text, re.S)
        assert m, "BrowserRouter 标签解析失败"
        attrs = m.group(1)
        assert "basename" in attrs, (
            "BrowserRouter 没传 basename —— 产物挂在 /ui 时路由看到的是 /ui/xxx，"
            "匹配不到任何路由 → path='*' 全回落成工作区，所有二级页都打不开"
        )
        assert "BASE_URL" in attrs, "basename 必须来自 import.meta.env.BASE_URL，别硬编码"


class TestSpaFallbackMount:
    """钉子 ④：不能用 mount('/ui')，它会吃掉所有 /ui/* 让 SPA 回落永不生效。"""

    def test_assets_mounted_on_subpath(self):
        main = _code(APP / "main.py")
        assert (
            'app.mount(\n            "/ui/assets"' in main or 'app.mount("/ui/assets"' in main
        ), (
            "找不到 /ui/assets 的精确挂载 —— mount('/ui') 会按注册顺序吃掉所有 /ui/*，"
            "SPA 回落路由永不匹配（实测 /ui/workspace 全 404）"
        )
        assert 'app.mount("/ui"' not in main, "mount('/ui') 会吃掉 SPA 回落路由"


class TestDoctorCanLoadDicom:
    """P0（2026-10-05 走查实测全站 input[type=file] = 0，含隐藏）：影像装载入口。"""

    def test_a_dicom_file_input_exists_in_source(self):
        hits: list[str] = []
        for path in SRC.rglob("*.tsx"):
            text = _code(path)
            if re.search(r"type\s*=\s*(['\"])file\1", text):
                hits.append(str(path.relative_to(REPO_ROOT)))
        assert hits, (
            "前端源码里没有任何 file input —— 医生无法在界面上装载影像，"
            "手册「步骤 2 上传 DICOM」做不到（2026-10-05 真浏览器走查实测，含隐藏控件也为 0）"
        )

    def test_file_input_accepts_dicom(self):
        upload = _code(SRC / "components" / "workspace" / "WorkspaceImageUpload.tsx")
        assert ".dcm" in upload and ".dicom" in upload

    def test_upload_is_not_visual_only(self):
        """控件必须真的上行 —— 本地显示不等于影像进了系统。"""
        upload = _code(SRC / "components" / "workspace" / "WorkspaceImageUpload.tsx")
        assert "useDicomUpload" in upload, "上传控件没有接 useDicomUpload，只是个装饰"


class TestWorkspaceHasNavExit:
    """P1（2026-10-05 走查实测 /ui/ 的 btn=[]）：工作区必须有导航出口。"""

    def test_workspace_page_renders_nav_items(self):
        page = _code(SRC / "pages" / "WorkspacePage.tsx")
        assert "navItems" in page, (
            "工作区没有 navItems —— 它是全站唯一不套 AppLayout 的页面（通栏 900 骨架），"
            "没有出口医生登录后就困在这里，只能手动改 URL"
        )

    def test_workspace_route_passes_nav(self):
        routes = _code(SRC / "app" / "routes.tsx")
        assert "navItems={" in routes, "路由层没把导航出口传给工作区"
