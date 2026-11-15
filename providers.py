import os
from openai import OpenAI

PROVIDERS = {
    "zai": {
        "base_url": "https://api.z.ai/api/paas/v4/",
        "model": "glm-4.5-flash",
        "key_env": "ZAI_API_KEY",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "key_env": "GROQ_API_KEY",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "key_env": "OPENROUTER_API_KEY",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen3:8b",
        "key_env": None,  # локальному серверу ключ не нужен
    },
    "gigachat": {
        # OpenAI-совместимый интерфейс GigaChat
        "base_url": "https://gigachat.devices.sberbank.ru/api/v1",
        "model": "GigaChat-2-Max",
        "key_env": "GIGACHAT_API_KEY",
    },
}


def get_client_and_model():
    """Возвращает (client, model) для провайдера из переменной PROVIDER."""
    name = os.environ.get("PROVIDER", "zai").lower()
    if name not in PROVIDERS:
        raise ValueError(f"Неизвестный PROVIDER={name}. Доступны: {list(PROVIDERS)}")

    cfg = PROVIDERS[name]
    # Ollama ключа не требует, но клиент OpenAI требует непустую строку
    api_key = os.environ.get(cfg["key_env"]) if cfg["key_env"] else "ollama"
    if not api_key:
        raise ValueError(f"Задайте ключ в переменной окружения {cfg['key_env']}")

    client = OpenAI(base_url=cfg["base_url"], api_key=api_key)
    model = os.environ.get("MODEL", cfg["model"])
    return client, model
