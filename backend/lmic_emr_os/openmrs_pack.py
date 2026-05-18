from __future__ import annotations

import csv
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .config_model import ClinicConfigModel, FormDefinition, FormQuestion
from .validation import ConceptResolver, validate_clinic_config


SLUG_RE = re.compile(r"[^a-z0-9]+")
MANAGED_QUEUE_SERVICE_SET_ID = "_tenaos-managed-queue-service-set"
MANAGED_QUEUE_SERVICE_SET_FSN = "TenaOS Managed Queue Service Set"
MANAGED_QUEUE_SERVICE_SET_SHORT_NAME = "Queue Services"
LEGACY_SHARED_QUEUE_CONCEPT_UUIDS = {
    MANAGED_QUEUE_SERVICE_SET_ID: "23f452ce-ae99-5906-a67d-5dd57f48b3e3",
    "registration-queue-service": "6ecc3405-566e-57e6-a60d-6ebdcbc650cb",
    "triage-queue-service": "06b1e90b-d2dc-5d92-8c99-4ec6639b9229",
    "consultation-queue-service": "b2f1eea1-0deb-5756-8356-1a114e6f29ed",
    "laboratory-queue-service": "839f7663-acec-5f5e-91cd-48068e7cc118",
    "imaging-queue-service": "5c33a905-4871-57cf-8ff5-7464ffbdb9da",
    "cashier-queue-service": "0d62a9d7-20a2-55ff-a08f-201ce5a27cbc",
    "pharmacy-queue-service": "736157d9-d1ae-52b5-9222-679425401c71",
    "antenatal-queue-service": "a4fc87c0-5f8e-53ea-bb06-b6a461c94098",
    "queue-status-waiting": "ceebd123-e1ac-5235-b262-819a5a17b8f4",
    "queue-status-in-progress": "3ca3cdf0-6f43-593a-921a-e8fa2778f7e7",
    "queue-status-completed": "d8794507-bad1-5049-bf78-69823cc720bf",
    "queue-status-set": "e6c585a2-322c-5716-87bf-34c117aea7a3",
    "queue-priority-routine": "fe6772b3-dd0a-5354-be66-7746a041b92e",
    "queue-priority-urgent": "22d26bfe-08b3-5281-89de-199cca789a02",
    "queue-priority-emergency": "0aadaed5-ff36-5aab-8f3c-508a28834ede",
    "queue-priority-set": "55984807-0428-5236-b31b-1e7ef440e424",
}


def _slugify(value: str) -> str:
    slug = SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return slug or "item"


def _stable_uuid(facility_code: str, domain: str, key: str) -> str:
    try:
        return str(uuid.UUID(key))
    except (ValueError, TypeError, AttributeError):
        pass
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"lmic-emr-os:{facility_code}:{domain}:{key}"))


def _stable_local_concept_uuid(facility_code: str, key: str) -> str:
    if key in LEGACY_SHARED_QUEUE_CONCEPT_UUIDS:
        return LEGACY_SHARED_QUEUE_CONCEPT_UUIDS[key]
    return _stable_uuid(facility_code, "concept", key)


def _local_concept_id(ref: str) -> str:
    normalized = str(ref or "").strip()
    if normalized.upper().startswith("LOCAL:"):
        return normalized.split(":", 1)[1].strip()
    return ""


@dataclass(slots=True)
class CompilationResult:
    output_dir: str
    written_files: list[str] = field(default_factory=list)
    deferred_manifests: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class OpenMRSPackCompiler:
    def __init__(
        self,
        *,
        include_html_forms: bool = False,
        concept_resolver: ConceptResolver | None = None,
    ) -> None:
        self.include_html_forms = include_html_forms
        self.concept_resolver = concept_resolver

    def compile(self, config: ClinicConfigModel, output_dir: str | Path) -> CompilationResult:
        report = validate_clinic_config(config, concept_resolver=self.concept_resolver)
        if report.errors:
            problems = "\n".join(f"- {issue.path}: {issue.message}" for issue in report.errors)
            raise ValueError(f"Clinic configuration is invalid:\n{problems}")

        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        result = CompilationResult(output_dir=str(target_dir))

        facility_code = config.facility_profile.code or "main"
        location_uuid = {
            location.id: _stable_uuid(facility_code, "location", location.id)
            for location in config.locations
        }
        encounter_uuid = {
            encounter.id: _stable_uuid(facility_code, "encounter", encounter.id)
            for encounter in config.encounter_types
        }
        program_uuid = {
            program.id: _stable_uuid(facility_code, "program", program.id)
            for program in config.programs
        }
        workflow_uuid = {
            workflow.id: _stable_uuid(facility_code, "program-workflow", workflow.id)
            for workflow in config.program_workflows
        }
        local_concept_uuid = {
            concept.id: _stable_local_concept_uuid(facility_code, concept.id) for concept in config.local_concepts if concept.id
        }

        location_name_by_id = {location.id: location.name for location in config.locations}
        encounter_name_by_id = {encounter.id: encounter.name for encounter in config.encounter_types}

        self._write_locations(config, target_dir, result, location_uuid, location_name_by_id)
        self._write_local_concepts(config, target_dir, result, local_concept_uuid)
        self._write_encounter_types(config, target_dir, result, encounter_uuid)
        self._write_global_properties(config, target_dir, result, location_uuid, local_concept_uuid)
        self._write_forms(
            config,
            target_dir,
            result,
            encounter_name_by_id,
            encounter_uuid,
            facility_code,
            local_concept_uuid,
        )
        self._write_idgen(config, target_dir, result, facility_code)
        self._write_programs(config, target_dir, result, program_uuid, local_concept_uuid)
        self._write_program_workflows(config, target_dir, result, program_uuid, workflow_uuid, local_concept_uuid)
        self._write_program_workflow_states(
            config,
            target_dir,
            result,
            workflow_uuid,
            facility_code,
            local_concept_uuid,
        )
        self._write_billing(config, target_dir, result, location_name_by_id, facility_code, local_concept_uuid)
        self._write_extension_manifests(
            config,
            target_dir,
            result,
            location_uuid,
            encounter_uuid,
            local_concept_uuid,
        )
        self._write_manifest(
            config,
            target_dir,
            result,
            location_uuid,
            encounter_uuid,
            program_uuid,
            workflow_uuid,
            local_concept_uuid,
        )
        return result

    def _write_csv(self, path: Path, headers: list[str], rows: list[list[Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            writer.writerows(rows)

    def _write_locations(
        self,
        config: ClinicConfigModel,
        output_dir: Path,
        result: CompilationResult,
        location_uuid: dict[str, str],
        location_name_by_id: dict[str, str],
    ) -> None:
        if not config.locations:
            return

        headers = [
            "Uuid",
            "Void/Retire",
            "Name",
            "Description",
            "Parent",
            "Tag|Login Location",
            "Tag|Facility Location",
            "Address 1",
            "Address 2",
            "Address 3",
            "Address 4",
            "Address 5",
            "Address 6",
            "City/Village",
            "County/District",
            "State/Province",
            "Postal Code",
            "Country",
            "Tags",
        ]
        rows: list[list[Any]] = []
        for location in config.locations:
            tags = list(dict.fromkeys(location.tags))
            if location.login_location and "Login Location" not in tags:
                tags.append("Login Location")
            if location.facility_location and "Facility Location" not in tags:
                tags.append("Facility Location")
            rows.append(
                [
                    location_uuid[location.id],
                    "",
                    location.name,
                    location.description,
                    location_name_by_id.get(location.parent_id, ""),
                    "TRUE" if location.login_location else "",
                    "TRUE" if location.facility_location else "",
                    location.address.address1,
                    location.address.address2,
                    location.address.address3,
                    location.address.address4,
                    location.address.address5,
                    location.address.address6,
                    location.address.city_village,
                    location.address.county_district,
                    location.address.state_province,
                    location.address.postal_code,
                    location.address.country or config.facility_profile.country,
                    "; ".join(tags),
                ]
            )
        path = output_dir / "locations" / "locations.csv"
        self._write_csv(path, headers, rows)
        result.written_files.append(str(path))

    def _resolve_concept_reference(self, ref: str, local_concept_uuid: dict[str, str]) -> str:
        local_id = _local_concept_id(ref)
        if not local_id:
            return ref
        return local_concept_uuid.get(local_id, ref)

    def _render_concept_list(self, refs: list[str], local_concept_uuid: dict[str, str]) -> str:
        resolved = [self._resolve_concept_reference(ref, local_concept_uuid) for ref in refs if ref]
        return "; ".join(ref for ref in resolved if ref)

    def _ordered_local_concepts(self, config: ClinicConfigModel) -> list[Any]:
        concepts_by_id = {concept.id: concept for concept in config.local_concepts if concept.id}
        ordered: list[Any] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(concept_id: str) -> None:
            if concept_id in visited or concept_id not in concepts_by_id:
                return
            if concept_id in visiting:
                ordered.append(concepts_by_id[concept_id])
                visited.add(concept_id)
                return
            visiting.add(concept_id)
            concept = concepts_by_id[concept_id]
            for ref in [*concept.answers, *concept.members]:
                nested_local_id = _local_concept_id(ref)
                if nested_local_id:
                    visit(nested_local_id)
            visiting.remove(concept_id)
            if concept_id not in visited:
                ordered.append(concept)
                visited.add(concept_id)

        for concept in config.local_concepts:
            if concept.id:
                visit(concept.id)
            elif concept not in ordered:
                ordered.append(concept)
        return ordered

    def _queue_service_concept_members(
        self,
        config: ClinicConfigModel,
        local_concept_uuid: dict[str, str],
    ) -> list[str]:
        members: list[str] = []
        seen: set[str] = set()
        for queue in config.queues:
            resolved = self._resolve_concept_reference(queue.service_concept, local_concept_uuid)
            if resolved and resolved not in seen:
                seen.add(resolved)
                members.append(resolved)
        return members

    def _queue_service_concept_set_uuid(
        self,
        config: ClinicConfigModel,
        local_concept_uuid: dict[str, str],
    ) -> str:
        if not self._queue_service_concept_members(config, local_concept_uuid):
            return ""
        if MANAGED_QUEUE_SERVICE_SET_ID in local_concept_uuid:
            return local_concept_uuid[MANAGED_QUEUE_SERVICE_SET_ID]
        return _stable_local_concept_uuid(config.facility_profile.code or "main", MANAGED_QUEUE_SERVICE_SET_ID)

    def _write_local_concepts(
        self,
        config: ClinicConfigModel,
        output_dir: Path,
        result: CompilationResult,
        local_concept_uuid: dict[str, str],
    ) -> None:
        queue_service_members = self._queue_service_concept_members(config, local_concept_uuid)
        if not config.local_concepts and not queue_service_members:
            return
        locale = config.facility_profile.languages[0] if config.facility_profile.languages else "en"
        headers = [
            "Uuid",
            "Void/Retire",
            f"Fully specified name:{locale}",
            f"Short name:{locale}",
            f"Description:{locale}",
            "Data class",
            "Data type",
            "Version",
            "Mappings|SAME-AS",
            "Answers",
            "Members",
            "_version:1",
            "_order:1000",
        ]
        rows: list[list[Any]] = []
        for index, concept in enumerate(self._ordered_local_concepts(config)):
            rows.append(
                [
                    local_concept_uuid.get(concept.id, _stable_local_concept_uuid(config.facility_profile.code or "main", concept.id)),
                    "",
                    concept.fully_specified_name,
                    concept.short_name,
                    concept.description,
                    concept.data_class,
                    concept.data_type,
                    concept.version,
                    "; ".join(concept.same_as_mappings),
                    self._render_concept_list(concept.answers, local_concept_uuid),
                    self._render_concept_list(concept.members, local_concept_uuid),
                    "",
                    index + 1,
                ]
            )
        if queue_service_members and MANAGED_QUEUE_SERVICE_SET_ID not in local_concept_uuid:
            rows.append(
                [
                    self._queue_service_concept_set_uuid(config, local_concept_uuid),
                    "",
                    MANAGED_QUEUE_SERVICE_SET_FSN,
                    MANAGED_QUEUE_SERVICE_SET_SHORT_NAME,
                    "Managed queue service concept set for Queue module validation.",
                    "ConvSet",
                    "N/A",
                    "",
                    "",
                    "",
                    "; ".join(queue_service_members),
                    "",
                    len(rows) + 1,
                ]
            )
        path = output_dir / "concepts" / "local-concepts.csv"
        self._write_csv(path, headers, rows)
        result.written_files.append(str(path))

    def _write_encounter_types(
        self,
        config: ClinicConfigModel,
        output_dir: Path,
        result: CompilationResult,
        encounter_uuid: dict[str, str],
    ) -> None:
        if not config.encounter_types:
            return
        headers = [
            "Uuid",
            "Void/Retire",
            "Name",
            "Description",
            "View privilege",
            "Edit privilege",
            "_order:100",
        ]
        rows = [
            [
                encounter_uuid[encounter.id],
                "",
                encounter.name,
                encounter.description,
                encounter.view_privilege,
                encounter.edit_privilege,
                index + 1,
            ]
            for index, encounter in enumerate(config.encounter_types)
        ]
        path = output_dir / "encountertypes" / "encountertypes.csv"
        self._write_csv(path, headers, rows)
        result.written_files.append(str(path))

    def _write_global_properties(
        self,
        config: ClinicConfigModel,
        output_dir: Path,
        result: CompilationResult,
        location_uuid: dict[str, str],
        local_concept_uuid: dict[str, str],
    ) -> None:
        config_root = ET.Element("config")
        global_properties = ET.SubElement(config_root, "globalProperties")

        def append_property(name: str, value: str) -> None:
            entry = ET.SubElement(global_properties, "globalProperty")
            ET.SubElement(entry, "property").text = name
            ET.SubElement(entry, "value").text = value

        default_locale = config.facility_profile.languages[0] if config.facility_profile.languages else "en"
        append_property("locale.allowed.list", ", ".join(config.facility_profile.languages or ["en"]))
        append_property("default_locale", default_locale)
        append_property("idgen.autogenerationEnabled", "true")

        login_location_id = config.registration_model.login_location_id
        if not login_location_id:
            for location in config.locations:
                if location.login_location:
                    login_location_id = location.id
                    break
        if login_location_id and login_location_id in location_uuid:
            append_property("emr.loginLocation", location_uuid[login_location_id])

        for key, value in sorted(config.billing_model.global_properties.items()):
            append_property(key, value)

        queue_service_set_uuid = self._queue_service_concept_set_uuid(config, local_concept_uuid)
        if queue_service_set_uuid:
            append_property("queue.serviceConceptSetName", queue_service_set_uuid)

        path = output_dir / "globalproperties" / "settings.xml"
        path.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(config_root).write(path, encoding="utf-8", xml_declaration=False)
        result.written_files.append(str(path))

    def _render_form_json(
        self,
        form: FormDefinition,
        local_concept_uuid: dict[str, str],
        encounter_name_by_id: dict[str, str],
    ) -> dict[str, Any]:
        pages: list[dict[str, Any]] = []
        for page in form.pages:
            sections: list[dict[str, Any]] = []
            for section in page.sections:
                questions: list[dict[str, Any]] = []
                for question in section.questions:
                    question_options: dict[str, Any] = {"rendering": question.rendering}
                    if question.concept:
                        question_options["concept"] = self._resolve_concept_reference(question.concept, local_concept_uuid)
                    if question.min_value:
                        question_options["min"] = question.min_value
                    if question.max_value:
                        question_options["max"] = question.max_value
                    if question.show_date:
                        question_options["showDate"] = True
                    if question.answers:
                        question_options["answers"] = [
                            {
                                "concept": self._resolve_concept_reference(answer.concept, local_concept_uuid),
                                "label": answer.label,
                            }
                            for answer in question.answers
                        ]
                    questions.append(
                        {
                            "label": question.label,
                            "type": question.question_type,
                            "id": question.id,
                            "required": question.required,
                            "questionOptions": question_options,
                        }
                    )
                sections.append(
                    {
                        "label": section.label,
                        "isExpanded": "true" if section.is_expanded else "false",
                        "questions": questions,
                    }
                )
            pages.append({"label": page.label, "sections": sections})
        return {
            "name": form.name,
            "description": form.description,
            "version": "1.0",
            "published": form.published,
            "retired": form.retired,
            "encounter": encounter_name_by_id.get(form.encounter, form.encounter),
            "pages": pages,
            "processor": form.processor,
            "referencedForms": form.referenced_forms,
        }

    def _render_obs_fragment(self, question: FormQuestion, local_concept_uuid: dict[str, str]) -> str:
        concept_id = self._resolve_concept_reference(question.concept, local_concept_uuid) if question.concept else ""
        if question.question_type != "obs":
            return f"<!-- Unsupported HTML Form Entry question type: {question.question_type} ({question.label}) -->"
        return f"{question.label}: <obs conceptId=\"{concept_id}\"/>"

    def _render_html_form_xml(
        self,
        form: FormDefinition,
        encounter_uuid: dict[str, str],
        facility_code: str,
        local_concept_uuid: dict[str, str],
    ) -> str:
        form_uuid = _stable_uuid(facility_code, "form", form.id)
        htmlform_uuid = _stable_uuid(facility_code, "htmlform", form.id)
        encounter_key = form.encounter
        encounter_ref = encounter_uuid.get(encounter_key, encounter_key)
        lines = [
            "<htmlform",
            f'        formUuid="{form_uuid}"',
            f'        formName="{form.name}"',
            f'        formDescription="{form.description}"',
            '        formVersion="1.0"',
            f'        formPublished="{"true" if form.published else "false"}"',
            f'        formRetired="{"true" if form.retired else "false"}"',
            f'        formEncounterType="{encounter_ref}"',
            f'        htmlformUuid="{htmlform_uuid}"',
            ">",
            "    Date: <encounterDate/>",
            "    Location: <encounterLocation/>",
            "    Provider: <encounterProvider role=\"Provider\"/>",
        ]
        for page in form.pages:
            lines.append(f"    <!-- {page.label} -->")
            for section in page.sections:
                lines.append(f"    <h3>{section.label}</h3>")
                for question in section.questions:
                    lines.append(f"    <p>{self._render_obs_fragment(question, local_concept_uuid)}</p>")
        lines.append("    <submit/>")
        lines.append("</htmlform>")
        return "\n".join(lines) + "\n"

    def _write_forms(
        self,
        config: ClinicConfigModel,
        output_dir: Path,
        result: CompilationResult,
        encounter_name_by_id: dict[str, str],
        encounter_uuid: dict[str, str],
        facility_code: str,
        local_concept_uuid: dict[str, str],
    ) -> None:
        if not config.forms:
            return

        for form in config.forms:
            json_path = output_dir / "ampathforms" / f"{_slugify(form.id or form.name)}.json"
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(
                json.dumps(
                    self._render_form_json(form, local_concept_uuid, encounter_name_by_id),
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            result.written_files.append(str(json_path))

            if self.include_html_forms:
                xml_path = output_dir / "htmlforms" / f"{_slugify(form.id or form.name)}.xml"
                xml_path.parent.mkdir(parents=True, exist_ok=True)
                xml_path.write_text(
                    self._render_html_form_xml(form, encounter_uuid, facility_code, local_concept_uuid),
                    encoding="utf-8",
                )
                result.written_files.append(str(xml_path))

    def _write_idgen(
        self,
        config: ClinicConfigModel,
        output_dir: Path,
        result: CompilationResult,
        facility_code: str,
    ) -> None:
        strategy = config.registration_model.identifier_strategy
        path = output_dir / "idgen" / "idgen.csv"
        generator_uuid = _stable_uuid(facility_code, "idgen", "generator")
        pool_uuid = _stable_uuid(facility_code, "idgen", "pool")
        content = "\n".join(
            [
                "# Sequential identifier generators and pools",
                "",
                "Uuid,Name,Description,Identifier type,Base character set,First identifier base,Prefix,Suffix,Min length,Max length,_order:100",
                f'{generator_uuid},"{config.facility_profile.name} ID Generator","Primary ID generator","{strategy.identifier_type}","{strategy.base_character_set}","{strategy.first_identifier_base}","{strategy.prefix}","{strategy.suffix}",{strategy.min_length},{strategy.max_length},',
                "",
                "Uuid,Name,Identifier type,Pool identifier source,Pool refill batch size,Pool minimum size,Pool refill with task,Pool sequential allocation,_order:300",
                f'{pool_uuid},"{config.facility_profile.name} ID Pool","{strategy.identifier_type}","{generator_uuid}",{strategy.pool_refill_batch_size},{strategy.pool_minimum_size},{"true" if strategy.pool_refill_with_task else "false"},{"true" if strategy.pool_sequential_allocation else "false"},',
                "",
            ]
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        result.written_files.append(str(path))

    def _write_programs(
        self,
        config: ClinicConfigModel,
        output_dir: Path,
        result: CompilationResult,
        program_uuid: dict[str, str],
        local_concept_uuid: dict[str, str],
    ) -> None:
        if not config.programs:
            return
        headers = ["Uuid", "Void/Retire", "Program concept", "Outcomes concept", "_order:1000"]
        rows = [
            [
                program_uuid[program.id],
                "",
                self._resolve_concept_reference(program.program_concept, local_concept_uuid),
                self._resolve_concept_reference(program.outcomes_concept, local_concept_uuid),
                index + 1,
            ]
            for index, program in enumerate(config.programs)
        ]
        path = output_dir / "programs" / "programs.csv"
        self._write_csv(path, headers, rows)
        result.written_files.append(str(path))

    def _write_program_workflows(
        self,
        config: ClinicConfigModel,
        output_dir: Path,
        result: CompilationResult,
        program_uuid: dict[str, str],
        workflow_uuid: dict[str, str],
        local_concept_uuid: dict[str, str],
    ) -> None:
        if not config.program_workflows:
            return
        headers = ["Uuid", "Void/Retire", "Program", "Workflow concept", "_order:1000"]
        rows = [
            [
                workflow_uuid[workflow.id],
                "",
                program_uuid.get(workflow.program_id, workflow.program_id),
                self._resolve_concept_reference(workflow.workflow_concept, local_concept_uuid),
                index + 1,
            ]
            for index, workflow in enumerate(config.program_workflows)
        ]
        path = output_dir / "programworkflows" / "workflows.csv"
        self._write_csv(path, headers, rows)
        result.written_files.append(str(path))

    def _write_program_workflow_states(
        self,
        config: ClinicConfigModel,
        output_dir: Path,
        result: CompilationResult,
        workflow_uuid: dict[str, str],
        facility_code: str,
        local_concept_uuid: dict[str, str],
    ) -> None:
        if not config.program_workflow_states:
            return
        headers = ["Uuid", "Void/Retire", "Workflow", "State concept", "Initial", "Terminal"]
        rows = [
            [
                _stable_uuid(facility_code, "program-workflow-state", state.id),
                "",
                workflow_uuid.get(state.workflow_id, state.workflow_id),
                self._resolve_concept_reference(state.state_concept, local_concept_uuid),
                "true" if state.initial else "",
                "true" if state.terminal else "",
            ]
            for state in config.program_workflow_states
        ]
        path = output_dir / "programworkflowstates" / "states.csv"
        self._write_csv(path, headers, rows)
        result.written_files.append(str(path))

    def _format_payment_mode_attributes(self, attributes: list[Any]) -> str:
        encoded: list[str] = []
        for attribute in attributes:
            fields = [
                attribute.name,
                attribute.format,
                attribute.regex,
                "True" if attribute.required else "",
            ]
            while fields and not fields[-1]:
                fields.pop()
            encoded.append("::".join(fields))
        return ";".join(encoded)

    def _write_billing(
        self,
        config: ClinicConfigModel,
        output_dir: Path,
        result: CompilationResult,
        location_name_by_id: dict[str, str],
        facility_code: str,
        local_concept_uuid: dict[str, str],
    ) -> None:
        if config.billing_model.billable_services:
            billable_headers = [
                "Uuid",
                "Void/Retire",
                "Service Name",
                "Short Name",
                "Concept",
                "Service Type",
                "Service Status",
            ]
            billable_rows = [
                [
                    _stable_uuid(facility_code, "billable-service", service.id or service.service_name),
                    "",
                    service.service_name,
                    service.short_name,
                    self._resolve_concept_reference(service.concept, local_concept_uuid),
                    self._resolve_concept_reference(service.service_type, local_concept_uuid),
                    service.service_status,
                ]
                for service in config.billing_model.billable_services
            ]
            path = output_dir / "billableservices" / "billableServices.csv"
            self._write_csv(path, billable_headers, billable_rows)
            result.written_files.append(str(path))

        if config.billing_model.payment_modes:
            payment_headers = ["uuid", "Void/Retire", "name", "attributes"]
            payment_rows = [
                [
                    _stable_uuid(facility_code, "payment-mode", mode.id or mode.name),
                    "",
                    mode.name,
                    self._format_payment_mode_attributes(mode.attributes),
                ]
                for mode in config.billing_model.payment_modes
            ]
            path = output_dir / "paymentmodes" / "paymentModes.csv"
            self._write_csv(path, payment_headers, payment_rows)
            result.written_files.append(str(path))

        if config.billing_model.cash_points:
            cashpoint_headers = ["uuid", "Void/Retire", "name", "description", "location"]
            cashpoint_rows = [
                [
                    _stable_uuid(facility_code, "cash-point", cash_point.id or cash_point.name),
                    "",
                    cash_point.name,
                    cash_point.description,
                    location_name_by_id.get(cash_point.location_id, cash_point.location_id),
                ]
                for cash_point in config.billing_model.cash_points
            ]
            path = output_dir / "cashpoints" / "cashPoints.csv"
            self._write_csv(path, cashpoint_headers, cashpoint_rows)
            result.written_files.append(str(path))

    def _write_extension_manifests(
        self,
        config: ClinicConfigModel,
        output_dir: Path,
        result: CompilationResult,
        location_uuid: dict[str, str],
        encounter_uuid: dict[str, str],
        local_concept_uuid: dict[str, str],
    ) -> None:
        extensions_dir = output_dir / "extensions"
        extensions_dir.mkdir(parents=True, exist_ok=True)

        if config.queues or config.queue_rooms or config.routing_rules:
            queues = []
            for queue in config.queues:
                queue_payload = asdict(queue)
                queue_payload["service_concept"] = self._resolve_concept_reference(
                    queue.service_concept,
                    local_concept_uuid,
                )
                queue_payload["status_concept_set"] = self._resolve_concept_reference(
                    queue.status_concept_set,
                    local_concept_uuid,
                )
                queue_payload["priority_concept_set"] = self._resolve_concept_reference(
                    queue.priority_concept_set,
                    local_concept_uuid,
                )
                queue_payload["allowed_statuses"] = [
                    self._resolve_concept_reference(value, local_concept_uuid) for value in queue.allowed_statuses
                ]
                queue_payload["allowed_priorities"] = [
                    self._resolve_concept_reference(value, local_concept_uuid) for value in queue.allowed_priorities
                ]
                queues.append(queue_payload)
            queue_manifest = {
                "contract": {
                    "contractId": "openmrs-routing-policy-v1",
                    "owner": "lmic-emr-os",
                    "mode": "adapter-owned",
                    "nativePersistence": False,
                    "runtimeConsumer": "BundleApplier._apply_openmrs_extensions",
                },
                "notes": [
                    "Queue/routing configuration is emitted as a bounded extension manifest because queue setup is module-driven, not a standard Initializer domain.",
                    "Apply through Queue module admin/API surfaces after metadata pack load.",
                ],
                "queues": queues,
                "queueRooms": [asdict(room) for room in config.queue_rooms],
                "routingRules": [asdict(rule) for rule in config.routing_rules],
                "locationUuidMap": location_uuid,
            }
            path = extensions_dir / "queue-routing.json"
            path.write_text(json.dumps(queue_manifest, indent=2) + "\n", encoding="utf-8")
            result.deferred_manifests.append(str(path))

        if config.billing_model.pricing_rules:
            pricing_manifest = {
                "notes": [
                    "Pricing rules are emitted separately because the billing module exposes supported metadata domains for services, payment modes, and cash points, but price schedules need a bounded runtime adapter.",
                ],
                "billableServices": [
                    {
                        **asdict(item),
                        "concept": self._resolve_concept_reference(item.concept, local_concept_uuid),
                        "service_type": self._resolve_concept_reference(item.service_type, local_concept_uuid),
                    }
                    for item in config.billing_model.billable_services
                ],
                "paymentModes": [asdict(item) for item in config.billing_model.payment_modes],
                "pricingRules": [asdict(rule) for rule in config.billing_model.pricing_rules],
            }
            path = extensions_dir / "billing-pricing.json"
            path.write_text(json.dumps(pricing_manifest, indent=2) + "\n", encoding="utf-8")
            result.deferred_manifests.append(str(path))

        if (
            config.stock_pharmacy_model.stock_locations
            or config.stock_pharmacy_model.operation_types
            or config.stock_pharmacy_model.rules
        ):
            stock_manifest = {
                "notes": [
                    "Stock/pharmacy configuration is emitted as a bounded extension manifest for stockmanagement service-layer adapters.",
                ],
                "stockLocations": [asdict(item) for item in config.stock_pharmacy_model.stock_locations],
                "operationTypes": [asdict(item) for item in config.stock_pharmacy_model.operation_types],
                "rules": [asdict(item) for item in config.stock_pharmacy_model.rules],
                "locationUuidMap": location_uuid,
            }
            path = extensions_dir / "stock-pharmacy.json"
            path.write_text(json.dumps(stock_manifest, indent=2) + "\n", encoding="utf-8")
            result.deferred_manifests.append(str(path))

        if config.identity_model.roles or config.identity_model.users:
            identity_manifest = {
                "notes": [
                    "Identity user and role configuration should be applied through bounded Keycloak/OpenMRS admin adapters, not by direct database mutation.",
                ],
                "roles": [asdict(role) for role in config.identity_model.roles],
                "users": [asdict(user) for user in config.identity_model.users],
                "encounterTypeUuidMap": encounter_uuid,
            }
            path = extensions_dir / "identity.json"
            path.write_text(json.dumps(identity_manifest, indent=2) + "\n", encoding="utf-8")
            result.deferred_manifests.append(str(path))

    def _write_manifest(
        self,
        config: ClinicConfigModel,
        output_dir: Path,
        result: CompilationResult,
        location_uuid: dict[str, str],
        encounter_uuid: dict[str, str],
        program_uuid: dict[str, str],
        workflow_uuid: dict[str, str],
        local_concept_uuid: dict[str, str],
    ) -> None:
        manifest = {
            "facility": {
                "code": config.facility_profile.code,
                "name": config.facility_profile.name,
                "archetype": config.archetype,
            },
            "writtenFiles": result.written_files,
            "deferredManifests": result.deferred_manifests,
            "maps": {
                "locations": location_uuid,
                "encounterTypes": encounter_uuid,
                "programs": program_uuid,
                "programWorkflows": workflow_uuid,
                "localConcepts": local_concept_uuid,
            },
        }
        path = output_dir / "manifest.json"
        path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        result.written_files.append(str(path))
