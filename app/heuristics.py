import re
from typing import Dict, Any, List, Optional

def compress_prompt(text: str) -> str:
    """
    Compresses the prompt by removing stop words and simplifying language patterns.
    Hypothesis: This aligns better with small models' "native thinking".
    """
    # Common stop words to remove
    stop_words = {
        "a", "an", "the", "and", "or", "but", "if", "then", "else", "when", 
        "at", "by", "for", "in", "of", "on", "to", "with", "is", "are", "was", 
        "were", "be", "been", "being", "have", "has", "had", "do", "does", "did",
        "can", "could", "will", "would", "shall", "should", "may", "might", "must",
        "i", "you", "he", "she", "it", "we", "they", "my", "your", "his", "her", 
        "its", "our", "their", "this", "that", "these", "those"
    }
    
    # Split into words, keep case for now
    words = text.split()
    
    # Filter out stop words (case-insensitive check)
    compressed_words = [w for w in words if w.lower() not in stop_words]
    
    return " ".join(compressed_words)

def parse_model_name(model_name: str) -> Dict[str, Any]:
    """
    Parses a model name to extract family, size, quantization, and version.
    """
    info = {
        "family": "unknown",
        "size": "unknown",
        "quantization": "unknown",
        "variant": "unknown",
        "version": "unknown",
        "warnings": []
    }
    
    lower_name = model_name.lower()
    
    # Family
    if "gpt" in lower_name or "chatgpt" in lower_name:
        info["family"] = "gpt"
    elif "llama" in lower_name: # Covers llama, llama2, llama3, codellama
        info["family"] = "llama"
    elif "mistral" in lower_name:
        info["family"] = "mistral"
    elif "qwen" in lower_name or "qwq" in lower_name:
        info["family"] = "qwen"
    elif "deepseek" in lower_name:
        info["family"] = "deepseek"
    elif "gemma" in lower_name:
        info["family"] = "gemma"
    elif "phi" in lower_name:
        info["family"] = "phi"
    elif "claude" in lower_name:
        info["family"] = "claude"
    elif "falcon" in lower_name:
        info["family"] = "falcon"
    elif "starcoder" in lower_name:
        info["family"] = "starcoder"
        
    # Variant refinement
    if "codellama" in lower_name or "code-llama" in lower_name or "codeqwen" in lower_name:
        info["family"] = "codellama" # Treat as distinct family for settings
        info["variant"] = "code"
    
    # Size
    # Matches: 7b, 1.5b, 70b, etc.
    size_match = re.search(r"(\d+(?:\.\d+)?)\s*(b|k|m|t)", lower_name)
    if size_match:
        num = float(size_match.group(1))
        unit = size_match.group(2)
        if unit == 'k':
            info["size"] = f"{num/1000}B"
        elif unit == 'm':
            info["size"] = f"{num/1000000}B"
        elif unit == 't':
            info["size"] = f"{num*1000}B"
        else:
            info["size"] = f"{num}B"

    # Quantization
    # Matches: q4_0, q4_k_m, q8_0, etc.
    quant_match = re.search(r"(q[2-8](?:_[kmf01]+)*)", lower_name)
    if quant_match:
        info["quantization"] = quant_match.group(1).upper()
        
    # Version
    # Matches: v1, v2.5, etc.
    version_match = re.search(r"(?:^|[^a-z0-9])v?(\d+(?:\.\d+)?)(?:$|[^0-9])", lower_name)
    if version_match:
        info["version"] = version_match.group(1)
        
    # Variant
    if "instruct" in lower_name or "chat" in lower_name:
        info["variant"] = "instruct"
    elif "base" in lower_name or "pretrained" in lower_name:
        info["variant"] = "base"
    elif "code" in lower_name or "coder" in lower_name:
        info["variant"] = "code"
    elif "math" in lower_name:
        info["variant"] = "math"
    elif "vision" in lower_name:
        info["variant"] = "vision"
        
    return info

def suggest_settings(model_name: str) -> Dict[str, Any]:
    """
    Suggests optimal settings (temperature, num_ctx, num_predict) based on model characteristics.
    """
    parsed = parse_model_name(model_name)
    
    settings = {
        "temperature": 0.7,
        "num_ctx": 4096,
        "num_predict": 2048, # maxTokens
        "reasoning": []
    }
    
    # Size-based adjustments
    if parsed["size"] != "unknown":
        try:
            size_val = float(parsed["size"].rstrip('B'))
            if size_val <= 3:
                settings["num_predict"] = 1024
                settings["num_ctx"] = 2048
                settings["temperature"] = 0.8 # Smaller models need more creativity
                settings["reasoning"].append("Small model (<=3B): reduced ctx/tokens, higher temp")
            elif size_val <= 7:
                settings["num_predict"] = 2048
                settings["num_ctx"] = 4096
            elif size_val <= 13:
                settings["num_predict"] = 3072
                settings["num_ctx"] = 8192
            elif size_val <= 30:
                settings["num_predict"] = 4096
                settings["num_ctx"] = 16384
            elif size_val <= 70:
                settings["num_predict"] = 6144
                settings["num_ctx"] = 32768
            else:
                settings["num_predict"] = 8192
                settings["num_ctx"] = 65536
                settings["temperature"] = 0.6 # Large models can be deterministic
                settings["reasoning"].append("Large model (>70B): increased ctx/tokens, lower temp")
        except ValueError:
            pass

    # Quantization-based adjustments
    if parsed["quantization"] != "unknown":
        q = parsed["quantization"]
        if "Q2" in q:
            settings["num_predict"] = int(settings["num_predict"] * 0.5)
            settings["temperature"] = min(settings["temperature"] + 0.3, 1.2)
            settings["reasoning"].append("Low quantization (Q2): reduced tokens, higher temp")
        elif "Q3" in q:
            settings["num_predict"] = int(settings["num_predict"] * 0.7)
            settings["temperature"] = min(settings["temperature"] + 0.2, 1.0)
            settings["reasoning"].append("Low quantization (Q3): reduced tokens, higher temp")
            
    # Family/Variant adjustments
    family = parsed["family"]
    variant = parsed["variant"]
    
    if family in ["codellama", "starcoder", "deepseek"] or variant == "code":
        settings["temperature"] = 0.3
        settings["reasoning"].append("Code model: low temp (0.3)")
    elif variant == "math":
        settings["temperature"] = 0.1
        settings["reasoning"].append("Math model: very low temp (0.1)")
    elif family == "claude":
        settings["temperature"] = 0.6
        settings["num_predict"] = int(settings["num_predict"] * 1.2)
    elif family == "falcon":
        settings["temperature"] = 0.8
    
    # Version adjustments
    if parsed["version"] != "unknown":
        try:
            ver = float(parsed["version"])
            if ver >= 3.0:
                settings["num_predict"] = int(settings["num_predict"] * 1.1)
                settings["reasoning"].append("Modern version (v3+): increased tokens")
        except ValueError:
            pass
            
    return settings
