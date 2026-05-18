from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config_model import ClinicConfigModel


DEFAULT_PACK_DIR = Path("examples/emr-os/department-packs")
RESERVED_SYMBOL_PREFIXES = {"CIEL", "LOCAL", "HTTP", "HTTPS"}


@dataclass(slots=True)
class DepartmentPackInfo:
    pack_id: str
    name: str
    description: str
    path: str


@dataclass(slots=True)
class CompositionResult:
    plan_id: str
    included_packs: list[str]
    namespaces: dict[str, str]
    warnings: list[str]
    config: ClinicConfigModel

    def to_dict(self) -> dict[str, Any]:
        return {
            "planId": self.plan_id,
            "includedPacks": self.included_packs,
            "namespaces": self.namespaces,
            "warnings": self.warnings,
            "config": self.config.to_dict(),
        }


def _deepcopy(value: Any) -> Any:
    return deepcopy(value)


def _compose_id(namespace: str, raw_id: str) -> str:
    return f"{namespace}__{raw_id}"


def _resolve_symbolic_ref(raw_value: str, namespaces: dict[str, str], *, strict: bool = False) -> str:
    if not isinstance(raw_value, str):
        return raw_value
    if ":" not in raw_value:
        return raw_value
    namespace, local_id = raw_value.split(":", 1)
    if namespace.upper() in RESERVED_SYMBOL_PREFIXES:
        return raw_value
    if namespace in namespaces:
        return _compose_id(namespaces[namespace], local_id)
    if strict:
        raise ValueError(f"Unknown composition namespace '{namespace}' in symbolic reference '{raw_value}'.")
    return raw_value


def _merge_lists(base: dict[str, Any], addition: dict[str, Any], key: str) -> None:
    if key not in addition:
        return
    base.setdefault(key, [])
    base[key].extend(_deepcopy(addition.get(key, [])))


def _merge_nested_lists(base: dict[str, Any], addition: dict[str, Any], parent_key: str, child_key: str) -> None:
    if parent_key not in addition:
        return
    base.setdefault(parent_key, {})
    base[parent_key].setdefault(child_key, [])
    base[parent_key][child_key].extend(_deepcopy(addition[parent_key].get(child_key, [])))


def _merge_nested_dict(base: dict[str, Any], addition: dict[str, Any], parent_key: str, child_key: str) -> None:
    if parent_key not in addition or child_key not in addition[parent_key]:
        return
    base.setdefault(parent_key, {})
    base[parent_key].setdefault(child_key, {})
    base[parent_key][child_key].update(_deepcopy(addition[parent_key].get(child_key, {})))


def _merge_scalar_if_present(base: dict[str, Any], addition: dict[str, Any], key: str) -> None:
    if key in addition and addition[key] not in (None, "", [], {}):
        base[key] = _deepcopy(addition[key])


def _merge_nested_scalars(base: dict[str, Any], addition: dict[str, Any], parent_key: str, keys: list[str]) -> None:
    if parent_key not in addition:
        return
    base.setdefault(parent_key, {})
    for key in keys:
        if key in addition[parent_key] and addition[parent_key][key] not in (None, "", [], {}):
            base[parent_key][key] = _deepcopy(addition[parent_key][key])


def _merge_partial_config(base: dict[str, Any], addition: dict[str, Any]) -> None:
    for key in [
        "localConcepts",
        "locationTags",
        "locations",
        "encounterTypes",
        "forms",
        "queues",
        "queueRooms",
        "routingRules",
        "programs",
        "programWorkflows",
        "programWorkflowStates",
    ]:
        _merge_lists(base, addition, key)

    _merge_nested_lists(base, addition, "staffingModel", "roles")

    for key in ["serviceTypeConceptSet", "waiverPaymentModeId"]:
        _merge_nested_scalars(base, addition, "billingModel", [key])
    for key in ["billableServices", "paymentModes", "cashPoints", "pricingRules"]:
        _merge_nested_lists(base, addition, "billingModel", key)
    _merge_nested_dict(base, addition, "billingModel", "globalProperties")

    for key in ["stockLocations", "operationTypes", "rules", "dispensingQueueIds"]:
        _merge_nested_lists(base, addition, "stockPharmacyModel", key)

    for key in [
        "enabled",
        "departmentLocationId",
        "integratedOrdering",
        "specimenHandoffMode",
        "resultDeliveryMode",
    ]:
        _merge_nested_scalars(base, addition, "labModel", [key])
    _merge_nested_lists(base, addition, "labModel", "resultReviewerRoleIds")

    for key in [
        "enabled",
        "departmentLocationId",
        "defaultViewer",
        "studyShareEnabled",
    ]:
        _merge_nested_scalars(base, addition, "imagingModel", [key])
    _merge_nested_dict(base, addition, "imagingModel", "rolePermissions")
    _merge_nested_dict(base, addition, "imagingModel", "authorizedLabels")

    _merge_nested_lists(base, addition, "identityModel", "roles")
    _merge_nested_lists(base, addition, "identityModel", "users")

    if "governance" in addition:
        base.setdefault("governance", {})
        for key, value in addition["governance"].items():
            if key == "allowedRequestorRoleIds":
                base["governance"].setdefault(key, [])
                base["governance"][key].extend(_deepcopy(value))
            elif value not in (None, "", [], {}):
                base["governance"][key] = _deepcopy(value)


def _build_id_maps(pack_config: dict[str, Any], namespace: str) -> dict[str, dict[str, str]]:
    maps = {
        "local_concept": {
            item["id"]: _compose_id(namespace, item["id"]) for item in pack_config.get("localConcepts", []) if item.get("id")
        },
        "location": {item["id"]: _compose_id(namespace, item["id"]) for item in pack_config.get("locations", []) if item.get("id")},
        "staff_role": {
            item["id"]: _compose_id(namespace, item["id"])
            for item in pack_config.get("staffingModel", {}).get("roles", [])
            if item.get("id")
        },
        "encounter": {
            item["id"]: _compose_id(namespace, item["id"])
            for item in pack_config.get("encounterTypes", [])
            if item.get("id")
        },
        "form": {item["id"]: _compose_id(namespace, item["id"]) for item in pack_config.get("forms", []) if item.get("id")},
        "queue": {item["id"]: _compose_id(namespace, item["id"]) for item in pack_config.get("queues", []) if item.get("id")},
        "queue_room": {
            item["id"]: _compose_id(namespace, item["id"])
            for item in pack_config.get("queueRooms", [])
            if item.get("id")
        },
        "billable_service": {
            item["id"]: _compose_id(namespace, item["id"])
            for item in pack_config.get("billingModel", {}).get("billableServices", [])
            if item.get("id")
        },
        "payment_mode": {
            item["id"]: _compose_id(namespace, item["id"])
            for item in pack_config.get("billingModel", {}).get("paymentModes", [])
            if item.get("id")
        },
        "cash_point": {
            item["id"]: _compose_id(namespace, item["id"])
            for item in pack_config.get("billingModel", {}).get("cashPoints", [])
            if item.get("id")
        },
        "stock_location": {
            item["id"]: _compose_id(namespace, item["id"])
            for item in pack_config.get("stockPharmacyModel", {}).get("stockLocations", [])
            if item.get("id")
        },
        "operation_type": {
            item["id"]: _compose_id(namespace, item["id"])
            for item in pack_config.get("stockPharmacyModel", {}).get("operationTypes", [])
            if item.get("id")
        },
        "stock_rule": {
            item["id"]: _compose_id(namespace, item["id"])
            for item in pack_config.get("stockPharmacyModel", {}).get("rules", [])
            if item.get("id")
        },
        "program": {
            item["id"]: _compose_id(namespace, item["id"])
            for item in pack_config.get("programs", [])
            if item.get("id")
        },
        "program_workflow": {
            item["id"]: _compose_id(namespace, item["id"])
            for item in pack_config.get("programWorkflows", [])
            if item.get("id")
        },
        "identity_role": {
            item["id"]: _compose_id(namespace, item["id"])
            for item in pack_config.get("identityModel", {}).get("roles", [])
            if item.get("id")
        },
    }
    return maps


def _rewrite_local_concept_refs(value: Any, local_concept_map: dict[str, str]) -> Any:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.upper().startswith("LOCAL:"):
            local_id = normalized.split(":", 1)[1].strip()
            return f"LOCAL:{local_concept_map.get(local_id, local_id)}"
        return value
    if isinstance(value, list):
        return [_rewrite_local_concept_refs(item, local_concept_map) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite_local_concept_refs(item, local_concept_map) for key, item in value.items()}
    return value


def _prefix_aux_form_ids(form: dict[str, Any], namespace: str) -> None:
    if form.get("id"):
        form["id"] = _compose_id(namespace, form["id"])
    for page in form.get("pages", []):
        if page.get("id"):
            page["id"] = _compose_id(namespace, page["id"])
        for section in page.get("sections", []):
            if section.get("id"):
                section["id"] = _compose_id(namespace, section["id"])
            for question in section.get("questions", []):
                if question.get("id"):
                    question["id"] = _compose_id(namespace, question["id"])


def _transform_pack_config(
    pack_config: dict[str, Any],
    *,
    namespace: str,
    mount_location_id: str | None = None,
) -> dict[str, Any]:
    transformed = _deepcopy(pack_config)
    maps = _build_id_maps(transformed, namespace)
    role_map = dict(maps["staff_role"])
    role_map.update(maps["identity_role"])

    for concept in transformed.get("localConcepts", []):
        original_id = concept.get("id")
        if original_id:
            concept["id"] = maps["local_concept"][original_id]

    transformed = _rewrite_local_concept_refs(transformed, maps["local_concept"])

    for item in transformed.get("locationTags", []):
        if item.get("id"):
            item["id"] = _compose_id(namespace, item["id"])

    for location in transformed.get("locations", []):
        original_id = location.get("id")
        if original_id:
            location["id"] = maps["location"][original_id]
        parent_id = location.get("parentId", "")
        if parent_id:
            location["parentId"] = maps["location"].get(parent_id, parent_id)
        elif mount_location_id:
            location["parentId"] = mount_location_id

    for role in transformed.get("staffingModel", {}).get("roles", []):
        original_id = role.get("id")
        if original_id:
            role["id"] = maps["staff_role"][original_id]
        role["queueIds"] = [maps["queue"].get(value, value) for value in role.get("queueIds", [])]

    for encounter in transformed.get("encounterTypes", []):
        original_id = encounter.get("id")
        if original_id:
            encounter["id"] = maps["encounter"][original_id]

    for form in transformed.get("forms", []):
        encounter_ref = form.get("encounter")
        if encounter_ref:
            form["encounter"] = maps["encounter"].get(encounter_ref, encounter_ref)
        form["referencedForms"] = [maps["form"].get(value, value) for value in form.get("referencedForms", [])]
        _prefix_aux_form_ids(form, namespace)

    for queue in transformed.get("queues", []):
        original_id = queue.get("id")
        if original_id:
            queue["id"] = maps["queue"][original_id]
        if queue.get("locationId"):
            queue["locationId"] = maps["location"].get(queue["locationId"], queue["locationId"])
        queue["roomIds"] = [maps["queue_room"].get(value, value) for value in queue.get("roomIds", [])]

    for room in transformed.get("queueRooms", []):
        original_id = room.get("id")
        if original_id:
            room["id"] = maps["queue_room"][original_id]
        if room.get("queueId"):
            room["queueId"] = maps["queue"].get(room["queueId"], room["queueId"])
        room["providerRoleIds"] = [role_map.get(value, value) for value in room.get("providerRoleIds", [])]

    for rule in transformed.get("routingRules", []):
        if rule.get("id"):
            rule["id"] = _compose_id(namespace, rule["id"])
        if rule.get("fromQueueId"):
            rule["fromQueueId"] = maps["queue"].get(rule["fromQueueId"], rule["fromQueueId"])
        if rule.get("toQueueId"):
            rule["toQueueId"] = maps["queue"].get(rule["toQueueId"], rule["toQueueId"])

    billing_model = transformed.get("billingModel", {})
    for service in billing_model.get("billableServices", []):
        if service.get("id"):
            service["id"] = maps["billable_service"][service["id"]]
    for payment_mode in billing_model.get("paymentModes", []):
        if payment_mode.get("id"):
            payment_mode["id"] = maps["payment_mode"][payment_mode["id"]]
    for cash_point in billing_model.get("cashPoints", []):
        if cash_point.get("id"):
            cash_point["id"] = maps["cash_point"][cash_point["id"]]
        if cash_point.get("locationId"):
            cash_point["locationId"] = maps["location"].get(cash_point["locationId"], cash_point["locationId"])
    for rule in billing_model.get("pricingRules", []):
        if rule.get("id"):
            rule["id"] = _compose_id(namespace, rule["id"])
        if rule.get("billableServiceId"):
            rule["billableServiceId"] = maps["billable_service"].get(rule["billableServiceId"], rule["billableServiceId"])
    if billing_model.get("waiverPaymentModeId"):
        billing_model["waiverPaymentModeId"] = maps["payment_mode"].get(
            billing_model["waiverPaymentModeId"],
            billing_model["waiverPaymentModeId"],
        )

    stock_model = transformed.get("stockPharmacyModel", {})
    for stock_location in stock_model.get("stockLocations", []):
        if stock_location.get("id"):
            stock_location["id"] = maps["stock_location"][stock_location["id"]]
        if stock_location.get("locationId"):
            stock_location["locationId"] = maps["location"].get(stock_location["locationId"], stock_location["locationId"])
    for operation_type in stock_model.get("operationTypes", []):
        if operation_type.get("id"):
            operation_type["id"] = maps["operation_type"][operation_type["id"]]
        operation_type["allowedLocationIds"] = [
            maps["location"].get(value, value) for value in operation_type.get("allowedLocationIds", [])
        ]
    for rule in stock_model.get("rules", []):
        if rule.get("id"):
            rule["id"] = maps["stock_rule"][rule["id"]]
        if rule.get("locationId"):
            rule["locationId"] = maps["location"].get(rule["locationId"], rule["locationId"])
    stock_model["dispensingQueueIds"] = [maps["queue"].get(value, value) for value in stock_model.get("dispensingQueueIds", [])]

    for program in transformed.get("programs", []):
        if program.get("id"):
            program["id"] = maps["program"][program["id"]]
    for workflow in transformed.get("programWorkflows", []):
        if workflow.get("id"):
            workflow["id"] = maps["program_workflow"][workflow["id"]]
        if workflow.get("programId"):
            workflow["programId"] = maps["program"].get(workflow["programId"], workflow["programId"])
    for state in transformed.get("programWorkflowStates", []):
        if state.get("id"):
            state["id"] = _compose_id(namespace, state["id"])
        if state.get("workflowId"):
            state["workflowId"] = maps["program_workflow"].get(state["workflowId"], state["workflowId"])

    lab_model = transformed.get("labModel", {})
    if lab_model.get("departmentLocationId"):
        lab_model["departmentLocationId"] = maps["location"].get(
            lab_model["departmentLocationId"],
            lab_model["departmentLocationId"],
        )
    lab_model["resultReviewerRoleIds"] = [role_map.get(value, value) for value in lab_model.get("resultReviewerRoleIds", [])]

    imaging_model = transformed.get("imagingModel", {})
    if imaging_model.get("departmentLocationId"):
        imaging_model["departmentLocationId"] = maps["location"].get(
            imaging_model["departmentLocationId"],
            imaging_model["departmentLocationId"],
        )
    if imaging_model.get("rolePermissions"):
        imaging_model["rolePermissions"] = {
            role_map.get(key, key): value for key, value in imaging_model["rolePermissions"].items()
        }
    if imaging_model.get("authorizedLabels"):
        imaging_model["authorizedLabels"] = {
            role_map.get(key, key): value for key, value in imaging_model["authorizedLabels"].items()
        }

    identity_model = transformed.get("identityModel", {})
    for role in identity_model.get("roles", []):
        if role.get("id"):
            role["id"] = maps["identity_role"][role["id"]]
    for user in identity_model.get("users", []):
        user["roleIds"] = [role_map.get(value, value) for value in user.get("roleIds", [])]

    governance = transformed.get("governance", {})
    governance["allowedRequestorRoleIds"] = [
        role_map.get(value, value) for value in governance.get("allowedRequestorRoleIds", [])
    ]
    return transformed


def _resolve_plan_symbols(aggregate: dict[str, Any], namespaces: dict[str, str]) -> None:
    registration_model = aggregate.get("registrationModel", {})
    if registration_model.get("loginLocationId"):
        registration_model["loginLocationId"] = _resolve_symbolic_ref(
            registration_model["loginLocationId"],
            namespaces,
            strict=True,
        )

    billing_model = aggregate.get("billingModel", {})
    if billing_model.get("waiverPaymentModeId"):
        billing_model["waiverPaymentModeId"] = _resolve_symbolic_ref(
            billing_model["waiverPaymentModeId"],
            namespaces,
            strict=True,
        )
    for cash_point in billing_model.get("cashPoints", []):
        if cash_point.get("locationId"):
            cash_point["locationId"] = _resolve_symbolic_ref(cash_point["locationId"], namespaces, strict=True)
    for rule in billing_model.get("pricingRules", []):
        if rule.get("billableServiceId"):
            rule["billableServiceId"] = _resolve_symbolic_ref(rule["billableServiceId"], namespaces, strict=True)

    stock_model = aggregate.get("stockPharmacyModel", {})
    for stock_location in stock_model.get("stockLocations", []):
        if stock_location.get("locationId"):
            stock_location["locationId"] = _resolve_symbolic_ref(stock_location["locationId"], namespaces, strict=True)
    for operation_type in stock_model.get("operationTypes", []):
        operation_type["allowedLocationIds"] = [
            _resolve_symbolic_ref(value, namespaces, strict=True)
            for value in operation_type.get("allowedLocationIds", [])
        ]
    for rule in stock_model.get("rules", []):
        if rule.get("locationId"):
            rule["locationId"] = _resolve_symbolic_ref(rule["locationId"], namespaces, strict=True)
    stock_model["dispensingQueueIds"] = [
        _resolve_symbolic_ref(value, namespaces, strict=True) for value in stock_model.get("dispensingQueueIds", [])
    ]

    lab_model = aggregate.get("labModel", {})
    if lab_model.get("departmentLocationId"):
        lab_model["departmentLocationId"] = _resolve_symbolic_ref(
            lab_model["departmentLocationId"],
            namespaces,
            strict=True,
        )

    imaging_model = aggregate.get("imagingModel", {})
    if imaging_model.get("departmentLocationId"):
        imaging_model["departmentLocationId"] = _resolve_symbolic_ref(
            imaging_model["departmentLocationId"],
            namespaces,
            strict=True,
        )


def _resolve_cross_pack_rules(plan: dict[str, Any], namespaces: dict[str, str]) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for raw_rule in plan.get("crossPackRoutingRules", []):
        rule = _deepcopy(raw_rule)
        if rule.get("fromQueueId"):
            rule["fromQueueId"] = _resolve_symbolic_ref(rule["fromQueueId"], namespaces, strict=True)
        if rule.get("toQueueId"):
            rule["toQueueId"] = _resolve_symbolic_ref(rule["toQueueId"], namespaces, strict=True)
        rules.append(rule)
    return rules


def _validate_pack_metadata(
    *,
    pack_id: str,
    payload: dict[str, Any],
    aggregate: dict[str, Any],
    included_pack_payloads: list[tuple[str, dict[str, Any]]],
) -> None:
    compatible_archetypes = [str(value) for value in payload.get("compatibleArchetypes", [])]
    archetype = str(aggregate.get("archetype", "") or "")
    if compatible_archetypes and archetype and archetype not in compatible_archetypes:
        raise ValueError(
            f"Department pack '{pack_id}' is not compatible with archetype '{archetype}'. "
            f"Supported archetypes: {', '.join(compatible_archetypes)}."
        )

    incompatible_pack_ids = {str(value) for value in payload.get("incompatiblePackIds", [])}
    already_included = {included_pack_id for included_pack_id, _ in included_pack_payloads}
    conflicting = sorted(already_included & incompatible_pack_ids)
    if conflicting:
        raise ValueError(
            f"Department pack '{pack_id}' is incompatible with already selected pack(s): {', '.join(conflicting)}."
        )

    for included_pack_id, included_payload in included_pack_payloads:
        reverse_incompatibilities = {str(value) for value in included_payload.get("incompatiblePackIds", [])}
        if pack_id in reverse_incompatibilities:
            raise ValueError(
                f"Department pack '{pack_id}' conflicts with already selected pack '{included_pack_id}'."
            )


def list_department_packs(pack_dir: str | Path | None = None) -> list[DepartmentPackInfo]:
    base_dir = Path(pack_dir or DEFAULT_PACK_DIR)
    packs: list[DepartmentPackInfo] = []
    if not base_dir.exists():
        return packs
    for path in sorted(base_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        packs.append(
            DepartmentPackInfo(
                pack_id=str(payload.get("packId", path.stem)),
                name=str(payload.get("name", path.stem)),
                description=str(payload.get("description", "")),
                path=str(path),
            )
        )
    return packs


def compose_from_plan_dict(plan: dict[str, Any], *, pack_dir: str | Path | None = None) -> CompositionResult:
    base_dir = Path(pack_dir or DEFAULT_PACK_DIR)
    aggregate = _deepcopy(plan.get("baseConfig", {}))
    aggregate.setdefault("schemaVersion", plan.get("schemaVersion", "1.0"))
    namespaces: dict[str, str] = {}
    included_packs: list[str] = []
    included_pack_payloads: list[tuple[str, dict[str, Any]]] = []
    warnings: list[str] = []

    for selection in plan.get("packSelections", []):
        pack_id = str(selection["packId"])
        namespace = str(selection.get("namespace") or pack_id)
        if namespace in namespaces:
            raise ValueError(f"Duplicate namespace '{namespace}' in composition plan '{plan.get('planId', '')}'.")
        pack_path = base_dir / f"{pack_id}.json"
        if not pack_path.exists():
            raise FileNotFoundError(f"Department pack '{pack_id}' not found at '{pack_path}'.")
        payload = json.loads(pack_path.read_text(encoding="utf-8"))
        _validate_pack_metadata(
            pack_id=pack_id,
            payload=payload,
            aggregate=aggregate,
            included_pack_payloads=included_pack_payloads,
        )
        namespaces[namespace] = namespace
        pack_config = _deepcopy(payload.get("config", {}))
        transformed = _transform_pack_config(
            pack_config,
            namespace=namespace,
            mount_location_id=str(selection.get("parentLocationId") or ""),
        )
        _merge_partial_config(aggregate, transformed)
        included_packs.append(pack_id)
        included_pack_payloads.append((pack_id, payload))

    cross_pack_rules = _resolve_cross_pack_rules(plan, namespaces)
    if cross_pack_rules:
        aggregate.setdefault("routingRules", [])
        aggregate["routingRules"].extend(cross_pack_rules)

    overlays = _deepcopy(plan.get("overlays", {}))
    if overlays:
        _merge_partial_config(aggregate, overlays)

    _resolve_plan_symbols(aggregate, namespaces)
    config = ClinicConfigModel.from_dict(aggregate)
    return CompositionResult(
        plan_id=str(plan.get("planId", "composition-plan")),
        included_packs=included_packs,
        namespaces=namespaces,
        warnings=warnings,
        config=config,
    )


def compose_from_plan_file(plan_path: str | Path, *, pack_dir: str | Path | None = None) -> CompositionResult:
    payload = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    return compose_from_plan_dict(payload, pack_dir=pack_dir)
