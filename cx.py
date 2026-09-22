#!/usr/bin/env python3
"""cx — Codex 模型直连切换器

无代理、无常驻进程。Codex 直连 deepseek(Responses API) / 原生 ChatGPT。
命令:
  cx status              查看当前模型配置与最近会话
  cx fix-all deepseek|gpt
                         把所有老会话批量切换到目标模型(需退 App)
                         例: cx fix-all deepseek --limit 10 (只改最近 10 个)
  cx use deepseek|gpt    切换默认模型(只针对新会话生效)
  cx doctor              体检: key/直连/会话健康
  cx key <API_KEY>       保存 deepseek key 并注入 GUI 环境(launchctl setenv)
  cx fix <会话ID>        修复单个会话(续跑一轮写入正确模型)
"""
import json, os, re, glob, sqlite3, subprocess, sys, shutil, time, urllib.request, urllib.error

CX_VERSION = "1.0.11"

CODEX_HOME = os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex"))
CONFIG = os.path.join(CODEX_HOME, "config.toml")
CX_DIR = os.path.expanduser("~/.cx")
KEY_FILE = os.path.join(CX_DIR, "deepseek.key")
CATALOG = os.path.join(CODEX_HOME, "cx-catalog.json")
CODEX_BIN = "/opt/homebrew/bin/codex"
APP_PGREP = "/Applications/ChatGPT.app/Contents/MacOS/ChatGPT"
APP_PGREP_LIST = [
    "/Applications/ChatGPT.app/Contents/MacOS/ChatGPT",
    "/Applications/Codex.app/Contents/MacOS/Codex",
]
APP_NAME = "ChatGPT"            # osascript / open -a 用的应用名

DS_MODEL = "deepseek-flash"   # V4.1-Flash;旧的 deepseek-chat/deepseek-reasoner 已被服务端别名到它,不再使用
DS_PROVIDER_BLOCK = """
# --- added by cx (direct deepseek) ---
[model_providers.deepseek]
name = "DeepSeek"
base_url = "https://api.deepseek.com/v1"
env_key = "DEEPSEEK_API_KEY"
wire_api = "responses"
"""

GPT_MODEL = "gpt-5.6-sol"

HELP = """cx — Codex 模型直连切换器

用法: cx <命令> [参数]

命令一览:
  setup <API_KEY>      全新机器一键接入 DeepSeek(无需 GPT 账号/登录,含冒烟测试)
  status               查看默认模型/provider/最近会话(不带参数执行 cx 等同于此)
  fix-all deepseek|gpt 批量把所有老会话切换到目标模型(自动退出 App,完事自动重开)
                       用法: cx fix-all deepseek|gpt [--limit N]
                       例: cx fix-all deepseek --limit 10 (只改最近 10 个)
  use deepseek|gpt     切换默认模型(只针对新会话生效;自动退出并重开 App)
  key <API_KEY>        保存 deepseek key 并注入 GUI 环境(桌面 App 需要)
  doctor               体检: key 有效性/直连连通性/会话健康
  fix <会话ID|last>    修复单个会话:清理不兼容历史条目 + 续跑一轮(ID 取 status 前 8 位)
  version              显示 cx 版本
  help                 显示本帮助

可选开关(配合 use / fix / fix-all):
  --no-reopen          改完不要把 App 重新打开(默认改完自动重开)
  --keep-app           (use 里同 --no-restart)完全不动 App,在运行则直接报错(旧行为)

要点:
  - 切换只影响新会话;老会话各自保持原模型,想搬运用 fix / fix-all
  - 改配置/改会话需要 App 重读,这三条命令会**自动优雅退出 App,完事再拉起来**
  - 仓库: https://github.com/git-sgg/codex-model-change
"""

def err(m): print("❌ " + m); sys.exit(1)
def ok(m): print("✅ " + m)
def info(m): print("ℹ️  " + m)
def warn(m): print("⚠️  " + m)

def read_cfg():
    if not os.path.exists(CONFIG):
        return ""   # 全新机器:尚未生成 config.toml,当空配置处理
    return open(CONFIG, encoding="utf-8").read()

def backup_cfg(tag=""):
    if not os.path.exists(CONFIG): return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = f"{CONFIG}.bak.cx-{tag}{ts}"
    shutil.copy2(CONFIG, dst)
    ok(f"已备份: {dst}")
    return dst

def load_key():
    if not os.path.exists(KEY_FILE): return None
    return open(KEY_FILE).read().strip() or None

# ---------- 目录文件 ----------
def _ds_entry(slug, name, desc, priority, ctx=1048576, compact=900000):
    return {
        "slug": slug, "display_name": name, "description": desc,
        "default_reasoning_level": "high",
        "supported_reasoning_levels": [
            {"effort": "low", "description": "Fast responses with lighter reasoning"},
            {"effort": "high", "description": "Greater reasoning depth for complex problems"}],
        "shell_type": "default", "visibility": "list", "supported_in_api": True,
        "priority": priority,
        "base_instructions": f"You are a coding agent powered by {slug}.",
        "include_skills_usage_instructions": False,
        "default_reasoning_summary": "none", "support_verbosity": False, "default_verbosity": "low",
        "apply_patch_tool_type": "freeform", "web_search_tool_type": "text",
        "truncation_policy": {"mode": "tokens", "limit": 10000},
        "supports_parallel_tool_calls": True,
        "supports_image_detail_original": False, "comp_hash": "cx1",
        "effective_context_window_percent": 95,
        "experimental_supported_tools": [], "input_modalities": ["text"],
        "supports_search_tool": False, "tool_mode": "default",
        "node_repl_disabled": True, "node_repl_auto_review_required": False,
        "include_plugin_usage_instructions": False, "include_apps_usage_instructions": False,
        "supports_reasoning_summaries": False, "context_window": ctx,
        "max_context_window": ctx, "auto_compact_token_limit": compact,
        "auto_review_model_override": None,
    }

CATALOG_MODELS = [
    _ds_entry("deepseek-flash", "DeepSeek Flash", "DeepSeek 直连 (deepseek-flash / V4.1, 1M 上下文)。", 5),
    _ds_entry("deepseek-v4-pro", "DeepSeek V4 Pro", "DeepSeek 直连 (deepseek-v4-pro, 1M 上下文)。", 4),
]
for slug in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", "gpt-5.4"):
    e = _ds_entry(slug, slug, slug, 1, ctx=400000, compact=360000)
    e["visibility"] = "hide"
    e["input_modalities"] = ["text", "image"]
    e["supports_reasoning_summaries"] = True
    CATALOG_MODELS.append(e)

def write_catalog():
    with open(CATALOG, "w", encoding="utf-8") as f:
        json.dump({"models": CATALOG_MODELS}, f, ensure_ascii=False, indent=1)

# ---------- config.toml 编辑(基于行锚点,不做完整 TOML 解析) ----------
def ensure_provider_block(text):
    if "[model_providers.deepseek]" in text:
        # 移除旧块再重写,保证与模板一致
        text = re.sub(r"\n?# --- added by cx \(direct deepseek\) ---\n\[model_providers\.deepseek\][^\[]*", "\n", text)
    return text.rstrip() + "\n" + DS_PROVIDER_BLOCK

def set_active_model(text, model, provider):
    text = re.sub(r"(?m)^model\s*=.*$", f'model = "{model}"', text, count=1)
    if "model = " not in text:
        text = f'model = "{model}"\n' + text
    if provider == "openai":
        # 注释掉 model_provider,回落到内置 openai(ChatGPT 登录态)
        text = re.sub(r"(?m)^model_provider\s*=.*$", "# model_provider = \"openai\"  # cx: 原生 gpt", text, count=1)
    else:
        if re.search(r"(?m)^model_provider\s*=", text):
            text = re.sub(r"(?m)^model_provider\s*=.*$", f'model_provider = "{provider}"', text, count=1)
        else:
            text = text.replace('model = ', f'model_provider = "{provider}"\nmodel = ', 1)
    return text

def ensure_catalog_key(text):
    """保证顶层 model_catalog_json 指向 cx 目录。必须插在首个 [table] 头之前,否则会被 TOML 归入表内而失效。"""
    if re.search(r"(?m)^model_catalog_json\s*=", text):
        return text
    line = f'model_catalog_json = "{CATALOG}"\n'
    m = re.search(r"(?m)^\[", text)
    if m:
        return text[:m.start()] + line + "\n" + text[m.start():]
    return text.rstrip() + "\n\n" + line

def clean_ocx_injections(text):
    lines, out = text.split("\n"), []
    for ln in lines:
        s = ln.strip()
        if s.startswith("openai_base_url ="): continue            # ocx 注入:指回原生
        if s.startswith("experimental_realtime_ws_base_url ="): continue
        if s.startswith("service_tier ="): continue               # deepseek 不支持,纯噪音
        if s.startswith("model_catalog_json ="):
            out.append(f'model_catalog_json = "{CATALOG}"'); continue
        out.append(ln)
    return "\n".join(out)


# ---------- App 投影缓存对齐 ----------
# 背景(踩过的坑,务必保留):
#   rollout 每条记录带顶层 "ordinal" 字段,且 App 要求它从 0 起、逐条 +1 连续。
#   cx fix-all 会两处改动 rollout:
#     1) rewrite_rollout 重新序列化 turn_context/session_meta → 整文件字节偏移漂移;
#     2) sanitize_rollout 删除 reasoning 行 → ordinal 出现缺口。
#   而 App 的投影(thread_history_1.sqlite)按 ordinal + 字节偏移增量推进,一旦遇到缺口
#   就永久卡死("expected ordinal N, got N+1"),新内容再也进不了投影缓存 ——
#   表现就是"跑完 fix-all 后重开 App,今天聊的内容消失了"(live 会话内存里其实还在)。
# 因此 fix-all 必须:先重编号 ordinal 消除缺口,再清掉该会话的投影缓存,
#   让 App 下次打开时从(已连续的)rollout 完整重建。重建是唯一 100% 安全的做法。
import re as _re
_ORD_PAT = _re.compile(rb'"ordinal":\s*(\d+)')
_ORD_SUB = _re.compile(rb'"ordinal":\s*\d+')

def read_ordinals(path):
    """按行序读出每行顶层 ordinal 值(缺失为 None)"""
    out = []
    for ln in open(path, "rb"):
        m = _ORD_PAT.search(ln)
        out.append(int(m.group(1)) if m else None)
    return out

def renumber_ordinals(path):
    """把每行 ordinal 重写为行号,消除删行造成的缺口。返回改写行数。
    正常 rollout 每行都有 ordinal 且原本 ordinal==行号,故以行号为准可完美复原连续性。"""
    lines = open(path, "rb").readlines()
    changed = 0
    for i, ln in enumerate(lines):
        new = _ORD_SUB.sub(b'"ordinal":' + str(i).encode(), ln, count=1)
        if new != ln:
            lines[i] = new; changed += 1
    if changed:
        open(path, "wb").writelines(lines)
    return changed

def reset_app_projection(thread_id):
    """清掉 App 对某会话的投影缓存,强制其下次打开时从 rollout 重建。
    rollout 被改写/删行后这是唯一安全做法:避免 ordinal 缺口与字节偏移错位残留。
    (thread_history_projection_state 上有 DELETE 触发器,会连带清 thread_realtime_items)"""
    db_path = os.path.join(CODEX_HOME, "thread_history_1.sqlite")
    if not (thread_id and os.path.exists(db_path)):
        return False
    db = sqlite3.connect(db_path)
    try:
        has = lambda t: db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
        for t in ("thread_items", "thread_turns", "thread_history_projection_state"):
            if has(t):
                db.execute(f"DELETE FROM {t} WHERE thread_id=?", (thread_id,))
        db.commit()
        return True
    finally:
        db.close()

# ---------- 命令 ----------
def cmd_use(which, restart=True):
    if which not in ("deepseek", "gpt"): err("用法: cx use deepseek|gpt [--no-restart]")
    flags = set(a for a in sys.argv[2:] if a.startswith("-"))
    if flags & {"--no-restart", "--keep-app"}: restart = False
    # 桌面 App 只在启动时读 config.toml:先自动退出,写完再拉起来,新会话即刻生效
    was_running = quit_app() if (restart and app_running()) else False
    backup_cfg(f"use-{which}-")
    text = read_cfg()
    if which == "deepseek":
        text = ensure_provider_block(text)
        text = set_active_model(text, DS_MODEL, "deepseek")
        text = ensure_catalog_key(text)
    else:
        text = set_active_model(text, GPT_MODEL, "openai")
    text = clean_ocx_injections(text)
    write_catalog()
    os.makedirs(CODEX_HOME, exist_ok=True)
    open(CONFIG, "w", encoding="utf-8").write(text)
    ok(f"已切换到 {which}: model 已写入,备份在同目录")
    if which == "deepseek" and not load_key():
        info("未保存 deepseek key,先运行: cx key <API_KEY> (或直接 cx setup <API_KEY> 一键完成)")
    if was_running:
        open_app()
    else:
        info("桌面 App 需完全退出重开才会重读配置(可让 cx 代劳:去掉 --no-restart)")

def save_key(key):
    os.makedirs(CX_DIR, exist_ok=True)
    os.chmod(CX_DIR, 0o700)
    open(KEY_FILE, "w").write(key); os.chmod(KEY_FILE, 0o600)
    subprocess.run(["launchctl", "setenv", "DEEPSEEK_API_KEY", key])
    plist = os.path.expanduser("~/Library/LaunchAgents/com.cx.codex-setenv.plist")
    # 不把 key 写进 plist,运行时从 key 文件现读,避免明文多存一份
    pl = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.cx.codex-setenv</string>
  <key>ProgramArguments</key><array>
    <string>/bin/sh</string><string>-c</string>
    <string>[ -f "{KEY_FILE}" ] &amp;&amp; launchctl setenv DEEPSEEK_API_KEY "$(cat {KEY_FILE})"</string>
  </array>
  <key>RunAtLoad</key><true/>
</dict></plist>"""
    open(plist, "w").write(pl); os.chmod(plist, 0o600)
    ok("key 已保存(~/.cx/deepseek.key)并注入 GUI 环境(重启后由 LaunchAgent 自动恢复)")
    info("正在运行的 App 需重启才能看到该环境变量")

def cmd_key():
    key = None
    if len(sys.argv) > 2 and sys.argv[2].strip():
        key = sys.argv[2].strip()
    else:
        # 尝试从 ocx 配置迁移
        ocx = os.path.expanduser("~/.opencodex/config.json")
        if os.path.exists(ocx):
            try:
                key = json.load(open(ocx))["providers"]["deepseek"]["apiKey"]
                info(f"从旧代理配置(~/.opencodex)读取到 key({key[:8]}...)")
            except Exception: pass
    if not key: err("用法: cx key <DEEPSEEK_API_KEY>")
    save_key(key)

def cmd_setup():
    """全新机器一键接入:只有 codex、没有 GPT 登录也能用上 DeepSeek。"""
    key = sys.argv[2].strip() if len(sys.argv) > 2 and sys.argv[2].strip() else load_key()
    if not key: err("用法: cx setup <DEEPSEEK_API_KEY>")
    if not os.path.exists(CODEX_BIN):
        err(f"未找到 codex({CODEX_BIN})。先安装: brew install codex 或 npm i -g @openai/codex")
    print(f"== cx setup: 为无 GPT 登录的全新环境接入 DeepSeek ==")
    save_key(key)
    # config.toml 不存在也能跑(read_cfg 返回空),use 内部会创建并写入完整配置
    cmd_use("deepseek")
    print()
    print("== 端到端冒烟测试(CLI 直连 DeepSeek) ==")
    env = dict(os.environ, DEEPSEEK_API_KEY=key)
    try:
        r = subprocess.run([CODEX_BIN, "exec", "--skip-git-repo-check", "只回复ok"],
                           env=env, cwd="/tmp", capture_output=True, text=True, timeout=120)
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0 and "ok" in out.lower():
            ok("冒烟测试通过: 无 GPT 登录也能正常对话 ✅")
        else:
            err(f"冒烟测试失败(exit={r.returncode}): {out.strip()[-400:]}")
    except subprocess.TimeoutExpired:
        err("冒烟测试超时(120s),检查网络或代理后重试 cx doctor")
    print()
    print("完成! 接下来:")
    print("  · CLI 现在就能用: codex \"你的问题\"")
    print("  · 桌面 App: 完全退出重开即可。若首次弹出登录页,选 Sign in another way;")
    print("    配置好自定义 provider 后通常可直接选项目开始用(无需 ChatGPT 账号)")
    print("  · 想把旧会话也切过来: cx fix-all deepseek")

def app_pids():
    out = set()
    for pat in APP_PGREP_LIST:
        r = subprocess.run(["pgrep", "-f", pat], capture_output=True, text=True)
        for line in (r.stdout or "").split():
            out.add(int(line))
    return sorted(out)

def app_running():
    return bool(app_pids())

def _wait_gone(timeout):
    """等 App 完全退出;返回 True 表示已退干净"""
    end = time.time() + timeout
    while time.time() < end:
        if not app_running(): return True
        time.sleep(0.5)
    return not app_running()

def quit_app(silent=False):
    """优雅退出桌面 App(先 osascript 正常退出,超时才逐级信号)。返回是否原本在运行。
    注意:会先尝试让 App 自行收尾(flush 会话),所以不会丢数据。"""
    if not app_running(): return False
    if not silent: info("检测到桌面 App 正在运行,正在自动退出(会话写入锁需要释放)...")
    subprocess.run(["osascript", "-e", f'quit app "{APP_NAME}"'],
                   capture_output=True, text=True, timeout=30)
    if _wait_gone(10):
        if not silent: ok("App 已优雅退出")
        return True
    # 还活着 → SIGTERM(可能是卡住了,或弹了确认框没人点)
    for p in app_pids():
        subprocess.run(["kill", "-TERM", str(p)], capture_output=True)
    if _wait_gone(6):
        if not silent: ok("App 已退出(TERM)")
        return True
    # 仍不响应 → SIGKILL
    for p in app_pids():
        subprocess.run(["kill", "-KILL", str(p)], capture_output=True)
    if _wait_gone(5):
        if not silent: print("⚠️  App 未响应正常退出,已强制结束")
        return True
    err("无法退出 App,请手动 Cmd+Q 退出后重试")

def open_app():
    subprocess.run(["open", "-a", APP_NAME], capture_output=True)
    time.sleep(2)
    ok("已重新打开桌面 App")

def cmd_status():
    print(f"cx 版本    : {CX_VERSION}")
    text = read_cfg()
    m = re.search(r'(?m)^model\s*=\s*"([^"]+)"', text)
    p = re.search(r'(?m)^model_provider\s*=\s*"([^"]+)"', text)
    print(f"默认模型   : {m.group(1) if m else '(未设置)'}")
    print(f"默认provider: {p.group(1) if p else 'openai(原生)'}")
    print(f"key        : {'已保存' if load_key() else '未保存(cx key <KEY>)'}")
    try:
        db = sqlite3.connect(f"file:{CODEX_HOME}/state_5.sqlite?mode=ro", uri=True)
        rows = db.execute("SELECT substr(id,1,8), model, substr(title,1,20) FROM threads "
                          "ORDER BY updated_at DESC LIMIT 6").fetchall()
        print("\n最近会话:")
        for r in rows: print(f"  {r[0]}  {str(r[1]):<28} {r[2]}")
        bad = db.execute("SELECT COUNT(*) FROM threads WHERE model LIKE 'deepseek/%' OR model='deepseek-v4-flash'").fetchone()[0]
        if bad: print(f"\n⚠️  {bad} 个会话仍是旧格式模型 id(可运行 cx fix-all deepseek 统一迁移)")
    except Exception as e:
        print("(会话库读取失败: %s)" % e)

def cmd_doctor():
    print("== cx doctor ==")
    key = load_key()
    print(("✅" if key else "❌") + " deepseek key: " + ("已保存 " + key[:8] + "..." if key else "未保存"))
    if key:
        req = urllib.request.Request("https://api.deepseek.com/v1/models",
                                     headers={"Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                ids = [m.get("id") for m in json.load(r).get("data", []) if m.get("id")]
            ok("deepseek API 直连: key 有效")
            if ids:
                info("账号可用模型: " + ", ".join(ids))
                gone = [m["slug"] for m in CATALOG_MODELS
                        if m.get("slug", "").startswith("deepseek") and m["slug"] not in ids]
                if gone:
                    warn("模型目录里这些名字已不在账号可用列表: " + ", ".join(gone)
                         + "  (运行 cx use deepseek 重建目录,或升级 cx)")
                else:
                    ok("模型目录中的 deepseek 模型名均可用")
        except urllib.error.HTTPError as e:
            err(f"deepseek API 返回 {e.code}(key 可能无效)")
        except Exception as e:
            err(f"deepseek API 连不上: {e}")
    text = read_cfg()
    ok("config.toml 读取正常") if "[model_providers.deepseek]" in text or "model_provider" in text else info("config 尚未配置 provider")
    cmd_status()

def cmd_fix():
    args = [a for a in sys.argv[2:] if not a.startswith("-")]
    flags = set(a for a in sys.argv[2:] if a.startswith("-"))
    if not args: err("用法: cx fix <会话UUID|last> [--keep-app] [--no-reopen]")
    sid = args[0]
    if sid == "last":
        db = sqlite3.connect(f"file:{CODEX_HOME}/state_5.sqlite?mode=ro", uri=True)
        r = db.execute("SELECT id FROM threads ORDER BY updated_at DESC LIMIT 1").fetchone()
        if not r: err("没有会话")
        sid = r[0]
    was_running = False
    if app_running():
        if "--keep-app" in flags:
            err("App 正在运行(会话被锁)。去掉 --keep-app 可让 cx 自动退出并恢复 App")
        was_running = quit_app()
    print(f"修复会话 {sid} ...")
    # 先就地清理 rollout:删掉会让 DeepSeek 报 "No tool output found" 的 image_resize_notice
    rp = rollout_path_of(sid)
    if rp and os.path.exists(rp):
        shutil.copy2(rp, rp + ".bak.cx-fix-" + time.strftime("%Y%m%d-%H%M%S"))
        stripped = strip_resize_notices(rp)
        fixed = renumber_ordinals(rp)
        if stripped or fixed:
            reset_app_projection(sid)
            print(f"  · 清理 {stripped} 条不兼容条目 / 重编号 {fixed} 行,已重置 App 投影")
    r = subprocess.run([CODEX_BIN, "exec", "resume", "--skip-git-repo-check",
                        "-c", f'model="{DS_MODEL}"', "-c", 'model_provider="deepseek"',
                        sid, "系统测试:请只回复两个字[正常]"],
                       capture_output=True, text=True, timeout=300)
    tail = "\n".join((r.stdout or "").split("\n")[-6:])
    print(tail)
    if was_running and "--no-reopen" not in flags: open_app()
    if r.returncode != 0: err(f"修复失败 exit={r.returncode}")
    ok("修复完成")

# ---------- 批量切换老会话 ----------
def sanitize_rollout(path):
    """移除旧代理时代 OpenAI 不兼容的 reasoning 条目(id 为 rs_ocx_*,或带 content 字段)。
    OpenAI 对这类条目会报 Invalid input[..].content / Item not found。返回移除数量。"""
    removed = 0
    out = []
    for line in open(path, encoding="utf-8"):
        try:
            d = json.loads(line)
            pl = d.get("payload", {})
            if (d.get("type") == "response_item" and pl.get("type") == "reasoning"
                    and (str(pl.get("id", "")).startswith("rs_ocx_") or pl.get("content"))):
                removed += 1
                continue
        except Exception:
            pass
        out.append(line)
    if removed:
        open(path, "w", encoding="utf-8").writelines(out)
    return removed

def strip_resize_notices(path):
    """删除 Codex 在 view_image 之后自动插入的 role=developer 的 <image_resize_notice> 消息。

    背景:Codex 连续发起多个 view_image 调用时,每条 output 后面会跟一条 developer 提示
    ("Image 1 of 1 in the preceding tool output was resized ...")。DeepSeek 的 Responses API
    在「多个 tool call 连续出现 + output 之间夹 message」这种组合下会丢失配对,
    报 `No tool output found for tool call <id>`(400,会话卡死无法继续)。
    这些 notice 只是缩放提示、无实质内容,删掉即可(保留全部 call/output,不丢对话)。
    返回删除条数。"""
    removed = 0
    keep = []
    for ln in open(path, "rb"):
        if b"image_resize_notice" in ln:
            try:
                d = json.loads(ln)
            except Exception:
                keep.append(ln); continue
            pl = d.get("payload") or {}
            if (d.get("type") == "response_item" and pl.get("type") == "message"
                    and pl.get("role") == "developer"):
                removed += 1
                continue
        keep.append(ln)
    if removed:
        open(path, "wb").writelines(keep)
    return removed

def rollout_path_of(tid):
    """按 thread id 取 rollout 文件路径"""
    try:
        db = sqlite3.connect(f"file:{CODEX_HOME}/state_5.sqlite?mode=ro", uri=True)
        r = db.execute("SELECT rollout_path FROM threads WHERE id=?", (tid,)).fetchone()
        db.close()
        return r[0] if r else None
    except Exception:
        return None

def rewrite_rollout(path, model, provider):
    """把 rollout 文件里记录的模型/provider 统一改写为指定值,返回是否修改"""
    changed = False
    new_lines = []
    for line in open(path, encoding="utf-8"):
        if '"turn_context"' in line or '"session_meta"' in line:
            try:
                d = json.loads(line)
                pl = d.get("payload", {})
                if d.get("type") == "session_meta":
                    if pl.get("model") != model: pl["model"] = model; changed = True
                    if pl.get("model_provider") != provider: pl["model_provider"] = provider; changed = True
                elif d.get("type") == "turn_context":
                    if pl.get("model") != model: pl["model"] = model; changed = True
                    st = (pl.get("collaboration_mode") or {}).get("settings") or {}
                    if st.get("model") and st.get("model") != model: st["model"] = model; changed = True
                if changed: line = json.dumps(d, ensure_ascii=False) + "\n"
            except Exception: pass
        new_lines.append(line)
    if changed:
        open(path, "w", encoding="utf-8").writelines(new_lines)
    return changed

def cmd_fix_all():
    args = sys.argv[2:]
    which = args[0] if args and not args[0].startswith("-") else ""
    limit = None
    for i, a in enumerate(args):
        if a == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1]) if args[i + 1].isdigit() else None
        elif a.startswith("--limit="):
            v = a.split("=", 1)[1]
            limit = int(v) if v.isdigit() else None
    if which not in ("deepseek", "gpt"):
        err("用法: cx fix-all deepseek|gpt [--limit N] [--keep-app] [--no-reopen]\n"
            "     (--limit N 只改最近 N 个,默认全部)")
    model, provider = (DS_MODEL, "deepseek") if which == "deepseek" else (GPT_MODEL, "openai")
    keep_app = "--keep-app" in [a for a in args if a.startswith("-")]
    no_reopen = "--no-reopen" in [a for a in args if a.startswith("-")]
    if keep_app and app_running():
        err("App 正在运行(会话有写入锁)。去掉 --keep-app 可让 cx 自动退出并恢复 App;单个会话可用 cx fix <ID>")
    dbpath = os.path.join(CODEX_HOME, "state_5.sqlite")
    db = sqlite3.connect(dbpath)
    q = ("SELECT id, model, model_provider, rollout_path FROM threads "
         "ORDER BY updated_at DESC")
    if limit: q += f" LIMIT {limit}"
    rows = db.execute(q).fetchall()
    todo = []
    for tid, m, p, rp in rows:
        needs_model = (m != model or p != provider)
        # 清洗判断不能只扫文件前缀:脏条目可能埋在大会话深处,两种目标都全量清洗(均幂等)
        # - openai  : 移除旧代理时代留下的 reasoning 脏条目
        # - deepseek: 移除 image_resize_notice(否则报 No tool output found,会话卡死)
        needs_sanitize = bool(rp and os.path.exists(rp))
        if needs_model or needs_sanitize:
            todo.append((tid, m, p, rp, needs_model))
    if not todo:
        ok(f"所有会话已经是 {model},无需切换"); db.close(); return
    scope = f"最近 {limit} 个中" if limit else ""
    print(f"{scope}待切换 {len(todo)} / {len(rows)} 个会话 → {model} ({provider})")
    if input("确认执行? [y/N] ").strip().lower() != "y":
        info("已取消"); db.close(); return
    was_running = quit_app() if app_running() else False
    reopened = False
    try:
        ts = time.strftime("%Y%m%d-%H%M%S")
        bak = os.path.join(CODEX_HOME, f"state_5.sqlite.bak.cx-fixall-{ts}")
        shutil.copy2(dbpath, bak)
        ok(f"数据库已备份: {bak}")
        n = 0
        for tid, m, p, rp, needs_model in todo:
            if rp and os.path.exists(rp):
                bakj = rp + f".bak.cx-fixall-{ts}"
                if not os.path.exists(bakj): shutil.copy2(rp, bakj)
                if needs_model: rewrite_rollout(rp, model, provider)
                stripped = sanitize_rollout(rp) if provider == "openai" else strip_resize_notices(rp)
                # 关键:删行会产生 ordinal 缺口 + 重排会移动字节偏移;必须重编号消除缺口,
                # 并清投影缓存让 App 从 rollout 重建,否则重开 App 后该会话内容会"消失"
                fixed = renumber_ordinals(rp)
                if needs_model or stripped or fixed:
                    reset_app_projection(tid)
                if stripped: print(f"  · {tid[:8]} 清理 {stripped} 条不兼容条目")
                if fixed: print(f"  · {tid[:8]} 重编号 {fixed} 行并重置 App 投影")
            if needs_model:
                db.execute("UPDATE threads SET model=?, model_provider=? WHERE id=?", (model, provider, tid))
            n += 1
        db.commit(); db.close()
        ok(f"已切换 {n} 个会话 → {model}")
        if was_running and not no_reopen:
            open_app(); reopened = True
        elif not was_running:
            info("重开 App 生效(若 App 在运行可让 cx 代劳:去掉 --no-reopen)")
    finally:
        # 中途出错也要把 App 恢复回去,避免用户以为 App 崩了
        if was_running and not no_reopen and not reopened:
            open_app()

def main():
    if len(sys.argv) < 2: cmd_status(); return
    c = sys.argv[1]
    if c == "status": cmd_status()
    elif c == "setup": cmd_setup()
    elif c == "use": cmd_use(sys.argv[2] if len(sys.argv) > 2 else "")
    elif c == "key": cmd_key()
    elif c == "doctor": cmd_doctor()
    elif c == "fix": cmd_fix()
    elif c == "fix-all": cmd_fix_all()
    elif c in ("version", "-V", "--version"): print(f"cx {CX_VERSION}")
    elif c in ("-h", "--help", "help"): print(HELP)
    else: err(f"未知命令: {c}(cx help 查看用法)")

if __name__ == "__main__":
    main()
