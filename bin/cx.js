#!/usr/bin/env node
/**
 * cx 的 npm 入口: Node 只负责被 npm 安装和转发参数,
 * 实际逻辑在 cx.py(Python, macOS 自带),无需任何依赖。
 */
const { spawn } = require("child_process");
const path = require("path");

const script = path.join(__dirname, "..", "cx.py");
const child = spawn("python3", [script, ...process.argv.slice(2)], {
  stdio: "inherit",
});

child.on("error", (err) => {
  if (err.code === "ENOENT") {
    console.error("❌ 未找到 python3(macOS 自带,若缺失请安装: brew install python3)");
  } else {
    console.error("❌ 启动失败:", err.message);
  }
  process.exit(1);
});

child.on("exit", (code) => process.exit(code ?? 1));
