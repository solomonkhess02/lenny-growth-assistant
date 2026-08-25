"""Pi Coding Agent runtime — the agent framework's execution boundary.

Pi (`@earendil-works/pi-coding-agent`) is the §3.1 agent framework for this
project. It was chosen over the Claude Agent SDK on measurement, not
preference: the SDK's harness prompt is an irreducible ~24,472 tokens against
our locked 8,192-token local context, while Pi's is **111**. Full comparison
in docs/agent-framework-comparison.md.

This module is deliberately small. Pi runs as a subprocess emitting JSON-lines
events; we consume those events, stream text deltas, and turn Pi's failures
into this application's error taxonomy. Everything else — retrieval, prompt
construction, grounding — stays where it already lives.

Four properties are enforced here rather than left to configuration, because
each one was a measured hazard:

  1. **`--no-tools`.** Pi ships read/write/edit/bash. In a web backend those
     are a live liability, and our retrieval is deterministic application code
     (skill 03), so the agent needs no tools at all.
  2. **A controlled working directory outside the repository.** Pi discovers
     and injects project context files. Measured: running from the repo root
     silently added **1,311 tokens of our own CLAUDE.md** to every request —
     16% of the context budget, invisible unless you count tokens.
  3. **`stopReason == "error"` is a real failure.** Pi exits 0 on unreachable
     endpoints, bad model ids and rejected credentials alike. A supervising
     process trusting the exit code would read every one of those as success.
  4. **The API key comes from the environment and is passed only to the child
     process.** It is never written to models.json, auth.json, a log line, or
     an error message.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from .config import Settings, get_settings
from .errors import ProviderMisconfigured, ProviderUnavailable

log = logging.getLogger("app.pi")

_TEXT_DELTA = "text_delta"

# Maximum bytes in ONE Pi JSON-lines event.
#
# asyncio.StreamReader defaults to 64 KiB per line and raises LimitOverrunError
# -> ValueError past it. Pi's terminal events (`turn_end`, `agent_end`) echo the
# whole conversation INCLUDING thinking content, so they scale with the
# generation rather than staying small. Measured in-container on a real Ship 30
# prompt (2026-08-25):
#
#     agent_end                     55,027 bytes
#     turn_end                      45,909 bytes
#     message_update/thinking_end   38,518 bytes   (8,656 thinking deltas)
#
# That run survived at 84% of the default limit. Three of six DeepSeek essay
# runs did not: the ValueError propagated out of the generator, and a COMPLETE
# essay was discarded after two to four minutes of generation while the reader
# saw `internal_error`. See docs/ship30-essays.md 10.5.
#
# 16 MiB is ~300x the largest event measured. It is a ceiling on one line, not
# a preallocation, and it does not change what is parsed -- only whether the
# reader refuses to parse it.
_STDOUT_LINE_LIMIT = 16 * 1024 * 1024


def classify_event(event: dict) -> tuple[str, str] | None:
    """Interpret one Pi JSON event.

    Returns ("delta", text), ("error", message), or None for events we ignore.

    Pure and separate from subprocess handling on purpose: *which* events
    matter is the part with real logic in it, and it is worth testing without
    spawning a process. It is also the part most likely to need updating when
    Pi's event schema moves.
    """
    message = event.get("message") or {}

    # Failure detection. Pi exits 0 for unreachable endpoints, bad model ids
    # and rejected credentials alike, so this is the only reliable signal.
    if message.get("stopReason") == "error":
        return "error", (message.get("errorMessage")
                         or "Pi reported an error with no message.")

    if event.get("type") != "message_update":
        return None

    delta_event = event.get("assistantMessageEvent") or {}
    if delta_event.get("type") != _TEXT_DELTA:
        return None

    text = delta_event.get("delta") or ""
    return ("delta", text) if text else None


def resolve_cli(configured: str) -> str | None:
    """Locate the Pi executable.

    On Windows npm installs a `pi.cmd` shim that `shutil.which` finds only
    when PATHEXT is consulted, so both spellings are tried.
    """
    if os.path.isabs(configured) and Path(configured).is_file():
        return configured
    found = shutil.which(configured)
    if found:
        return found
    if sys.platform == "win32":
        for suffix in (".cmd", ".exe", ".ps1"):
            found = shutil.which(configured + suffix)
            if found:
                return found
    return None


class PiRuntime:
    """Runs one Pi turn and streams its text deltas."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # -- environment -------------------------------------------------------
    @property
    def cli_path(self) -> str | None:
        return resolve_cli(self.settings.pi_cli_path)

    def workdir(self) -> Path:
        """A controlled directory that contains no project context files.

        Requirement 5. Configurable, but the default is a dedicated directory
        under the system temp root — never the repository, and never the
        process CWD, which in a container is the application root.
        """
        configured = self.settings.pi_working_dir.strip()
        base = Path(configured) if configured else Path(tempfile.gettempdir()) / "lenny-pi-workdir"
        base.mkdir(parents=True, exist_ok=True)
        return base

    def child_env(self, pi_provider: str) -> dict[str, str]:
        """Environment for the child process.

        The cloud key is read from configuration (which reads the environment)
        and handed to the child under the exact name Pi resolves for that
        provider — `pi-ai/dist/env-api-keys.js` maps provider -> env var by
        name. It is never persisted anywhere.
        """
        env = dict(os.environ)
        # Never let an ambient key leak into a provider that should not see it.
        env.pop("DEEPSEEK_API_KEY", None)
        if pi_provider == "deepseek" and self.settings.deepseek_api_key:
            env["DEEPSEEK_API_KEY"] = self.settings.deepseek_api_key
        return env

    # -- execution ---------------------------------------------------------
    def build_command(self, *, pi_provider: str, model: str,
                      prompt_ref: str,
                      system_prompt_file: Path | None = None,
                      append_system_prompt_file: Path | None = None
                      ) -> list[str]:
        cli = self.cli_path
        if cli is None:
            raise ProviderMisconfigured(
                f"Pi CLI not found (looked for '{self.settings.pi_cli_path}'). "
                f"Install it with: npm install -g @earendil-works/pi-coding-agent"
            )
        cmd = [
            cli, "-p", "--mode", "json",
            "--provider", pi_provider,
            "--model", model,
            # Requirement 4: no filesystem or shell surface, ever.
            "--no-tools",
            # Prompt templates are discovered from disk; keep the runtime
            # deterministic and free of ambient configuration.
            "--no-prompt-templates",
            # Skills are discovered from ~/.pi/agent/skills, ~/.agents/skills
            # and project directories, and those GLOBAL locations are found
            # regardless of our controlled working directory. We never rely on
            # Pi's skill discovery: it is progressive-disclosure, so a skill's
            # body only reaches the model through the `read` tool, which
            # --no-tools removes. Disabling it keeps an unrelated skill on the
            # host from silently entering a grounded generation.
            "--no-skills",
        ]
        # Prompt content is passed as FILES, never as argv. A multi-line
        # prompt on a command line is mangled by Windows quoting and can blow
        # the argv limit; Pi reads either flag's argument as a file when the
        # path exists (resolve-prompt-input), and otherwise treats it as
        # literal text -- so a wrong path would silently become the prompt.
        # These paths always exist because stream() has just written them.
        if system_prompt_file is not None:
            cmd += ["--system-prompt", str(system_prompt_file)]
        if append_system_prompt_file is not None:
            cmd += ["--append-system-prompt", str(append_system_prompt_file)]

        cmd.append(prompt_ref)
        return cmd

    async def stream(self, *, pi_provider: str, model: str, prompt: str,
                     system_prompt: str | None = None,
                     append_system_prompt: str | None = None
                     ) -> AsyncIterator[str]:
        """Yield text deltas from one Pi turn.

        Raises ProviderUnavailable / ProviderMisconfigured on failure; never
        returns a partial answer as if it had succeeded.

        `system_prompt` REPLACES Pi's default coding-assistant prompt and
        `append_system_prompt` is added after it. Both are written to files in
        the controlled working directory and referenced by absolute path,
        which is what keeps their content off the command line and out of any
        process listing. Passing `system_prompt` also overrides Pi's discovery
        of an ambient `~/.pi/agent/SYSTEM.md`.
        """
        # Fail before spawning anything. A missing credential is not fixed by
        # starting a Node process and waiting for a remote 401, and the
        # resulting error is far less actionable.
        if pi_provider == "deepseek" and not self.settings.deepseek_api_key:
            raise ProviderMisconfigured(
                "DEEPSEEK_API_KEY is not set. Set it in the environment, or "
                "switch LLM_PROVIDER=ollama.")

        workdir = self.workdir()
        # The prompt is written to a file and referenced with Pi's `@` syntax
        # rather than passed as an argv string: a multi-line evidence block on
        # a command line is mangled by shell quoting on Windows, and a large
        # prompt can exceed argv limits.
        # Everything from the first write to the spawn is guarded, because a
        # failure in between used to strand these files permanently:
        # build_command raises ProviderMisconfigured when the Pi CLI is
        # missing, and that happened BEFORE any cleanup existed. Each stranded
        # file holds the full evidence block, so this is a disclosure leak in a
        # shared temp directory, not just untidiness.
        scratch: list[Path] = []
        try:
            # The prompt is written to a file and referenced with Pi's `@`
            # syntax rather than passed as an argv string: a multi-line
            # evidence block on a command line is mangled by shell quoting on
            # Windows, and a large prompt can exceed argv limits.
            prompt_file = workdir / f"prompt-{uuid.uuid4().hex}.txt"
            prompt_file.write_text(prompt, encoding="utf-8")
            scratch.append(prompt_file)

            # Written alongside it and cleaned up with it. Absolute paths,
            # because Pi resolves a relative one against its own cwd.
            sys_file = append_file = None
            if system_prompt is not None:
                sys_file = workdir / f"system-{uuid.uuid4().hex}.txt"
                sys_file.write_text(system_prompt, encoding="utf-8")
                scratch.append(sys_file)
            if append_system_prompt is not None:
                append_file = workdir / f"append-{uuid.uuid4().hex}.txt"
                append_file.write_text(append_system_prompt, encoding="utf-8")
                scratch.append(append_file)

            cmd = self.build_command(
                pi_provider=pi_provider, model=model,
                prompt_ref=f"@{prompt_file.name}",
                system_prompt_file=sys_file.resolve() if sys_file else None,
                append_system_prompt_file=(
                    append_file.resolve() if append_file else None),
            )
        except BaseException:
            for f in scratch:
                f.unlink(missing_ok=True)
            raise
        t0 = time.perf_counter()
        emitted = 0
        oversized = 0
        failure: Exception | None = None

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workdir),
                env=self.child_env(pi_provider),
                # Not the 64 KiB default: Pi's terminal events carry the whole
                # conversation. See _STDOUT_LINE_LIMIT.
                limit=_STDOUT_LINE_LIMIT,
            )
        except FileNotFoundError as exc:
            for f in scratch:
                f.unlink(missing_ok=True)
            raise ProviderMisconfigured(
                f"Could not start the Pi CLI at '{self.cli_path}'."
            ) from exc

        try:
            assert proc.stdout is not None
            while True:
                try:
                    raw = await proc.stdout.readline()
                except ValueError:
                    # One event past _STDOUT_LINE_LIMIT. asyncio has already
                    # dropped it from the buffer, so the stream stays usable and
                    # the remaining events still arrive.
                    #
                    # Skipping beats propagating: letting this escape is what
                    # discarded finished essays. It is logged loudly rather than
                    # swallowed, and counted into the turn's log line, because a
                    # skipped terminal event could in principle have carried a
                    # stopReason -- and a silent skip would be exactly the kind
                    # of hidden failure this module exists to prevent.
                    oversized += 1
                    log.warning("pi_oversized_event", extra={
                        "pi_provider": pi_provider, "model": model,
                        "limit_bytes": _STDOUT_LINE_LIMIT,
                        "deltas_so_far": emitted, "outcome": "degraded"})
                    continue
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("{"):
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("pi_malformed_event", extra={"line": line[:200]})
                    continue

                classified = classify_event(event)
                if classified is None:
                    continue
                kind, payload = classified
                if kind == "error":
                    failure = map_error(payload, pi_provider)
                    break
                emitted += 1
                yield payload
        finally:
            stderr = b""
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            except (asyncio.TimeoutError, ValueError):
                pass
            for f in scratch:
                f.unlink(missing_ok=True)

            log.info("pi_turn_finished", extra={
                "framework": "pi", "pi_provider": pi_provider, "model": model,
                "deltas": emitted,
                "oversized_events": oversized,
                "system_prompt_override": system_prompt is not None,
                "append_system_prompt": append_system_prompt is not None,
                "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
                "outcome": "error" if failure else "ok",
            })

        if failure is not None:
            raise failure

        if emitted == 0:
            # A silent empty turn is indistinguishable from success downstream,
            # so it is treated as a failure rather than an empty answer.
            detail = stderr.decode("utf-8", errors="replace")[:200] if stderr else ""
            if oversized:
                # "No output" would send an operator looking for a dead
                # provider. Pi DID answer; the transport could not read it.
                raise ProviderUnavailable(
                    f"Pi produced {oversized} event(s) larger than the "
                    f"{_STDOUT_LINE_LIMIT} byte line limit for provider "
                    f"'{pi_provider}', and no readable output.")
            raise ProviderUnavailable(
                f"Pi produced no output for provider '{pi_provider}'."
                + (f" stderr: {detail}" if detail else ""))


def map_error(message: str, pi_provider: str) -> Exception:
    """Map a Pi error message onto this application's error taxonomy.

    The message is Pi's verbatim text. It is safe to surface: the provider
    masks credentials in its own errors, and we never interpolate the key.
    """
    lowered = message.lower()

    if "401" in lowered or "authentication" in lowered or "403" in lowered:
        return ProviderMisconfigured(
            f"Provider '{pi_provider}' rejected the credentials. Check the "
            f"API key in your environment. ({message[:160]})")

    if "unknown provider" in lowered:
        return ProviderMisconfigured(
            f"Pi does not know provider '{pi_provider}'. Check "
            f"~/.pi/agent/models.json. ({message[:160]})")

    if "404" in lowered or "not found" in lowered:
        return ProviderUnavailable(
            f"Model not available on provider '{pi_provider}'. For Ollama, "
            f"pull it first. ({message[:160]})")

    if ("connection" in lowered or "econnrefused" in lowered
            or "fetch failed" in lowered or "timeout" in lowered):
        return ProviderUnavailable(
            f"Cannot reach provider '{pi_provider}'. Is the model server "
            f"running? ({message[:160]})")

    return ProviderUnavailable(
        f"Pi turn failed on provider '{pi_provider}': {message[:200]}")
