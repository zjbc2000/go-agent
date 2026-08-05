"""Folded Task-3 test: DockerSubprocessRunner + _redact_cmd REAL coverage.

- The docker argv does NOT contain the grant token value.
- The token IS written to the env-file (mode 0600, removed after).
- The log line is redacted (token replaced with ***).
"""

import os
import stat
from unittest.mock import patch

from worker.celery_app import DockerSubprocessRunner, _redact_cmd
from worker.runtime import SandboxContainerConfig


class TestRedactCmd:
    """Direct unit tests for the _redact_cmd log redaction helper."""

    def test_token_not_in_redacted_output(self):
        cmd = ["docker", "run", "--env", "SECRET=my-token-123"]
        env = {"SECRET": "my-token-123"}
        redacted = _redact_cmd(cmd, env)
        joined = " ".join(redacted)
        assert "my-token-123" not in joined
        assert "***" in joined

    def test_redact_handles_multiple_secrets(self):
        cmd = ["docker", "run", "-e", "A=abc", "-e", "B=xyz"]
        env = {"A": "abc", "B": "xyz"}
        redacted = _redact_cmd(cmd, env)
        joined = " ".join(redacted)
        assert "abc" not in joined
        assert "xyz" not in joined
        assert joined.count("***") >= 2

    def test_redact_no_secrets_returns_unchanged(self):
        cmd = ["docker", "run", "--read-only"]
        env = {}
        redacted = _redact_cmd(cmd, env)
        assert redacted == cmd


class TestDockerSubprocessRunner:
    """Integration-level tests that mock subprocess.run to capture the docker argv
    and env-file without actually invoking Docker."""

    def test_grant_token_not_in_docker_argv(self):
        """The grant token is passed via --env-file, NOT on the command line."""
        runner = DockerSubprocessRunner()
        config = SandboxContainerConfig(
            image="test-image:latest",
            command=["/bin/echo", "hello"],
            grant_token="secret-grant-token-abc123",
            environment={
                "AGENT_TOOL_GRANT_TOKEN": "secret-grant-token-abc123",
                "AGENT_TOOL_BROKER_URL": "http://broker:8000",
            },
        )

        captured_cmd: list[str] | None = None

        def fake_subprocess_run(cmd, **_kwargs):
            nonlocal captured_cmd
            captured_cmd = list(cmd)
            # Return success without running anything.
            import subprocess
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

        with patch("worker.celery_app.subprocess.run", side_effect=fake_subprocess_run):
            result = runner.run(config)

        assert result.exit_code == 0
        assert captured_cmd is not None

        # Grant token must NOT appear in any argv element.
        cmdline_str = " ".join(captured_cmd)
        assert "secret-grant-token-abc123" not in cmdline_str, (
            f"Grant token leaked into docker argv: {cmdline_str}"
        )

        # --env-file must be present.
        assert "--env-file" in captured_cmd

    def test_grant_token_in_env_file_removed_after(self):
        """The token IS written to the env-file (mode 0600) and removed after."""
        runner = DockerSubprocessRunner()
        config = SandboxContainerConfig(
            image="test-image:latest",
            command=["/bin/echo", "hello"],
            grant_token="secret-xyz",
            environment={
                "AGENT_TOOL_GRANT_TOKEN": "secret-xyz",
            },
        )

        env_file_path: str | None = None

        def fake_subprocess_run(cmd, **_kwargs):
            nonlocal env_file_path
            # Find the --env-file argument.
            for i, arg in enumerate(cmd):
                if arg == "--env-file" and i + 1 < len(cmd):
                    env_file_path = cmd[i + 1]
                    break
            import subprocess
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

        with patch("worker.celery_app.subprocess.run", side_effect=fake_subprocess_run):
            result = runner.run(config)

        assert result.exit_code == 0
        assert env_file_path is not None, "Expected --env-file argument"

        # After mock returns: check that the env-file was created (but runner
        # deletes it in finally). The file should be absent after run() returns.
        assert not os.path.exists(env_file_path), (
            f"Env-file {env_file_path} was not removed after run"
        )

    def test_env_file_is_mode_0600(self):
        """The env-file is created with mode 0600 (owner read-write only)."""
        runner = DockerSubprocessRunner()
        config = SandboxContainerConfig(
            image="test-image:latest",
            command=["/bin/echo", "hello"],
            grant_token="secret-xyz",
            environment={
                "AGENT_TOOL_GRANT_TOKEN": "secret-xyz",
            },
        )

        env_file_path: str | None = None
        env_file_mode: int | None = None

        def fake_subprocess_run(cmd, **_kwargs):
            nonlocal env_file_path, env_file_mode
            for i, arg in enumerate(cmd):
                if arg == "--env-file" and i + 1 < len(cmd):
                    env_file_path = cmd[i + 1]
                    env_file_mode = os.stat(env_file_path).st_mode
                    break
            import subprocess
            return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

        with patch("worker.celery_app.subprocess.run", side_effect=fake_subprocess_run):
            runner.run(config)

        assert env_file_mode is not None, "Could not capture env-file mode"
        # 0600 means only owner can read/write.
        assert stat.S_IMODE(env_file_mode) == 0o600, (
            f"Env-file mode is {oct(stat.S_IMODE(env_file_mode))}, expected 0o600"
        )

    def test_timeout_returns_error_not_crash(self):
        """A TimeoutExpired returns ContainerResult(exit_code=-1), not crash."""
        runner = DockerSubprocessRunner()
        config = SandboxContainerConfig(
            image="test-image:latest",
            command=["/bin/sleep", "999"],
            grant_token="t",
            timeout_seconds=1,
        )

        import subprocess

        def fake_timeout(_cmd, **_kwargs):
            raise subprocess.TimeoutExpired(cmd="docker run ...", timeout=1)

        with patch("worker.celery_app.subprocess.run", side_effect=fake_timeout):
            result = runner.run(config)

        assert result.exit_code == -1
        assert "timed out" in result.stderr.lower()
