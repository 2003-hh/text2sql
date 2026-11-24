"""用两个独立的 Python 进程验证真实 Redis checkpoint。"""

import argparse
from typing import TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from t2s.settings import Settings
from t2s.workflow.checkpoint import open_checkpointer


class SmokeState(TypedDict):
    stage: str


def wait_for_confirmation(state: SmokeState) -> SmokeState:
    return {"stage": interrupt({"stage": state["stage"]})}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("pause", "resume"))
    parser.add_argument("id", type=UUID)
    args = parser.parse_args()
    config = {"configurable": {"thread_id": f"t2s-smoke-v1:{args.id}"}}

    definition = StateGraph(SmokeState).add_node("confirm", wait_for_confirmation)
    definition.add_edge(START, "confirm").add_edge("confirm", END)
    with open_checkpointer(Settings()) as saver:
        graph = definition.compile(checkpointer=saver)
        snapshot = graph.get_state(config)
        if args.action == "pause":
            if snapshot.values:
                raise RuntimeError("Use a fresh smoke-test ID")
            graph.invoke({"stage": "waiting"}, config)
            snapshot = graph.get_state(config)
            if snapshot.values.get("stage") != "waiting" or snapshot.next != ("confirm",):
                raise RuntimeError("Checkpoint did not save the waiting state")
            print("Redis checkpoint persisted: waiting")
            return

        if snapshot.values.get("stage") != "waiting" or snapshot.next != ("confirm",):
            raise RuntimeError("Expected a waiting checkpoint")
        graph.invoke(Command(resume="confirmed"), config)
        snapshot = graph.get_state(config)
        if snapshot.values.get("stage") != "confirmed" or snapshot.next:
            raise RuntimeError("Workflow did not finish after resume")
        saver.delete_thread(config["configurable"]["thread_id"])
        print("Redis checkpoint restored in a new process and resumed: confirmed")


if __name__ == "__main__":
    main()
