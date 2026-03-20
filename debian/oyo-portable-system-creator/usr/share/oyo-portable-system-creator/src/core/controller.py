from __future__ import annotations

from src.core.state import ExecutionState
from src.core.workflow import Workflow


class Controller:
    def __init__(self, workflow: Workflow) -> None:
        self.workflow = workflow

    def validate(self, state: ExecutionState) -> ExecutionState:
        self.workflow.precheck(state)
        return state

    def run(self, state: ExecutionState) -> ExecutionState:
        if state.mode == "create":
            return self.workflow.run_create(state)
        return self.workflow.run_backup(state)
