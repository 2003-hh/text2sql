from collections.abc import Callable
from importlib.resources import files
import json
from copy import deepcopy

from langchain_ollama import ChatOllama
from langsmith import tracing_context

from t2s.planning.models import Decision
from t2s.settings import Settings

Planner = Callable[[list[str]], Decision]
EVIDENCE_KEYS = ("metric", "date", "dimension", "category")


class PlannerError(RuntimeError):
    """模型不可用或未返回可读的候选决策。"""


def catalog_summary(catalog: dict) -> dict:
    return {
        "models": sorted(catalog["models"]),
        "ambiguousTerms": catalog["ambiguousTerms"],
        "dimensionDefinitions": catalog["dimensionDefinitions"],
        "metrics": {
            name: {key: metric[key] for key in ("name", "aliases", "description", "dimensions", "tables")}
            for name, metric in catalog["metrics"].items()
        },
    }


def _output_schema() -> dict:
    schema = Decision.model_json_schema()
    evidence = schema["properties"]["evidence"]
    entry = evidence.pop("additionalProperties")
    evidence["properties"] = {key: deepcopy(entry) for key in EVIDENCE_KEYS}
    evidence["additionalProperties"] = False
    schema["required"] = ["status", "reason", "plan", "tables", "missing_fields", "evidence"]
    plan = schema["$defs"]["PartialPlan"]
    plan["required"] = list(plan["properties"])
    for value in plan["properties"].values():
        value.pop("default", None)
    return schema


def build_planner(settings: Settings, catalog: dict) -> Planner:
    policy = files("t2s").joinpath("resources", "prompts", "policy.txt").read_text(encoding="utf-8")
    system = policy + "\n业务目录：\n" + json.dumps(catalog_summary(catalog), ensure_ascii=False)
    model = ChatOllama(
        base_url=settings.ollama_url, model=settings.ollama_model,
        temperature=0, reasoning=False, num_predict=1000, num_ctx=8192,
        client_kwargs={"timeout": settings.ollama_timeout},
    ).with_structured_output(_output_schema(), method="json_schema")

    def interpret(messages: list[str]) -> Decision:
        payload = json.dumps({"messages_oldest_first": messages}, ensure_ascii=False)
        try:
            # 明确禁用第三方跟踪，后续再接本地公开事件。
            with tracing_context(enabled=False):
                result = model.invoke([("system", system), ("human", payload)])
            return Decision.model_validate(result)
        except Exception as error:
            raise PlannerError("模型暂不可用，或输出格式无法读取，请重试") from error

    return interpret
