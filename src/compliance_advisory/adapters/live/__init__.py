"""``live`` profile adapters: real inference and the real regulatory corpus.

The live profile is the demo-facing stack: generation runs on a local OpenAI-compatible
model server (MLX / Ollama / vLLM / llama.cpp) and retrieval serves only the REAL
regulator instruments ingested by ``pipelines.refresh_job`` — never the fictional
built-in seed. Everything else reuses the SDK-free local adapters.
"""
