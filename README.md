# cx — Codex 模型直连切换器

让 OpenAI Codex（桌面 App / CLI）**直连 DeepSeek**（或切回原生 ChatGPT），无需任何代理软件。

`cx` 是一个单文件 Python CLI，无常驻进程、无 launchd 服务依赖（仅一个可选的开机环境变量注入）。

## 工作原理

新版 Codex 已移除 `wire_api = "chat"`，但其 `model_provider` 机制原生支持 **Responses API**。DeepSeek 官方 API（`https://api.deepseek.com/v1`）已支持 Responses 协议，因此：

```
Codex ──直连──> api.deepseek.com        (cx 配置一条 model_provider)
Codex ──原生──> ChatGPT 登录态          (cx 注释掉 model_provider 即回落)
```

中间没有任何协议翻译层，保持协议原生匹配。

## 安装

**方式一：npm 安装（推荐）**

```bash
npm install -g codex-model-change
```

包内是零依赖的 Python 脚本（macOS 自带 python3），Node 只做入口转发，装完即可用 `cx` 命令。

**方式二：源码安装**

```bash
# 1. 下载或 clone 本仓库
git clone https://github.com/git-sgg/codex-model-change.git
cd codex-model-change

# 2. 运行安装脚本（复制到 PATH）
./install.sh                      # 默认装到 /usr/local/bin/cx
# 或指定位置: ./install.sh /opt/homebrew/bin/cx
```

前置要求：已安装 [OpenAI Codex CLI](https://github.com/openai/codex)（`brew install codex`）并至少成功运行过一次（`~/.codex/config.toml` 存在）。

## 命令

| 命令 | 作用 |
|---|---|
| `cx status` | 查看默认模型 / provider / 最近会话各自用的模型 |
| `cx use deepseek` | 切换到 DeepSeek 直连（改写 `~/.codex/config.toml`，自动备份） |
| `cx use gpt` | 切回原生 ChatGPT（走 ChatGPT 登录态，无需 API key） |
| `cx key <API_KEY>` | 保存 DeepSeek key 并注入 GUI 环境（桌面 App 需要） |
| `cx doctor` | 体检：key 有效性 / 直连连通性 / 会话健康 |
| `cx fix <会话ID>` | 修复某个打不开/报 404 的会话（ID 取 `cx status` 里显示的前 8 位即可） |
| `cx fix last` | 修复最近一个会话 |
| `cx fix-all <目标>` | **批量把所有老会话切换到目标模型**（`deepseek` 或 `gpt`，需退 App） |
| `cx fix-all deepseek --limit 20` | 只批量切换最近 20 个会话 |

**关于老会话**：每个会话记录着自己创建时的模型，`cx use` 只影响新会话——老会话继续用原模型，互不干扰。想把老会话搬到新模型：单个用 `cx fix`，全部用 `cx fix-all`（会先备份数据库和会话文件，确认后执行）。

每次 `cx use` 都会自动备份 `config.toml`（`config.toml.bak.cx-<时间戳>`），随时可手动回滚。

## 从零添加 DeepSeek（Codex 里还没有 DeepSeek 时）

如果你的 Codex 从未配置过 DeepSeek（`~/.codex/config.toml` 里没有 `[model_providers.deepseek]`），按下面四步走，**全程不需要手改任何配置文件**：

**第 1 步：获取 DeepSeek API key**

到 [platform.deepseek.com](https://platform.deepseek.com) 注册/登录，在「API Keys」页面创建一个 key（`sk-` 开头），并确保账户有余额。

**第 2 步：安装 cx**（见上方「安装」）

**第 3 步：保存 key 并切换**

```bash
cx key sk-你的key
cx use deepseek
```

`cx key` 会把 key 存到本机 `~/.cx/deepseek.key` 并注入 GUI 环境；`cx use deepseek` 会自动在 `~/.codex/config.toml` 末尾写入完整的 provider 配置：

```toml
# --- added by cx (direct deepseek) ---
[model_providers.deepseek]
name = "DeepSeek"
base_url = "https://api.deepseek.com/v1"
env_key = "DEEPSEEK_API_KEY"
wire_api = "responses"
```

并把顶部改为 `model_provider = "deepseek"` / `model = "deepseek-chat"`，同时生成模型目录 `~/.codex/cx-catalog.json`。

**第 4 步：完成桌面 App 配置**（见下方「桌面 App 的一次性配置」），然后完全退出并重开 App。

验证：跑 `cx doctor`，三项全 ✅ 即配置成功；或在 App 里发条消息试试。

> 如果 `cx use deepseek` 报「找不到 config.toml」，说明你还没运行过 codex CLI——先随便跑一次 `codex "hello"` 让它生成配置文件，再执行上面的步骤。

## 桌面 App 的一次性配置

Codex 桌面 App 由 launchd 拉起，读不到 shell 里的环境变量，需要把 key 注入 GUI 环境：

```bash
launchctl setenv DEEPSEEK_API_KEY "$(cat ~/.cx/deepseek.key)"
```

`cx key` 会自动装一个 LaunchAgent（`~/Library/LaunchAgents/com.cx.codex-setenv.plist`），**下次重启登录时自动完成上述注入**，所以手动只需执行一次。

之后**完全退出并重开 Codex App** 即可。App 的模型选择器里会出现 DeepSeek Chat / DeepSeek Reasoner。

> **从其它代理工具迁移过来的用户**：如果你的老会话记录的是代理别名（如 `deepseek/deepseek-v4-flash`），直连后无法续聊。先完全退出 App，然后执行 `cx fix-all deepseek` 一次性把所有会话迁移为直连格式。

## 常见问题

**Q: 切换/迁移后 App 里还是报错？**
桌面 App 启动时读取配置，必须**完全退出**（⌘Q，不是关窗口）再打开。

**Q: 终端里 codex 能用，桌面 App 报 key 错误？**
App 没读到 `DEEPSEEK_API_KEY`。执行上面「桌面 App 的一次性配置」后重启 App。

**Q: 老会话续聊报 `Model metadata not found` 或 404？**
会话自带的模型 id 与当前配置不一致。用 `cx fix <会话UUID>`（可在 `cx status` 里看到会话 UUID）。

**Q: `launchctl setenv` 报 `Not privileged to set domain environment`？**
必须在你自己登录会话的终端（Terminal.app）里执行，不能通过 ssh/部分自动化环境执行。

**Q: 提示 `deepseek 拒绝了 exec 自定义工具` 之类的 400？**
模型目录（`~/.codex/cx-catalog.json`）被改坏了，重跑一次 `cx use deepseek` 会重新生成。

## 隐私说明

- API key 仅保存在本机 `~/.cx/deepseek.key`（权限 600）和 launchd 环境中，**不写入任何配置文件明文**，不外发。
- 本工具不收集、不上报任何数据；唯一的网络请求是 `cx doctor` 对 `api.deepseek.com` 的 key 有效性检查。
- 请求链路：Codex → `api.deepseek.com` 直连（或 Codex 原生链路），无中间人。

## 兼容性

- macOS（依赖 `launchctl` 做 GUI 环境变量注入；纯 CLI 使用则不依赖）
- Codex CLI / ChatGPT 桌面 App（内含 codex 的版本）
- DeepSeek `deepseek-chat` / `deepseek-reasoner`（Responses API）

> 注意：DeepSeek 的上下文窗口（128K 级）与 GPT 不同，cx 生成的模型目录已按 DeepSeek 实际参数配置（截断策略、auto-compact 阈值等），请勿手工改坏后不带备份地覆盖。

## License

MIT
