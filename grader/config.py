import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(os.environ.get("MINI_HOMEWORK_HOME", os.environ.get("ELEARNING_HOME", Path.cwd()))).resolve()
DATA = ROOT / ".data"


def initialize():
    path = ROOT / ".secrets.json"
    if path.exists():
        raise ValueError(".secrets.json 已存在，初始化不会覆盖你的配置")
    save_json(path, {"canvas_base_url": "https://elearning.fudan.edu.cn", "canvas_token": "", "network_mode": "system", "models": [{"name": "your-model-name", "base_url": "https://your-model-provider.example/v1", "api_key": "", "chat_template_kwargs": {}}]})
    return path


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save_bytes(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".download-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(value)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def settings():
    path = ROOT / ".secrets.json"
    cfg = json.loads(path.read_text()) if path.exists() else {}
    cfg["canvas_base_url"] = os.environ.get("CANVAS_BASE_URL", cfg.get("canvas_base_url", "https://elearning.fudan.edu.cn"))
    cfg["canvas_token"] = os.environ.get("CANVAS_TOKEN", cfg.get("canvas_token", ""))
    return cfg


def models_from_doc(path=None):
    """Read only the documented chat examples; never persist their API keys."""
    cfg = settings()
    configured = cfg.get("models", [])
    if os.environ.get("MODEL_NAME"):
        configured = [{"name": os.environ["MODEL_NAME"], "base_url": os.environ.get("MODEL_BASE_URL", ""), "api_key": os.environ.get("MODEL_API_KEY", ""), "chat_template_kwargs": {}}]
    if configured:
        clean = []
        for model in configured:
            base = model.get("base_url", "").rstrip("/")
            parsed = urlsplit(base)
            if not model.get("name") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("模型配置需要 name 和合法的 base_url，凭证不能放在 URL 中")
            if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")):
                raise ValueError("远程模型必须使用 HTTPS；HTTP 仅支持本机模型")
            clean.append({"name": model["name"], "base_url": base, "api_key": model.get("api_key", ""), "chat_template_kwargs": model.get("chat_template_kwargs", {})})
        return clean
    doc = Path(path or ROOT / "api.md")
    if not doc.exists():
        raise ValueError("请配置 .secrets.json 的 models，或设置 MODEL_NAME、MODEL_BASE_URL、MODEL_API_KEY")
    source = doc.read_text(encoding="utf-8")
    source = re.sub(r"\\([.\-_*{}])", r"\1", source)
    source = source.split("## 3、")[0]
    models = []
    for section in re.split(r"(?m)^### ", source)[1:]:
        if "已下线" in section.splitlines()[0]:
            continue
        key = re.search(r'export\s+INF_API_KEY\s*=\s*"([^"\n]+)"', section)
        url = re.search(r'https://[a-z0-9.-]+\.openapi-sj\.sii\.edu\.cn', section.replace("*", ""))
        model = re.search(r'"model"\s*:\s*"([^"\n]+)"', section)
        if not (key and url and model):
            continue
        name = model.group(1)
        extra = {"thinking": False} if name.startswith("dsv4") else {"enable_thinking": False}
        models.append({"name": name, "base_url": url.group(0), "api_key": key.group(1), "chat_template_kwargs": extra})
    if not models:
        raise ValueError("api.md 中未找到有效模型配置")
    return models


def selected_model(name=None):
    choice = DATA / "model.json"
    if not name and choice.exists():
        name = json.loads(choice.read_text())["name"]
    models = models_from_doc()
    name = name or models[0]["name"]
    for model in models:
        if model["name"] == name:
            return model
    raise ValueError("未找到所选模型，请重新运行 benchmark")
