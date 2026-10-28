"""AI repair: send one small file plus the symptom to Groq, with key and model fallback."""
import os
import re

SYSTEM = (
    "You are an SRE bot repairing one tiny Python FastAPI microservice. You get its full source, "
    "a failing health probe, and recent logs. Find the defect and fix it with the smallest "
    "possible change: touch only the faulty line(s), keep every route, response shape and "
    "constant that is not at fault. The probe shows ONE example of the failure; fix the "
    "underlying defect in general and never special-case the values the probe uses. If the logs "
    "show a connection error to another service, check the URL/port constants against the "
    "service map. Reply with ONLY the complete corrected Python file: no markdown fences, "
    "no commentary."
)


class Keys:
    """Primary key first, then fallbacks; remembers which one last worked."""

    LABELS = ["primary", "fallback 1", "fallback 2"]

    def __init__(self):
        names = ["GROQ_API_KEY", "GROQ_API_KEY_2", "GROQ_API_KEY_3"]
        pairs = [(self.LABELS[i], os.getenv(n, "").strip()) for i, n in enumerate(names)]
        self.keys = [(label, key) for label, key in pairs if key]
        models = os.getenv("GROQ_FIX_MODELS", "openai/gpt-oss-120b,openai/gpt-oss-20b")
        self.models = [m.strip() for m in models.split(",") if m.strip()]
        self.tokens = 0
        self.last_label = None

    @property
    def live(self):
        return bool(self.keys)

    def public(self):
        return {"live": self.live, "keys": [label for label, _ in self.keys],
                "models": self.models, "tokens_used": self.tokens, "last_used": self.last_label}


def _strip_fences(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:python)?\s*\n(.*?)\n```\s*$", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"


def ask(keys: Keys, user_prompt: str, system: str = SYSTEM, max_tokens: int = 2500, raw: bool = False):
    """Return (text, meta). Raises RuntimeError if every key/model combination failed.

    Code replies have markdown fences stripped; pass raw=True for prose.
    """
    from groq import Groq
    errors = []
    for label, key in keys.keys:
        client = Groq(api_key=key)
        for model in keys.models:
            kwargs = dict(model=model, temperature=0, max_tokens=max_tokens,
                          messages=[{"role": "system", "content": system},
                                    {"role": "user", "content": user_prompt}])
            if "gpt-oss" in model:
                kwargs["reasoning_effort"] = "low"
            try:
                resp = client.chat.completions.create(**kwargs)
            except Exception as e:  # rate limit, auth, model gone: try the next combination
                errors.append(f"{label}/{model}: {type(e).__name__}")
                continue
            text = resp.choices[0].message.content or ""
            if not text.strip():
                errors.append(f"{label}/{model}: empty reply")
                continue
            used = getattr(resp.usage, "total_tokens", 0) or 0
            keys.tokens += used
            keys.last_label = label
            return (text.strip() if raw else _strip_fences(text)), {"key": label, "model": model, "tokens": used}
    raise RuntimeError("all Groq keys/models failed: " + "; ".join(errors))
