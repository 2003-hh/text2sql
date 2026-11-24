import json
from importlib.resources import files


def load_catalog() -> dict:
    """检查打包后的课程目录能加载，且含有工作流所需的结构。"""
    resource = files("t2s").joinpath("resources", "catalog", "semantic-model.json")
    catalog = json.loads(resource.read_text(encoding="utf-8"))
    shapes = {
        "models": dict,
        "relationships": list,
        "metrics": dict,
        "ambiguousTerms": list,
        "dimensionDefinitions": dict,
    }
    if not isinstance(catalog, dict) or any(
        not isinstance(catalog.get(key), expected) for key, expected in shapes.items()
    ):
        raise ValueError("Invalid semantic catalog")
    if not catalog["metrics"] or not catalog["models"]:
        raise ValueError("Empty semantic catalog")

    return catalog


def check_catalog() -> None:
    load_catalog()
