from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol

from .config_model import ClinicConfigModel
from .config_schema import SchemaValidationError

LOCAL_CONCEPT_PREFIX = "LOCAL:"


class ConceptResolver(Protocol):
    def validate_concept_refs(self, refs: Iterable[str], allow_retired: bool = False) -> list["ValidationIssue"]:
        ...


@dataclass(slots=True)
class ValidationIssue:
    severity: str
    path: str
    message: str


@dataclass(slots=True)
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    def ok(self) -> bool:
        return not self.errors


@dataclass(slots=True)
class LoadValidationResult:
    model: ClinicConfigModel | None
    report: ValidationReport


def _unique_issues(items: Iterable[ValidationIssue]) -> list[ValidationIssue]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[ValidationIssue] = []
    for item in items:
        key = (item.severity, item.path, item.message)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _collect_concept_refs(config: ClinicConfigModel) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []

    for concept in config.local_concepts:
        for index, mapping in enumerate(concept.same_as_mappings):
            if mapping:
                refs.append((f"local_concepts[{concept.id}].same_as_mappings[{index}]", mapping))
        for index, answer in enumerate(concept.answers):
            if answer:
                refs.append((f"local_concepts[{concept.id}].answers[{index}]", answer))
        for index, member in enumerate(concept.members):
            if member:
                refs.append((f"local_concepts[{concept.id}].members[{index}]", member))

    for queue in config.queues:
        for field_name, value in (
            ("service_concept", queue.service_concept),
            ("status_concept_set", queue.status_concept_set),
            ("priority_concept_set", queue.priority_concept_set),
        ):
            if value:
                refs.append((f"queues[{queue.id}].{field_name}", value))

    for form in config.forms:
        for page in form.pages:
            for section in page.sections:
                for question in section.questions:
                    if question.concept:
                        refs.append((f"forms[{form.id}].questions[{question.id}].concept", question.concept))
                    for index, answer in enumerate(question.answers):
                        if answer.concept:
                            refs.append(
                                (
                                    f"forms[{form.id}].questions[{question.id}].answers[{index}]",
                                    answer.concept,
                                )
                            )

    for service in config.billing_model.billable_services:
        for field_name, value in (
            ("concept", service.concept),
            ("service_type", service.service_type),
        ):
            if value:
                refs.append((f"billing_model.billable_services[{service.id}].{field_name}", value))

    if config.billing_model.service_type_concept_set:
        refs.append(("billing_model.service_type_concept_set", config.billing_model.service_type_concept_set))

    for program in config.programs:
        refs.append((f"programs[{program.id}].program_concept", program.program_concept))
        refs.append((f"programs[{program.id}].outcomes_concept", program.outcomes_concept))

    for workflow in config.program_workflows:
        refs.append((f"program_workflows[{workflow.id}].workflow_concept", workflow.workflow_concept))

    for state in config.program_workflow_states:
        refs.append((f"program_workflow_states[{state.id}].state_concept", state.state_concept))

    return refs


def _local_concept_id(ref: str) -> str:
    normalized = str(ref or "").strip()
    if normalized.upper().startswith(LOCAL_CONCEPT_PREFIX):
        return normalized.split(":", 1)[1].strip()
    return ""


def _append_duplicate_issues(
    issues: list[ValidationIssue],
    values: Iterable[str],
    *,
    path: str,
    label: str,
) -> None:
    counts: dict[str, int] = {}
    for value in values:
        normalized = str(value).strip()
        if not normalized:
            continue
        counts[normalized] = counts.get(normalized, 0) + 1
    for value, count in sorted(counts.items()):
        if count > 1:
            issues.append(ValidationIssue("error", path, f"Duplicate {label} '{value}' is not allowed."))


def validate_clinic_config(
    config: ClinicConfigModel,
    *,
    concept_resolver: ConceptResolver | None = None,
) -> ValidationReport:
    issues: list[ValidationIssue] = []
    external_concept_refs: list[str] = []

    if not config.facility_profile.name:
        issues.append(ValidationIssue("error", "facility_profile.name", "Facility name is required."))
    if not config.facility_profile.languages:
        issues.append(ValidationIssue("error", "facility_profile.languages", "At least one language is required."))

    _append_duplicate_issues(
        issues,
        [tag.id for tag in config.location_tags],
        path="location_tags.id",
        label="location tag id",
    )
    _append_duplicate_issues(
        issues,
        [tag.name for tag in config.location_tags],
        path="location_tags.name",
        label="location tag name",
    )
    _append_duplicate_issues(issues, [concept.id for concept in config.local_concepts], path="local_concepts.id", label="local concept id")
    _append_duplicate_issues(
        issues,
        [concept.fully_specified_name for concept in config.local_concepts],
        path="local_concepts.fully_specified_name",
        label="local concept fully specified name",
    )
    _append_duplicate_issues(issues, [location.id for location in config.locations], path="locations.id", label="location id")
    _append_duplicate_issues(issues, [location.name for location in config.locations], path="locations.name", label="location name")
    _append_duplicate_issues(
        issues,
        [encounter.id for encounter in config.encounter_types],
        path="encounter_types.id",
        label="encounter type id",
    )
    _append_duplicate_issues(
        issues,
        [encounter.name for encounter in config.encounter_types],
        path="encounter_types.name",
        label="encounter type name",
    )
    _append_duplicate_issues(issues, [form.id for form in config.forms], path="forms.id", label="form id")
    _append_duplicate_issues(issues, [form.name for form in config.forms], path="forms.name", label="form name")
    _append_duplicate_issues(issues, [queue.id for queue in config.queues], path="queues.id", label="queue id")
    _append_duplicate_issues(issues, [queue.name for queue in config.queues], path="queues.name", label="queue name")
    _append_duplicate_issues(issues, [room.id for room in config.queue_rooms], path="queue_rooms.id", label="queue room id")
    _append_duplicate_issues(
        issues,
        [service.id for service in config.billing_model.billable_services],
        path="billing_model.billable_services.id",
        label="billable service id",
    )
    _append_duplicate_issues(
        issues,
        [service.service_name for service in config.billing_model.billable_services],
        path="billing_model.billable_services.service_name",
        label="billable service name",
    )
    _append_duplicate_issues(
        issues,
        [mode.id for mode in config.billing_model.payment_modes],
        path="billing_model.payment_modes.id",
        label="payment mode id",
    )
    _append_duplicate_issues(
        issues,
        [mode.name for mode in config.billing_model.payment_modes],
        path="billing_model.payment_modes.name",
        label="payment mode name",
    )
    _append_duplicate_issues(
        issues,
        [cash_point.id for cash_point in config.billing_model.cash_points],
        path="billing_model.cash_points.id",
        label="cash point id",
    )
    _append_duplicate_issues(
        issues,
        [cash_point.name for cash_point in config.billing_model.cash_points],
        path="billing_model.cash_points.name",
        label="cash point name",
    )
    _append_duplicate_issues(
        issues,
        [rule.id for rule in config.billing_model.pricing_rules],
        path="billing_model.pricing_rules.id",
        label="pricing rule id",
    )
    _append_duplicate_issues(
        issues,
        [stock_location.id for stock_location in config.stock_pharmacy_model.stock_locations],
        path="stock_pharmacy_model.stock_locations.id",
        label="stock location id",
    )
    _append_duplicate_issues(
        issues,
        [operation.id for operation in config.stock_pharmacy_model.operation_types],
        path="stock_pharmacy_model.operation_types.id",
        label="stock operation type id",
    )
    _append_duplicate_issues(
        issues,
        [operation.name for operation in config.stock_pharmacy_model.operation_types],
        path="stock_pharmacy_model.operation_types.name",
        label="stock operation type name",
    )
    _append_duplicate_issues(
        issues,
        [rule.id for rule in config.stock_pharmacy_model.rules],
        path="stock_pharmacy_model.rules.id",
        label="stock rule id",
    )
    _append_duplicate_issues(issues, [program.id for program in config.programs], path="programs.id", label="program id")
    _append_duplicate_issues(
        issues,
        [workflow.id for workflow in config.program_workflows],
        path="program_workflows.id",
        label="program workflow id",
    )
    _append_duplicate_issues(
        issues,
        [state.id for state in config.program_workflow_states],
        path="program_workflow_states.id",
        label="program workflow state id",
    )
    _append_duplicate_issues(issues, [role.id for role in config.identity_model.roles], path="identity_model.roles.id", label="clinic role id")
    _append_duplicate_issues(issues, [role.name for role in config.identity_model.roles], path="identity_model.roles.name", label="clinic role name")
    _append_duplicate_issues(issues, [user.username for user in config.identity_model.users], path="identity_model.users.username", label="username")

    local_concept_ids = {concept.id for concept in config.local_concepts if concept.id}

    for concept in config.local_concepts:
        if not concept.id:
            issues.append(ValidationIssue("error", "local_concepts", "Local concepts must define an id."))
        if not concept.fully_specified_name:
            issues.append(
                ValidationIssue(
                    "error",
                    f"local_concepts[{concept.id}].fully_specified_name",
                    "Local concepts must define a fully specified name.",
                )
            )
        if not concept.data_class:
            issues.append(
                ValidationIssue(
                    "error",
                    f"local_concepts[{concept.id}].data_class",
                    "Local concepts must define a data class.",
                )
            )
        if not concept.data_type:
            issues.append(
                ValidationIssue(
                    "error",
                    f"local_concepts[{concept.id}].data_type",
                    "Local concepts must define a data type.",
                )
            )

    location_ids = {location.id for location in config.locations if location.id}
    location_names = {location.name for location in config.locations if location.name}
    defined_location_tags = {tag.id for tag in config.location_tags if tag.id} | {
        tag.name for tag in config.location_tags if tag.name
    }

    if not config.locations:
        issues.append(ValidationIssue("error", "locations", "At least one location must be defined."))

    for location in config.locations:
        if not location.id:
            issues.append(ValidationIssue("error", "locations", f"Location '{location.name}' is missing an id."))
        if not location.name:
            issues.append(ValidationIssue("error", f"locations[{location.id}].name", "Location name is required."))
        if location.parent_id and location.parent_id not in location_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    f"locations[{location.id}].parent_id",
                    f"Unknown parent location id '{location.parent_id}'.",
                )
            )
        if defined_location_tags:
            for tag in location.tags:
                if tag not in defined_location_tags:
                    issues.append(
                        ValidationIssue(
                            "error",
                            f"locations[{location.id}].tags",
                            f"Location references unknown location tag '{tag}'.",
                        )
                    )

    if config.registration_model.login_location_id and config.registration_model.login_location_id not in location_ids:
        issues.append(
            ValidationIssue(
                "error",
                "registration_model.login_location_id",
                f"Unknown login location '{config.registration_model.login_location_id}'.",
            )
        )

    encounter_name_set = {encounter.name for encounter in config.encounter_types if encounter.name}
    encounter_id_set = {encounter.id for encounter in config.encounter_types if encounter.id}

    for encounter in config.encounter_types:
        if not encounter.id:
            issues.append(ValidationIssue("error", "encounter_types", f"Encounter '{encounter.name}' is missing an id."))
        if not encounter.name:
            issues.append(ValidationIssue("error", f"encounter_types[{encounter.id}].name", "Encounter type name is required."))

    for form in config.forms:
        if not form.id:
            issues.append(ValidationIssue("error", "forms", f"Form '{form.name}' is missing an id."))
        if not form.encounter:
            issues.append(ValidationIssue("error", f"forms[{form.id}].encounter", "Each form must reference an encounter type."))
        elif form.encounter not in encounter_name_set and form.encounter not in encounter_id_set:
            issues.append(
                ValidationIssue(
                    "error",
                    f"forms[{form.id}].encounter",
                    f"Form references unknown encounter '{form.encounter}'.",
                )
            )
        for page in form.pages:
            for section in page.sections:
                for question in section.questions:
                    if question.question_type == "obs" and not question.concept:
                        issues.append(
                            ValidationIssue(
                                "error",
                                f"forms[{form.id}].questions[{question.id}].concept",
                                "Obs questions require a concept reference.",
                            )
                        )
                    if question.rendering == "select" and not question.answers:
                        issues.append(
                            ValidationIssue(
                                "warning",
                                f"forms[{form.id}].questions[{question.id}]",
                                "Select questions should declare coded answers.",
                            )
                        )

    queue_ids = {queue.id for queue in config.queues if queue.id}
    room_ids = {room.id for room in config.queue_rooms if room.id}
    role_ids = {role.id for role in config.identity_model.roles if role.id}

    for role in config.staffing_model.roles:
        for queue_id in role.queue_ids:
            if queue_id not in queue_ids:
                issues.append(
                    ValidationIssue(
                        "error",
                        f"staffing_model.roles[{role.id}].queue_ids",
                        f"Staff role references unknown queue '{queue_id}'.",
                    )
                )

    for queue in config.queues:
        if not queue.id:
            issues.append(ValidationIssue("error", "queues", f"Queue '{queue.name}' is missing an id."))
        if queue.location_id and queue.location_id not in location_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    f"queues[{queue.id}].location_id",
                    f"Queue references unknown location '{queue.location_id}'.",
                )
            )
        if not queue.service_concept:
            issues.append(ValidationIssue("error", f"queues[{queue.id}].service_concept", "Queue service concept is required."))
        for room_id in queue.room_ids:
            if room_id not in room_ids:
                issues.append(
                    ValidationIssue(
                        "error",
                        f"queues[{queue.id}].room_ids",
                        f"Queue references unknown room '{room_id}'.",
                    )
                )

    for room in config.queue_rooms:
        if room.queue_id not in queue_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    f"queue_rooms[{room.id}].queue_id",
                    f"Queue room references unknown queue '{room.queue_id}'.",
                )
            )
        for role_id in room.provider_role_ids:
            if role_id not in role_ids:
                issues.append(
                    ValidationIssue(
                        "error",
                        f"queue_rooms[{room.id}].provider_role_ids",
                        f"Queue room references unknown identity role '{role_id}'.",
                    )
                )

    for rule in config.routing_rules:
        if rule.from_queue_id not in queue_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    f"routing_rules[{rule.id}].from_queue_id",
                    f"Routing rule references unknown source queue '{rule.from_queue_id}'.",
                )
            )
        if rule.to_queue_id not in queue_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    f"routing_rules[{rule.id}].to_queue_id",
                    f"Routing rule references unknown target queue '{rule.to_queue_id}'.",
                )
            )

    billable_service_ids = {service.id for service in config.billing_model.billable_services if service.id}
    payment_mode_ids = {mode.id for mode in config.billing_model.payment_modes if mode.id}

    for cash_point in config.billing_model.cash_points:
        if cash_point.location_id and cash_point.location_id not in location_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    f"billing_model.cash_points[{cash_point.id}].location_id",
                    f"Cash point references unknown location '{cash_point.location_id}'.",
                )
            )

    for pricing_rule in config.billing_model.pricing_rules:
        if pricing_rule.billable_service_id not in billable_service_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    f"billing_model.pricing_rules[{pricing_rule.id}].billable_service_id",
                    f"Pricing rule references unknown billable service '{pricing_rule.billable_service_id}'.",
                )
            )

    if (
        config.billing_model.waiver_payment_mode_id
        and config.billing_model.waiver_payment_mode_id not in payment_mode_ids
    ):
        issues.append(
            ValidationIssue(
                "error",
                "billing_model.waiver_payment_mode_id",
                f"Unknown waiver payment mode '{config.billing_model.waiver_payment_mode_id}'.",
            )
        )

    for user in config.identity_model.users:
        for role_id in user.role_ids:
            if role_id not in role_ids:
                issues.append(
                    ValidationIssue(
                        "error",
                        f"identity_model.users[{user.username}].role_ids",
                        f"User references unknown role '{role_id}'.",
                    )
                )

    if config.lab_model.department_location_id and config.lab_model.department_location_id not in location_ids:
        issues.append(
            ValidationIssue(
                "error",
                "lab_model.department_location_id",
                f"Lab model references unknown location '{config.lab_model.department_location_id}'.",
            )
        )
    for role_id in config.lab_model.result_reviewer_role_ids:
        if role_id not in role_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    "lab_model.result_reviewer_role_ids",
                    f"Lab model references unknown role '{role_id}'.",
                )
            )

    if config.imaging_model.department_location_id and config.imaging_model.department_location_id not in location_ids:
        issues.append(
            ValidationIssue(
                "error",
                "imaging_model.department_location_id",
                f"Imaging model references unknown location '{config.imaging_model.department_location_id}'.",
            )
        )
    for role_id in config.imaging_model.role_permissions:
        if role_id not in role_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    "imaging_model.role_permissions",
                    f"Imaging model references unknown role '{role_id}'.",
                )
            )
    for role_id in config.imaging_model.authorized_labels:
        if role_id not in role_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    "imaging_model.authorized_labels",
                    f"Imaging model references unknown role '{role_id}'.",
                )
            )

    for requestor_role_id in config.governance.allowed_requestor_role_ids:
        if requestor_role_id not in role_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    "governance.allowed_requestor_role_ids",
                    f"Governance policy references unknown role '{requestor_role_id}'.",
                )
            )

    program_ids = {program.id for program in config.programs if program.id}
    workflow_ids = {workflow.id for workflow in config.program_workflows if workflow.id}

    for workflow in config.program_workflows:
        if workflow.program_id not in program_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    f"program_workflows[{workflow.id}].program_id",
                    f"Workflow references unknown program '{workflow.program_id}'.",
                )
            )

    for state in config.program_workflow_states:
        if state.workflow_id not in workflow_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    f"program_workflow_states[{state.id}].workflow_id",
                    f"Program workflow state references unknown workflow '{state.workflow_id}'.",
                )
            )

    for stock_location in config.stock_pharmacy_model.stock_locations:
        if stock_location.location_id not in location_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    f"stock_pharmacy_model.stock_locations[{stock_location.id}].location_id",
                    f"Stock location references unknown location '{stock_location.location_id}'.",
                )
            )

    for operation in config.stock_pharmacy_model.operation_types:
        for location_id in operation.allowed_location_ids:
            if location_id not in location_ids:
                issues.append(
                    ValidationIssue(
                        "error",
                        f"stock_pharmacy_model.operation_types[{operation.id}]",
                        f"Operation type references unknown location '{location_id}'.",
                    )
                )

    for stock_rule in config.stock_pharmacy_model.rules:
        if stock_rule.location_id not in location_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    f"stock_pharmacy_model.rules[{stock_rule.id}].location_id",
                    f"Stock rule references unknown location '{stock_rule.location_id}'.",
                )
            )

    for queue_id in config.stock_pharmacy_model.dispensing_queue_ids:
        if queue_id not in queue_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    "stock_pharmacy_model.dispensing_queue_ids",
                    f"Dispensing workflow references unknown queue '{queue_id}'.",
                )
            )

    for path, ref in _collect_concept_refs(config):
        local_id = _local_concept_id(ref)
        if local_id:
            if local_id not in local_concept_ids:
                issues.append(
                    ValidationIssue(
                        "error",
                        path,
                        f"Unknown local concept reference '{ref}'.",
                    )
                )
            continue
        external_concept_refs.append(ref)

    if concept_resolver is not None and external_concept_refs:
        issues.extend(concept_resolver.validate_concept_refs(external_concept_refs))

    return ValidationReport(_unique_issues(issues))


def load_and_validate(
    payload: Mapping[str, Any],
    *,
    concept_resolver: ConceptResolver | None = None,
) -> LoadValidationResult:
    try:
        model = ClinicConfigModel.from_dict(payload)
    except SchemaValidationError as exc:
        report = ValidationReport([ValidationIssue("error", "schema", str(exc))])
        return LoadValidationResult(model=None, report=report)

    return LoadValidationResult(
        model=model,
        report=validate_clinic_config(model, concept_resolver=concept_resolver),
    )
