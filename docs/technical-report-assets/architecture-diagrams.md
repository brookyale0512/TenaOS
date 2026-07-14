# TenaOS Architecture Diagrams

This file keeps the Mermaid source for the technical report diagrams. The diagrams are intentionally small and implementation-backed so they can render cleanly on GitHub and the website.

## System Context

```mermaid
flowchart LR
  Clinician[Clinician] --> Browser[ClinicalWorkspace]
  Facility[ClinicServer] --> Browser
  Browser --> TenaOS[TenaOSLocalStack]
  TenaOS --> OpenMRS[OpenMRS]
  TenaOS --> Gemma4E4B[Gemma4E4B]
  TenaOS --> GuidelineKB[WHO_MSF_KB]
  TenaOS --> CielKB[CIEL_KB]
  OpenMRS --> LocalData[FacilityOwnedData]
  GuidelineKB --> LocalEvidence[LocalEvidenceStore]
  CielKB --> Standards[CIEL_OpenMRSConcepts]
```

## Single-Container Runtime

```mermaid
flowchart TB
  Browser[Browser] --> Nginx[NginxPort80]
  subgraph TenaOSContainer[TenaOSContainer]
    Nginx --> ReactSPA[ReactSPA]
    Nginx --> OpenMRS[OpenMRS8080]
    Nginx --> TenaAgent[TenaAgent8095]
    TenaAgent --> LlamaServer[LlamaServer8001]
    TenaAgent --> GuidelineDaemon[GuidelineKB4276]
    TenaAgent --> CielDaemon[CielKB4277]
    TenaAgent --> OpenMRS
    GuidelineDaemon --> Qdrant[Qdrant6333]
    CielDaemon --> Qdrant
    CielDaemon --> CIELSQLite[CIELSQLite]
    OpenMRS --> MariaDB[MariaDB]
    LlamaServer --> GemmaWeights[GemmaGGUF_mmproj]
  end
```

## AI Safety Boundary

```mermaid
flowchart LR
  UserInput[ClinicalInput] --> Gemma4E4B[Gemma4E4B]
  Gemma4E4B --> ToolCalls[AllowListedTools]
  ToolCalls --> Retrieval[Guideline_CIELRetrieval]
  ToolCalls --> DraftStore[DraftStore]
  DraftStore --> Validators[DeterministicValidators]
  Validators --> Review[ClinicianReview]
  Review --> OpenMRS[OpenMRSPersistence]
  Validators --> Reject[RejectUnsafeState]
```

## WHO/MSF KB Build

```mermaid
flowchart LR
  Pdfs[WHO_MSFPDFs] --> Pulse[PulseOCRExtract]
  Pulse --> Markdown[MarkdownLayoutJSON]
  Markdown --> Docling[DoclingJSON]
  Docling --> Classify[ClassifyDocs]
  Classify --> Chunkers[DocTypeChunkers]
  Chunkers --> Enrich[RecommendationMetadata]
  Enrich --> EmbedGemma[EmbedGemma768]
  Enrich --> BM25[BM25Sparse]
  EmbedGemma --> QdrantGuidelines[QdrantWhoMsf]
  BM25 --> QdrantGuidelines
  QdrantGuidelines --> Snapshot[who_msf_guidelinesSnapshot]
```

## WHO/MSF Runtime Retrieval

```mermaid
flowchart LR
  Query[ClinicalQuery] --> Expand[SynonymExpansion]
  Expand --> Dense[EmbedGemmaQuery]
  Expand --> Sparse[BM25Query]
  Dense --> RRF[QdrantRRF]
  Sparse --> RRF
  RRF --> Rerank[ClinicalReranker]
  Rerank --> Evidence[EvidenceChunks]
  Evidence --> Gemma4E4B[Gemma4E4B]
  Gemma4E4B --> CitedOutput[CitedCDSOrMaterial]
```

## CIEL KB Build

```mermaid
flowchart LR
  OCL[OCL_CIELExport] --> Stream[StreamingParser]
  Stream --> SQLite[SQLiteBundles]
  SQLite --> FTS5[FTS5Search]
  SQLite --> SearchText[SearchText]
  SearchText --> SapBERT[SapBERTDense]
  SearchText --> BM25[BM25Sparse]
  SapBERT --> QdrantCIEL[QdrantCielConcepts]
  BM25 --> QdrantCIEL
  QdrantCIEL --> CielSnapshot[ciel_conceptsSnapshot]
```

## CIEL Runtime Resolution

```mermaid
flowchart LR
  Phrase[ClinicalPhrase] --> HybridSearch[SapBERT_BM25_RRF]
  HybridSearch --> CandidateIds[CandidateConceptIds]
  CandidateIds --> Hydrate[SQLiteHydration]
  Hydrate --> Inspect[ConceptBundleInspect]
  Inspect --> Validate[ClassDatatypeRetiredChecks]
  Validate --> Commit[DraftField]
  Validate --> Reject[RejectOrAskReview]
```

## Form Builder Workflow

```mermaid
flowchart LR
  Request[FormRequest] --> Research[WHO_MSFResearch]
  Research --> Worklist[QuestionWorklist]
  Worklist --> Resolve[CIELResolution]
  Resolve --> Basket[DraftBasket]
  Basket --> Repair[CoverageRepair]
  Repair --> Schema[OpenMRSSchemaBuild]
  Schema --> Review[ClinicianReview]
  Review --> Publish[OpenMRSPublish]
```

## Scribe Workflow

```mermaid
flowchart LR
  Note[TextOrVoiceNote] --> SOAP[SOAPExtraction]
  SOAP --> Search[CIELSearch]
  Search --> Inspect[ConceptInspection]
  Inspect --> Structured[StructuredFindings]
  Structured --> Review[ClinicianReview]
  Review --> Encounter[OpenMRSEncounter]
```

## CDS Workflow

```mermaid
flowchart LR
  Patient[OpenMRSPatientContext] --> Gemma4E4B[Gemma4E4B]
  Gemma4E4B --> Search[SearchGuidelines]
  Search --> Evidence[RetrievedEvidence]
  Evidence --> Followup[FollowupQueries]
  Followup --> Finalize[FormatCDS]
  Finalize --> CitedCard[CitedCDSCard]
  CitedCard --> Review[ClinicianUse]
```

## Patient Education Workflow

```mermaid
flowchart LR
  PatientContext[PatientContext] --> Search[GuidelineSearch]
  Search --> Evidence[EvidenceChunks]
  Evidence --> Draft[SevenSectionMaterial]
  Draft --> Translate[OptionalTranslation]
  Draft --> Edit[ClinicianEdit]
  Edit --> Print[PrintOrShare]
```

## Report Builder Workflow

```mermaid
flowchart LR
  Question[PlainLanguageQuestion] --> Spec[ReportSpecDraft]
  Spec --> CIEL[CIELConceptFilters]
  CIEL --> Compiler[DeterministicCompiler]
  Compiler --> FHIRPlan[FHIRQueryPlan]
  FHIRPlan --> OpenMRSFHIR[OpenMRSFHIR2]
  OpenMRSFHIR --> Results[CountsCohortsIndicators]
  Results --> Visualization[ReportVisualization]
```

## GEPA Optimization Loop

```mermaid
flowchart LR
  SeedPrompts[SeedPrompts] --> Overlay[PromptOverlay]
  Overlay --> RealPipeline[RealV2Pipeline]
  RealPipeline --> Metric[CIELCoverageMetric]
  Metric --> Feedback[TextFeedback]
  Feedback --> ReflectionLM[DeepSeekR1Reflection]
  ReflectionLM --> CandidatePrompts[CandidatePrompts]
  CandidatePrompts --> Overlay
  CandidatePrompts --> Export[OptimizedPrompts]
  Export --> RuntimeFlag[TENAOS_USE_OPTIMIZED_PROMPTS]
```

## LoRA Data Path

```mermaid
flowchart LR
  Requests[GeneratedRequests] --> TeacherTraces[TeacherTraces]
  RuntimeTraces[RuntimeTraces] --> TraceStore[TraceStore]
  TeacherTraces --> Validators[TraceValidators]
  TraceStore --> Validators
  Validators --> SFTRecords[TrainingReadyRecords]
  SFTRecords --> TrainingSet[TrainingSet]
  TrainingSet --> AdapterEval[AdapterEvaluation]
```
