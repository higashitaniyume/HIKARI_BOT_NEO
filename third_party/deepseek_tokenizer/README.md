# DeepSeek V3 Tokenizer

Vendored HuggingFace-format DeepSeek-V3 tokenizer, used by the AI Agent
quota module (`plugins/aiagent/quota.py`) to estimate token counts locally.

- `tokenizer.json` — ByteLevel BPE, vocab 128000 + 818 added tokens (~7.8 MB)
- `tokenizer_config.json` — config + chat template (informational; counting
  uses the raw tokenizer, not the chat template)

Origin: official DeepSeek-V3 tokenizer distribution (downloaded as
`deepseek_v3_tokenizer.zip`, files dated 2025-01-20). Do not modify directly;
replace wholesale with the upstream files if a newer version is released.

Parsed at runtime with the `tokenizers` PyPI package (HuggingFace). If the
package or files are unavailable, `quota.py` falls back to a character-based
estimate so quota enforcement never breaks chat.
