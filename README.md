# cx — Codex 模型直连切换器

让 OpenAI Codex（桌面 App / CLI）**直连 DeepSeek**（或切回原生 ChatGPT），无需任何代理软件（如 ocx）。

`cx` 是一个单文件 Python CLI，无常驻进程、无 launchd 服务依赖（仅一个可选的开机环境变量注入）。

## 工作原理

新版 Codex 已移除 `wire_api = "chat"`，但其 `model_provider` 机制原生支持 **Responses API**。DeepSeek 官方 API（`https://api.deepseek.com/v1`）已支持 Responses 协议，因此：

```
Codex ──直连──> api.deepseek.com        (cx 配置一条 model_provider)
Codex ──原生──> ChatGPT 登录态          (cx 注释掉 model_provider 即回落)
```

中间没有任何协议翻译层——这是 cx 与 ocx 类代理工具的本质区别。

## 安装

```bash
# 1. 下载或 clone 本仓库
git clone https://github.com/git-sgg/codex-model-change.git
cd codex-model-change

# 2. 安装（复制到 PATH 并生成模型目录）
chmod +x cx
sudo cp cx /usr/local/bin/cx        # 或 /opt/homebrew/bin/cx

# 3. 保存你的 DeepSeek API key
cx key sk-xxxxxxxxxxxxxxxx
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
| `cx fix <会话UUID>` | 修复某个打不开/报 404 的老会话 |
| `cx fix last` | 修复最近一个会话 |
| `cx migrate-sessions` | 一次性把旧代理（ocx）时代的 DeepSeek 会话迁移为直连格式 |

每次 `cx use` 都会自动备份 `config.toml`（`config.toml.bak.cx-<时间戳>`），随时可手动回滚。

## 桌面 App 的一次性配置

Codex 桌面 App 由 launchd 拉起，读不到 shell 里的环境变量，需要把 key 注入 GUI 环境：

```bash
launchctl setenv DEEPSEEK_API_KEY "$(cat ~/.cx/deepseek.key)"
```

`cx key` 会自动装一个 LaunchAgent（`~/Library/LaunchAgents/com.cx.codex-setenv.plist`），**下次重启登录时自动完成上述注入**，所以手动只需执行一次。

之后**完全退出并重开 Codex App** 即可。App 的模型选择器里会出现 DeepSeek Chat / DeepSeek Reasoner。

## 从 ocx 迁移

如果你之前用 ocx（或其它把模型改名的代理），老会话记录的是代理别名（如 `deepseek/deepseek-v4-flash`），直连后无法续聊。执行：

```bash
# 先完全退出 Codex App（会话有写入锁）
cx migrate-sessions
```

它会备份 `state_5.sqlite` 和所有 rollout 文件后，把 DeepSeek 系会话统一改为 `deepseek-chat` / `provider=deepseek`。

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
