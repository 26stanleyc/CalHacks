"""Shared settings: mock/stub switches and the model assignment from spec 2.1."""

import os

from dotenv import load_dotenv

load_dotenv()


def flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def use_mock_llm() -> bool:
    """Mock until Bitdeer credits arrive: forced by USE_MOCK_LLM, or implied by a missing key."""
    return flag("USE_MOCK_LLM") or not os.environ.get("BITDEER_API_KEY")


def use_stub_engine() -> bool:
    """Lets the chat agents and dashboard run before the real engine exists."""
    return flag("USE_STUB_ENGINE")


# Role -> Bitdeer model id (spec 2.1). Override any role with MODEL_<ROLE>, e.g. MODEL_INVESTIGATOR.
MODEL_ROLES: dict[str, str] = {
    "critic": "zai-org/GLM-5.3",
    "critic_second": "deepseek-ai/DeepSeek-V4.1-Flash",
    "investigator": "deepseek-ai/DeepSeek-V4.1-Flash",
    "timing": "zai-org/GLM-5.3-Flash",
    "graph": "deepseek-ai/DeepSeek-V4.1-Flash",
    "identity": "zai-org/GLM-5.3",
    "velocity": "deepseek-ai/DeepSeek-V4-Flash",
    "mandate": "zai-org/GLM-5.3-Flash",
    "outsider_a": "deepseek-ai/DeepSeek-V4-Flash",
    "outsider_b": "zai-org/GLM-5.3-Flash",
    "page_shopper": "deepseek-ai/DeepSeek-V4-Flash",
    "threat_extractor": "zai-org/GLM-5.3",
    "chat": "zai-org/GLM-5.3-Flash",
    "held_out": "Qwen/Qwen3.8-27B",  # reserved: used by nothing else
}

GLM_MODELS = ("zai-org/GLM-5.3", "zai-org/GLM-5.3-Flash")
DEEPSEEK_MODELS = ("deepseek-ai/DeepSeek-V4.1-Flash", "deepseek-ai/DeepSeek-V4-Flash")


def model_for(role: str) -> str:
    return os.environ.get(f"MODEL_{role.upper()}", MODEL_ROLES[role])


def family(model: str) -> str:
    return model.split("/")[0]


def attacker_model_for(parent_model: str) -> str:
    """Spawned attackers use the other family than their parent, and the variant the parent doesn't use."""
    pool = DEEPSEEK_MODELS if family(parent_model) == family(GLM_MODELS[0]) else GLM_MODELS
    return next((m for m in pool if m != parent_model), pool[0])
