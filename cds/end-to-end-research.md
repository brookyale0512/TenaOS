# Gemma 4 + WHO DAK for OpenMRS Clinical Decision Support

Date: 2026-05-10

## Executive conclusion

The proposed system is feasible, but only if Gemma 4 is used as an orchestrator, interface, summarizer, and explanation layer around deterministic guideline execution. It should not be the primary clinical reasoning engine.

The reliable source of clinical truth should be the WHO SMART Guidelines/DAK artifacts, preferably the L3 FHIR Implementation Guide artifacts: PlanDefinition, ActivityDefinition, Questionnaire, ValueSet, CodeSystem, StructureMap, Library, and CQL. Gemma 4's function calling can route patient context to tools that retrieve, validate, and execute these artifacts, then generate clinician-readable CDS output with citations and traceability.

This differs materially from the current intended use of a DAK. A WHO DAK is not primarily an LLM knowledge base. It is a structured, standards-based pathway for transforming WHO guidance into locally adaptable, testable, machine-readable artifacts that digital health systems can implement. In the proposed architecture, Gemma 4 uses the DAK through tools. It does not replace the DAK pipeline, the CQL engine, the FHIR server, test cases, or local clinical governance.

## Source-grounded findings

### Gemma 4 status and capabilities

Google's Gemma release notes list Gemma 4 as released on 2026-03-31, with Gemma 4 MTP released on 2026-04-16. The Gemma 4 overview describes E2B, E4B, 31B dense, and 26B A4B mixture-of-experts variants.

Official Gemma 4 docs describe built-in function-calling support, configurable thinking modes, native system prompt support, multimodal inputs, and 128K to 256K context windows depending on model size. The docs are explicit that Gemma does not execute tools or code by itself: the application must parse the model's tool call, validate the function name and arguments, run the tool, and return the tool response to the model.

Important implication: Gemma 4 can choose and parameterize calls like `apply_plan_definition`, `evaluate_cql`, or `lookup_dak_artifact`, but the CDS platform must own execution, authorization, validation, logging, and safety controls.

### FunctionGemma and MedGemma are adjacent but distinct

FunctionGemma is a specialized Gemma 3 270M model tuned for function calling. It is useful evidence that Google supports local, private tool-use workflows, but it is not the same as Gemma 4 and is not a medical model.

MedGemma is built on Gemma 3 and is Google/Health's medical model family. Google describes MedGemma as useful for medical text comprehension, EHR understanding, triage, summarization, and clinical decision support prototyping, but it requires validation before production. For this project:

- Gemma 4 is attractive for agentic orchestration and tool calling.
- MedGemma may be useful for medical language understanding and explanation quality.
- Neither should be treated as a validated clinical authority without domain-specific evaluation.

### WHO DAK / SMART Guidelines intended use

WHO SMART Guidelines use layered knowledge representation.

- L2 DAK: business requirements and operational content. It includes narrative guidance, personas, user scenarios, BPMN business processes, data dictionaries, decision tables, DMN files, scheduling logic, indicators, and system requirements.
- L3 FHIR Implementation Guide: machine-readable technical artifacts derived from L2. L3 maps DAK content into FHIR resources such as PlanDefinition, ActivityDefinition, Questionnaire, StructureMap, ValueSet, CodeSystem, ConceptMap, Library, Measure, TestPlan, and TestScript.
- L4: executable reference applications and services that accurately represent L1-L3 requirements and can be localized to country context.

WHO's L3 authoring guidance says decision tables become PlanDefinitions and CQL libraries, and every decision/scheduling artifact should have CQL expressions and test cases. This means the DAK's intended computable use is deterministic guideline execution and conformance testing, not open-ended narrative retrieval.

### OpenMRS integration surface

OpenMRS FHIR2 exposes FHIR endpoints under `/ws/fhir2/{release}/`, currently R3 and R4. Patient, Observation, Encounter, MedicationRequest, ServiceRequest, AllergyIntolerance, and related resources can be queried through FHIR. OpenMRS maps Patient objects to FHIR Patient, Obs to FHIR Observation, and encounters/visits to FHIR Encounter.

OpenMRS community work has already explored SMART Guidelines using Open Concept Lab for WHO code mapping, OpenMRS Form Builder for workflow triggers, CQL engines and FHIR IGs with PlanDefinitions for rule execution, and Patient Flag/FHIR modules for surfacing results.

### CDS delivery standard

HL7 CDS Hooks v2.0.1 defines a workflow-triggered REST pattern for EHR-integrated CDS. A CDS Client calls a CDS Service at a hook such as `patient-view`, `order-select`, or `order-sign`, passing context, optional prefetch FHIR resources, and FHIR server authorization. The CDS Service returns cards with information, suggestions, links, or SMART app launches.

This is a better outer integration contract than inventing a custom chat-only workflow. Gemma can operate inside the CDS Service, but the clinician-facing EHR integration should still use CDS Hooks cards or OpenMRS-native flags/tasks.

## Recommended architecture

### Principle

Separate clinical truth from language generation.

The DAK/CQL/FHIR layer decides what is clinically recommended. Gemma 4 decides which tool to call, asks for missing data when needed, and writes an explanation that is constrained to the returned evidence.

### End-to-end flow

1. OpenMRS triggers CDS from a clinical workflow.
   - Initial hooks: `patient-view`, `encounter-start`, form submit, order selection, or order signing.
   - Standards-friendly option: CDS Hooks service.
   - OpenMRS-native option: Patient Flag/task/notification module.

2. CDS service collects patient context from OpenMRS FHIR.
   - Patient demographics.
   - Current encounter.
   - Relevant observations.
   - Conditions/diagnoses if available.
   - Medications/orders.
   - Immunizations, allergies, pregnancy status, labs, vitals, program enrollment depending on DAK domain.

3. Data normalization layer maps OpenMRS data to DAK expectations.
   - Terminology mapping through CIEL, LOINC, SNOMED CT, ICD, RxNorm, Open Concept Lab, or WHO CodeSystems.
   - FHIR version bridging where needed, because CDS Hooks and many clinical artifacts use FHIR R4, while the current WHO Starter Kit is based on FHIR R5 and some WHO IGs use R4.
   - Missing data detection.

4. Gemma 4 receives a constrained case summary and available tool schemas.
   - No raw unrestricted EHR dump unless necessary.
   - No PHI sent outside the local/private deployment boundary if privacy requires local inference.
   - System prompt should explicitly forbid unsupported clinical advice and require tool-backed recommendations.

5. Gemma 4 chooses tool calls.
   - `find_applicable_dak_guidelines(patient_context, workflow_context)`
   - `get_dak_artifact(guideline_id, artifact_type)`
   - `apply_plan_definition(plan_definition_url, patient_id, encounter_id, parameters)`
   - `evaluate_cql(library_url, expression, patient_bundle, parameters)`
   - `get_evidence_and_rationale(artifact_id)`
   - `detect_missing_required_data(plan_definition_url, patient_bundle)`

6. Deterministic services execute DAK logic.
   - FHIR server/knowledge repository stores PlanDefinitions, Libraries, ValueSets, and CodeSystems.
   - CQL engine evaluates expressions.
   - PlanDefinition `$apply` returns proposed actions, usually as Bundle/RequestOrchestration/CarePlan/CommunicationRequest/MedicationRequest/ServiceRequest depending on the artifact.

7. Gemma 4 generates clinician-facing CDS output from tool results only.
   - Recommendation.
   - Rationale.
   - Patient-specific facts used.
   - Guideline artifact/version.
   - Missing data warnings.
   - Confidence/validity status based on deterministic checks, not model self-confidence.
   - Suggested next action or "no recommendation" if criteria are not met.

8. CDS response is returned to OpenMRS.
   - CDS Hooks cards for standards-based integration.
   - Patient Flags/tasks/forms for OpenMRS-native integration.
   - Audit log stores input resource IDs, DAK artifact versions, CQL expressions evaluated, tool calls, outputs, and clinician action.

## Tool-use design

### Tools Gemma should be allowed to call

- `search_dak_catalog`: Finds candidate DAK/SMART guideline artifacts by clinical domain, workflow, age/sex/pregnancy constraints, country profile, and hook.
- `get_artifact_metadata`: Returns artifact title, canonical URL, version, status, publisher, jurisdiction, and related citations.
- `get_required_data_elements`: Lists required patient facts and FHIR search queries for a given rule or PlanDefinition.
- `fetch_openmrs_fhir_bundle`: Retrieves a narrowly scoped FHIR Bundle from OpenMRS.
- `normalize_patient_bundle`: Maps local OpenMRS concepts and resource shapes to DAK profiles/value sets.
- `evaluate_cql`: Evaluates a named CQL expression over a patient bundle.
- `apply_plan_definition`: Executes the PlanDefinition `$apply` operation.
- `format_cds_card`: Converts deterministic output into a CDS Hooks card or OpenMRS flag/task payload.
- `log_cds_trace`: Stores traceability data for audit and evaluation.

### Tools Gemma should not have

- Direct unrestricted database access.
- A generic shell/tool execution function.
- Ability to write orders, update patient data, or persist recommendations without a separate clinical action workflow.
- Ability to invent local policy overrides.
- Ability to silently ignore missing data.

## Reliability assessment

### What can be reliable

The system can be reliable if:

- The clinical recommendation comes from CQL/PlanDefinition execution.
- The patient data is normalized and validated before evaluation.
- Tool calls are schema-validated and allow-listed.
- The system returns no recommendation, or asks for missing facts, when required data is absent.
- Explanations are generated from deterministic tool outputs and cited DAK artifacts.
- Test cases cover each decision table row and local adaptation.

### What cannot be considered reliable by default

- Asking Gemma to read a DAK PDF/spreadsheet and independently reason to a clinical recommendation.
- Letting Gemma choose recommendations from its parametric medical knowledge.
- Treating model confidence as clinical confidence.
- Relying on prompt instructions alone for safety.
- Using a generic RAG answer as CDS without CQL/PlanDefinition validation.

### Main risks

- Wrong tool selection or wrong tool arguments.
- Terminology mismatch between OpenMRS concepts and DAK value sets.
- FHIR version mismatch between OpenMRS R4, CDS Hooks R4, and newer WHO artifacts.
- Local policy differences from WHO generic guidance.
- Missing or stale patient data.
- LLM explanation drift: the generated explanation may overstate what the executed rule actually found.
- Clinical safety and regulatory risk if recommendations are presented as autonomous decisions rather than clinician support.

## How this differs from current DAK intended use

Current intended DAK use:

- Convert WHO guidance into structured operational requirements.
- Author machine-readable FHIR and CQL artifacts.
- Validate artifacts with test cases.
- Localize them to country workflows, policies, and terminology.
- Integrate them into digital systems through standards-based services and applications.

Proposed Gemma-enabled use:

- Add an LLM layer that chooses which DAK tool/artifact to invoke.
- Let the model help bridge natural clinical context to computable artifacts.
- Use the model to explain recommendations in clinician-friendly language.
- Potentially help identify missing data or ask follow-up questions.

The proposed use is an extension, not a replacement. It moves the DAK from "directly implemented guideline logic" to "tool-addressable clinical knowledge and executable logic inside an agentic CDS service." The DAK remains the source of truth; Gemma becomes the interaction and orchestration layer.

## Recommended MVP

### Scope

Choose one narrow guideline area first. Best candidates:

- ANC decision support if that is already aligned with OpenMRS SMART work.
- HIV care and treatment if program data is already structured.
- Immunization if vaccine history and age-based scheduling data are available.

Avoid starting with broad differential diagnosis or general medical advice.

### MVP components

1. DAK artifact repository
   - Import one WHO SMART Guideline/DAK L3 package.
   - Store canonical URLs, versions, ValueSets, CodeSystems, Libraries, PlanDefinitions, and test fixtures.

2. OpenMRS FHIR extraction
   - Build patient-context fetchers for the selected domain only.
   - Include `_include` and `_revinclude` where helpful.

3. Mapping layer
   - Map local OpenMRS concepts to DAK value sets.
   - Track unmapped concepts explicitly.

4. CQL/PlanDefinition execution
   - Use a CQL engine/CQF-compatible service.
   - Support PlanDefinition `$apply`.

5. Gemma 4 tool orchestrator
   - Provide only the small set of tools required for the selected domain.
   - Force structured output.
   - Validate all tool calls before execution.

6. CDS output
   - Return CDS Hooks cards first, or an OpenMRS-native equivalent.
   - Include source artifact, patient facts used, and missing data.

7. Evaluation harness
   - Golden test cases from DAK decision table rows.
   - Synthetic OpenMRS/FHIR patient bundles.
   - Regression tests for tool choice, CQL output, and final CDS card wording.

## Evaluation plan

### Unit tests

- Tool schema validation.
- FHIR bundle normalization.
- Terminology mapping.
- CQL expression evaluation.
- PlanDefinition `$apply` output.

### Scenario tests

- One synthetic patient per DAK decision-table row.
- Boundary conditions: age thresholds, pregnancy status, lab result thresholds, medication contraindications, incomplete immunization history, missing encounter data.
- Negative controls where no recommendation should fire.

### LLM-specific tests

- Does Gemma select the right tool?
- Does it pass the correct patient/rule identifiers?
- Does it refuse to answer when deterministic execution fails?
- Does it mention only facts returned by tools?
- Does it preserve source artifact/version/citation?
- Does it avoid adding unsupported medical advice?

### Clinical validation

- SME review of every recommendation class.
- Compare outputs against DAK expected actions.
- Local policy review before deployment.
- Prospective silent-mode evaluation before showing live clinicians.

### Metrics

- Rule accuracy against gold cases.
- Tool-call validity rate.
- Missing-data detection rate.
- Unsupported statement rate in generated explanations.
- Latency per hook.
- Clinician acceptance/override rate.
- Audit trace completeness.

## Deployment recommendation

For privacy and safety, start with a local or private-network deployment:

- Gemma 4 served inside the clinic or controlled cloud boundary.
- DAK/FHIR/CQL services inside the same trust boundary.
- No raw PHI sent to public APIs.
- Full audit logging.
- Read-only CDS until validated.

Model choice:

- Start with Gemma 4 E4B or 31B depending on available GPU memory and required reasoning quality.
- Use deterministic decoding for tool calls.
- Consider MedGemma only as a separate comparison arm for explanation quality or medical text summarization.
- Do not use FunctionGemma as the clinical model; it may be useful later for tiny local command-routing tasks.

## Open questions before implementation

- Which DAK domain will be first: ANC, HIV, TB, immunization, or another?
- Do we have L3 FHIR artifacts available for that domain, or only L2 DAK materials?
- What FHIR release will be used internally for CDS: R4 end-to-end, or R4/R5 translation?
- Which OpenMRS concepts are already mapped to WHO/OCL/LOINC/SNOMED/CIEL?
- Will CDS be surfaced through CDS Hooks, Patient Flags, form validation, tasks, or a SMART app?
- Is Gemma required to run fully local for PHI, or can a private cloud endpoint be used?
- What level of clinical governance is required before live clinician exposure?

## Recommended next build step

Build a proof-of-concept CDS service for one narrow DAK decision table:

1. Select a single DAK decision table and its L3 PlanDefinition/CQL if available.
2. Create three to ten synthetic FHIR patient bundles covering expected outcomes.
3. Run deterministic CQL/PlanDefinition evaluation.
4. Wrap it in two Gemma 4 tools: `detect_missing_required_data` and `apply_plan_definition`.
5. Make Gemma generate a CDS Hooks card from the returned result.
6. Test whether the final card is accurate, traceable, and refuses to over-answer when data is missing.

If this works, expand by adding more decision tables. If it fails, fix the data mapping and execution layer before tuning prompts or models.

## Key references

- WHO SMART Guidelines Starter Kit v2.0.0: https://smart.who.int/ig-starter-kit/
- WHO L3 authoring overview: https://smart.who.int/ig-starter-kit/v2.0.0/authoring_overview.html
- WHO decision table authoring: https://smart.who.int/ig-starter-kit/v2.0.0/l3_decisiontables.html
- WHO CQL authoring: https://smart.who.int/ig-starter-kit/v2.0.0/l3_cql.html
- WHO adapting guidelines for country use: https://build.fhir.org/ig/WorldHealthOrganization/smart-hiv/adapting.html
- Gemma 4 overview: https://ai.google.dev/gemma/docs/core
- Gemma releases: https://ai.google.dev/gemma/docs/releases
- Gemma 4 function calling: https://ai.google.dev/gemma/docs/capabilities/text/function-calling-gemma4
- FunctionGemma overview: https://ai.google.dev/gemma/docs/functiongemma
- MedGemma overview: https://developers.google.com/health-ai-developer-foundations/medgemma
- CDS Hooks v2.0.1: https://cds-hooks.hl7.org/
- FHIR PlanDefinition `$apply`: http://hl7.org/fhir/R5/plandefinition-operation-apply.html
- CQL Evaluation Service capability statement: https://hl7.org/fhir/uv/cql/CapabilityStatement-cql-evaluation-service.html
- OpenMRS SMART Guidelines adaptation article: https://openmrs.org/a-smart-recipe-for-adapting-smart-guidelines-for-openmrs
- OpenMRS Patient FHIR resource: https://openmrs.atlassian.net/wiki/spaces/docs/pages/26935802/Patient+Resource
- OpenMRS Observation FHIR resource: https://openmrs.atlassian.net/wiki/spaces/docs/pages/26935806/Observation+Resource
