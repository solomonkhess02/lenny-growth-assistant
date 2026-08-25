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
