from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class SchemaValidationError(ValueError):
    errors: list[str]

    def __str__(self) -> str:
        return "Clinic configuration schema validation failed:\n" + "\n".join(f"- {error}" for error in self.errors)


def _scalar(kind: str) -> dict[str, Any]:
    return {"type": kind}


def _array(item: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "item": item}


def _mapping(values: dict[str, Any]) -> dict[str, Any]:
    return {"type": "mapping", "values": values}


def _object(fields: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"type": "object", "fields": fields}


def _field(spec: dict[str, Any], *aliases: str) -> dict[str, Any]:
    return {"aliases": tuple(aliases), "spec": spec}


STRING = _scalar("string")
BOOLEAN = _scalar("boolean")
NUMBER = _scalar("number")
INTEGER = _scalar("integer")


ADDRESS_SPEC = _object(
    {
        "address1": _field(STRING, "address_1"),
        "address2": _field(STRING, "address_2"),
        "address3": _field(STRING, "address_3"),
        "address4": _field(STRING, "address_4"),
        "address5": _field(STRING, "address_5"),
        "address6": _field(STRING, "address_6"),
        "cityVillage": _field(STRING, "city_village"),
        "countyDistrict": _field(STRING, "county_district"),
        "stateProvince": _field(STRING, "state_province"),
        "postalCode": _field(STRING, "postal_code"),
        "country": _field(STRING),
    }
)

IDENTIFIER_STRATEGY_SPEC = _object(
    {
        "identifierType": _field(STRING, "identifier_type"),
        "baseCharacterSet": _field(STRING, "base_character_set"),
        "firstIdentifierBase": _field(STRING, "first_identifier_base"),
        "prefix": _field(STRING),
        "suffix": _field(STRING),
        "minLength": _field(INTEGER, "min_length"),
        "maxLength": _field(INTEGER, "max_length"),
        "poolRefillBatchSize": _field(INTEGER, "pool_refill_batch_size"),
        "poolMinimumSize": _field(INTEGER, "pool_minimum_size"),
        "poolRefillWithTask": _field(BOOLEAN, "pool_refill_with_task"),
        "poolSequentialAllocation": _field(BOOLEAN, "pool_sequential_allocation"),
    }
)

FORM_QUESTION_OPTION_SPEC = _object(
    {
        "concept": _field(STRING),
        "conceptRef": _field(STRING, "concept_ref"),
        "rendering": _field(STRING),
        "min": _field(STRING),
        "minValue": _field(STRING, "min_value"),
        "max": _field(STRING),
        "maxValue": _field(STRING, "max_value"),
        "showDate": _field(BOOLEAN, "show_date"),
        "answers": _field(
            _array(
                _object(
                    {
                        "concept": _field(STRING),
                        "label": _field(STRING),
                    }
                )
            )
        ),
    }
)

FORM_QUESTION_SPEC = _object(
    {
        "id": _field(STRING),
        "label": _field(STRING),
        "type": _field(STRING),
        "questionType": _field(STRING, "question_type"),
        "rendering": _field(STRING),
        "concept": _field(STRING),
        "conceptRef": _field(STRING, "concept_ref"),
        "required": _field(BOOLEAN),
        "min": _field(STRING),
        "minValue": _field(STRING, "min_value"),
        "max": _field(STRING),
        "maxValue": _field(STRING, "max_value"),
        "showDate": _field(BOOLEAN, "show_date"),
        "answers": _field(
            _array(
                _object(
                    {
                        "concept": _field(STRING),
                        "label": _field(STRING),
                    }
                )
            )
        ),
        "questionOptions": _field(FORM_QUESTION_OPTION_SPEC, "question_options"),
    }
)

LOCAL_CONCEPT_SPEC = _object(
    {
        "id": _field(STRING),
        "fullySpecifiedName": _field(STRING, "fully_specified_name"),
        "shortName": _field(STRING, "short_name"),
        "description": _field(STRING),
        "dataClass": _field(STRING, "data_class"),
        "dataType": _field(STRING, "data_type"),
        "version": _field(STRING),
        "sameAsMappings": _field(_array(STRING), "same_as_mappings"),
        "answers": _field(_array(STRING)),
        "members": _field(_array(STRING)),
    }
)

CONFIG_SCHEMA = _object(
    {
        "schemaVersion": _field(STRING, "schema_version"),
        "archetype": _field(STRING),
        "facilityProfile": _field(
            _object(
                {
                    "code": _field(STRING),
                    "name": _field(STRING),
                    "facilityType": _field(STRING, "facility_type"),
                    "ownership": _field(STRING),
                    "country": _field(STRING),
                    "timezone": _field(STRING),
                    "languages": _field(_array(STRING)),
                    "defaultCurrency": _field(STRING, "default_currency"),
                }
            ),
            "facility_profile",
        ),
        "locationTags": _field(
            _array(
                _object(
                    {
                        "id": _field(STRING),
                        "name": _field(STRING),
                        "description": _field(STRING),
                    }
                )
            ),
            "location_tags",
        ),
        "locations": _field(
            _array(
                _object(
                    {
                        "id": _field(STRING),
                        "name": _field(STRING),
                        "kind": _field(STRING),
                        "description": _field(STRING),
                        "parentId": _field(STRING, "parent_id"),
                        "tags": _field(_array(STRING)),
                        "address": _field(ADDRESS_SPEC),
                        "loginLocation": _field(BOOLEAN, "login_location"),
                        "facilityLocation": _field(BOOLEAN, "facility_location"),
                    }
                )
            )
        ),
        "staffingModel": _field(
            _object(
                {
                    "roles": _field(
                        _array(
                            _object(
                                {
                                    "id": _field(STRING),
                                    "name": _field(STRING),
                                    "cadre": _field(STRING),
                                    "responsibilities": _field(_array(STRING)),
                                    "queueIds": _field(_array(STRING), "queue_ids"),
                                }
                            )
                        )
                    )
                }
            ),
            "staffing_model",
        ),
        "registrationModel": _field(
            _object(
                {
                    "identifierStrategy": _field(IDENTIFIER_STRATEGY_SPEC, "identifier_strategy"),
                    "loginLocationId": _field(STRING, "login_location_id"),
                    "walkInAllowed": _field(BOOLEAN, "walk_in_allowed"),
                    "appointmentMode": _field(STRING, "appointment_mode"),
                    "referralSources": _field(_array(STRING), "referral_sources"),
                }
            ),
            "registration_model",
        ),
        "localConcepts": _field(_array(LOCAL_CONCEPT_SPEC), "local_concepts"),
        "encounterTypes": _field(
            _array(
                _object(
                    {
                        "id": _field(STRING),
                        "name": _field(STRING),
                        "description": _field(STRING),
                        "viewPrivilege": _field(STRING, "view_privilege"),
                        "editPrivilege": _field(STRING, "edit_privilege"),
                    }
                )
            ),
            "encounter_types",
        ),
        "forms": _field(
            _array(
                _object(
                    {
                        "id": _field(STRING),
                        "name": _field(STRING),
                        "description": _field(STRING),
                        "encounter": _field(STRING),
                        "processor": _field(STRING),
                        "published": _field(BOOLEAN),
                        "retired": _field(BOOLEAN),
                        "pages": _field(
                            _array(
                                _object(
                                    {
                                        "id": _field(STRING),
                                        "label": _field(STRING),
                                        "sections": _field(
                                            _array(
                                                _object(
                                                    {
                                                        "id": _field(STRING),
                                                        "label": _field(STRING),
                                                        "isExpanded": _field(BOOLEAN, "is_expanded"),
                                                        "questions": _field(_array(FORM_QUESTION_SPEC)),
                                                    }
                                                )
                                            )
                                        ),
                                    }
                                )
                            )
                        ),
                        "referencedForms": _field(_array(STRING), "referenced_forms"),
                    }
                )
            )
        ),
        "queues": _field(
            _array(
                _object(
                    {
                        "id": _field(STRING),
                        "name": _field(STRING),
                        "locationId": _field(STRING, "location_id"),
                        "serviceConcept": _field(STRING, "service_concept"),
                        "description": _field(STRING),
                        "roomIds": _field(_array(STRING), "room_ids"),
                        "statusConceptSet": _field(STRING, "status_concept_set"),
                        "priorityConceptSet": _field(STRING, "priority_concept_set"),
                        "sortWeightGenerator": _field(STRING, "sort_weight_generator"),
                        "allowedStatuses": _field(_array(STRING), "allowed_statuses"),
                        "allowedPriorities": _field(_array(STRING), "allowed_priorities"),
                    }
                )
            )
        ),
        "queueRooms": _field(
            _array(
                _object(
                    {
                        "id": _field(STRING),
                        "queueId": _field(STRING, "queue_id"),
                        "name": _field(STRING),
                        "description": _field(STRING),
                        "providerRoleIds": _field(_array(STRING), "provider_role_ids"),
                    }
                )
            ),
            "queue_rooms",
        ),
        "routingRules": _field(
            _array(
                _object(
                    {
                        "id": _field(STRING),
                        "fromQueueId": _field(STRING, "from_queue_id"),
                        "toQueueId": _field(STRING, "to_queue_id"),
                        "mode": _field(STRING),
                        "conditionSummary": _field(STRING, "condition_summary"),
                        "requiresPayment": _field(BOOLEAN, "requires_payment"),
                        "requiresResultReview": _field(BOOLEAN, "requires_result_review"),
                    }
                )
            ),
            "routing_rules",
        ),
        "billingModel": _field(
            _object(
                {
                    "serviceTypeConceptSet": _field(STRING, "service_type_concept_set"),
                    "waiverPaymentModeId": _field(STRING, "waiver_payment_mode_id"),
                    "billableServices": _field(
                        _array(
                            _object(
                                {
                                    "id": _field(STRING),
                                    "serviceName": _field(STRING, "service_name"),
                                    "shortName": _field(STRING, "short_name"),
                                    "concept": _field(STRING),
                                    "serviceType": _field(STRING, "service_type"),
                                    "serviceStatus": _field(STRING, "service_status"),
                                }
                            )
                        ),
                        "billable_services",
                    ),
                    "paymentModes": _field(
                        _array(
                            _object(
                                {
                                    "id": _field(STRING),
                                    "name": _field(STRING),
                                    "attributes": _field(
                                        _array(
                                            _object(
                                                {
                                                    "name": _field(STRING),
                                                    "format": _field(STRING),
                                                    "regex": _field(STRING),
                                                    "required": _field(BOOLEAN),
                                                }
                                            )
                                        )
                                    ),
                                }
                            )
                        ),
                        "payment_modes",
                    ),
                    "cashPoints": _field(
                        _array(
                            _object(
                                {
                                    "id": _field(STRING),
                                    "name": _field(STRING),
                                    "locationId": _field(STRING, "location_id"),
                                    "description": _field(STRING),
                                }
                            )
                        ),
                        "cash_points",
                    ),
                    "pricingRules": _field(
                        _array(
                            _object(
                                {
                                    "id": _field(STRING),
                                    "billableServiceId": _field(STRING, "billable_service_id"),
                                    "amount": _field(NUMBER),
                                    "patientCategory": _field(STRING, "patient_category"),
                                    "requiresPaymentBeforeService": _field(BOOLEAN, "requires_payment_before_service"),
                                }
                            )
                        ),
                        "pricing_rules",
                    ),
                    "globalProperties": _field(_mapping(STRING), "global_properties"),
                }
            ),
            "billing_model",
        ),
        "stockPharmacyModel": _field(
            _object(
                {
                    "stockLocations": _field(
                        _array(
                            _object(
                                {
                                    "id": _field(STRING),
                                    "locationId": _field(STRING, "location_id"),
                                    "tags": _field(_array(STRING)),
                                }
                            )
                        ),
                        "stock_locations",
                    ),
                    "operationTypes": _field(
                        _array(
                            _object(
                                {
                                    "id": _field(STRING),
                                    "name": _field(STRING),
                                    "description": _field(STRING),
                                    "allowedLocationIds": _field(_array(STRING), "allowed_location_ids"),
                                }
                            )
                        ),
                        "operation_types",
                    ),
                    "rules": _field(
                        _array(
                            _object(
                                {
                                    "id": _field(STRING),
                                    "stockItemName": _field(STRING, "stock_item_name"),
                                    "locationId": _field(STRING, "location_id"),
                                    "reorderLevel": _field(NUMBER, "reorder_level"),
                                }
                            )
                        )
                    ),
                    "dispensingQueueIds": _field(_array(STRING), "dispensing_queue_ids"),
                }
            ),
            "stock_pharmacy_model",
        ),
        "programs": _field(
            _array(
                _object(
                    {
                        "id": _field(STRING),
                        "programConcept": _field(STRING, "program_concept"),
                        "outcomesConcept": _field(STRING, "outcomes_concept"),
                    }
                )
            )
        ),
        "programWorkflows": _field(
            _array(
                _object(
                    {
                        "id": _field(STRING),
                        "programId": _field(STRING, "program_id"),
                        "workflowConcept": _field(STRING, "workflow_concept"),
                    }
                )
            ),
            "program_workflows",
        ),
        "programWorkflowStates": _field(
            _array(
                _object(
                    {
                        "id": _field(STRING),
                        "workflowId": _field(STRING, "workflow_id"),
                        "stateConcept": _field(STRING, "state_concept"),
                        "initial": _field(BOOLEAN),
                        "terminal": _field(BOOLEAN),
                    }
                )
            ),
            "program_workflow_states",
        ),
        "labModel": _field(
            _object(
                {
                    "enabled": _field(BOOLEAN),
                    "departmentLocationId": _field(STRING, "department_location_id"),
                    "integratedOrdering": _field(BOOLEAN, "integrated_ordering"),
                    "specimenHandoffMode": _field(STRING, "specimen_handoff_mode"),
                    "resultDeliveryMode": _field(STRING, "result_delivery_mode"),
                    "resultReviewerRoleIds": _field(_array(STRING), "result_reviewer_role_ids"),
                }
            ),
            "lab_model",
        ),
        "imagingModel": _field(
            _object(
                {
                    "enabled": _field(BOOLEAN),
                    "departmentLocationId": _field(STRING, "department_location_id"),
                    "defaultViewer": _field(STRING, "default_viewer"),
                    "studyShareEnabled": _field(BOOLEAN, "study_share_enabled"),
                    "rolePermissions": _field(_mapping(_array(STRING)), "role_permissions"),
                    "authorizedLabels": _field(_mapping(_array(STRING)), "authorized_labels"),
                }
            ),
            "imaging_model",
        ),
        "identityModel": _field(
            _object(
                {
                    "roles": _field(
                        _array(
                            _object(
                                {
                                    "id": _field(STRING),
                                    "name": _field(STRING),
                                    "description": _field(STRING),
                                    "keycloakRoles": _field(_array(STRING), "keycloak_roles"),
                                    "openmrsRoles": _field(_array(STRING), "openmrs_roles"),
                                    "openelisRoles": _field(_array(STRING), "openelis_roles"),
                                    "orthancPermissions": _field(_array(STRING), "orthanc_permissions"),
                                }
                            )
                        )
                    ),
                    "users": _field(
                        _array(
                            _object(
                                {
                                    "username": _field(STRING),
                                    "firstName": _field(STRING, "first_name"),
                                    "lastName": _field(STRING, "last_name"),
                                    "email": _field(STRING),
                                    "roleIds": _field(_array(STRING), "role_ids"),
                                }
                            )
                        )
                    ),
                }
            ),
            "identity_model",
        ),
        "governance": _field(
            _object(
                {
                    "approvalRequired": _field(BOOLEAN, "approval_required"),
                    "allowedRequestorRoleIds": _field(_array(STRING), "allowed_requestor_role_ids"),
                    "dryRunByDefault": _field(BOOLEAN, "dry_run_by_default"),
                    "changeTicketPrefix": _field(STRING, "change_ticket_prefix"),
                    "approvalEnvironments": _field(_array(STRING), "approval_environments"),
                    "promotionEnvironments": _field(_array(STRING), "promotion_environments"),
                }
            )
        ),
    }
)


def validate_config_payload(payload: Mapping[str, Any]) -> None:
    errors: list[str] = []
    _validate_value(payload, CONFIG_SCHEMA, "config", errors)
    if errors:
        raise SchemaValidationError(errors)


def _validate_value(value: Any, spec: dict[str, Any], path: str, errors: list[str]) -> None:
    spec_type = spec["type"]
    if spec_type == "object":
        if not isinstance(value, Mapping):
            errors.append(f"{path}: expected object, got {type(value).__name__}.")
            return
        _validate_object(value, spec, path, errors)
        return
    if spec_type == "array":
        if not isinstance(value, list):
            errors.append(f"{path}: expected array, got {type(value).__name__}.")
            return
        for index, item in enumerate(value):
            _validate_value(item, spec["item"], f"{path}[{index}]", errors)
        return
    if spec_type == "mapping":
        if not isinstance(value, Mapping):
            errors.append(f"{path}: expected object map, got {type(value).__name__}.")
            return
        for key, nested in value.items():
            if not isinstance(key, str):
                errors.append(f"{path}: expected string map keys, got {type(key).__name__}.")
                continue
            _validate_value(nested, spec["values"], f"{path}.{key}", errors)
        return
    if spec_type == "string":
        if not isinstance(value, str):
            errors.append(f"{path}: expected string, got {type(value).__name__}.")
        return
    if spec_type == "boolean":
        if not isinstance(value, bool):
            errors.append(f"{path}: expected boolean, got {type(value).__name__}.")
        return
    if spec_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{path}: expected number, got {type(value).__name__}.")
        return
    if spec_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"{path}: expected integer, got {type(value).__name__}.")
        return
    errors.append(f"{path}: unsupported schema type '{spec_type}'.")


def _validate_object(value: Mapping[str, Any], spec: dict[str, Any], path: str, errors: list[str]) -> None:
    fields = spec["fields"]
    alias_lookup: dict[str, str] = {}
    for canonical_name, field_spec in fields.items():
        alias_lookup[canonical_name] = canonical_name
        for alias in field_spec.get("aliases", ()):
            alias_lookup[str(alias)] = canonical_name

    for key, nested in value.items():
        canonical_name = alias_lookup.get(str(key))
        if canonical_name is None:
            errors.append(f"{path}: unexpected field '{key}'.")
            continue
        _validate_value(nested, fields[canonical_name]["spec"], f"{path}.{canonical_name}", errors)
