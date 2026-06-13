"""LocalAdapter: run an OpenSquilla agent as a host subprocess.

Unlike the swebench OpenSquillaAdapter (which crosses a Docker boundary via
``docker exec``), this runs ``opensquilla agent`` directly on the host with
the repo as the working directory. The provider API key is inherited from
the runner's environment — no env-file is needed because there is no
container boundary to cross (codex review #3).
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from pathlib import Path

from opensquilla.contrib.codetask.config import (
    DEFAULT_AGENT_TIMEOUT,
    DEFAULT_ITERATION_TIMEOUT,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_MAX_PROVIDER_RETRIES,
    agent_python,
)
from opensquilla.contrib.codetask.types import AgentOutcome

logger = logging.getLogger(__name__)

SUBPROCESS_TIMEOUT_BUFFER = 120

_JSON_OBJECT_RE = re.compile(r"\{(?:[^{}]|(?:\{[^{}]*\}))*\}")


class LocalAdapter:
    """Drives the host ``opensquilla agent`` CLI and returns a structured result."""

    def __init__(
        self,
        *,
        model: str = "",
        thinking: str = "",
        timeout: int = DEFAULT_AGENT_TIMEOUT,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
    ):
        self.model = model
        self.thinking = thinking
        self.timeout = timeout
        self.max_iterations = max_iterations

    def run(
        self,
        prompt: str,
        *,
        repo: Path,
        scratch_dir: Path,
        artifact_dir: Path,
    ) -> AgentOutcome:
        """Run one agent turn with ``repo`` as the workspace.

        Writes agent_stdout.log / agent_stderr.log / transcript.jsonl /
        usage.json into ``artifact_dir``.
        """
        scratch_dir.mkdir(parents=True, exist_ok=True)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = artifact_dir / "transcript.jsonl"
        usage_path = artifact_dir / "usage.json"

        argv: list[str] = [
            "opensquilla",
            "agent",
            "--message",
            prompt,
            "--workspace",
            str(repo),
            # Read containment to the repo (codex review #9: explicit, not default).
            "--workspace-strict",
            # Write containment: writes must stay under workspace or scratch.
            "--workspace-lockdown",
            "--scratch-dir",
            str(scratch_dir),
            "--stateless",
            "--stateless-keep-project-rules",
            "--no-memory-capture",
            "--session-db-path",
            ":memory:",
            "--json",
            "--timeout",
            str(self.timeout),
            "--max-iterations",
            str(self.max_iterations),
            "--iteration-timeout-seconds",
            str(DEFAULT_ITERATION_TIMEOUT),
            "--max-provider-retries",
            str(DEFAULT_MAX_PROVIDER_RETRIES),
            "--transcript-path",
            str(transcript_path),
            "--usage-path",
            str(usage_path),
            "--permissions",
            "bypass",
        ]
        if self.model:
            argv += ["--model", self.model]
        if self.thinking:
            argv += ["--thinking", self.thinking]

        py_code = f"import sys\nsys.argv = {argv!r}\nfrom opensquilla.cli.main import app\napp()\n"
        cmd = [agent_python(), "-c", py_code]

        start = time.time()
        timed_out = False
        try:
            # cwd = repo so relative tool paths and test commands resolve there.
            # env is inherited (carries OPENROUTER_API_KEY); no env-file needed.
            result = subprocess.run(
                cmd,
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=self.timeout + SUBPROCESS_TIMEOUT_BUFFER,
            )
            exit_code = result.returncode
            stdout, stderr = result.stdout, result.stderr
        except subprocess.TimeoutExpired as e:
            timed_out = True
            exit_code = -1
            stdout = _decode(e.stdout)
            stderr = _decode(e.stderr)
            logger.warning("agent subprocess timed out after %ds", self.timeout)
        except FileNotFoundError as exc:
            raise RuntimeError(f"could not launch agent interpreter: {exc}") from exc

        duration = time.time() - start
        (artifact_dir / "agent_stdout.log").write_text(stdout or "")
        (artifact_dir / "agent_stderr.log").write_text(stderr or "")

        envelope = _parse_json_envelope(stdout)
        if timed_out:
            finish = "timeout"
        elif exit_code != 0:
            finish = "error"
        elif envelope is None:
            finish = "empty"
        else:
            status = envelope.get("status")
            text = (envelope.get("text") or "").strip()
            errors = envelope.get("errors") or []
            if status == "ok" and text:
                finish = "stop"
            elif errors:
                finish = "error"
            else:
                finish = "empty"

        usage = (envelope or {}).get("usage") or {}
        return AgentOutcome(
            success=finish == "stop",
            timeout=timed_out,
            exit_code=exit_code,
            finish_reason=finish,
            duration_seconds=round(duration, 1),
            session_id=(envelope or {}).get("session_key"),
            usage=usage,
        )


def _parse_json_envelope(stdout: str) -> dict | None:
    if not stdout:
        return None
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        if line.startswith("{") and line.endswith("}"):
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass
            break
    for raw in reversed(_JSON_OBJECT_RE.findall(stdout)):
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict) and "status" in obj:
                return obj
        except json.JSONDecodeError:
            continue
    return None


def _decode(data) -> str:
    if isinstance(data, bytes):
        return data.decode(errors="replace")
    return data or ""
