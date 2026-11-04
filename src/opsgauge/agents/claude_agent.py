"""Harbor Claude Code agent with a GitHub-independent Node installation path."""

from __future__ import annotations

import shlex
from typing import Any, override

from harbor.agents.installed.claude_code import ClaudeCode
from harbor.environments.base import BaseEnvironment


class ClaudeAgent(ClaudeCode):
    """Run Harbor's Claude Code integration with a container-local Node install.

    Harbor's run, authentication, and ATIF logic remain unchanged. Only setup
    is replaced: mission task containers lack Node, and the default installer
    depends on downloads.claude.ai / distro Node packages.
    """

    _NODE_HOME = "/installed-agent/claude-node"
    _NODE_ENV = 'export PATH="/installed-agent/claude-node/bin:$PATH"; '

    @override
    def get_version_command(self) -> str | None:
        return f"{self._NODE_ENV}claude --version"

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        if await self._installed_claude_satisfies_version(environment):
            self.logger.debug("Claude Code is already available at the requested version")
            return

        await self._install_system_dependencies(environment)
        prepare = f"mkdir -p {shlex.quote(self._NODE_HOME)}"
        if environment.default_user is not None:
            prepare += (
                f" && chown -R {shlex.quote(str(environment.default_user))} "
                f"{shlex.quote(self._NODE_HOME)}"
            )
        await self.exec_as_root(environment, prepare)

        version_spec = f"@{self._version}" if self._version else "@latest"
        package = shlex.quote(f"@anthropic-ai/claude-code{version_spec}")
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                f"{self._NODE_ENV}"
                "if command -v claude >/dev/null 2>&1; then "
                "claude --version; exit 0; fi; "
                "case $(uname -m) in "
                "x86_64) node_arch=x64 ;; "
                "aarch64|arm64) node_arch=arm64 ;; "
                '*) echo "Unsupported Node architecture: $(uname -m)" >&2; exit 1 ;; '
                "esac; "
                "node_tmp=$(mktemp -d /tmp/claude-node.XXXXXX); "
                "trap 'rm -rf \"$node_tmp\"' EXIT; "
                "curl -fsSL https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt "
                '-o "$node_tmp/SHASUMS256.txt"; '
                'node_archive=$(awk -v arch="$node_arch" '
                '\'$2 ~ ("linux-" arch "\\\\.tar\\\\.gz$") {print $2; exit}\' '
                '"$node_tmp/SHASUMS256.txt"); '
                'test -n "$node_archive"; '
                'curl -fsSL "https://nodejs.org/dist/latest-v22.x/$node_archive" '
                '-o "$node_tmp/$node_archive"; '
                "expected=$(awk -v file=\"$node_archive\" '$2 == file {print $1}' "
                '"$node_tmp/SHASUMS256.txt"); '
                "actual=$(sha256sum \"$node_tmp/$node_archive\" | awk '{print $1}'); "
                'test "$actual" = "$expected"; '
                f"rm -rf {shlex.quote(self._NODE_HOME)}/*; "
                f'tar -xzf "$node_tmp/$node_archive" -C {shlex.quote(self._NODE_HOME)} '
                "--strip-components=1; "
                f"{self._NODE_ENV}node --version; npm --version; "
                f"npm install -g {package}; claude --version"
            ),
            timeout_sec=600,
        )
        await self.exec_as_root(
            environment,
            command=(
                f"ln -sf {shlex.quote(self._NODE_HOME)}/bin/node /usr/local/bin/node; "
                f"ln -sf {shlex.quote(self._NODE_HOME)}/bin/claude /usr/local/bin/claude"
            ),
        )

    async def _install_system_dependencies(self, environment: BaseEnvironment) -> None:
        await self.exec_as_root(
            environment,
            command=(
                "set -e; deps_ok=1; "
                "for dep in curl rg tar gzip sha256sum awk bash ps; do "
                'command -v "$dep" >/dev/null 2>&1 || deps_ok=0; done; '
                'if [ "$deps_ok" = 1 ]; then exit 0; fi; '
                "if command -v apt-get >/dev/null 2>&1; then "
                "export DEBIAN_FRONTEND=noninteractive; apt-get update; "
                "apt-get install -y --no-install-recommends "
                "curl ripgrep ca-certificates coreutils bash procps; "
                "elif command -v dnf >/dev/null 2>&1; then "
                "dnf install -y curl ripgrep ca-certificates coreutils bash procps; "
                "elif command -v yum >/dev/null 2>&1; then "
                "yum install -y curl ripgrep ca-certificates coreutils bash procps; "
                "elif command -v apk >/dev/null 2>&1; then "
                "apk add --no-cache curl ripgrep ca-certificates coreutils bash procps; "
                "else echo 'Unsupported base image package manager' >&2; exit 1; fi"
            ),
            timeout_sec=300,
        )
