#!/usr/bin/env bash
# Read-only safe Codex CLI launcher.
#
# This script never installs packages at runtime. In hardened containers it can
# require the build-time npm package/native dependency before invoking codex, so
# a broken image fails before Codex reaches its own runtime remediation path.
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "用法: bash scripts/lib/run_codex_cli.sh <agent> [codex args...]" >&2
  exit 2
fi

AGENT="$1"
shift
export CODEX_AGENT="${CODEX_AGENT:-$AGENT}"

fail() {
  cat >&2 <<'EOF'
❌ Codex CLI 预检失败。
   运行期全局包安装已禁用：不要在 prod-hardened/read_only rootfs 中修改工具链。
   请重新构建镜像，确保 Dockerfile 构建期已安装并校验 @openai/codex 及平台 native package。
EOF
  [ "$#" -gt 0 ] && printf '   细节: %s\n' "$*" >&2
  exit 127
}

if ! command -v codex >/dev/null 2>&1; then
  fail "PATH 中找不到 codex"
fi

# Host installs may come from Homebrew or another package manager. Only enforce
# npm package/native checks when the container entrypoint opts in.
if [ "${CLAUDETEAM_CODEX_REQUIRE_NPM_PACKAGE:-0}" = "1" ]; then
  node <<'NODE' || fail "npm package 或 native optional dependency 缺失"
const fs = require("fs");
const path = require("path");

const roots = [
  process.env.npm_config_prefix && path.join(process.env.npm_config_prefix, "lib", "node_modules"),
  "/usr/local/lib/node_modules",
  "/usr/lib/node_modules",
].filter(Boolean);

const nativeByPlatform = {
  "linux:x64": "@openai/codex-linux-x64",
  "linux:arm64": "@openai/codex-linux-arm64",
  "darwin:x64": "@openai/codex-darwin-x64",
  "darwin:arm64": "@openai/codex-darwin-arm64",
  "win32:x64": "@openai/codex-win32-x64",
};

let found = "";
for (const root of roots) {
  const pkg = path.join(root, "@openai", "codex", "package.json");
  if (fs.existsSync(pkg)) {
    found = root;
    break;
  }
}
if (!found) {
  process.exit(1);
}

const nativePkg = nativeByPlatform[`${process.platform}:${process.arch}`];
if (nativePkg) {
  const nativePath = path.join(found, "@openai", "codex", "node_modules", nativePkg, "package.json");
  if (!fs.existsSync(nativePath)) {
    process.exit(1);
  }
}
NODE
fi

exec codex "$@"
