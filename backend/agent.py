"""
agent.py  –  LLM-powered reasoning engine for CIPHER.

Now includes a lightweight provider orchestrator with automatic fallback
across multiple LLM APIs to improve reliability when a key is invalid,
quota is hit, or a provider is temporarily unavailable.
"""

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from dotenv import load_dotenv
from memory_store import get_memory_stats, get_relevant_memories, remember_from_message

load_dotenv()

# ── The CIPHER system prompt ───────────────────────────────────────
SYSTEM_PROMPT = """You are CIPHER — an advanced AI assistant built to help the user control their Windows laptop and answer questions intelligently.

The user's name is Uzair.

You must ALWAYS respond in valid JSON with these exact keys:
{
  "thought": "Your step-by-step internal reasoning about the request (invisible to user). Break down complex tasks logically.",
  "response": "Your spoken response to the user — be natural, concise, and helpful. Use a professional but warm tone, like a trusted assistant.",
  "command": null
}

CORE RULES:
1. If the user asks you to DO something on their computer (open an app, check battery, create a file, search something, change settings, manage processes, etc.), populate "command" with the exact PowerShell command to accomplish it on Windows.
2. If the user asks a QUESTION or wants a conversation, set "command" to null and just answer intelligently in "response".
3. Keep responses concise but informative. Don't be robotic — be warm and natural.
4. For app launches, prefer 'Start-Process' in PowerShell.
5. NEVER output anything outside the JSON object. No markdown, no backticks, just raw JSON.
6. If a request is ambiguous, ask for clarification in your "response" and set "command" to null.
7. You can chain multiple commands using semicolons if needed.
8. For web searches, open the default browser with the search URL.
9. Be proactive — if the user says "it's hot", you could offer to check the weather or adjust power settings. Think about what would be MOST helpful.
10. Address the user as "Uzair" naturally in your response (once is enough — don't force it).

ADVANCED REASONING:
11. For multi-step tasks, think through each step in "thought" before generating a combined command.
12. If a task requires checking something first (e.g., "delete the file if it exists"), write a command that handles both cases gracefully using PowerShell conditionals.
13. When the user asks about what happened (after a command ran), you may receive COMMAND OUTPUT context. Interpret the output naturally and explain what it means.
14. If a command is likely to fail (e.g., running something that requires admin), warn the user in your response and suggest alternatives.
15. Never hallucinate or guess commands. If you're unsure about the exact syntax, say so.
16. Commands should always include Write-Host or output so results are visible.

MEMORY RULES:
17. You may receive "LONG-TERM USER MEMORY" notes in a system message. Use them only when relevant to the current request.
18. Never mention hidden system-memory mechanics. If the user says "how do you remember that?", you can say you keep notes to serve them better.

POWERSHELL COMMAND GUIDELINES:
- Always use full parameter names for clarity (e.g., -Path, -ItemType, -Force)
- Use Write-Host for user-facing output
- For file operations, default to the user's Desktop or Documents unless specified
- Use $env:USERPROFILE for home directory paths
- Wrap paths with spaces in quotes
- For long-running operations, add progress or status messages

EXAMPLES:
User: "Open Chrome"
{"thought":"User wants to launch Google Chrome.","response":"On it, Uzair — opening Chrome now.","command":"Start-Process 'chrome'"}

User: "What's the capital of France?"
{"thought":"Simple geography question — no command needed.","response":"The capital of France is Paris — the City of Light, known for the Eiffel Tower and its rich history.","command":null}

User: "Check my battery level"
{"thought":"User wants battery info. I'll use WMI to query battery charge.","response":"Checking your battery now, Uzair.","command":"(Get-WmiObject Win32_Battery).EstimatedChargeRemaining | ForEach-Object { Write-Host \\"Battery is at $_%.\\" }"}

User: "Create a folder called Projects on my desktop"
{"thought":"User wants a new folder on their desktop. I'll use New-Item with -Force so it won't error if it already exists.","response":"Creating the Projects folder on your desktop, Uzair.","command":"New-Item -Path \\"$env:USERPROFILE\\\\Desktop\\\\Projects\\" -ItemType Directory -Force | ForEach-Object { Write-Host \\"Created: $($_.FullName)\\" }"}

User: "How much RAM is my laptop using?"
{"thought":"User wants current memory usage. I'll use Get-CimInstance to get total and available memory, then compute usage.","response":"Let me check your memory usage, Uzair.","command":"$os = Get-CimInstance Win32_OperatingSystem; $total = [math]::Round($os.TotalVisibleMemorySize/1MB, 1); $free = [math]::Round($os.FreePhysicalMemory/1MB, 1); $used = $total - $free; Write-Host \\"RAM: $used GB used / $total GB total ($([math]::Round($used/$total*100))% usage)\\""}

User: "List all running processes using more than 100MB"
{"thought":"User wants heavy processes. I'll use Get-Process sorted by WorkingSet64.","response":"Scanning for memory-hungry processes, Uzair.","command":"Get-Process | Where-Object { $_.WorkingSet64 -gt 100MB } | Sort-Object WorkingSet64 -Descending | Select-Object -First 10 Name, @{N='RAM(MB)';E={[math]::Round($_.WorkingSet64/1MB)}} | Format-Table -AutoSize"}
"""

# ── Conversation memory (lightweight, last N turns) ────────────────
conversation_history: list[dict] = []
MAX_HISTORY_TURNS = 14  # keep last N user+assistant pairs for speed


# ── LLM Orchestrator ───────────────────────────────────────────────

@dataclass
class ProviderConfig:
    name: str
    kind: str  # "openai-compatible" or "gemini"
    api_key_env: str
    model_env: str
    default_model: str
    base_url: str | None = None


class ProviderCallError(Exception):
    def __init__(self, provider: str, message: str):
        super().__init__(message)
        self.provider = provider
        self.message = message


PROVIDER_PRIORITY: list[ProviderConfig] = [
    # 1) Groq first: best latency + great for conversational command routing.
    ProviderConfig(
        name="groq",
        kind="openai-compatible",
        api_key_env="GROQ_API_KEY",
        model_env="GROQ_MODEL",
        default_model="llama-3.3-70b-versatile",
        base_url="https://api.groq.com/openai/v1",
    ),
    # 2) Together second: strong uptime/model availability and OpenAI-compatible API.
    ProviderConfig(
        name="together",
        kind="openai-compatible",
        api_key_env="TOGETHER_API_KEY",
        model_env="TOGETHER_MODEL",
        default_model="meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
        base_url="https://api.together.xyz/v1",
    ),
    # 3) OpenRouter third: broad model routing, good reliability backup.
    ProviderConfig(
        name="openrouter",
        kind="openai-compatible",
        api_key_env="OPENROUTER_API_KEY",
        model_env="OPENROUTER_MODEL",
        default_model="meta-llama/llama-3.3-70b-instruct",
        base_url="https://openrouter.ai/api/v1",
    ),
    # 4) Mistral fourth: stable direct provider fallback.
    ProviderConfig(
        name="mistral",
        kind="openai-compatible",
        api_key_env="MISTRAL_API_KEY",
        model_env="MISTRAL_MODEL",
        default_model="mistral-large-latest",
        base_url="https://api.mistral.ai/v1",
    ),
    # 5) Gemini last: excellent backup, separate API shape from OpenAI-compatible group.
    ProviderConfig(
        name="gemini",
        kind="gemini",
        api_key_env="GEMINI_API_KEY",
        model_env="GEMINI_MODEL",
        default_model="gemini-1.5-flash",
    ),
]


def _parse_json_or_fallback(raw: str) -> dict[str, Any]:
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {
            "thought": "Failed to parse model output as JSON.",
            "response": raw,
            "command": None,
        }

    result.setdefault("thought", "")
    result.setdefault("response", "I'm not sure how to respond to that.")
    result.setdefault("command", None)
    return result


# _ensure_uzair_addressing removed — the enhanced system prompt handles this
# naturally. Forced prepending created awkward phrasing.


def _is_non_retryable_auth_or_key_error(message: str) -> bool:
    m = message.lower()
    return any(token in m for token in ["api key", "api_key", "unauthorized", "invalid key", "authentication"])


def _is_rate_or_capacity_error(message: str) -> bool:
    m = message.lower()
    return any(token in m for token in ["429", "rate", "quota", "capacity", "overloaded", "too many requests"])


def _http_json_post(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    req_headers = {
        "Content-Type": "application/json",
        "User-Agent": "CIPHER-Agent/1.0",
        **headers
    }
    req = request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers=req_headers,
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8")
            return json.loads(text)
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
        raise ProviderCallError("http", f"HTTP {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise ProviderCallError("http", f"Network error: {exc.reason}") from exc


def _openai_compatible_chat(provider: ProviderConfig, api_key: str, messages: list[dict[str, str]]) -> str:
    model = os.getenv(provider.model_env, provider.default_model)
    url = f"{provider.base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.4,
        "max_tokens": 1024,
    }

    # Most OpenAI-compatible providers support this; safe to include for structured JSON output.
    payload["response_format"] = {"type": "json_object"}

    headers = {"Authorization": f"Bearer {api_key}"}
    if provider.name == "openrouter":
        # Optional but recommended by OpenRouter docs.
        headers["HTTP-Referer"] = "http://localhost"
        headers["X-Title"] = "Real Jarvis"

    data = _http_json_post(url=url, headers=headers, payload=payload)
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError) as exc:
        raise ProviderCallError(provider.name, f"Unexpected response format from {provider.name}: {data}") from exc


def _gemini_chat(provider: ProviderConfig, api_key: str, messages: list[dict[str, str]]) -> str:
    model = os.getenv(provider.model_env, provider.default_model)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    convo_lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "user").upper()
        content = msg.get("content", "")
        convo_lines.append(f"{role}: {content}")
    conversation_text = "\n".join(convo_lines)

    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": (
                            "Return ONLY a valid JSON object with keys: thought, response, command.\n\n"
                            f"Conversation:\n{conversation_text}"
                        )
                    }
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 1024,
            "responseMimeType": "application/json",
        },
    }

    data = _http_json_post(url=url, headers={}, payload=payload)

    # Handle blocked or empty candidates gracefully.
    candidates = data.get("candidates", [])
    if not candidates:
        prompt_feedback = data.get("promptFeedback", {})
        raise ProviderCallError(provider.name, f"Gemini returned no candidates: {prompt_feedback or data}")

    parts = candidates[0].get("content", {}).get("parts", [])
    if not parts:
        raise ProviderCallError(provider.name, f"Gemini returned no content parts: {data}")

    text = parts[0].get("text", "").strip()
    if not text:
        raise ProviderCallError(provider.name, f"Gemini returned empty text: {data}")
    return text


def _call_provider(provider: ProviderConfig, messages: list[dict[str, str]]) -> str:
    api_key = os.getenv(provider.api_key_env, "").strip()
    if not api_key:
        raise ProviderCallError(provider.name, f"{provider.api_key_env} not configured")

    try:
        if provider.kind == "openai-compatible":
            return _openai_compatible_chat(provider=provider, api_key=api_key, messages=messages)
        if provider.kind == "gemini":
            return _gemini_chat(provider=provider, api_key=api_key, messages=messages)
        raise ProviderCallError(provider.name, f"Unknown provider type: {provider.kind}")
    except ProviderCallError:
        raise
    except Exception as exc:
        raise ProviderCallError(provider.name, str(exc)) from exc


def _orchestrated_llm_call(messages: list[dict[str, str]]) -> tuple[str, str]:
    """
    Try providers in priority order and return (raw_response, provider_name).
    Falls back on auth/rate/transient failures.
    """
    failures: list[str] = []

    for provider in PROVIDER_PRIORITY:
        try:
            raw = _call_provider(provider=provider, messages=messages)
            return raw, provider.name
        except ProviderCallError as exc:
            msg = exc.message
            # Keep moving to the next provider for missing key, auth, rate, and transient errors.
            if (
                "not configured" in msg.lower()
                or _is_non_retryable_auth_or_key_error(msg)
                or _is_rate_or_capacity_error(msg)
                or "network error" in msg.lower()
                or "http 5" in msg.lower()
            ):
                failures.append(f"{provider.name}: {msg}")
                continue

            # Unknown errors also fail over, but we still capture them for visibility.
            failures.append(f"{provider.name}: {msg}")
            continue

    raise RuntimeError("All providers failed. " + " | ".join(failures))


def reset_history():
    """Clear conversation memory."""
    global conversation_history
    conversation_history = []


def ask_jarvis(user_message: str) -> dict:
    """
    Send a user message to the LLM and return the parsed JSON response.

    Returns
    -------
    dict with keys: thought, response, command
    """
    global conversation_history

    # Learn from user statements and persist important facts to long-term memory.
    try:
        remembered_count = remember_from_message(user_message)
    except Exception:
        remembered_count = 0

    conversation_history.append({"role": "user", "content": user_message})

    # Trim history: keep last N turns (user+assistant pairs) for context
    max_msgs = MAX_HISTORY_TURNS * 2
    if len(conversation_history) > max_msgs:
        conversation_history = conversation_history[-max_msgs:]

    try:
        relevant_memories = get_relevant_memories(user_message, limit=6)
    except Exception:
        relevant_memories = []

    memory_context = ""
    if relevant_memories:
        bullets = "\n".join(f"- {m}" for m in relevant_memories)
        memory_context = (
            "LONG-TERM USER MEMORY (use only if relevant to the user request):\n"
            f"{bullets}"
        )

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if memory_context:
        messages.append({"role": "system", "content": memory_context})
    messages += conversation_history

    try:
        import time as _time
        _t0 = _time.perf_counter()
        raw, provider_used = _orchestrated_llm_call(messages=messages)
        _latency_ms = round((_time.perf_counter() - _t0) * 1000)

        result = _parse_json_or_fallback(raw)

        # Add assistant response to history
        conversation_history.append({"role": "assistant", "content": raw})

        # Metadata for visibility/debugging in frontend/logs.
        result.setdefault("provider", provider_used)
        result.setdefault("latency_ms", _latency_ms)
        result.setdefault("memory_count", len(relevant_memories))
        result.setdefault("learned_memories", remembered_count)
        return result

    except Exception as e:
        error_msg = str(e)
        if "api key" in error_msg.lower() or "api_key" in error_msg.lower() or "auth" in error_msg.lower():
            return {
                "thought": "API key issue.",
                "response": "I couldn't authenticate with the configured AI providers. Please verify your .env keys.",
                "command": None,
            }
        return {
            "thought": f"Error occurred: {error_msg}",
            "response": f"I encountered an error: {error_msg}",
            "command": None,
            "memory_stats": get_memory_stats(),
        }


def interpret_command_output(command: str, output: str, success: bool) -> str | None:
    """
    Feed a command's output back to the LLM for natural interpretation.

    Returns a human-friendly interpretation string, or None on failure.
    This lets CIPHER say things like "Your battery is at 78%, looking good!"
    instead of just dumping raw PowerShell output.
    """
    if not output or not output.strip():
        return None

    # Don't interpret very long outputs — just let the user read them
    if len(output) > 800:
        return None

    global conversation_history

    feedback_msg = (
        f"COMMAND OUTPUT (interpret this naturally for the user):\n"
        f"Command: {command}\n"
        f"Success: {success}\n"
        f"Output:\n{output.strip()}"
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": feedback_msg},
    ]

    try:
        import time as _time
        _t0 = _time.perf_counter()
        raw, _ = _orchestrated_llm_call(messages=messages)
        result = _parse_json_or_fallback(raw)
        return result.get("response") or None
    except Exception:
        return None
