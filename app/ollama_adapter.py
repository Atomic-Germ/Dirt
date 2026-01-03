import json
import subprocess
import logging
import sys

logger = logging.getLogger(__name__)


class OllamaAdapter:
    """Adapter to call Ollama models via Python package or CLI fallback.

    This is a best-effort adapter: it prefers an importable `ollama` Python
    package (if available) and falls back to calling the `ollama` CLI.
    The adapter turns a tool name + arguments into a prompt and returns
    parsed JSON when possible.
    """

    def __init__(self, model: str = "erukude/multiagent-orchestrator:3b"):
        self.model = model
        self._ollama = None
        try:
            import ollama as _ollama  # type: ignore
            self._ollama = _ollama
            logger.info("Using python 'ollama' package for model calls")
        except Exception:
            logger.debug("python 'ollama' package not available, will fallback to CLI")

    def _build_prompt(self, tool_name: str, arguments: dict) -> str:
        return (
            f"Invoke tool: {tool_name}\n"
            f"Arguments: {json.dumps(arguments, ensure_ascii=False)}\n"
            "Return a JSON-encodable result only (no extra commentary).\n"
        )

    def call(self, tool_name: str, arguments: dict) -> dict:
        prompt = self._build_prompt(tool_name, arguments)

        # Try python package first (best-effort with several possible APIs)
        if self._ollama is not None:
            try:
                # Common possible entrypoints: generate, chat, or a client class
                if hasattr(self._ollama, "generate"):
                    res = self._ollama.generate(self.model, prompt=prompt)
                    text = getattr(res, "text", str(res))
                elif hasattr(self._ollama, "chat"):
                    res = self._ollama.chat(self.model, prompt)
                    text = getattr(res, "text", str(res))
                else:
                    Ollama = getattr(self._ollama, "Ollama", None)
                    if Ollama is not None:
                        client = Ollama()
                        res = client.generate(self.model, prompt)
                        text = res.get("text") if isinstance(res, dict) else str(res)
                    else:
                        raise RuntimeError("Unknown ollama python API shape")

                try:
                    return json.loads(text)
                except Exception:
                    return {"output": text}
            except Exception:
                logger.exception("Python ollama package call failed; falling back to CLI")

        # CLI fallback: best-effort using `ollama generate <model>` reading prompt from stdin
        try:
            proc = subprocess.run(
                ["ollama", "generate", self.model],
                input=prompt,
                capture_output=True,
                text=True,
                check=False,
            )
            out = (proc.stdout or "").strip()
            try:
                return json.loads(out)
            except Exception:
                return {"output": out, "returncode": proc.returncode}
        except FileNotFoundError:
            logger.error("ollama CLI not found on PATH")
            return {"error": "ollama_cli_not_found"}
        except Exception as e:
            logger.exception("Unexpected error calling ollama CLI")
            return {"error": str(e)}


def example_usage():
    a = OllamaAdapter()
    return a.call("example_tool", {"hello": "world"})


if __name__ == "__main__":
    print(json.dumps(example_usage(), indent=2))
