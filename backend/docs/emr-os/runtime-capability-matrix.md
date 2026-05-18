# Runtime Capability Matrix

This matrix is generated from the repository runtime artifacts and start scripts so the agent control plane targets supported configuration surfaces instead of source-code patches.

## OpenMRS Module Inventory

- `addresshierarchy`
- `attachments`
- `bedmanagement`
- `billing`
- `calculation`
- `cohort`
- `emrapi`
- `event`
- `fhir2`
- `htmlwidgets`
- `idgen`
- `initializer`
- `legacyui`
- `metadatamapping`
- `o3forms`
- `ordertemplates`
- `patientdocuments`
- `patientflags`
- `queue`
- `reporting`
- `reportingrest`
- `serialization.xstream`
- `stockmanagement`
- `webservices.rest`

## Supported Surfaces

| Product | Domain | Status | Supported surfaces | Supported operations | Evidence | Limitations |
|---|---|---|---|---|---|---|
| OpenMRS | metadata-packs | supported | Initializer configuration directory<br>CSV/XML/JSON domains in app data | load locations<br>load encounter types<br>load forms<br>load id generators<br>load programs and workflows<br>load billing CSV domains when billing module is present | backups/default-port-validation/openmrs-data/.openmrs-lib-cache/initializer/moduleApplicationContext.xml<br>scripts/run-openmrs.sh | not every module exposes an Initializer domain<br>custom prices and deep workflow logic still require bounded adapters |
| OpenMRS | forms | supported | O3/AMPATH JSON forms<br>HTML Form Entry XML | create intake forms<br>create consultation forms<br>seed specialty forms | backups/default-port-validation/openmrs-data/.openmrs-lib-cache/o3forms/webModuleApplicationContext.xml | requires concept validation before publishing |
| OpenMRS | operational-routing | supported | Queue module REST/API surfaces<br>queue configuration tables | define queues and rooms<br>assign providers<br>apply status and priority concept sets<br>model patient movement between service points | backups/default-port-validation/openmrs-data/.openmrs-lib-cache/queue/liquibase.xml | not a full BPMN or rules engine |
| OpenMRS | billing | supported-with-constraints | billing module repositories<br>Initializer billableservices/paymentmodes/cashpoints | define billable services<br>define payment modes<br>define cash points<br>set billing global properties | backups/default-port-validation/openmrs-data/.openmrs-lib-cache/billing/moduleApplicationContext.xml<br>backups/default-port-validation/openmrs-data/.openmrs-lib-cache/billing/liquibase.xml | pricing schedules need a bounded adapter or admin-surface automation<br>clinic-specific exemptions need explicit policy validation |
| OpenMRS | stock-pharmacy | supported-with-constraints | stockmanagement service layer<br>stock metadata tables | define dispensing structures<br>scope stock operations by location<br>persist operational stock rules | backups/default-port-validation/openmrs-data/.openmrs-lib-cache/stockmanagement/moduleApplicationContext.xml<br>backups/default-port-validation/openmrs-data/.openmrs-lib-cache/stockmanagement/liquibase.xml | initializer coverage is weaker than core metadata domains |
| OpenMRS | patient-identifiers | supported | Initializer idgen domain<br>idgen runtime properties | configure sequential generators<br>configure pools<br>enable auto-generation | scripts/run-openmrs.sh | - |
| OpenMRS | interoperability | supported | FHIR2 module REST endpoints | patient/search APIs<br>order/result integration<br>downstream backend verification | backups/default-port-validation/openmrs-data/.openmrs-lib-cache/fhir2 | - |
| OpenELIS | runtime-config | supported-with-constraints | generated properties files<br>boot scripts<br>FHIR integration settings | site profile configuration<br>authentication integration<br>FHIR endpoint configuration<br>service-user integration setup | scripts/render-runtime-config.py<br>scripts/run-openelis-webapp.sh<br>scripts/run-openelis-fhir.sh | not the primary metadata substrate for clinic-to-clinic customization<br>deep LIS workflow customization should stay within supported OpenELIS admin surfaces |
| Orthanc | authorization-and-policy | supported-with-constraints | generated orthanc.json<br>orthanc-auth-service env/config<br>permissions.json role map | enable imaging service<br>bind Keycloak-backed roles to Orthanc permissions<br>set share-policy defaults | scripts/render-runtime-config.py<br>scripts/run-orthanc-auth.sh<br>configs/orthanc-auth/permissions.json | specialized PACS behavior should remain policy-driven, not source-code driven |
| Keycloak | identity-and-role-control-plane | supported-with-constraints | rendered realm JSON<br>OIDC clients<br>service accounts<br>admin API | provision bounded clinic users<br>map clinic roles to product-native roles<br>maintain SSO client configuration | scripts/render-runtime-config.py<br>scripts/run-keycloak.sh<br>docs/keycloak-e2e-audit.md | runtime clinic agent should not hold broad realm-management privileges<br>sensitive admin actions should remain in a privileged control plane |
