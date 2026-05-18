import json
from urllib.parse import quote

import httpx

pid = "US9108981B2"
u = f"patent/{pid}/en?oq="
url = "https://patents.google.com/xhr/query?url=" + quote(u, safe="")
d = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60).json()


def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if isinstance(v, str) and len(v) > 80 and k in {
                "title",
                "snippet",
                "abstract",
                "description",
                "claims",
                "text",
                "content",
            }:
                print(p, len(v), v[:100].replace("\n", " "))
            elif isinstance(v, (dict, list)) and len(path.split(".")) < 6:
                walk(v, p)
    elif isinstance(obj, list):
        for i, item in enumerate(obj[:3]):
            walk(item, f"{path}[{i}]")


walk(d)
