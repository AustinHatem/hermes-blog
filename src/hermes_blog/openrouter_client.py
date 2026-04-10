from __future__ import annotations

import json
import os
from dataclasses import dataclass

import requests


class OpenRouterError(RuntimeError):
    pass


@dataclass
class OpenRouterClient:
    api_key: str
    base_url: str = "https://openrouter.ai/api/v1"
    referer: str = "https://github.com/AustinHatem/hermes-blog"
    title: str = "hermes-blog"

    @classmethod
    def from_env(cls) -> "OpenRouterClient":
        api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            raise OpenRouterError("OPENROUTER_API_KEY is missing")
        return cls(api_key=api_key)

    def chat(self, *, model: str, system: str, user: str, temperature: float = 0.7, max_tokens: int = 2500) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.referer,
            "X-Title": self.title,
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        response = requests.post(f"{self.base_url}/chat/completions", headers=headers, json=payload, timeout=300)
        if response.status_code != 200:
            raise OpenRouterError(f"OpenRouter error {response.status_code}: {response.text[:500]}")
        data = response.json()
        try:
            return data["choices"][0]["message"]["content"]
        except Exception as exc:
            raise OpenRouterError(f"Unexpected OpenRouter response: {json.dumps(data)[:500]}") from exc
