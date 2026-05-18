from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from .config_model import ClinicConfigModel, QueueDefinition, RoutingRule
from .validation import ValidationIssue


def _normalize(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")


def _guess_queue_kind(queue: QueueDefinition) -> str:
    text = f"{queue.id} {queue.name}".lower()
    if "register" in text or "registration" in text:
        return "registration"
    if "triage" in text:
        return "triage"
    if "consult" in text or "doctor" in text or "clinician" in text:
        return "consultation"
    if "cash" in text or "bill" in text:
        return "cashier"
    if "pharmacy" in text or "dispens" in text:
        return "pharmacy"
    if "lab" in text:
        return "laboratory"
    if "imag" in text or "radio" in text or "xray" in text or "ultra" in text:
        return "imaging"
    return "general"


@dataclass(slots=True)
class WorkflowNode:
    queue_id: str
    name: str
    location_id: str
    queue_kind: str


@dataclass(slots=True)
class WorkflowEdge:
    rule_id: str
    from_queue_id: str
    to_queue_id: str
    mode: str
    condition_summary: str
    requires_payment: bool
    requires_result_review: bool


@dataclass(slots=True)
class RouteSimulation:
    start_queue_id: str
    path_queue_ids: list[str]
    path_queue_names: list[str]
    requires_payment: bool
    requires_result_review: bool
    edge_rule_ids: list[str]


@dataclass(slots=True)
class QueueMetricDefinition:
    metric_id: str
    title: str
    scope: str
    description: str


@dataclass(slots=True)
class OperationalAnalysis:
    start_queue_ids: list[str]
    terminal_queue_ids: list[str]
    isolated_queue_ids: list[str]
    unreachable_queue_ids: list[str]
    cycle_paths: list[list[str]]
    route_simulations: list[RouteSimulation]
    metrics: list[QueueMetricDefinition]
    mermaid: str
    issues: list[ValidationIssue] = field(default_factory=list)
    nodes: list[WorkflowNode] = field(default_factory=list)
    edges: list[WorkflowEdge] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "startQueueIds": self.start_queue_ids,
            "terminalQueueIds": self.terminal_queue_ids,
            "isolatedQueueIds": self.isolated_queue_ids,
            "unreachableQueueIds": self.unreachable_queue_ids,
            "cyclePaths": self.cycle_paths,
            "routeSimulations": [asdict(item) for item in self.route_simulations],
            "metrics": [asdict(item) for item in self.metrics],
            "mermaid": self.mermaid,
            "issues": [asdict(item) for item in self.issues],
            "nodes": [asdict(item) for item in self.nodes],
            "edges": [asdict(item) for item in self.edges],
        }


def _detect_cycles(adjacency: dict[str, list[str]]) -> list[list[str]]:
    visited: set[str] = set()
    stack: list[str] = []
    in_stack: set[str] = set()
    cycles: list[list[str]] = []

    def visit(node: str) -> None:
        visited.add(node)
        stack.append(node)
        in_stack.add(node)
        for neighbor in adjacency.get(node, []):
            if neighbor not in visited:
                visit(neighbor)
            elif neighbor in in_stack:
                start = stack.index(neighbor)
                cycle = stack[start:] + [neighbor]
                if cycle not in cycles:
                    cycles.append(cycle)
        stack.pop()
        in_stack.remove(node)

    for node in adjacency:
        if node not in visited:
            visit(node)
    return cycles


def _enumerate_paths(
    start_queue_id: str,
    adjacency_rules: dict[str, list[RoutingRule]],
    queue_name_by_id: dict[str, str],
    terminal_queue_ids: set[str],
    max_depth: int,
) -> list[RouteSimulation]:
    paths: list[RouteSimulation] = []

    def dfs(
        current_queue_id: str,
        queue_path: list[str],
        rule_path: list[str],
        requires_payment: bool,
        requires_result_review: bool,
    ) -> None:
        outgoing_rules = adjacency_rules.get(current_queue_id, [])
        if (
            current_queue_id in terminal_queue_ids
            or not outgoing_rules
            or len(queue_path) >= max_depth
        ):
            paths.append(
                RouteSimulation(
                    start_queue_id=start_queue_id,
                    path_queue_ids=list(queue_path),
                    path_queue_names=[queue_name_by_id.get(queue_id, queue_id) for queue_id in queue_path],
                    requires_payment=requires_payment,
                    requires_result_review=requires_result_review,
                    edge_rule_ids=list(rule_path),
                )
            )
            return

        progressed = False
        for rule in outgoing_rules:
            if rule.to_queue_id in queue_path:
                continue
            progressed = True
            dfs(
                rule.to_queue_id,
                queue_path + [rule.to_queue_id],
                rule_path + [rule.id],
                requires_payment or rule.requires_payment,
                requires_result_review or rule.requires_result_review,
            )
        if not progressed:
            paths.append(
                RouteSimulation(
                    start_queue_id=start_queue_id,
                    path_queue_ids=list(queue_path),
                    path_queue_names=[queue_name_by_id.get(queue_id, queue_id) for queue_id in queue_path],
                    requires_payment=requires_payment,
                    requires_result_review=requires_result_review,
                    edge_rule_ids=list(rule_path),
                )
            )

    dfs(start_queue_id, [start_queue_id], [], False, False)
    return paths


def _metric_definitions(nodes: list[WorkflowNode], config: ClinicConfigModel) -> list[QueueMetricDefinition]:
    metrics: list[QueueMetricDefinition] = []
    for node in nodes:
        metrics.append(
            QueueMetricDefinition(
                metric_id=f"queue_length.{node.queue_id}",
                title=f"{node.name} queue length",
                scope=node.queue_id,
                description="Current number of waiting patients in this queue.",
            )
        )
        metrics.append(
            QueueMetricDefinition(
                metric_id=f"wait_time.{node.queue_id}",
                title=f"{node.name} median wait time",
                scope=node.queue_id,
                description="Median minutes between queue entry and service start.",
            )
        )
        metrics.append(
            QueueMetricDefinition(
                metric_id=f"throughput.{node.queue_id}",
                title=f"{node.name} daily throughput",
                scope=node.queue_id,
                description="Number of queue entries completed per day.",
            )
        )

    kinds = {node.queue_kind for node in nodes}
    if "registration" in kinds and "consultation" in kinds:
        metrics.append(
            QueueMetricDefinition(
                metric_id="patient_flow.registration_to_consult",
                title="Registration to consultation elapsed time",
                scope="facility",
                description="Minutes from registration queue entry to first consultation service.",
            )
        )
    if any(rule.requires_payment for rule in config.routing_rules):
        metrics.append(
            QueueMetricDefinition(
                metric_id="patient_flow.payment_gate_conversion",
                title="Payment gate conversion",
                scope="facility",
                description="Share of payment-gated visits that complete the cashier step and continue.",
            )
        )
    if config.lab_model.enabled:
        metrics.append(
            QueueMetricDefinition(
                metric_id="lab.turnaround_time",
                title="Laboratory turnaround time",
                scope="laboratory",
                description="Minutes from lab order routing to result review readiness.",
            )
        )
    if config.imaging_model.enabled:
        metrics.append(
            QueueMetricDefinition(
                metric_id="imaging.turnaround_time",
                title="Imaging turnaround time",
                scope="imaging",
                description="Minutes from imaging routing to report or image review readiness.",
            )
        )
    return metrics


def _render_mermaid(nodes: list[WorkflowNode], edges: list[WorkflowEdge]) -> str:
    lines = ["flowchart LR"]
    for node in nodes:
        node_id = _normalize(node.queue_id)
        label = f"{node.name}\\n{node.queue_kind}"
        lines.append(f'  {node_id}["{label}"]')
    for edge in edges:
        from_id = _normalize(edge.from_queue_id)
        to_id = _normalize(edge.to_queue_id)
        flags: list[str] = []
        if edge.mode:
            flags.append(edge.mode)
        if edge.requires_payment:
            flags.append("payment")
        if edge.requires_result_review:
            flags.append("result-review")
        if edge.condition_summary:
            flags.append(edge.condition_summary)
        label = " / ".join(flags)
        if label:
            lines.append(f"  {from_id} -->|{label}| {to_id}")
        else:
            lines.append(f"  {from_id} --> {to_id}")
    return "\n".join(lines) + "\n"


def analyze_operational_policies(config: ClinicConfigModel) -> OperationalAnalysis:
    queue_by_id = {queue.id: queue for queue in config.queues}
    queue_name_by_id = {queue.id: queue.name for queue in config.queues}
    nodes = [
        WorkflowNode(
            queue_id=queue.id,
            name=queue.name,
            location_id=queue.location_id,
            queue_kind=_guess_queue_kind(queue),
        )
        for queue in config.queues
    ]
    edges = [
        WorkflowEdge(
            rule_id=rule.id,
            from_queue_id=rule.from_queue_id,
            to_queue_id=rule.to_queue_id,
            mode=rule.mode,
            condition_summary=rule.condition_summary,
            requires_payment=rule.requires_payment,
            requires_result_review=rule.requires_result_review,
        )
        for rule in config.routing_rules
    ]

    incoming: dict[str, set[str]] = defaultdict(set)
    outgoing: dict[str, set[str]] = defaultdict(set)
    adjacency_rules: dict[str, list[RoutingRule]] = defaultdict(list)
    for rule in config.routing_rules:
        incoming[rule.to_queue_id].add(rule.from_queue_id)
        outgoing[rule.from_queue_id].add(rule.to_queue_id)
        adjacency_rules[rule.from_queue_id].append(rule)

    start_queue_ids = [queue.id for queue in config.queues if not incoming.get(queue.id)]
    terminal_queue_ids = [queue.id for queue in config.queues if not outgoing.get(queue.id)]
    isolated_queue_ids = [
        queue.id
        for queue in config.queues
        if not incoming.get(queue.id) and not outgoing.get(queue.id)
    ]

    adjacency = {queue.id: sorted(outgoing.get(queue.id, set())) for queue in config.queues}
    cycle_paths = _detect_cycles(adjacency)

    reachable: set[str] = set()
    frontier = list(start_queue_ids)
    while frontier:
        current = frontier.pop()
        if current in reachable:
            continue
        reachable.add(current)
        frontier.extend(adjacency.get(current, []))
    unreachable_queue_ids = [queue.id for queue in config.queues if queue.id not in reachable and queue.id not in start_queue_ids]

    route_simulations: list[RouteSimulation] = []
    for start_queue_id in start_queue_ids:
        route_simulations.extend(
            _enumerate_paths(
                start_queue_id,
                adjacency_rules,
                queue_name_by_id,
                set(terminal_queue_ids),
                max_depth=max(len(queue_by_id) + 1, 2),
            )
        )

    issues: list[ValidationIssue] = []
    if config.queues and not start_queue_ids:
        issues.append(
            ValidationIssue(
                "error",
                "routingRules",
                "No starting queue could be identified from the routing graph.",
            )
        )
    for queue_id in isolated_queue_ids:
        issues.append(
            ValidationIssue(
                "warning",
                f"queues[{queue_id}]",
                "Queue is isolated and will never participate in patient routing.",
            )
        )
    for queue_id in unreachable_queue_ids:
        issues.append(
            ValidationIssue(
                "warning",
                f"queues[{queue_id}]",
                "Queue is not reachable from any starting queue.",
            )
        )
    for cycle in cycle_paths:
        issues.append(
            ValidationIssue(
                "warning",
                "routingRules",
                f"Routing graph contains a cycle: {' -> '.join(cycle)}",
            )
        )

    if any(rule.requires_payment for rule in config.routing_rules):
        cashier_queues = [node for node in nodes if node.queue_kind == "cashier"]
        if not cashier_queues:
            issues.append(
                ValidationIssue(
                    "error",
                    "routingRules",
                    "At least one payment-gated routing rule exists but no cashier queue is defined.",
                )
            )
        if not config.billing_model.payment_modes:
            issues.append(
                ValidationIssue(
                    "error",
                    "billingModel.paymentModes",
                    "Payment-gated routing requires at least one payment mode.",
                )
            )
        if not config.billing_model.cash_points:
            issues.append(
                ValidationIssue(
                    "error",
                    "billingModel.cashPoints",
                    "Payment-gated routing requires at least one cash point.",
                )
            )

    if config.stock_pharmacy_model.dispensing_queue_ids and not config.stock_pharmacy_model.stock_locations:
        issues.append(
            ValidationIssue(
                "error",
                "stockPharmacyModel.stockLocations",
                "Dispensing queues are configured but no stock locations are defined.",
            )
        )
    for queue_id in config.stock_pharmacy_model.dispensing_queue_ids:
        if queue_id not in queue_by_id:
            issues.append(
                ValidationIssue(
                    "error",
                    "stockPharmacyModel.dispensingQueueIds",
                    f"Unknown dispensing queue '{queue_id}'.",
                )
            )

    if config.lab_model.enabled:
        lab_nodes = [node for node in nodes if node.queue_kind == "laboratory"]
        if not lab_nodes:
            issues.append(
                ValidationIssue(
                    "warning",
                    "labModel",
                    "Laboratory is enabled but no laboratory queue is defined in routing.",
                )
            )

    if config.imaging_model.enabled:
        imaging_nodes = [node for node in nodes if node.queue_kind == "imaging"]
        if not imaging_nodes:
            issues.append(
                ValidationIssue(
                    "warning",
                    "imagingModel",
                    "Imaging is enabled but no imaging queue is defined in routing.",
                )
            )

    metrics = _metric_definitions(nodes, config)
    mermaid = _render_mermaid(nodes, edges)
    return OperationalAnalysis(
        start_queue_ids=start_queue_ids,
        terminal_queue_ids=terminal_queue_ids,
        isolated_queue_ids=isolated_queue_ids,
        unreachable_queue_ids=unreachable_queue_ids,
        cycle_paths=cycle_paths,
        route_simulations=route_simulations,
        metrics=metrics,
        mermaid=mermaid,
        issues=issues,
        nodes=nodes,
        edges=edges,
    )
