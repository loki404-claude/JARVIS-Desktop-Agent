"""AI dimaag: Groq + Gemini, tool calling aur automatic fallback ke saath."""
import base64
import json
import re

from core import config, memory, tools

MAX_STEPS = 6
MODES = ["Auto", "Groq", "Groq Fast", "Gemini"]


def stt_prompt() -> str:
    """Whisper ko shabdon ka andaza: spelling sudharti hai (224 token se chhota rakho)."""
    words = "Jarvis, Chrome, YouTube, Google, WhatsApp, VS Code, Notepad, screenshot, volume, mic, voice"
    extra = f", {config.STT_WORDS[:150]}" if config.STT_WORDS else ""
    return f"Jarvis, Chrome खोलो। YouTube पर गाना search करो। शब्द: {words}{extra}."


def is_prompt_echo(text: str) -> bool:
    """Chuppi par Whisper kabhi prompt hi dohra deta hai."""
    plain = text.strip(" .।")
    return len(plain.split()) >= 5 and plain in stt_prompt()


def clean(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def short_error(exc: Exception) -> str:
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if code in (401, 403):
        return "API key galat hai ya permission nahi hai"
    if code == 429:
        return "limit khatam (429), thodi der baad try karo"
    if code == 404:
        return "model nahi mila, .env mein model naam check karo"
    return f"{type(exc).__name__}: {str(exc)[:160]}"


class Brain:
    def __init__(self):
        self.history = []  # [{"role": "user"/"assistant", "content": str}]
        self.mode = "Auto"
        self.groq = None
        self.gemini = None
        self.errors = []
        if config.GROQ_API_KEY:
            try:
                from groq import Groq
                self.groq = Groq(api_key=config.GROQ_API_KEY, timeout=45, max_retries=1)
            except Exception as exc:
                self.errors.append(f"Groq: {short_error(exc)}")
        if config.GEMINI_API_KEY:
            try:
                from google import genai
                from google.genai import types
                self.gemini = genai.Client(api_key=config.GEMINI_API_KEY,
                                           http_options=types.HttpOptions(timeout=60000))
            except Exception as exc:
                self.errors.append(f"Gemini: {short_error(exc)}")
        tools.vision_callback = self.vision

    def _system(self) -> str:
        text = config.system_prompt() + f"\nAbhi: {tools.get_time()}."
        facts = [f["text"] for f in memory.items("facts")[-30:]]
        if facts:
            text += "\nUser ke baare mein yaad baatein: " + "; ".join(facts)
        return text

    def _chain(self) -> list:
        groq = [("groq", config.GROQ_CHAT_MODEL), ("groq", config.GROQ_FAST_MODEL)]
        gemini = [("gemini", config.GEMINI_CHAT_MODEL), ("gemini", config.GEMINI_FALLBACK_MODEL)]
        order = {"Gemini": gemini + groq, "Groq Fast": groq[::-1] + gemini}.get(self.mode, groq + gemini)
        available = {"groq": self.groq, "gemini": self.gemini}
        return [(p, m) for p, m in order if available[p]]

    def ask(self, text: str):
        """Return: (jawab, model jisne jawab diya)."""
        chain = self._chain()
        if not chain:
            return ("API key nahi mili. .env file mein GROQ_API_KEY ya GEMINI_API_KEY daalo "
                    "aur Jarvis dobara chalao."), "-"
        problems = []
        for provider, model in chain:
            done = []  # jo tools chal chuke hain
            try:
                runner = self._ask_groq if provider == "groq" else self._ask_gemini
                answer = runner(model, text, done) or "Mujhe koi jawab nahi mila, dobara bolo."
                self._remember(text, answer)
                return answer, model
            except Exception as exc:
                problems.append(f"{model}: {short_error(exc)}")
                if done:
                    # Kaam ho chuka hai, doosre model se dobara karwana galat hoga
                    answer = "Tool chala, par AI ka jawab beech mein ruk gaya. Result: " + "; ".join(done)
                    self._remember(text, answer)
                    return answer, model
        return "Abhi AI se connect nahi ho paya. " + " | ".join(problems), "-"

    def _remember(self, user: str, assistant: str):
        self.history += [{"role": "user", "content": user},
                         {"role": "assistant", "content": assistant}]
        self.history = self.history[-16:]

    def _ask_groq(self, model, text, done):
        messages = [{"role": "system", "content": self._system()}, *self.history,
                    {"role": "user", "content": text}]
        specs = [{"type": "function", "function": s} for s in tools.schemas()]
        extra = {"reasoning_effort": "low"} if "gpt-oss" in model else {}
        for _ in range(MAX_STEPS):
            msg = self.groq.chat.completions.create(
                model=model, messages=messages, tools=specs, tool_choice="auto",
                max_completion_tokens=2048, **extra).choices[0].message
            if not msg.tool_calls:
                return clean(msg.content)
            messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in msg.tool_calls]})
            for call in msg.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                    result = tools.run(call.function.name, args)
                except ValueError:
                    result = "ERROR: arguments sahi JSON nahi the."
                done.append(f"{call.function.name} -> {result[:150]}")
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
        return "Kaam bahut lamba ho gaya, maine yahin rok diya."

    def _ask_gemini(self, model, text, done):
        from google.genai import types
        contents = [types.Content(role="user" if h["role"] == "user" else "model",
                                  parts=[types.Part(text=h["content"])]) for h in self.history]
        contents.append(types.Content(role="user", parts=[types.Part(text=text)]))
        declarations = [types.FunctionDeclaration(
            name=s["name"], description=s["description"],
            parameters_json_schema=s["parameters"]) for s in tools.schemas()]
        cfg = types.GenerateContentConfig(
            system_instruction=self._system(),
            tools=[types.Tool(function_declarations=declarations)])
        for _ in range(MAX_STEPS):
            response = self.gemini.models.generate_content(model=model, contents=contents, config=cfg)
            calls = response.function_calls
            if not calls:
                return clean(response.text)
            contents.append(response.candidates[0].content)  # thought signature safe rehta hai
            parts = []
            for call in calls:
                result = tools.run(call.name, call.args or {})
                done.append(f"{call.name} -> {result[:150]}")
                parts.append(types.Part(function_response=types.FunctionResponse(
                    id=call.id, name=call.name, response={"result": result})))
            contents.append(types.Content(role="user", parts=parts))
        return "Kaam bahut lamba ho gaya, maine yahin rok diya."

    # ---------------- Vision, speech-to-text ----------------
    def vision(self, jpeg: bytes, question: str) -> str:
        problems = []
        if self.gemini:
            from google.genai import types
            for model in (config.GEMINI_VISION_MODEL, config.GEMINI_FALLBACK_MODEL):
                try:
                    return clean(self.gemini.models.generate_content(model=model, contents=[
                        types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"), question]).text)
                except Exception as exc:
                    problems.append(f"{model}: {short_error(exc)}")
        if self.groq:
            try:
                url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
                reply = self.groq.chat.completions.create(
                    model=config.GROQ_VISION_MODEL, reasoning_effort="none",
                    max_completion_tokens=1024, messages=[{"role": "user", "content": [
                        {"type": "text", "text": question},
                        {"type": "image_url", "image_url": {"url": url}}]}])
                return clean(reply.choices[0].message.content)
            except Exception as exc:
                problems.append(f"{config.GROQ_VISION_MODEL}: {short_error(exc)}")
        return "ERROR: vision fail hua. " + " | ".join(problems)

    def transcribe(self, wav: bytes) -> str:
        problem = None
        if self.groq:
            try:
                extra = {"language": config.STT_LANGUAGE} if config.STT_LANGUAGE != "auto" else {}
                result = self.groq.audio.transcriptions.create(
                    file=("speech.wav", wav), model=config.GROQ_STT_MODEL,
                    prompt=stt_prompt(), temperature=0, **extra)
                text = result.text.strip()
                return "" if is_prompt_echo(text) else text
            except Exception as exc:
                problem = exc
        if self.gemini:
            from google.genai import types
            result = self.gemini.models.generate_content(
                model=config.GEMINI_FALLBACK_MODEL, contents=[
                    types.Part.from_bytes(data=wav, mime_type="audio/wav"),
                    "Transcribe this speech exactly. Return only the words. If no speech, return nothing."])
            return (result.text or "").strip()
        if problem:
            raise problem
        raise RuntimeError("Mic ke liye GROQ_API_KEY ya GEMINI_API_KEY chahiye")
