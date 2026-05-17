"""Local LLM parser that calls a Savio-hosted vLLM endpoint.

Use this on Savio after the Scrapling collection job has stored raw patent pages.
The parser sends raw text to your Qwen service through an OpenAI-compatible API and expects strict JSON output.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

from .models import PatentRecord, RawDocument, ReactionRecord


class LLMParserError(RuntimeError):
    """Raised when the parser API returns invalid data."""


class QwenLLMParser:
    """Parser client for a vLLM-served Qwen model."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = 180.0,
    ):
        self.base_url = (base_url or os.getenv("PATENT_LLM_BASE_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.model = model or os.getenv("PATENT_LLM_MODEL", "qwen")
        self.api_key = api_key or os.getenv("PATENT_LLM_API_KEY")
        self.timeout = timeout

    def parse(self, document: RawDocument) -> PatentRecord:
        payload = self._build_payload(document)
        response = self._chat_completion(payload)
        data = self._extract_json(response)
        # post-process with chemistry NER/validation
        try:
            from .chem_ner import extract_chem_entities, normalize_smiles

            chem = extract_chem_entities(document.raw_text or "")
            # merge parser output with chemdataextractor hints
            if isinstance(data, dict):
                # augment reactions if empty
                if not data.get("reactions") and chem.get("smiles_normalized"):
                    data.setdefault("reactions", [])
                    for i, s in enumerate(chem.get("smiles_normalized") or [], start=1):
                        data["reactions"].append({
                            "reaction_id": f"{data.get('patent_id','tmp')}:{i}",
                            "reactant_smiles": [],
                            "product_smiles": s,
                        })
        except Exception:
            pass

        # Convert to PatentRecord first
        patent = self._to_patent_record(document, data)

        # Multi-pass verification: validate and canonicalize SMILES, compute confidence
        try:
            from .chem_ner import verify_smiles

            for r in patent.reactions:
                ps = r.product_smiles
                conf = None
                if ps:
                    try:
                        v = verify_smiles(ps)
                        # heuristic scoring
                        score = 0.0
                        if v.get('rdkit_valid'):
                            score += 0.6
                        if v.get('pubchem_match'):
                            score += 0.4
                        # if canonical differs but rdkit valid, give moderate score
                        if v.get('canonical') and v.get('rdkit_valid') and not v.get('pubchem_match'):
                            score = max(score, 0.5)
                        conf = float(score)
                        if v.get('canonical'):
                            r.product_smiles = v.get('canonical')
                    except Exception:
                        conf = 0.0
                else:
                    conf = 0.0
                r.metadata['confidence'] = conf
                if conf < 0.6:
                    r.metadata['needs_review'] = True
        except Exception:
            pass

        return patent

    def _build_payload(self, document: RawDocument) -> list[dict[str, str]]:
        system_prompt = (
            "You are a patent chemistry extraction engine. "
            "Extract exact structured synthesis data from the input. "
            "Return only valid JSON with no markdown and no extra text. "
            "If a field is unknown, use null or an empty list. "
            "Focus on patent identifiers, titles, abstracts, inventors, assignees, "
            "reaction steps, SMILES, reaction SMARTS, yields, temperatures, solvents, "
            "catalysts, time, mechanism notes, and domain tags."
        )
        user_prompt = f"""
Extract structured patent chemistry data from this document.

Required JSON shape:
{{
  "patent_id": "string",
  "title": "string",
  "abstract": "string or null",
  "source_url": "string or null",
  "publication_date": "YYYY-MM-DD or null",
  "inventors": ["string"],
  "assignee": "string or null",
  "domain_tags": ["string"],
  "target_terms": ["string"],
  "reactions": [
    {{
      "reaction_id": "string",
      "reaction_smarts": "string or null",
      "reactant_smiles": ["string"],
      "product_smiles": "string or null",
      "yield_percent": 0.0,
      "temperature_celsius": 0.0,
      "solvent": "string or null",
      "catalyst": "string or null",
      "time_hours": 0.0,
      "mechanism_text": "string or null",
      "notes": "string or null"
    }}
  ]
}}

Document metadata:
- source_url: {document.source_url}
- source_type: {document.source_type}
- title: {document.title}

Raw text:
{document.raw_text or ""}

Raw HTML:
{document.raw_html or ""}
""".strip()
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def _chat_completion(self, messages: list[dict[str, str]]) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()

        data = response.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMParserError("Unexpected response format from LLM server") from exc

    def _extract_json(self, content: str) -> dict[str, Any]:
        content = content.strip()
        if content.startswith("```"):
            content = content.strip("`")
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMParserError(f"Parser returned invalid JSON: {content[:500]}") from exc

    def _to_patent_record(self, document: RawDocument, data: dict[str, Any]) -> PatentRecord:
        patent_id = str(data.get("patent_id") or document.metadata.get("patent_id") or document.title or document.source_url)
        reactions: list[ReactionRecord] = []
        for index, reaction in enumerate(data.get("reactions", []), start=1):
            reactions.append(
                ReactionRecord(
                    reaction_id=str(reaction.get("reaction_id") or f"{patent_id}:{index}"),
                    patent_id=patent_id,
                    reaction_smarts=reaction.get("reaction_smarts"),
                    reactant_smiles=list(reaction.get("reactant_smiles") or []),
                    product_smiles=reaction.get("product_smiles"),
                    yield_percent=reaction.get("yield_percent"),
                    temperature_celsius=reaction.get("temperature_celsius"),
                    solvent=reaction.get("solvent"),
                    catalyst=reaction.get("catalyst"),
                    time_hours=reaction.get("time_hours"),
                    mechanism_text=reaction.get("mechanism_text"),
                    notes=reaction.get("notes"),
                    metadata={k: v for k, v in reaction.items() if k not in {
                        "reaction_id",
                        "reaction_smarts",
                        "reactant_smiles",
                        "product_smiles",
                        "yield_percent",
                        "temperature_celsius",
                        "solvent",
                        "catalyst",
                        "time_hours",
                        "mechanism_text",
                        "notes",
                    }},
                )
            )

        return PatentRecord(
            patent_id=patent_id,
            title=str(data.get("title") or document.title or patent_id),
            abstract=data.get("abstract") or document.raw_text[:1000] if document.raw_text else None,
            source_url=data.get("source_url") or document.source_url,
            publication_date=data.get("publication_date"),
            inventors=list(data.get("inventors") or []),
            assignee=data.get("assignee"),
            domain_tags=list(data.get("domain_tags") or []),
            target_terms=list(data.get("target_terms") or []),
            raw_text=document.raw_text,
            reactions=reactions,
            metadata={
                **document.metadata,
                "parser": "qwen_llm",
                "parser_model": self.model,
            },
        )
