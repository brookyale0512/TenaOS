from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TypeVar

from .config_schema import validate_config_payload


T = TypeVar("T")


def _get(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return default


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _string_list(value: Any) -> list[str]:
    items = _as_list(value)
    normalized: list[str] = []
    for item in items:
        if item is None:
            continue
        normalized.append(str(item).strip())
    return [item for item in normalized if item]


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _load_many(value: Any, loader: Callable[[Mapping[str, Any]], T]) -> list[T]:
    results: list[T] = []
    for item in _as_list(value):
        if not isinstance(item, Mapping):
            continue
        results.append(loader(item))
    return results


@dataclass(slots=True)
class Address:
    address1: str = ""
    address2: str = ""
    address3: str = ""
    address4: str = ""
    address5: str = ""
    address6: str = ""
    city_village: str = ""
    county_district: str = ""
    state_province: str = ""
    postal_code: str = ""
    country: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "Address":
        if not payload:
            return cls()
        return cls(
            address1=str(_get(payload, "address1", "address_1", default="") or ""),
            address2=str(_get(payload, "address2", "address_2", default="") or ""),
            address3=str(_get(payload, "address3", "address_3", default="") or ""),
            address4=str(_get(payload, "address4", "address_4", default="") or ""),
            address5=str(_get(payload, "address5", "address_5", default="") or ""),
            address6=str(_get(payload, "address6", "address_6", default="") or ""),
            city_village=str(_get(payload, "cityVillage", "city_village", default="") or ""),
            county_district=str(_get(payload, "countyDistrict", "county_district", default="") or ""),
            state_province=str(_get(payload, "stateProvince", "state_province", default="") or ""),
            postal_code=str(_get(payload, "postalCode", "postal_code", default="") or ""),
            country=str(_get(payload, "country", default="") or ""),
        )


@dataclass(slots=True)
class FacilityProfile:
    code: str
    name: str
    facility_type: str
    ownership: str = ""
    country: str = ""
    timezone: str = "UTC"
    languages: list[str] = field(default_factory=list)
    default_currency: str = "USD"

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FacilityProfile":
        return cls(
            code=str(_get(payload, "code", default="main") or "main"),
            name=str(_get(payload, "name", default="") or ""),
            facility_type=str(_get(payload, "facilityType", "facility_type", default="general-outpatient") or "general-outpatient"),
            ownership=str(_get(payload, "ownership", default="") or ""),
            country=str(_get(payload, "country", default="") or ""),
            timezone=str(_get(payload, "timezone", default="UTC") or "UTC"),
            languages=_string_list(_get(payload, "languages", default=["en"])),
            default_currency=str(_get(payload, "defaultCurrency", "default_currency", default="USD") or "USD"),
        )


@dataclass(slots=True)
class LocationTagDefinition:
    id: str
    name: str
    description: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LocationTagDefinition":
        return cls(
            id=str(_get(payload, "id", default="") or ""),
            name=str(_get(payload, "name", default="") or ""),
            description=str(_get(payload, "description", default="") or ""),
        )


@dataclass(slots=True)
class LocationDefinition:
    id: str
    name: str
    kind: str
    description: str = ""
    parent_id: str = ""
    tags: list[str] = field(default_factory=list)
    address: Address = field(default_factory=Address)
    login_location: bool = False
    facility_location: bool = False

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LocationDefinition":
        return cls(
            id=str(_get(payload, "id", default="") or ""),
            name=str(_get(payload, "name", default="") or ""),
            kind=str(_get(payload, "kind", default="department") or "department"),
            description=str(_get(payload, "description", default="") or ""),
            parent_id=str(_get(payload, "parentId", "parent_id", default="") or ""),
            tags=_string_list(_get(payload, "tags", default=[])),
            address=Address.from_dict(_get(payload, "address", default={})),
            login_location=_as_bool(_get(payload, "loginLocation", "login_location", default=False)),
            facility_location=_as_bool(_get(payload, "facilityLocation", "facility_location", default=False)),
        )


@dataclass(slots=True)
class StaffRole:
    id: str
    name: str
    cadre: str
    responsibilities: list[str] = field(default_factory=list)
    queue_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StaffRole":
        return cls(
            id=str(_get(payload, "id", default="") or ""),
            name=str(_get(payload, "name", default="") or ""),
            cadre=str(_get(payload, "cadre", default="general") or "general"),
            responsibilities=_string_list(_get(payload, "responsibilities", default=[])),
            queue_ids=_string_list(_get(payload, "queueIds", "queue_ids", default=[])),
        )


@dataclass(slots=True)
class StaffingModel:
    roles: list[StaffRole] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "StaffingModel":
        if not payload:
            return cls()
        return cls(roles=_load_many(_get(payload, "roles", default=[]), StaffRole.from_dict))


@dataclass(slots=True)
class EncounterTypeDefinition:
    id: str
    name: str
    description: str = ""
    view_privilege: str = ""
    edit_privilege: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EncounterTypeDefinition":
        return cls(
            id=str(_get(payload, "id", default="") or ""),
            name=str(_get(payload, "name", default="") or ""),
            description=str(_get(payload, "description", default="") or ""),
            view_privilege=str(_get(payload, "viewPrivilege", "view_privilege", default="") or ""),
            edit_privilege=str(_get(payload, "editPrivilege", "edit_privilege", default="") or ""),
        )


@dataclass(slots=True)
class IdentifierStrategy:
    identifier_type: str = "OpenMRS ID"
    base_character_set: str = "0123456789"
    first_identifier_base: str = "10000"
    prefix: str = ""
    suffix: str = ""
    min_length: int = 5
    max_length: int = 10
    pool_refill_batch_size: int = 500
    pool_minimum_size: int = 100
    pool_refill_with_task: bool = True
    pool_sequential_allocation: bool = True

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "IdentifierStrategy":
        if not payload:
            return cls()
        return cls(
            identifier_type=str(_get(payload, "identifierType", "identifier_type", default="OpenMRS ID") or "OpenMRS ID"),
            base_character_set=str(_get(payload, "baseCharacterSet", "base_character_set", default="0123456789") or "0123456789"),
            first_identifier_base=str(_get(payload, "firstIdentifierBase", "first_identifier_base", default="10000") or "10000"),
            prefix=str(_get(payload, "prefix", default="") or ""),
            suffix=str(_get(payload, "suffix", default="") or ""),
            min_length=int(_get(payload, "minLength", "min_length", default=5) or 5),
            max_length=int(_get(payload, "maxLength", "max_length", default=10) or 10),
            pool_refill_batch_size=int(_get(payload, "poolRefillBatchSize", "pool_refill_batch_size", default=500) or 500),
            pool_minimum_size=int(_get(payload, "poolMinimumSize", "pool_minimum_size", default=100) or 100),
            pool_refill_with_task=_as_bool(_get(payload, "poolRefillWithTask", "pool_refill_with_task", default=True), default=True),
            pool_sequential_allocation=_as_bool(_get(payload, "poolSequentialAllocation", "pool_sequential_allocation", default=True), default=True),
        )


@dataclass(slots=True)
class RegistrationModel:
    identifier_strategy: IdentifierStrategy = field(default_factory=IdentifierStrategy)
    login_location_id: str = ""
    walk_in_allowed: bool = True
    appointment_mode: str = "walk-in"
    referral_sources: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "RegistrationModel":
        if not payload:
            return cls()
        return cls(
            identifier_strategy=IdentifierStrategy.from_dict(_get(payload, "identifierStrategy", "identifier_strategy", default={})),
            login_location_id=str(_get(payload, "loginLocationId", "login_location_id", default="") or ""),
            walk_in_allowed=_as_bool(_get(payload, "walkInAllowed", "walk_in_allowed", default=True), default=True),
            appointment_mode=str(_get(payload, "appointmentMode", "appointment_mode", default="walk-in") or "walk-in"),
            referral_sources=_string_list(_get(payload, "referralSources", "referral_sources", default=[])),
        )


@dataclass(slots=True)
class LocalConceptDefinition:
    id: str
    fully_specified_name: str
    short_name: str = ""
    description: str = ""
    data_class: str = "Misc"
    data_type: str = "N/A"
    version: str = ""
    same_as_mappings: list[str] = field(default_factory=list)
    answers: list[str] = field(default_factory=list)
    members: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LocalConceptDefinition":
        return cls(
            id=str(_get(payload, "id", default="") or ""),
            fully_specified_name=str(
                _get(payload, "fullySpecifiedName", "fully_specified_name", default="") or ""
            ),
            short_name=str(_get(payload, "shortName", "short_name", default="") or ""),
            description=str(_get(payload, "description", default="") or ""),
            data_class=str(_get(payload, "dataClass", "data_class", default="Misc") or "Misc"),
            data_type=str(_get(payload, "dataType", "data_type", default="N/A") or "N/A"),
            version=str(_get(payload, "version", default="") or ""),
            same_as_mappings=_string_list(_get(payload, "sameAsMappings", "same_as_mappings", default=[])),
            answers=_string_list(_get(payload, "answers", default=[])),
            members=_string_list(_get(payload, "members", default=[])),
        )


@dataclass(slots=True)
class FormQuestionOption:
    concept: str
    label: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FormQuestionOption":
        return cls(
            concept=str(_get(payload, "concept", default="") or ""),
            label=str(_get(payload, "label", default="") or ""),
        )


@dataclass(slots=True)
class FormQuestion:
    id: str
    label: str
    question_type: str
    rendering: str = "text"
    concept: str = ""
    required: bool = False
    min_value: str = ""
    max_value: str = ""
    show_date: bool = False
    answers: list[FormQuestionOption] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FormQuestion":
        question_options = _get(payload, "questionOptions", "question_options", default={}) or {}
        answers_payload = _get(question_options, "answers", default=None)
        if answers_payload is None:
            answers_payload = _get(payload, "answers", default=[])
        return cls(
            id=str(_get(payload, "id", default="") or ""),
            label=str(_get(payload, "label", default="") or ""),
            question_type=str(_get(payload, "type", "questionType", "question_type", default="obs") or "obs"),
            rendering=str(_get(question_options, "rendering", default=_get(payload, "rendering", default="text")) or "text"),
            concept=str(
                _get(
                    question_options,
                    "concept",
                    "conceptRef",
                    "concept_ref",
                    default=_get(payload, "concept", "conceptRef", "concept_ref", default=""),
                )
                or ""
            ),
            required=_as_bool(_get(payload, "required", default=False)),
            min_value=str(
                _get(
                    question_options,
                    "min",
                    "minValue",
                    "min_value",
                    default=_get(payload, "min", "minValue", "min_value", default=""),
                )
                or ""
            ),
            max_value=str(
                _get(
                    question_options,
                    "max",
                    "maxValue",
                    "max_value",
                    default=_get(payload, "max", "maxValue", "max_value", default=""),
                )
                or ""
            ),
            show_date=_as_bool(
                _get(
                    question_options,
                    "showDate",
                    "show_date",
                    default=_get(payload, "showDate", "show_date", default=False),
                )
            ),
            answers=_load_many(answers_payload, FormQuestionOption.from_dict),
        )


@dataclass(slots=True)
class FormSection:
    id: str
    label: str
    is_expanded: bool = True
    questions: list[FormQuestion] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FormSection":
        return cls(
            id=str(_get(payload, "id", default="") or _get(payload, "label", default="")),
            label=str(_get(payload, "label", default="") or ""),
            is_expanded=_as_bool(_get(payload, "isExpanded", "is_expanded", default=True), default=True),
            questions=_load_many(_get(payload, "questions", default=[]), FormQuestion.from_dict),
        )


@dataclass(slots=True)
class FormPage:
    id: str
    label: str
    sections: list[FormSection] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FormPage":
        return cls(
            id=str(_get(payload, "id", default="") or _get(payload, "label", default="")),
            label=str(_get(payload, "label", default="") or ""),
            sections=_load_many(_get(payload, "sections", default=[]), FormSection.from_dict),
        )


@dataclass(slots=True)
class FormDefinition:
    id: str
    name: str
    description: str
    encounter: str
    processor: str = "EncounterFormProcessor"
    published: bool = True
    retired: bool = False
    pages: list[FormPage] = field(default_factory=list)
    referenced_forms: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FormDefinition":
        return cls(
            id=str(_get(payload, "id", default="") or ""),
            name=str(_get(payload, "name", default="") or ""),
            description=str(_get(payload, "description", default="") or ""),
            encounter=str(_get(payload, "encounter", default="") or ""),
            processor=str(_get(payload, "processor", default="EncounterFormProcessor") or "EncounterFormProcessor"),
            published=_as_bool(_get(payload, "published", default=True), default=True),
            retired=_as_bool(_get(payload, "retired", default=False)),
            pages=_load_many(_get(payload, "pages", default=[]), FormPage.from_dict),
            referenced_forms=_string_list(_get(payload, "referencedForms", "referenced_forms", default=[])),
        )


@dataclass(slots=True)
class QueueRoomDefinition:
    id: str
    queue_id: str
    name: str
    description: str = ""
    provider_role_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "QueueRoomDefinition":
        return cls(
            id=str(_get(payload, "id", default="") or ""),
            queue_id=str(_get(payload, "queueId", "queue_id", default="") or ""),
            name=str(_get(payload, "name", default="") or ""),
            description=str(_get(payload, "description", default="") or ""),
            provider_role_ids=_string_list(_get(payload, "providerRoleIds", "provider_role_ids", default=[])),
        )


@dataclass(slots=True)
class QueueDefinition:
    id: str
    name: str
    location_id: str
    service_concept: str
    description: str = ""
    room_ids: list[str] = field(default_factory=list)
    status_concept_set: str = ""
    priority_concept_set: str = ""
    sort_weight_generator: str = "existingValueSortWeightGenerator"
    allowed_statuses: list[str] = field(default_factory=list)
    allowed_priorities: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "QueueDefinition":
        return cls(
            id=str(_get(payload, "id", default="") or ""),
            name=str(_get(payload, "name", default="") or ""),
            location_id=str(_get(payload, "locationId", "location_id", default="") or ""),
            service_concept=str(_get(payload, "serviceConcept", "service_concept", default="") or ""),
            description=str(_get(payload, "description", default="") or ""),
            room_ids=_string_list(_get(payload, "roomIds", "room_ids", default=[])),
            status_concept_set=str(_get(payload, "statusConceptSet", "status_concept_set", default="") or ""),
            priority_concept_set=str(_get(payload, "priorityConceptSet", "priority_concept_set", default="") or ""),
            sort_weight_generator=str(_get(payload, "sortWeightGenerator", "sort_weight_generator", default="existingValueSortWeightGenerator") or "existingValueSortWeightGenerator"),
            allowed_statuses=_string_list(_get(payload, "allowedStatuses", "allowed_statuses", default=[])),
            allowed_priorities=_string_list(_get(payload, "allowedPriorities", "allowed_priorities", default=[])),
        )


@dataclass(slots=True)
class RoutingRule:
    id: str
    from_queue_id: str
    to_queue_id: str
    mode: str = "manual"
    condition_summary: str = ""
    requires_payment: bool = False
    requires_result_review: bool = False

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RoutingRule":
        return cls(
            id=str(_get(payload, "id", default="") or ""),
            from_queue_id=str(_get(payload, "fromQueueId", "from_queue_id", default="") or ""),
            to_queue_id=str(_get(payload, "toQueueId", "to_queue_id", default="") or ""),
            mode=str(_get(payload, "mode", default="manual") or "manual"),
            condition_summary=str(_get(payload, "conditionSummary", "condition_summary", default="") or ""),
            requires_payment=_as_bool(_get(payload, "requiresPayment", "requires_payment", default=False)),
            requires_result_review=_as_bool(_get(payload, "requiresResultReview", "requires_result_review", default=False)),
        )


@dataclass(slots=True)
class BillableService:
    id: str
    service_name: str
    short_name: str = ""
    concept: str = ""
    service_type: str = ""
    service_status: str = "Enabled"

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BillableService":
        return cls(
            id=str(_get(payload, "id", default="") or ""),
            service_name=str(_get(payload, "serviceName", "service_name", default="") or ""),
            short_name=str(_get(payload, "shortName", "short_name", default="") or ""),
            concept=str(_get(payload, "concept", default="") or ""),
            service_type=str(_get(payload, "serviceType", "service_type", default="") or ""),
            service_status=str(_get(payload, "serviceStatus", "service_status", default="Enabled") or "Enabled"),
        )


@dataclass(slots=True)
class PaymentModeAttribute:
    name: str
    format: str = ""
    regex: str = ""
    required: bool = False

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PaymentModeAttribute":
        return cls(
            name=str(_get(payload, "name", default="") or ""),
            format=str(_get(payload, "format", default="") or ""),
            regex=str(_get(payload, "regex", default="") or ""),
            required=_as_bool(_get(payload, "required", default=False)),
        )


@dataclass(slots=True)
class PaymentMode:
    id: str
    name: str
    attributes: list[PaymentModeAttribute] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PaymentMode":
        return cls(
            id=str(_get(payload, "id", default="") or ""),
            name=str(_get(payload, "name", default="") or ""),
            attributes=_load_many(_get(payload, "attributes", default=[]), PaymentModeAttribute.from_dict),
        )


@dataclass(slots=True)
class CashPoint:
    id: str
    name: str
    location_id: str
    description: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CashPoint":
        return cls(
            id=str(_get(payload, "id", default="") or ""),
            name=str(_get(payload, "name", default="") or ""),
            location_id=str(_get(payload, "locationId", "location_id", default="") or ""),
            description=str(_get(payload, "description", default="") or ""),
        )


@dataclass(slots=True)
class PricingRule:
    id: str
    billable_service_id: str
    amount: float
    patient_category: str = ""
    requires_payment_before_service: bool = False

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PricingRule":
        return cls(
            id=str(_get(payload, "id", default="") or ""),
            billable_service_id=str(_get(payload, "billableServiceId", "billable_service_id", default="") or ""),
            amount=float(_get(payload, "amount", default=0.0) or 0.0),
            patient_category=str(_get(payload, "patientCategory", "patient_category", default="") or ""),
            requires_payment_before_service=_as_bool(
                _get(payload, "requiresPaymentBeforeService", "requires_payment_before_service", default=False)
            ),
        )


@dataclass(slots=True)
class BillingModel:
    service_type_concept_set: str = ""
    waiver_payment_mode_id: str = ""
    billable_services: list[BillableService] = field(default_factory=list)
    payment_modes: list[PaymentMode] = field(default_factory=list)
    cash_points: list[CashPoint] = field(default_factory=list)
    pricing_rules: list[PricingRule] = field(default_factory=list)
    global_properties: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "BillingModel":
        if not payload:
            return cls()
        return cls(
            service_type_concept_set=str(_get(payload, "serviceTypeConceptSet", "service_type_concept_set", default="") or ""),
            waiver_payment_mode_id=str(_get(payload, "waiverPaymentModeId", "waiver_payment_mode_id", default="") or ""),
            billable_services=_load_many(_get(payload, "billableServices", "billable_services", default=[]), BillableService.from_dict),
            payment_modes=_load_many(_get(payload, "paymentModes", "payment_modes", default=[]), PaymentMode.from_dict),
            cash_points=_load_many(_get(payload, "cashPoints", "cash_points", default=[]), CashPoint.from_dict),
            pricing_rules=_load_many(_get(payload, "pricingRules", "pricing_rules", default=[]), PricingRule.from_dict),
            global_properties={
                str(key): str(value)
                for key, value in (_get(payload, "globalProperties", "global_properties", default={}) or {}).items()
            },
        )


@dataclass(slots=True)
class StockLocation:
    id: str
    location_id: str
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StockLocation":
        return cls(
            id=str(_get(payload, "id", default="") or ""),
            location_id=str(_get(payload, "locationId", "location_id", default="") or ""),
            tags=_string_list(_get(payload, "tags", default=[])),
        )


@dataclass(slots=True)
class StockOperationType:
    id: str
    name: str
    description: str = ""
    allowed_location_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StockOperationType":
        return cls(
            id=str(_get(payload, "id", default="") or ""),
            name=str(_get(payload, "name", default="") or ""),
            description=str(_get(payload, "description", default="") or ""),
            allowed_location_ids=_string_list(_get(payload, "allowedLocationIds", "allowed_location_ids", default=[])),
        )


@dataclass(slots=True)
class StockRule:
    id: str
    stock_item_name: str
    location_id: str
    reorder_level: float = 0.0

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StockRule":
        return cls(
            id=str(_get(payload, "id", default="") or ""),
            stock_item_name=str(_get(payload, "stockItemName", "stock_item_name", default="") or ""),
            location_id=str(_get(payload, "locationId", "location_id", default="") or ""),
            reorder_level=float(_get(payload, "reorderLevel", "reorder_level", default=0.0) or 0.0),
        )


@dataclass(slots=True)
class StockPharmacyModel:
    stock_locations: list[StockLocation] = field(default_factory=list)
    operation_types: list[StockOperationType] = field(default_factory=list)
    rules: list[StockRule] = field(default_factory=list)
    dispensing_queue_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "StockPharmacyModel":
        if not payload:
            return cls()
        return cls(
            stock_locations=_load_many(_get(payload, "stockLocations", "stock_locations", default=[]), StockLocation.from_dict),
            operation_types=_load_many(_get(payload, "operationTypes", "operation_types", default=[]), StockOperationType.from_dict),
            rules=_load_many(_get(payload, "rules", default=[]), StockRule.from_dict),
            dispensing_queue_ids=_string_list(_get(payload, "dispensingQueueIds", "dispensing_queue_ids", default=[])),
        )


@dataclass(slots=True)
class ProgramDefinition:
    id: str
    program_concept: str
    outcomes_concept: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProgramDefinition":
        return cls(
            id=str(_get(payload, "id", default="") or ""),
            program_concept=str(_get(payload, "programConcept", "program_concept", default="") or ""),
            outcomes_concept=str(_get(payload, "outcomesConcept", "outcomes_concept", default="") or ""),
        )


@dataclass(slots=True)
class ProgramWorkflowDefinition:
    id: str
    program_id: str
    workflow_concept: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProgramWorkflowDefinition":
        return cls(
            id=str(_get(payload, "id", default="") or ""),
            program_id=str(_get(payload, "programId", "program_id", default="") or ""),
            workflow_concept=str(_get(payload, "workflowConcept", "workflow_concept", default="") or ""),
        )


@dataclass(slots=True)
class ProgramWorkflowStateDefinition:
    id: str
    workflow_id: str
    state_concept: str
    initial: bool = False
    terminal: bool = False

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProgramWorkflowStateDefinition":
        return cls(
            id=str(_get(payload, "id", default="") or ""),
            workflow_id=str(_get(payload, "workflowId", "workflow_id", default="") or ""),
            state_concept=str(_get(payload, "stateConcept", "state_concept", default="") or ""),
            initial=_as_bool(_get(payload, "initial", default=False)),
            terminal=_as_bool(_get(payload, "terminal", default=False)),
        )


@dataclass(slots=True)
class LabModel:
    enabled: bool = False
    department_location_id: str = ""
    integrated_ordering: bool = True
    specimen_handoff_mode: str = "manual"
    result_delivery_mode: str = "fhir"
    result_reviewer_role_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "LabModel":
        if not payload:
            return cls()
        return cls(
            enabled=_as_bool(_get(payload, "enabled", default=False)),
            department_location_id=str(_get(payload, "departmentLocationId", "department_location_id", default="") or ""),
            integrated_ordering=_as_bool(_get(payload, "integratedOrdering", "integrated_ordering", default=True), default=True),
            specimen_handoff_mode=str(_get(payload, "specimenHandoffMode", "specimen_handoff_mode", default="manual") or "manual"),
            result_delivery_mode=str(_get(payload, "resultDeliveryMode", "result_delivery_mode", default="fhir") or "fhir"),
            result_reviewer_role_ids=_string_list(_get(payload, "resultReviewerRoleIds", "result_reviewer_role_ids", default=[])),
        )


@dataclass(slots=True)
class ImagingModel:
    enabled: bool = False
    department_location_id: str = ""
    default_viewer: str = "oe2"
    study_share_enabled: bool = True
    role_permissions: dict[str, list[str]] = field(default_factory=dict)
    authorized_labels: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "ImagingModel":
        if not payload:
            return cls()
        role_permissions = {
            str(key): _string_list(value)
            for key, value in (_get(payload, "rolePermissions", "role_permissions", default={}) or {}).items()
        }
        authorized_labels = {
            str(key): _string_list(value)
            for key, value in (_get(payload, "authorizedLabels", "authorized_labels", default={}) or {}).items()
        }
        return cls(
            enabled=_as_bool(_get(payload, "enabled", default=False)),
            department_location_id=str(_get(payload, "departmentLocationId", "department_location_id", default="") or ""),
            default_viewer=str(_get(payload, "defaultViewer", "default_viewer", default="oe2") or "oe2"),
            study_share_enabled=_as_bool(_get(payload, "studyShareEnabled", "study_share_enabled", default=True), default=True),
            role_permissions=role_permissions,
            authorized_labels=authorized_labels,
        )


@dataclass(slots=True)
class ClinicRole:
    id: str
    name: str
    description: str = ""
    keycloak_roles: list[str] = field(default_factory=list)
    openmrs_roles: list[str] = field(default_factory=list)
    openelis_roles: list[str] = field(default_factory=list)
    orthanc_permissions: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ClinicRole":
        return cls(
            id=str(_get(payload, "id", default="") or ""),
            name=str(_get(payload, "name", default="") or ""),
            description=str(_get(payload, "description", default="") or ""),
            keycloak_roles=_string_list(_get(payload, "keycloakRoles", "keycloak_roles", default=[])),
            openmrs_roles=_string_list(_get(payload, "openmrsRoles", "openmrs_roles", default=[])),
            openelis_roles=_string_list(_get(payload, "openelisRoles", "openelis_roles", default=[])),
            orthanc_permissions=_string_list(_get(payload, "orthancPermissions", "orthanc_permissions", default=[])),
        )


@dataclass(slots=True)
class IdentityUser:
    username: str
    first_name: str
    last_name: str
    email: str = ""
    role_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "IdentityUser":
        return cls(
            username=str(_get(payload, "username", default="") or ""),
            first_name=str(_get(payload, "firstName", "first_name", default="") or ""),
            last_name=str(_get(payload, "lastName", "last_name", default="") or ""),
            email=str(_get(payload, "email", default="") or ""),
            role_ids=_string_list(_get(payload, "roleIds", "role_ids", default=[])),
        )


@dataclass(slots=True)
class IdentityModel:
    roles: list[ClinicRole] = field(default_factory=list)
    users: list[IdentityUser] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "IdentityModel":
        if not payload:
            return cls()
        return cls(
            roles=_load_many(_get(payload, "roles", default=[]), ClinicRole.from_dict),
            users=_load_many(_get(payload, "users", default=[]), IdentityUser.from_dict),
        )


@dataclass(slots=True)
class GovernanceModel:
    approval_required: bool = True
    allowed_requestor_role_ids: list[str] = field(default_factory=list)
    dry_run_by_default: bool = True
    change_ticket_prefix: str = "CFG"
    approval_environments: list[str] = field(default_factory=list)
    promotion_environments: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "GovernanceModel":
        if not payload:
            return cls()
        return cls(
            approval_required=_as_bool(_get(payload, "approvalRequired", "approval_required", default=True), default=True),
            allowed_requestor_role_ids=_string_list(
                _get(payload, "allowedRequestorRoleIds", "allowed_requestor_role_ids", default=[])
            ),
            dry_run_by_default=_as_bool(_get(payload, "dryRunByDefault", "dry_run_by_default", default=True), default=True),
            change_ticket_prefix=str(_get(payload, "changeTicketPrefix", "change_ticket_prefix", default="CFG") or "CFG"),
            approval_environments=_string_list(
                _get(payload, "approvalEnvironments", "approval_environments", default=[])
            ),
            promotion_environments=_string_list(
                _get(payload, "promotionEnvironments", "promotion_environments", default=[])
            ),
        )


@dataclass(slots=True)
class ClinicConfigModel:
    schema_version: str
    facility_profile: FacilityProfile
    location_tags: list[LocationTagDefinition] = field(default_factory=list)
    locations: list[LocationDefinition] = field(default_factory=list)
    staffing_model: StaffingModel = field(default_factory=StaffingModel)
    registration_model: RegistrationModel = field(default_factory=RegistrationModel)
    local_concepts: list[LocalConceptDefinition] = field(default_factory=list)
    encounter_types: list[EncounterTypeDefinition] = field(default_factory=list)
    forms: list[FormDefinition] = field(default_factory=list)
    queues: list[QueueDefinition] = field(default_factory=list)
    queue_rooms: list[QueueRoomDefinition] = field(default_factory=list)
    routing_rules: list[RoutingRule] = field(default_factory=list)
    billing_model: BillingModel = field(default_factory=BillingModel)
    stock_pharmacy_model: StockPharmacyModel = field(default_factory=StockPharmacyModel)
    programs: list[ProgramDefinition] = field(default_factory=list)
    program_workflows: list[ProgramWorkflowDefinition] = field(default_factory=list)
    program_workflow_states: list[ProgramWorkflowStateDefinition] = field(default_factory=list)
    lab_model: LabModel = field(default_factory=LabModel)
    imaging_model: ImagingModel = field(default_factory=ImagingModel)
    identity_model: IdentityModel = field(default_factory=IdentityModel)
    governance: GovernanceModel = field(default_factory=GovernanceModel)
    archetype: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ClinicConfigModel":
        validate_config_payload(payload)
        return cls(
            schema_version=str(_get(payload, "schemaVersion", "schema_version", default="1.0") or "1.0"),
            facility_profile=FacilityProfile.from_dict(_get(payload, "facilityProfile", "facility_profile", default={})),
            location_tags=_load_many(_get(payload, "locationTags", "location_tags", default=[]), LocationTagDefinition.from_dict),
            locations=_load_many(_get(payload, "locations", default=[]), LocationDefinition.from_dict),
            staffing_model=StaffingModel.from_dict(_get(payload, "staffingModel", "staffing_model", default={})),
            registration_model=RegistrationModel.from_dict(
                _get(payload, "registrationModel", "registration_model", default={})
            ),
            local_concepts=_load_many(_get(payload, "localConcepts", "local_concepts", default=[]), LocalConceptDefinition.from_dict),
            encounter_types=_load_many(_get(payload, "encounterTypes", "encounter_types", default=[]), EncounterTypeDefinition.from_dict),
            forms=_load_many(_get(payload, "forms", default=[]), FormDefinition.from_dict),
            queues=_load_many(_get(payload, "queues", default=[]), QueueDefinition.from_dict),
            queue_rooms=_load_many(_get(payload, "queueRooms", "queue_rooms", default=[]), QueueRoomDefinition.from_dict),
            routing_rules=_load_many(_get(payload, "routingRules", "routing_rules", default=[]), RoutingRule.from_dict),
            billing_model=BillingModel.from_dict(_get(payload, "billingModel", "billing_model", default={})),
            stock_pharmacy_model=StockPharmacyModel.from_dict(
                _get(payload, "stockPharmacyModel", "stock_pharmacy_model", default={})
            ),
            programs=_load_many(_get(payload, "programs", default=[]), ProgramDefinition.from_dict),
            program_workflows=_load_many(
                _get(payload, "programWorkflows", "program_workflows", default=[]), ProgramWorkflowDefinition.from_dict
            ),
            program_workflow_states=_load_many(
                _get(payload, "programWorkflowStates", "program_workflow_states", default=[]),
                ProgramWorkflowStateDefinition.from_dict,
            ),
            lab_model=LabModel.from_dict(_get(payload, "labModel", "lab_model", default={})),
            imaging_model=ImagingModel.from_dict(_get(payload, "imagingModel", "imaging_model", default={})),
            identity_model=IdentityModel.from_dict(_get(payload, "identityModel", "identity_model", default={})),
            governance=GovernanceModel.from_dict(_get(payload, "governance", default={})),
            archetype=str(_get(payload, "archetype", default="") or ""),
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "ClinicConfigModel":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("Clinic configuration file must contain a JSON object.")
        return cls.from_dict(payload)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write_json(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
