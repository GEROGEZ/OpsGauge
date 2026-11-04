"""Harbor Codex agent with a GitHub-independent Node installation path."""

from __future__ import annotations

import shlex
from typing import Any, override

from harbor.agents.installed.codex import Codex
from harbor.environments.base import BaseEnvironment


class CodexAgent(Codex):
    """Run Harbor's Codex integration without depending on NVM/GitHub.

    Harbor's run, authentication, MCP, skills, and ATIF logic remain unchanged.
    Only setup is replaced because ``raw.githubusercontent.com`` is unreachable
    from the loop host's task containers.
    """

    _NODE_HOME = "/installed-agent/codex-node"
    _NODE_ENV = 'export PATH="/installed-agent/codex-node/bin:$PATH"; '
    _COMPATIBLE_PROVIDER_ID = "harbor_openai_compatible"

    def __init__(
        self,
        *args: Any,
        supports_websockets: bool = False,
        **kwargs: Any,
    ) -> None:
        self._supports_websockets = supports_websockets
        super().__init__(*args, **kwargs)

    @override
    def _build_effective_config(
        self, openai_base_url: str | None = None
    ) -> dict[str, Any]:
        config = super()._build_effective_config(openai_base_url)
        if not openai_base_url:
            return config

        # The built-in OpenAI provider may prefer the Responses WebSocket
        # transport. OpenAI-compatible gateways commonly implement only the
        # HTTPS/SSE Responses endpoint, so describe the gateway explicitly.
        providers = config.setdefault("model_providers", {})
        providers[self._COMPATIBLE_PROVIDER_ID] = {
            "name": "Harbor OpenAI-compatible gateway",
            "base_url": openai_base_url,
            "env_key": "OPENAI_API_KEY",
            "wire_api": "responses",
            "supports_websockets": self._supports_websockets,
        }
        config["model_provider"] = self._COMPATIBLE_PROVIDER_ID
        config.pop("openai_base_url", None)
        return config

    @override
    def get_version_command(self) -> str | None:
        return f"{self._NODE_ENV}codex --version"

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        if await self._installed_codex_satisfies_version(environment):
            self.logger.debug("Codex is already available at the requested version")
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
        package = shlex.quote(f"@openai/codex{version_spec}")
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                f"{self._NODE_ENV}"
                "if command -v codex >/dev/null 2>&1; then "
                "codex --version; exit 0; fi; "
                "case $(uname -m) in "
                "x86_64) node_arch=x64 ;; "
                "aarch64|arm64) node_arch=arm64 ;; "
                '*) echo "Unsupported Node architecture: $(uname -m)" >&2; exit 1 ;; '
                "esac; "
                "node_tmp=$(mktemp -d /tmp/codex-node.XXXXXX); "
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
                f"npm install -g {package}; codex --version"
            ),
            timeout_sec=600,
        )
        await self.exec_as_root(
            environment,
            command=(
                f"ln -sf {shlex.quote(self._NODE_HOME)}/bin/node /usr/local/bin/node; "
                f"ln -sf {shlex.quote(self._NODE_HOME)}/bin/codex /usr/local/bin/codex"
            ),
        )

    async def _install_system_dependencies(self, environment: BaseEnvironment) -> None:
        await self.exec_as_root(
            environment,
            command=(
                "set -e; deps_ok=1; "
                "for dep in curl rg tar gzip sha256sum awk; do "
                'command -v "$dep" >/dev/null 2>&1 || deps_ok=0; done; '
                'if [ "$deps_ok" = 1 ]; then exit 0; fi; '
                "if command -v apt-get >/dev/null 2>&1; then "
                "export DEBIAN_FRONTEND=noninteractive; apt-get update; "
                "apt-get install -y --no-install-recommends "
                "curl ripgrep ca-certificates coreutils; "
                "elif command -v dnf >/dev/null 2>&1; then "
                "dnf install -y curl ripgrep ca-certificates coreutils; "
                "elif command -v yum >/dev/null 2>&1; then "
                "yum install -y curl ripgrep ca-certificates coreutils; "
                "elif command -v apk >/dev/null 2>&1; then "
                "apk add --no-cache curl ripgrep ca-certificates coreutils; "
                "else echo 'Unsupported base image package manager' >&2; exit 1; fi"
            ),
            timeout_sec=300,
        )
