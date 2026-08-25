"""Pi Coding Agent adoption — the agent framework boundary.

Pi is the §3.1 agent framework. These tests pin the properties that made it
adoptable and the hazards that would silently undo them:

  - no tool surface reaches a web backend
  - Pi never runs from the repository, or it injects our own CLAUDE.md
  - Pi exits 0 on failure, so `stopReason: "error"` is the only real signal
  - the cloud key comes from the environment and is never written down
  - switching provider is configuration, with no branch in application code

Tests that need a live model are marked and skip cleanly; everything
structural runs offline.
"""
from __future__ import annotations

import inspect
import json
import logging
import os
from pathlib import Path

import pytest

from app.config import get_settings
from app.errors import ProviderMisconfigured, ProviderUnavailable
from app.pi_runtime import PiRuntime, map_error, resolve_cli
from app.providers import ModelProvider, get_provider

def _repo_root() -> Path | None:
    """The repository root, or None when tests run from an image.

    `parents[2]` resolves to `/` inside the container (tests live at
    /srv/tests), and every path has `/` as a parent -- which made the
    "workdir is outside the repo" assertion vacuously false. Detect the repo
    by a marker instead, and fall back to the application root.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "backend" / "app").is_dir():
            return candidate
    return None


APP_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def runtime() -> PiRuntime:
    return PiRuntime(get_settings())


@pytest.fixture(scope="session")
def pi_ready():
    """Skip cleanly when the Pi CLI is not installed."""
    if resolve_cli(get_settings().pi_cli_path) is None:
        pytest.skip("Pi CLI not installed. "
                    "npm install -g @earendil-works/pi-coding-agent")
    return True


# --------------------------------------------------------------------------
# Requirement 4 — no tool surface
# --------------------------------------------------------------------------
def test_command_always_disables_tools(runtime):
    cmd = runtime.build_command(pi_provider="ollama", model="m",
                                prompt_ref="@p.txt")
    assert "--no-tools" in cmd, "Pi's read/write/edit/bash reached a web backend"


def test_no_tool_allowlist_is_ever_passed(runtime):
    cmd = runtime.build_command(pi_provider="ollama", model="m",
                                prompt_ref="@p.txt")
    for flag in ("--tools", "--no-builtin-tools", "--exclude-tools"):
        assert flag not in cmd, f"{flag} implies a tool surface exists"


def test_prompt_templates_are_disabled(runtime):
    """Templates are discovered from disk; ambient config breaks determinism."""
    assert "--no-prompt-templates" in runtime.build_command(
        pi_provider="ollama", model="m", prompt_ref="@p.txt")


def test_command_uses_json_event_mode(runtime):
    cmd = runtime.build_command(pi_provider="ollama", model="m",
                                prompt_ref="@p.txt")
    assert "--mode" in cmd and "json" in cmd
    assert "-p" in cmd


# --------------------------------------------------------------------------
# Requirement 5 — controlled working directory
# --------------------------------------------------------------------------
def test_workdir_is_outside_the_repository(runtime):
    """Measured hazard: running from the repo root injected 1,311 tokens of
    our own CLAUDE.md into every request."""
    workdir = runtime.workdir().resolve()

    repo = _repo_root()
    if repo is not None:                      # running from a source checkout
        assert repo not in workdir.parents
        assert workdir != repo

    # Always true, source checkout or container image: Pi must not run from
    # the application tree, where it would discover project context files.
    assert APP_ROOT not in workdir.parents
    assert workdir != APP_ROOT


def test_workdir_contains_no_project_context_files(runtime):
    workdir = runtime.workdir()
    for name in ("CLAUDE.md", "AGENTS.md", ".pi", "pyproject.toml"):
        assert not (workdir / name).exists(), (
            f"{name} in Pi's workdir will be injected into every prompt")


def test_workdir_exists_and_is_writable(runtime):
    workdir = runtime.workdir()
    assert workdir.is_dir()
    probe = workdir / "._writable_probe"
    probe.write_text("x", encoding="utf-8")
    probe.unlink()


# --------------------------------------------------------------------------
# Requirement 6 — the key comes from the environment only
# --------------------------------------------------------------------------
def test_key_is_passed_only_for_deepseek(runtime, monkeypatch):
    monkeypatch.setattr(runtime.settings, "deepseek_api_key", "sk-test-value")
    assert "DEEPSEEK_API_KEY" not in runtime.child_env("ollama"), \
        "the cloud key was handed to the local provider"
    assert runtime.child_env("deepseek")["DEEPSEEK_API_KEY"] == "sk-test-value"


def test_ambient_key_cannot_leak_into_the_local_provider(runtime, monkeypatch):
    """An operator's shell export must not reach the Ollama child process."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ambient-leak")
    assert "DEEPSEEK_API_KEY" not in runtime.child_env("ollama")


def test_key_is_never_written_to_pi_config_files(runtime):
    """Requirement 6: never models.json, never auth.json."""
    key = get_settings().deepseek_api_key
    if not key:
        pytest.skip("no DeepSeek key configured")
    base = Path(os.path.expanduser("~/.pi/agent"))
    for name in ("models.json", "auth.json"):
        path = base / name
        if path.is_file():
            assert key not in path.read_text(encoding="utf-8", errors="ignore"), \
                f"the API key is stored at rest in {name}"


def test_key_never_appears_in_the_command_line(runtime, monkeypatch):
    """argv is visible to any process listing on the host."""
    monkeypatch.setattr(runtime.settings, "deepseek_api_key", "sk-test-value")
    cmd = runtime.build_command(pi_provider="deepseek", model="m",
                                prompt_ref="@p.txt")
    assert not any("sk-test-value" in part for part in cmd)
    assert "--api-key" not in cmd


def test_error_mapping_does_not_echo_credentials():
    err = map_error("401 Authentication Fails, Your api key: ****seek is invalid",
                    "deepseek")
    assert isinstance(err, ProviderMisconfigured)
    assert "sk-" not in err.message


# --------------------------------------------------------------------------
# Requirement 7 — Pi errors become application errors
# --------------------------------------------------------------------------
@pytest.mark.parametrize("message,expected", [
    ("401 Authentication Fails, Your api key: ****abcd is invalid", ProviderMisconfigured),
    ("403 Forbidden", ProviderMisconfigured),
    ('Error: Unknown provider "nope"', ProviderMisconfigured),
    ("404 model 'nope:99b' not found", ProviderUnavailable),
    ("Connection error.", ProviderUnavailable),
    ("fetch failed", ProviderUnavailable),
    ("request timeout after 30s", ProviderUnavailable),
    ("something entirely unexpected", ProviderUnavailable),
])
def test_error_taxonomy_mapping(message, expected):
    assert isinstance(map_error(message, "ollama"), expected)


def test_mapped_errors_carry_http_status_for_fastapi():
    """Requirement 7: failure must reach the API layer as a real error."""
    assert map_error("401 auth", "deepseek").http_status == 500
    assert map_error("Connection error.", "ollama").http_status == 503


def test_unknown_provider_message_names_the_config_file():
    err = map_error('Error: Unknown provider "zzz"', "zzz")
    assert "models.json" in err.message


def test_model_not_found_is_actionable():
    err = map_error("404 model 'x' not found", "ollama")
    assert "pull" in err.message.lower()


# --------------------------------------------------------------------------
# Requirement 3 — the seam, and no branching in application code
# --------------------------------------------------------------------------
def test_both_providers_declare_a_pi_provider_name():
    assert get_provider("ollama").pi_provider == "ollama"
    # MUST be exactly "deepseek": Pi resolves DEEPSEEK_API_KEY by provider name.
    assert get_provider("deepseek").pi_provider == "deepseek"


def test_providers_report_the_configured_models():
    settings = get_settings()
    assert get_provider("ollama").model == settings.ollama_model
    assert get_provider("deepseek").model == settings.deepseek_model


def test_generation_is_not_reimplemented_per_provider():
    """One inherited stream() is what makes config-only switching structural."""
    for name in ("ollama", "deepseek"):
        cls = type(get_provider(name))
        assert "stream" not in cls.__dict__, (
            f"{cls.__name__} overrides stream(); provider-specific generation "
            f"is how 'switch by configuration' quietly stops being true")
    assert "stream" in ModelProvider.__dict__


def test_agent_layer_has_no_pi_specific_logic():
    """Adopting Pi must not leak the framework into the agent."""
    import app.agent as agent_mod
    src = inspect.getsource(agent_mod)
    for token in ("pi_runtime", "PiRuntime", "--no-tools", "subprocess"):
        assert token not in src, f"Pi detail leaked into the agent layer: {token}"


def test_describe_surfaces_the_framework():
    for name in ("ollama", "deepseek"):
        described = get_provider(name).describe()
        assert described["agent_framework"] == "pi"
        assert described["pi_provider"]


# --------------------------------------------------------------------------
# CLI resolution
# --------------------------------------------------------------------------
def test_missing_cli_raises_a_configured_error(runtime, monkeypatch):
    monkeypatch.setattr(runtime.settings, "pi_cli_path", "definitely-not-a-real-binary")
    with pytest.raises(ProviderMisconfigured) as e:
        runtime.build_command(pi_provider="ollama", model="m", prompt_ref="@p.txt")
    assert "npm install" in e.value.message


def test_cli_resolution_finds_the_installed_pi(pi_ready, runtime):
    assert Path(resolve_cli(runtime.settings.pi_cli_path)).exists()


# --------------------------------------------------------------------------
# Live execution
# --------------------------------------------------------------------------
@pytest.mark.usefixtures("pi_ready", "ollama_ready")
async def test_ollama_execution_streams_deltas(runtime):
    parts = [d async for d in runtime.stream(
        pi_provider="ollama", model=get_settings().ollama_model,
        prompt="Reply with exactly one word: BANANA")]
    assert parts, "no deltas received"
    assert len(parts) >= 1
    assert "BANANA" in "".join(parts).upper()


@pytest.mark.usefixtures("pi_ready", "ollama_ready")
async def test_prompt_file_is_cleaned_up(runtime):
    before = set(runtime.workdir().glob("prompt-*.txt"))
    async for _ in runtime.stream(pi_provider="ollama",
                                  model=get_settings().ollama_model,
                                  prompt="Say OK."):
        pass
    assert set(runtime.workdir().glob("prompt-*.txt")) == before, \
        "prompt files are accumulating in the working directory"


@pytest.mark.usefixtures("pi_ready", "ollama_ready")
async def test_bad_model_becomes_an_application_error(runtime):
    """Pi exits 0 here. The event stream is the only signal."""
    with pytest.raises(ProviderUnavailable) as e:
        async for _ in runtime.stream(pi_provider="ollama",
                                      model="definitely-not-a-model:99b",
                                      prompt="Say OK."):
            pass
    assert "not available" in e.value.message.lower()


@pytest.mark.usefixtures("pi_ready")
async def test_unknown_provider_becomes_an_application_error(runtime):
    with pytest.raises((ProviderMisconfigured, ProviderUnavailable)):
        async for _ in runtime.stream(pi_provider="not-a-real-provider",
                                      model="x", prompt="Say OK."):
            pass


@pytest.mark.usefixtures("pi_ready")
async def test_rejected_credentials_become_a_configuration_error(runtime,
                                                                 monkeypatch):
    if not get_settings().deepseek_api_key:
        pytest.skip("no DeepSeek key configured")
    monkeypatch.setattr(runtime.settings, "deepseek_api_key", "sk-obviously-invalid")
    with pytest.raises(ProviderMisconfigured) as e:
        async for _ in runtime.stream(
                pi_provider="deepseek",
                model=get_settings().deepseek_model, prompt="Say OK."):
            pass
    assert "credential" in e.value.message.lower() or "key" in e.value.message.lower()


# --------------------------------------------------------------------------
# Requirement 9 — end-to-end guarantees through the real framework
# --------------------------------------------------------------------------
@pytest.mark.usefixtures("pi_ready", "corpus_ready", "ollama_ready")
async def test_unsupported_question_never_spawns_pi(corpus_db, monkeypatch):
    """Abstention must not reach the agent framework at all.

    Counts real subprocess launches rather than trusting a stub: the point is
    that no Pi process is created, not merely that a mock went uncalled.
    """
    import app.pi_runtime as pi_mod
    from app.agent import ABSTENTION, answer_question

    spawns = []
    real = pi_mod.asyncio.create_subprocess_exec

    async def counting(*args, **kwargs):
        spawns.append(args[0] if args else "?")
        return await real(*args, **kwargs)

    monkeypatch.setattr(pi_mod.asyncio, "create_subprocess_exec", counting)

    result = await answer_question(
        corpus_db, "How do I make a sourdough starter from scratch?",
        provider=get_provider("ollama"))

    assert result.abstained is True
    assert result.answer == ABSTENTION
    assert spawns == [], f"Pi was launched for an unsupported question: {spawns}"


@pytest.mark.usefixtures("pi_ready", "corpus_ready", "ollama_ready")
async def test_grounded_answer_through_pi_on_ollama(corpus_db):
    """The mandated demo path, end to end, through the agent framework."""
    from app.agent import answer_question

    result = await answer_question(
        corpus_db, "How does Duolingo use streaks to improve retention?",
        provider=get_provider("ollama"))

    assert result.abstained is False
    assert result.supported is True
    assert result.provider == "ollama"
    assert result.answer.strip()
    assert result.sources
    # Grounding still runs, and still gates. Requirement 8.
    assert result.grounding.invalid_tags == [], \
        f"model invented citations: {result.grounding.invalid_tags}"


@pytest.mark.usefixtures("pi_ready", "corpus_ready")
async def test_provider_switch_needs_no_application_code_change(corpus_db):
    """Same call site, different configuration, both answer.

    Skips DeepSeek rather than failing when no cloud key is present, so the
    suite stays runnable offline.
    """
    from app.agent import answer_question
    if not get_settings().deepseek_api_key:
        pytest.skip("no DeepSeek key configured")

    question = "How does Duolingo use streaks to improve retention?"
    seen = {}
    for name in ("ollama", "deepseek"):
        result = await answer_question(corpus_db, question,
                                       provider=get_provider(name))
        seen[name] = result

    assert seen["ollama"].provider == "ollama"
    assert seen["deepseek"].provider == "deepseek"
    for name, result in seen.items():
        assert result.supported, f"{name} returned no evidence"
        assert result.answer.strip(), f"{name} produced no answer"
        assert result.grounding.invalid_tags == [], \
            f"{name} invented citations: {result.grounding.invalid_tags}"


# --------------------------------------------------------------------------
# Event classification (pure — no subprocess)
# --------------------------------------------------------------------------
def test_classify_text_delta():
    from app.pi_runtime import classify_event
    event = {"type": "message_update",
             "assistantMessageEvent": {"type": "text_delta", "delta": "hi"}}
    assert classify_event(event) == ("delta", "hi")


def test_classify_error_takes_priority_over_everything():
    from app.pi_runtime import classify_event
    kind, msg = classify_event(
        {"type": "message_update",
         "message": {"stopReason": "error", "errorMessage": "401 nope"}})
    assert kind == "error"
    assert msg == "401 nope"


def test_classify_error_without_a_message_still_reports():
    from app.pi_runtime import classify_event
    kind, msg = classify_event({"message": {"stopReason": "error"}})
    assert kind == "error"
    assert msg.strip(), "an error with no text must still surface something"


@pytest.mark.parametrize("event", [
    {"type": "agent_start"},
    {"type": "turn_start"},
    {"type": "session", "id": "x"},
    {"type": "message_update", "assistantMessageEvent": {"type": "text_start"}},
    {"type": "message_update", "assistantMessageEvent":
        {"type": "text_delta", "delta": ""}},
    {},
])
def test_classify_ignores_noise(event):
    from app.pi_runtime import classify_event
    assert classify_event(event) is None


def test_successful_turn_is_not_misread_as_an_error():
    from app.pi_runtime import classify_event
    assert classify_event({"type": "turn_end",
                           "message": {"stopReason": "stop"}}) is None


# --------------------------------------------------------------------------
# Phase 6: prompt files, and the skill discovery we deliberately refuse
# --------------------------------------------------------------------------
class TestPromptFileDelivery:
    """How the Ship 30 instructions reach Pi, and why not via `--skill`.

    Pi's skill loader is progressive-disclosure: only a skill's name and
    description enter the system prompt, and the body arrives when the model
    calls `read`. We run --no-tools, so that body would never arrive. The
    instructions are passed as prompt FILES instead, which is deterministic and
    needs no tool surface.
    """

    def _runtime(self):
        from app.config import get_settings
        from app.pi_runtime import PiRuntime
        return PiRuntime(get_settings())

    def test_skill_discovery_is_always_disabled(self):
        """Global skill dirs (~/.pi/agent/skills, ~/.agents/skills) are found
        regardless of our controlled working directory, so the workdir fix does
        not cover them. An unrelated skill on the host must not be able to
        enter a grounded generation."""
        cmd = self._runtime().build_command(
            pi_provider="ollama", model="m", prompt_ref="@p.txt")
        assert "--no-skills" in cmd

    def test_tools_stay_disabled_when_prompts_are_passed(self):
        """The Phase 6 flags must not have loosened the Phase 4 guarantees."""
        from pathlib import Path
        cmd = self._runtime().build_command(
            pi_provider="ollama", model="m", prompt_ref="@p.txt",
            system_prompt_file=Path("/tmp/s.txt"),
            append_system_prompt_file=Path("/tmp/a.txt"))
        assert "--no-tools" in cmd
        assert "--no-prompt-templates" in cmd
        assert "--no-skills" in cmd

    def test_prompt_flags_are_absent_on_the_chat_path(self):
        """Answers must call Pi exactly as they always did."""
        cmd = self._runtime().build_command(
            pi_provider="ollama", model="m", prompt_ref="@p.txt")
        assert "--system-prompt" not in cmd
        assert "--append-system-prompt" not in cmd

    def test_prompt_content_never_reaches_the_command_line(self):
        """S4. Files, not argv.

        A multi-line prompt on a command line is mangled by Windows quoting and
        can exceed the argv limit -- and anything on argv is visible in a
        process listing.
        """
        from pathlib import Path
        cmd = self._runtime().build_command(
            pi_provider="ollama", model="m", prompt_ref="@p.txt",
            system_prompt_file=Path("/tmp/system-abc.txt"),
            append_system_prompt_file=Path("/tmp/append-abc.txt"))
        joined = " ".join(cmd)
        assert "ONLY the provided evidence" not in joined
        assert "Ship 30" not in joined

    async def test_prompt_files_are_written_in_the_workdir_and_cleaned_up(self):
        """S3. Written where nothing else can read them, gone afterwards.

        Pi treats a non-existent --system-prompt path as literal prompt TEXT,
        so these files existing at spawn time is load-bearing, not hygiene.
        """
        from app.errors import ProviderMisconfigured, ProviderUnavailable

        runtime = self._runtime()
        workdir = runtime.workdir()
        before = set(workdir.iterdir())

        # A bogus CLI makes the spawn fail immediately; the cleanup path is
        # what is under test, not the generation.
        runtime.settings = runtime.settings.model_copy(
            update={"pi_cli_path": "definitely-not-a-real-cli-xyz"})
        try:
            async for _ in runtime.stream(
                    pi_provider="ollama", model="m", prompt="hello",
                    system_prompt="RULES", append_system_prompt="SKILL"):
                pass
        except (ProviderMisconfigured, ProviderUnavailable):
            pass

        leftover = set(workdir.iterdir()) - before
        assert not leftover, f"scratch prompt files were left behind: {leftover}"


# --------------------------------------------------------------------------
# Oversized JSON-lines events (the 64 KiB transport defect)
#
# asyncio.StreamReader defaults to 64 KiB per line. Pi's terminal events
# (turn_end, agent_end) echo the whole conversation INCLUDING thinking content,
# so they grow with the generation: agent_end was measured at 55,027 bytes on a
# real Ship 30 prompt, and three of six DeepSeek essay runs exceeded the limit
# outright. The ValueError escaped the generator, so a COMPLETE essay was
# thrown away after minutes of generation and the reader saw internal_error.
#
# These tests drive the reader with a stub CLI rather than a live model: the
# defect is in how stdout is consumed, and that is reproducible deterministically
# and offline.
# --------------------------------------------------------------------------
import sys


def _stub_cli(tmp_path: Path, lines: list[str]) -> list[str]:
    """A command that prints the given stdout lines and exits 0.

    Stands in for the Pi CLI. Pi's own exit code is meaningless (it exits 0 on
    failure), so nothing here depends on it.
    """
    script = tmp_path / "stub_pi.py"
    payload = json.dumps(lines)
    script.write_text(
        "import sys, json\n"
        f"for line in json.loads({payload!r}):\n"
        "    sys.stdout.write(line + '\\n')\n"
        "sys.stdout.flush()\n",
        encoding="utf-8")
    return [sys.executable, str(script)]


def _delta(text: str) -> str:
    return json.dumps({
        "type": "message_update",
        "assistantMessageEvent": {"type": "text_delta", "delta": text},
    })


def _huge_terminal_event(min_bytes: int, *, stop_reason: str | None = None) -> str:
    """One Pi terminal event padded past `min_bytes`, as thinking content does."""
    message: dict = {"content": [{"type": "thinking", "thinking": "x" * min_bytes}]}
    if stop_reason:
        message["stopReason"] = stop_reason
        message["errorMessage"] = "model not found: definitely-not-a-model"
    line = json.dumps({"type": "turn_end", "message": message})
    assert len(line.encode()) > min_bytes
    return line


async def test_an_event_larger_than_64_kib_does_not_kill_the_stream(
        runtime, tmp_path, monkeypatch):
    """The regression. 100 KiB in one line used to raise ValueError.

    The deltas here arrive BEFORE the oversized terminal event, exactly as a
    finished essay's text does -- which is what made the old behaviour so
    expensive: the content was already complete when the read threw it away.
    """
    lines = [_delta("Ship 30 essay, "), _delta("complete."),
             _huge_terminal_event(100 * 1024)]
    monkeypatch.setattr(runtime, "build_command",
                        lambda **kw: _stub_cli(tmp_path, lines))

    parts = [d async for d in runtime.stream(
        pi_provider="ollama", model="m", prompt="p")]

    assert parts == ["Ship 30 essay, ", "complete."]


async def test_an_oversized_event_is_parsed_not_skipped(
        runtime, tmp_path, monkeypatch):
    """Raising the limit must PARSE the big line, not discard it.

    A skipped terminal event would lose `stopReason: "error"` -- the only
    reliable failure signal Pi gives us -- and a failed turn would be reported
    as a successful one. So the signal is put inside the oversized line itself.
    """
    lines = [_delta("partial"),
             _huge_terminal_event(100 * 1024, stop_reason="error")]
    monkeypatch.setattr(runtime, "build_command",
                        lambda **kw: _stub_cli(tmp_path, lines))

    with pytest.raises(ProviderUnavailable) as exc:
        async for _ in runtime.stream(pi_provider="ollama", model="m",
                                      prompt="p"):
            pass
    assert "not available" in exc.value.message.lower()


async def test_an_event_past_even_the_raised_limit_degrades_visibly(
        runtime, tmp_path, monkeypatch, caplog):
    """Past the ceiling the turn degrades; it never throws away the stream.

    asyncio drops the offending line from its buffer, so events after it still
    arrive. The skip is logged and counted rather than swallowed, because a
    silent skip is the failure mode this module exists to prevent.
    """
    import app.pi_runtime as pi_mod
    monkeypatch.setattr(pi_mod, "_STDOUT_LINE_LIMIT", 8 * 1024)

    lines = [_delta("before "), _huge_terminal_event(64 * 1024),
             _delta("after")]
    monkeypatch.setattr(runtime, "build_command",
                        lambda **kw: _stub_cli(tmp_path, lines))

    with caplog.at_level(logging.WARNING, logger="app.pi"):
        parts = [d async for d in runtime.stream(
            pi_provider="ollama", model="m", prompt="p")]

    assert parts == ["before ", "after"], "events after the oversized one were lost"
    assert any(r.message == "pi_oversized_event" for r in caplog.records), \
        "an oversized event was skipped without a warning"


async def test_scratch_files_are_cleaned_up_on_the_oversized_path(
        runtime, tmp_path, monkeypatch):
    """S3 still holds when a line blows the limit.

    The prompt, system and append files each hold the full evidence block, so
    leaving them behind in a shared temp directory is a disclosure leak, not
    untidiness.
    """
    import app.pi_runtime as pi_mod
    monkeypatch.setattr(pi_mod, "_STDOUT_LINE_LIMIT", 8 * 1024)

    workdir = runtime.workdir()
    before = set(workdir.iterdir())

    lines = [_delta("x"), _huge_terminal_event(64 * 1024)]
    monkeypatch.setattr(runtime, "build_command",
                        lambda **kw: _stub_cli(tmp_path, lines))

    async for _ in runtime.stream(pi_provider="ollama", model="m", prompt="p",
                                  system_prompt="RULES",
                                  append_system_prompt="SKILL"):
        pass

    leftover = set(workdir.iterdir()) - before
    assert not leftover, f"scratch files left behind: {leftover}"


def test_the_line_limit_is_far_above_what_pi_actually_emits():
    """The ceiling is justified by measurement, not by taste.

    Largest event measured in-container on a real Ship 30 prompt was
    agent_end at 55,027 bytes, already 84% of the old 64 KiB default.
    """
    from app.pi_runtime import _STDOUT_LINE_LIMIT
    largest_measured = 55_027
    assert _STDOUT_LINE_LIMIT > 65_536, "still at or below the default that broke"
    assert _STDOUT_LINE_LIMIT >= largest_measured * 100, \
        "too little headroom over the largest event actually observed"


async def test_an_entirely_unreadable_turn_says_so_not_no_output(
        runtime, tmp_path, monkeypatch):
    """"No output" would send an operator hunting a dead provider.

    Pi answered; the transport could not read it. Those are different faults
    with different fixes, so they get different messages.
    """
    import app.pi_runtime as pi_mod
    monkeypatch.setattr(pi_mod, "_STDOUT_LINE_LIMIT", 8 * 1024)

    lines = [_huge_terminal_event(64 * 1024)]
    monkeypatch.setattr(runtime, "build_command",
                        lambda **kw: _stub_cli(tmp_path, lines))

    with pytest.raises(ProviderUnavailable) as exc:
        async for _ in runtime.stream(pi_provider="ollama", model="m",
                                      prompt="p"):
            pass
    assert "larger than" in exc.value.message
    assert "no output" not in exc.value.message.lower()
