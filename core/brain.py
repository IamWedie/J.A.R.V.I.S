import asyncio
import json
from datetime import datetime

from openai import AsyncOpenAI

import core.config as config
from core.tools import pc_tools, web_tools

SYSTEM_PROMPT = (
    "You are JARVIS, a witty, precise AI assistant with a voice, living on the user's Windows PC. "
    "Your replies are SPOKEN aloud to the user: keep them short and conversational (1-3 sentences), "
    "never output lists, markdown, code blocks, or raw data unless explicitly asked. "
    "Address the user respectfully.\n"
    "Tool rules:\n"
    "- For greetings, thanks, farewells, opinions, or general chat, reply directly WITHOUT tools.\n"
    "- Use tools when the request needs a PC action or live data.\n"
    "- After a tool result arrives, answer briefly using it; never invent values that were not returned.\n"
    "- NEVER close an app unless explicitly asked. NEVER repeat a tool call with identical arguments.\n"
    "- Never mention tools, JSON, or result mechanics; speak naturally."
)

TOOLS = [
    {"type": "function", "function": {
        "name": "launch_app",
        "description": "Open ANY application installed on the PC by fuzzy name match.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "close_app",
        "description": "Force-close an application by name. ONLY when the user explicitly asks.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"},
        }, "required": ["name"]},
    }},
    {"type": "function", "function": {
        "name": "set_volume",
        "description": "Set master speaker volume (0-100).",
        "parameters": {"type": "object", "properties": {
            "level": {"type": "integer"},
        }, "required": ["level"]},
    }},
    {"type": "function", "function": {
        "name": "get_volume",
        "description": "Get current master volume percentage.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "media_key",
        "description": "Send a media key: play_pause, next, previous, stop, mute.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["play_pause", "next", "previous", "stop", "mute"]},
        }, "required": ["action"]},
    }},
    {"type": "function", "function": {
        "name": "take_screenshot",
        "description": "Capture the screen and save it as an image file.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "system_info",
        "description": "Get CPU %, RAM %, battery status, disk usage.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "top_processes",
        "description": "List apps consuming the most RAM or CPU right now with real numbers.",
        "parameters": {"type": "object", "properties": {
            "metric": {"type": "string", "enum": ["memory", "cpu"]},
            "limit": {"type": "integer"},
        }},
    }},
    {"type": "function", "function": {
        "name": "list_running_apps",
        "description": "List names of running applications.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "search_files",
        "description": "Search files by name in user folders or a specific folder path.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"},
            "location": {"type": "string", "description": "'all', 'documents', 'downloads', etc., or an absolute folder path"},
            "limit": {"type": "integer"},
        }, "required": ["pattern"]},
    }},
    {"type": "function", "function": {
        "name": "get_clipboard",
        "description": "Read the current clipboard text.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "set_clipboard",
        "description": "Copy given text to the clipboard.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"},
        }, "required": ["text"]},
    }},
    {"type": "function", "function": {
        "name": "minimize_all_windows",
        "description": "Minimize every open window.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "focus_window",
        "description": "Bring a window to front by part of its title.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string"},
        }, "required": ["title"]},
    }},
    {"type": "function", "function": {
        "name": "type_text",
        "description": "Type text into the focused window via paste. Requires user approval in the UI.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"},
        }, "required": ["text"]},
    }},
    {"type": "function", "function": {
        "name": "open_url",
        "description": "Open a website URL in the default browser.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"},
        }, "required": ["url"]},
    }},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web for current information.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "lock_screen",
        "description": "Lock the screen. Requires user approval in the UI.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "sleep_pc",
        "description": "Put the PC to sleep. Requires user approval in the UI.",
        "parameters": {"type": "object", "properties": {}},
    }},
]

TOOL_FUNCTIONS = {
    "launch_app": pc_tools.launch_app,
    "close_app": pc_tools.close_app,
    "set_volume": pc_tools.set_volume,
    "get_volume": pc_tools.get_volume,
    "media_key": pc_tools.media_key,
    "take_screenshot": pc_tools.take_screenshot,
    "system_info": pc_tools.system_info,
    "top_processes": pc_tools.top_processes,
    "list_running_apps": pc_tools.list_running_apps,
    "search_files": pc_tools.search_files,
    "get_clipboard": pc_tools.get_clipboard,
    "set_clipboard": pc_tools.set_clipboard,
    "minimize_all_windows": pc_tools.minimize_all_windows,
    "focus_window": pc_tools.focus_window,
    "type_text": pc_tools.type_text,
    "open_url": pc_tools.open_url,
    "web_search": web_tools.web_search,
    "lock_screen": pc_tools.lock_screen,
    "sleep_pc": pc_tools.sleep_pc,
}

APPROVAL_REQUIRED = {"type_text", "lock_screen", "sleep_pc"}

APPROVAL_DESCRIPTIONS = {
    "type_text": lambda a: f'Type "{a.get("text", "")[:80]}" into the focused window',
    "lock_screen": lambda a: "Lock the screen",
    "sleep_pc": lambda a: "Put the PC to sleep",
}

MAX_TOOL_ROUNDS = 6
FORCE_ANSWER_AT_ROUND = 4

FRIENDLY_NAMES = {
    "x-preview-f-free": "Ox Alpha Free",
    "big-pickle": "Big Pickle",
    "claude-fable-5": "Claude Fable 5",
    "claude-opus-5": "Claude Opus 5",
    "claude-opus-4-8": "Claude Opus 4.8",
    "claude-opus-4-7": "Claude Opus 4.7",
    "claude-opus-4-6": "Claude Opus 4.6",
    "claude-opus-4-5": "Claude Opus 4.5",
    "claude-sonnet-5": "Claude Sonnet 5",
    "claude-sonnet-4-6": "Claude Sonnet 4.6",
    "claude-sonnet-4-5": "Claude Sonnet 4.5",
    "claude-haiku-4-5": "Claude Haiku 4.5",
    "deepseek-v4-pro": "DeepSeek V4 Pro",
    "deepseek-v4-flash": "DeepSeek V4 Flash",
    "gemini-3-flash": "Gemini 3 Flash",
    "gemini-3.7-flash": "Gemini 3.7 Flash",
    "gemini-3.6-flash": "Gemini 3.6 Flash",
    "gemini-3.5-flash": "Gemini 3.5 Flash",
    "gemini-3.1-pro": "Gemini 3.1 Pro",
    "glm-5.2": "GLM 5.2",
    "glm-5.1": "GLM 5.1",
    "gpt-5": "GPT 5",
    "gpt-5-nano": "GPT 5 Nano",
    "gpt-5.5": "GPT 5.5",
    "gpt-5.6-luna": "GPT 5.6 Luna",
    "gpt-5.6-sol": "GPT 5.6 Sol",
    "gpt-5.6-terra": "GPT 5.6 Terra",
    "grok-4.5": "Grok 4.5",
    "grok-4.6": "Grok 4.6",
    "kimi-k2.5": "Kimi K2.5",
    "kimi-k2.6": "Kimi K2.6",
    "kimi-k3": "Kimi K3",
    "minimax-m2.5": "MiniMax M2.5",
    "minimax-m3": "MiniMax M3",
    "qwen3.5-plus": "Qwen 3.5 Plus",
    "qwen3.6-plus": "Qwen 3.6 Plus",
}

FREE_MODELS = {
    "x-preview-f-free", "big-pickle", "mimo-v2.5-free", "hy3-free",
    "nemotron-3-ultra-free", "nemotron-3.5-lightning-free",
    "muse-spark-1.2-contributor-free", "laguna-s-2.1-free",
    "deepseek-v4-flash-free",
}


def is_free_model(mid):
    return mid in FREE_MODELS or mid.endswith("-free")


class Brain:
    def __init__(self):
        self.client = None
        self.model = config.DEFAULT_MODEL
        self.history = []
        self.approval_future = None

    def reset_client(self):
        self.client = None
        self.model = config.DEFAULT_MODEL
        self.reset_history()

    def _ensure_client(self):
        if self.client is None:
            if not config.ZEN_API_KEY:
                raise RuntimeError("ZEN_API_KEY is not set. Add your Zen key to jarvis\\.env")
            self.client = AsyncOpenAI(
                base_url=config.ZEN_BASE_URL,
                api_key=config.ZEN_API_KEY,
                timeout=60,
            )
        return self.client

    FALLBACK_MODELS = ["big-pickle", "nemotron-3.5-lightning-free"]

    async def _completion(self, messages, tools):
        client = self._ensure_client()
        candidates = [self.model] + [m for m in self.FALLBACK_MODELS if m != self.model]
        last_error = None
        for i, model in enumerate(candidates):
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=tools,
                    temperature=0.4,
                )
                if i > 0:
                    print(f"(switched brain to {model})")
                    self.model = model
                return response
            except Exception as e:
                last_error = e
                continue
        raise last_error

    async def fetch_models(self):
        client = self._ensure_client()
        response = await client.models.list()
        result = []
        for m in response.data:
            mid = m.id
            label = FRIENDLY_NAMES.get(mid) or " ".join(
                w.upper() if w in ("ai", "llm") else w.capitalize()
                for w in mid.replace("-", " ").replace("_", " ").split()
            )
            free = is_free_model(mid)
            if free:
                label += "  •FREE"
            result.append({"id": mid, "label": label, "free": free})
        result.sort(key=lambda x: (not x["free"], x["label"].lower()))
        return result

    def reset_history(self):
        self.history = []

    async def ask(self, user_text):
        client = self._ensure_client()
        system_message = {
            "role": "system",
            "content": SYSTEM_PROMPT + f"\nCurrent date and time: {datetime.now():%A %d %B %Y, %H:%M}.",
        }
        messages = [system_message] + self.history + [
            {"role": "user", "content": user_text}
        ]
        last_call_key = None

        for round_index in range(MAX_TOOL_ROUNDS):
            use_tools = TOOLS if round_index < FORCE_ANSWER_AT_ROUND else None
            response = await self._completion(messages, use_tools)
            msg = response.choices[0].message
            if not msg.tool_calls:
                reply = msg.content or ""
                self.history.append({"role": "user", "content": user_text})
                self.history.append({"role": "assistant", "content": reply})
                if len(self.history) > 40:
                    self.history = self.history[-40:]
                return reply

            messages.append(msg.model_dump(exclude_none=True))
            for call in msg.tool_calls:
                fn = TOOL_FUNCTIONS.get(call.function.name)
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                if fn is None:
                    result = f"Unknown tool: {call.function.name}"
                elif call.function.name in APPROVAL_REQUIRED:
                    approved = await self.request_approval(call.function.name, args)
                    if approved:
                        try:
                            result = await asyncio.to_thread(fn, **args)
                        except Exception as e:
                            result = f"Tool error: {e}"
                    else:
                        result = "The user DENIED this action. Do not retry it."
                else:
                    try:
                        result = await asyncio.to_thread(fn, **args)
                    except Exception as e:
                        result = f"Tool error: {e}"
                call_key = (call.function.name, json.dumps(args, sort_keys=True))
                if call_key == last_call_key:
                    result = f"{result}\n(Already provided. Answer now.)"
                last_call_key = call_key
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": str(result),
                })

        return "I apologize sir, I could not complete that request."

    async def request_approval(self, name, args):
        desc = APPROVAL_DESCRIPTIONS.get(name, lambda a: name)(args)
        from server import send_event
        future = asyncio.get_running_loop().create_future()
        self.approval_future = future
        await send_event({
            "type": "approval_request",
            "tool": name,
            "description": desc,
        })
        try:
            return await asyncio.wait_for(future, timeout=120)
        except asyncio.TimeoutError:
            return False
        finally:
            self.approval_future = None

    def resolve_approval(self, approved):
        if self.approval_future and not self.approval_future.done():
            self.approval_future.set_result(bool(approved))
