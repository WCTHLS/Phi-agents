"""Configuration for open-source AI models, embeddings, and prompt guardrails."""

from typing import Optional
from pydantic import BaseModel, Field


class OpenSourceModelConfig(BaseModel):
    """Configuration for local or on-premises open-source model serving."""
    # LLM Settings (vLLM / Ollama / TGI / llama.cpp OpenAI-compatible server)
    llm_provider: str = Field("vllm", description="Provider type: 'vllm', 'ollama', or 'mock'")
    llm_api_base: str = Field("http://localhost:8000/v1", description="OpenAI-compatible server endpoint")
    llm_model_name: str = Field("Qwen/Qwen2.5-14B-Instruct", description="Target open-weight model name")
    llm_api_key: Optional[str] = Field("EMPTY", description="API key if required by local gateway")
    temperature: float = Field(0.0, description="Temperature 0.0 for deterministic factual adherence")
    max_tokens: int = Field(1024, description="Maximum tokens generated per response")

    # Dense Embedding Settings
    embedding_model_name: str = Field(
        "sentence-transformers/all-MiniLM-L6-v2",
        description="CPU/GPU fast dense embedding model (e.g. all-MiniLM-L6-v2 or BAAI/bge-small-en-v1.5)"
    )

    # Reranker Settings
    reranker_model_name: Optional[str] = Field(
        "BAAI/bge-reranker-base",
        description="Cross-encoder reranker for precision evidence ranking"
    )

    # Compliance Prompt Guardrails
    system_prompt: str = (
        "You are a regulatory and pharmaceutical formulation assistant. "
        "Your task is to provide scientifically rigorous, evidence-grounded answers to formulation scientists.\n"
        "STRICT COMPLIANCE RULES:\n"
        "1. Answer ONLY using the facts present in the provided retrieved evidence passages.\n"
        "2. For every factual claim, provide a verbatim quote and cite the exact source title, section, and page.\n"
        "3. DO NOT extrapolate, assume, or hallucinate pharmaceutical limits, equations, or chemical properties.\n"
        "4. If the retrieved evidence is insufficient, explicitly state what is missing under 'Evidence Gaps'.\n"
        "5. Output must adhere strictly to the requested JSON response format."
    )


model_config = OpenSourceModelConfig()
