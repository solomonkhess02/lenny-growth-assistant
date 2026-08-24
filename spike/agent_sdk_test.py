"""SPIKE ONLY - throwaway. Validates Claude Agent SDK against Ollama and DeepSeek."""
import anyio, os, sys, time
from claude_agent_sdk import (query, ClaudeAgentOptions, AssistantMessage, ResultMessage,
                              TextBlock, ToolUseBlock, tool, create_sdk_mcp_server)


# --- secret redaction: nothing below may ever emit the API key -------------
class _Redactor:
    def __init__(self, stream, secrets):
        self._s, self._secrets = stream, [x for x in secrets if x and len(x) > 6]
    def write(self, data):
        for sec in self._secrets:
            data = data.replace(sec, "***REDACTED***")
        return self._s.write(data)
    def flush(self):
        return self._s.flush()

_secrets = [os.environ.get("DEEPSEEK_API_KEY", "")]
sys.stdout = _Redactor(sys.stdout, _secrets)
sys.stderr = _Redactor(sys.stderr, _secrets)
# ---------------------------------------------------------------------------

CLI = r"C:\Users\solom\.vscode\extensions\anthropic.claude-code-2.1.241-win32-x64\resources\native-binary\claude.exe"

# --- THE PROVIDER SEAM: the only provider-aware code in the system ---------
def provider_env(name: str) -> dict[str, str]:
    if name == "ollama":
        return {"ANTHROPIC_BASE_URL": "http://localhost:11434",
                "ANTHROPIC_AUTH_TOKEN": "ollama",
                "ANTHROPIC_MODEL": os.environ.get("OLLAMA_MODEL", "qwen3:4b-instruct"),
                "ANTHROPIC_SMALL_FAST_MODEL": os.environ.get("OLLAMA_MODEL", "qwen3:4b-instruct")}
    if name == "deepseek":
        key = os.environ.get("DEEPSEEK_API_KEY", "")
        return {"ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
                "ANTHROPIC_AUTH_TOKEN": key, "ANTHROPIC_API_KEY": key,
                "ANTHROPIC_MODEL": "deepseek-v4-pro",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-v4-pro",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-v4-flash",
                "ANTHROPIC_SMALL_FAST_MODEL": "deepseek-v4-flash"}
    raise ValueError(name)
# ---------------------------------------------------------------------------

@tool("lookup_evidence", "Return transcript evidence for a growth topic", {"topic": str})
async def lookup_evidence(args):
    return {"content": [{"type": "text",
            "text": f"[E1] Brian Balfour on '{args['topic']}': when new distribution "
                    f"platforms emerge, startups are usually the fastest to take advantage "
                    f"of them; it is slower for the incumbents to move."}]}

async def run(provider, prompt, *, use_tool=False, use_skills=False, max_turns=3):
    srv = create_sdk_mcp_server(name="ev", version="1.0.0", tools=[lookup_evidence])
    opts = ClaudeAgentOptions(
        env=provider_env(provider),
        cli_path=CLI,
        system_prompt="You are concise. Answer directly.",
        permission_mode="bypassPermissions",
        max_turns=max_turns,
        allowed_tools=(["mcp__ev__lookup_evidence"] if use_tool else [])
                      + (["Skill", "Read"] if use_skills else []),
        tools=(["mcp__ev__lookup_evidence"] if use_tool else [])
              + (["Skill", "Read"] if use_skills else []),
        mcp_servers={"ev": srv} if use_tool else {},
        setting_sources=["project"] if use_skills else None,
        skills=["05-ship30-writing"] if use_skills else None,
        cwd=r"D:\IIT CODES\PROJECTS\Oogway Labs Assignment",
    )
    text, tools_called, result = [], [], None
    t0 = time.time()
    async for msg in query(prompt=prompt, options=opts):
        if isinstance(msg, AssistantMessage):
            for b in msg.content:
                if isinstance(b, TextBlock): text.append(b.text)
                elif isinstance(b, ToolUseBlock): tools_called.append(b.name)
        elif isinstance(msg, ResultMessage):
            result = msg
    return {"elapsed": time.time() - t0, "text": "".join(text),
            "tools": tools_called,
            "is_error": getattr(result, "is_error", None),
            "subtype": getattr(result, "subtype", None)}

async def main():
    provider = sys.argv[1] if len(sys.argv) > 1 else "ollama"
    which = sys.argv[2] if len(sys.argv) > 2 else "all"
    print(f"### PROVIDER = {provider}  (model={provider_env(provider).get('ANTHROPIC_MODEL')})\n")

    if which in ("all", "1"):
        print("--- T1: basic text ---")
        try:
            r = await run(provider, "Reply with exactly: HELLO_FROM_AGENT_SDK")
            print(f"  {r['elapsed']:.1f}s  err={r['is_error']} subtype={r['subtype']}")
            print(f"  text: {r['text'][:200]!r}")
        except Exception as e:
            print(f"  FAILED {type(e).__name__}: {str(e)[:400]}")

    if which in ("all", "2"):
        print("\n--- T2: custom tool call ---")
        try:
            r = await run(provider, "Use the lookup_evidence tool for topic 'distribution', then summarise in one sentence.", use_tool=True)
            print(f"  {r['elapsed']:.1f}s  tools_called={r['tools']}  err={r['is_error']}")
            print(f"  text: {r['text'][:300]!r}")
        except Exception as e:
            print(f"  FAILED {type(e).__name__}: {str(e)[:400]}")

    if which in ("all", "3"):
        print("\n--- T3: skills loading ---")
        try:
            r = await run(provider, "List the section headings of the Ship 30 writing skill you have loaded. If you have no such skill, say NO_SKILL.", use_skills=True)
            print(f"  {r['elapsed']:.1f}s  err={r['is_error']}")
            print(f"  text: {r['text'][:400]!r}")
        except Exception as e:
            print(f"  FAILED {type(e).__name__}: {str(e)[:400]}")

anyio.run(main)
