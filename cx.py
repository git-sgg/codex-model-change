#!/usr/bin/env python3
"""cx — Codex 模型直连切换器（代替 ocx 的轻量方案）

无代理、无常驻进程。Codex 直连 deepseek(Responses API) / 原生 ChatGPT。
命令:
  cx status            查看当前模型配置与最近会话
  cx use deepseek|gpt  切换默认模型(改写 ~/.codex/config.toml, 自动备份)
  cx doctor            体检: key/直连/会话健康
  cx key <API_KEY>     保存 deepseek key 并注入 GUI 环境(launchctl setenv)
  cx fix <会话UUID>    修复坏会话(续跑一轮写入正确模型)
  cx migrate-sessions  把 ocx 时代的 deepseek 会话迁移为直连 id(需退 App)
"""
import json, os, re, glob, sqlite3, subprocess, sys, shutil, time, urllib.request, urllib.error

CODEX_HOME = os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex"))
CONFIG = os.path.join(CODEX_HOME, "config.toml")
CX_DIR = os.path.expanduser("~/.cx")
KEY_FILE = os.path.join(CX_DIR, "deepseek.key")
CATALOG = os.path.join(CODEX_HOME, "cx-catalog.json")
CODEX_BIN = "/opt/homebrew/bin/codex"
APP_PGREP = "/Applications/ChatGPT.app/Contents/MacOS/ChatGPT"

DS_MODEL = "deepseek-chat"
DS_PROVIDER_BLOCK = """
# --- added by cx (direct deepseek) ---
[model_providers.deepseek]
name = "DeepSeek"
base_url = "https://api.deepseek.com/v1"
env_key = "DEEPSEEK_API_KEY"
wire_api = "responses"
"""

GPT_MODEL = "gpt-5.6-sol"
DS_IDS = ("deepseek/deepseek-v4-flash", "deepseek-v4-flash")

def err(m): print("❌ " + m); sys.exit(1)
def ok(m): print("✅ " + m)
def info(m): print("ℹ️  " + m)

def read_cfg():
    if not os.path.exists(CONFIG): err(f"找不到 {CONFIG}")
    return open(CONFIG, encoding="utf-8").read()

def backup_cfg(tag=""):
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = f"{CONFIG}.bak.cx-{tag}{ts}"
    shutil.copy2(CONFIG, dst)
    ok(f"已备份: {dst}")
    return dst

def load_key():
    if not os.path.exists(KEY_FILE): return None
    return open(KEY_FILE).read().strip() or None

# ---------- 目录文件 ----------
def _ds_entry(slug, name, desc, priority):
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
        "supports_reasoning_summaries": False, "context_window": 131072,
        "max_context_window": 131072, "auto_compact_token_limit": 118000,
        "auto_review_model_override": None,
    }

CATALOG_MODELS = [
    _ds_entry("deepseek-chat", "DeepSeek Chat", "DeepSeek 直连 (deepseek-chat)。", 5),
    _ds_entry("deepseek-reasoner", "DeepSeek Reasoner", "DeepSeek 直连 (deepseek-reasoner)。", 4),
]
for slug in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", "gpt-5.4"):
    e = _ds_entry(slug, slug, slug, 1)
    e["visibility"] = "hide"
    e["input_modalities"] = ["text", "image"]
    e["context_window"] = e["max_context_window"] = 400000
    e["auto_compact_token_limit"] = 360000
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

# ---------- 命令 ----------
def cmd_use(which):
    if which not in ("deepseek", "gpt"): err("用法: cx use deepseek|gpt")
    backup_cfg(f"use-{which}-")
    text = read_cfg()
    if which == "deepseek":
        text = ensure_provider_block(text)
        text = set_active_model(text, DS_MODEL, "deepseek")
    else:
        text = set_active_model(text, GPT_MODEL, "openai")
    text = clean_ocx_injections(text)
    write_catalog()
    open(CONFIG, "w", encoding="utf-8").write(text)
    ok(f"已切换到 {which}: model 已写入,备份在同目录")
    if which == "deepseek" and not load_key():
        info("未保存 deepseek key,先运行: cx key <API_KEY>")
    info("桌面 App 需完全退出重开才会重读配置")

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
                info(f"从 ocx 配置读取到 key({key[:8]}...)")
            except Exception: pass
    if not key: err("用法: cx key <DEEPSEEK_API_KEY>")
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

def app_running():
    return subprocess.run(["pgrep", "-f", APP_PGREP], capture_output=True).returncode == 0

def cmd_status():
    text = read_cfg()
    m = re.search(r'(?m)^model\s*=\s*"([^"]+)"', text)
    p = re.search(r'(?m)^model_provider\s*=\s*"([^"]+)"', text)
    print(f"默认模型   : {m.group(1) if m else '(未设置)'}")
    print(f"默认provider: {p.group(1) if p else 'openai(原生)'}")
    print(f"ocx 代理   : {'运行中' if ocx_alive() else '未运行'}")
    print(f"key        : {'已保存' if load_key() else '未保存(cx key <KEY>)'}")
    try:
        db = sqlite3.connect(f"file:{CODEX_HOME}/state_5.sqlite?mode=ro", uri=True)
        rows = db.execute("SELECT substr(id,1,8), model, substr(title,1,20) FROM threads "
                          "ORDER BY updated_at DESC LIMIT 6").fetchall()
        print("\n最近会话:")
        for r in rows: print(f"  {r[0]}  {str(r[1]):<28} {r[2]}")
        bad = db.execute("SELECT COUNT(*) FROM threads WHERE model LIKE 'deepseek/%' OR model='deepseek-v4-flash'").fetchone()[0]
        if bad: print(f"\n⚠️  {bad} 个会话仍是 ocx 时代模型 id(需要 cx migrate-sessions)")
    except Exception as e:
        print("(会话库读取失败: %s)" % e)

def ocx_alive():
    try:
        urllib.request.urlopen("http://127.0.0.1:10100/healthz", timeout=2)
        return True
    except Exception: return False

def cmd_doctor():
    print("== cx doctor ==")
    key = load_key()
    print(("✅" if key else "❌") + " deepseek key: " + ("已保存 " + key[:8] + "..." if key else "未保存"))
    if key:
        req = urllib.request.Request("https://api.deepseek.com/v1/models",
                                     headers={"Authorization": f"Bearer {key}"})
        try:
            urllib.request.urlopen(req, timeout=10); ok("deepseek API 直连: key 有效")
        except urllib.error.HTTPError as e:
            err(f"deepseek API 返回 {e.code}(key 可能无效)")
        except Exception as e:
            err(f"deepseek API 连不上: {e}")
    text = read_cfg()
    ok("config.toml 读取正常") if "[model_providers.deepseek]" in text or "model_provider" in text else info("config 尚未配置 provider")
    print(("✅" if ocx_alive() else "ℹ️ ") + " ocx 代理: " + ("运行中(后备可用)" if ocx_alive() else "未运行"))
    cmd_status()

def cmd_fix():
    if len(sys.argv) < 3: err("用法: cx fix <会话UUID|last>")
    sid = sys.argv[2]
    if sid == "last":
        db = sqlite3.connect(f"file:{CODEX_HOME}/state_5.sqlite?mode=ro", uri=True)
        r = db.execute("SELECT id FROM threads ORDER BY updated_at DESC LIMIT 1").fetchone()
        if not r: err("没有会话")
        sid = r[0]
    if app_running(): err("请先完全退出 ChatGPT/Codex App(会话被锁)")
    print(f"修复会话 {sid} ...")
    r = subprocess.run([CODEX_BIN, "exec", "resume", "--skip-git-repo-check",
                        "-c", f'model="{DS_MODEL}"', "-c", 'model_provider="deepseek"',
                        sid, "系统测试:请只回复两个字[正常]"],
                       capture_output=True, text=True, timeout=300)
    tail = "\n".join((r.stdout or "").split("\n")[-6:])
    print(tail)
    ok("修复完成") if r.returncode == 0 else err(f"修复失败 exit={r.returncode}")

def is_ds(model):
    return isinstance(model, str) and (model in DS_IDS or model.startswith("deepseek"))

def cmd_migrate():
    force = "--yes" in sys.argv
    if app_running() and not force:
        err("请先完全退出 ChatGPT/Codex App(或加 --yes 跳过检查,不推荐)")
    db = sqlite3.connect(os.path.join(CODEX_HOME, "state_5.sqlite"))
    rows = db.execute("SELECT id, model, rollout_path FROM threads WHERE model LIKE 'deepseek%'").fetchall()
    if not rows: ok("没有需要迁移的会话"); return
    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = os.path.join(CODEX_HOME, f"state_5.sqlite.bak.cx-migrate-{ts}")
    shutil.copy2(os.path.join(CODEX_HOME, "state_5.sqlite"), bak)
    ok(f"数据库已备份: {bak}")
    n = 0
    for tid, model, path in rows:
        if os.path.exists(path):
            bakj = path + f".bak.cx-{ts}"
            if not os.path.exists(bakj): shutil.copy2(path, bakj)
            new_lines = []
            changed = False
            for line in open(path, encoding="utf-8"):
                if '"turn_context"' in line or '"session_meta"' in line:
                    try:
                        d = json.loads(line)
                        pl = d.get("payload", {})
                        if d.get("type") == "session_meta":
                            if is_ds(pl.get("model")): pl["model"] = DS_MODEL; changed = True
                            if pl.get("model_provider") == "openai": pl["model_provider"] = "deepseek"; changed = True
                        elif d.get("type") == "turn_context":
                            if is_ds(pl.get("model")): pl["model"] = DS_MODEL; changed = True
                            cm = pl.get("collaboration_mode") or {}
                            st = cm.get("settings") or {}
                            if is_ds(st.get("model")): st["model"] = DS_MODEL; changed = True
                        if changed: line = json.dumps(d, ensure_ascii=False) + "\n"
                    except Exception: pass
                new_lines.append(line)
            if changed:
                open(path, "w", encoding="utf-8").writelines(new_lines); n += 1
    db.execute("UPDATE threads SET model=?, model_provider='deepseek' WHERE model LIKE 'deepseek%'", (DS_MODEL,))
    db.commit()
    ok(f"已迁移 {n} 个 rollout / {len(rows)} 条记录 → deepseek-chat")
    db.close()

def main():
    if len(sys.argv) < 2: cmd_status(); return
    c = sys.argv[1]
    if c == "status": cmd_status()
    elif c == "use": cmd_use(sys.argv[2] if len(sys.argv) > 2 else "")
    elif c == "key": cmd_key()
    elif c == "doctor": cmd_doctor()
    elif c == "fix": cmd_fix()
    elif c == "migrate-sessions": cmd_migrate()
    elif c in ("-h", "--help", "help"): print(__doc__)
    else: err(f"未知命令: {c}(cx help 查看用法)")

if __name__ == "__main__":
    main()
