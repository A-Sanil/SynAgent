import os
import json
from typing import Any
import httpx


class GeminiClient:
    """Small wrapper for calling a Gemini-compatible generative endpoint.

    Configure via environment variables:
    - GEMINI_API_KEY: API key string
    - GEMINI_API_URL: full URL to the generative API endpoint
    """

    def __init__(self, api_key: str | None = None, api_url: str | None = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.api_url = api_url or os.environ.get("GEMINI_API_URL")
        self.model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is missing. Set it in your environment.")
        # If GEMINI_API_URL is omitted, fall back to the Google Generative Language API.

    def generate(self, prompt: str, **kwargs: Any) -> dict:
        """Send a simple generation request and return parsed JSON response.

        This wrapper is intentionally minimal — adapt the request body to the
        specific Gemini/Generative Models API you are using.
        """
        if self.api_url:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            body = {"input": prompt}
            body.update(kwargs)

            with httpx.Client(timeout=60) as client:
                r = client.post(self.api_url, headers=headers, json=body)
                r.raise_for_status()
        else:
            # Google AI Studio / Generative Language default request shape.
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.model}:generateContent?key={self.api_key}"
            )
            body = {
                "contents": [
                    {
                        "parts": [{"text": prompt}],
                    }
                ]
            }
            body.update(kwargs)

            with httpx.Client(timeout=60) as client:
                r = client.post(url, json=body)
                r.raise_for_status()

        try:
            return r.json()
        except json.JSONDecodeError:
            return {"text": r.text}
