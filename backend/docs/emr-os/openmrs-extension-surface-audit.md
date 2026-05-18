# OpenMRS Extension Surface Audit

This note grounds the next implementation step for OpenMRS extension manifests. The goal is to identify which parts of the generated `extensions/` manifests can be executed through supported OpenMRS module surfaces and which parts still require a bounded adapter-owned policy layer.

## Scope

- queue and routing execution from `extensions/queue-routing.json`
- billing pricing execution from `extensions/billing-pricing.json`
- stock and pharmacy execution from `extensions/stock-pharmacy.json`

## Audit Inputs

- local runtime cache artifacts in `backups/default-port-validation/openmrs-data/.openmrs-lib-cache/`
- compiler output shape in `lmic_emr_os/openmrs_pack.py`
- current apply boundary in `lmic_emr_os/runtime_apply.py`
- upstream OpenMRS module REST resources for `queue`, `billing`, and `stockmanagement`

## Queue And Routing

Verdict: `partial live apply supported`

Supported mutable surfaces:

- `ws/rest/v1/queue`
  - supports create, update, delete, and list of queue metadata
  - creatable fields include `name`, `description`, `location`, `service`, `priorityConceptSet`, and `statusConceptSet`
- `ws/rest/v1/queue-room`
  - supports create, update, delete, and list of queue rooms
  - creatable fields include `name`, `description`, and `queue`
- `ws/rest/v1/queue-room-provider`
  - supports create, update, delete, and list of room-provider assignments
  - creatable fields include `queueRoom` and `provider`
- `ws/rest/v1/queue-entry/transition`
  - supports runtime transition of an existing queue entry to another queue, status, or priority

Supporting evidence:

- local queue module artifacts in `backups/default-port-validation/openmrs-data/.openmrs-lib-cache/queue/liquibase.xml` add REST-facing privileges such as `Get Queues`, `Manage Queues`, `Get Queue Rooms`, and `Manage Queue Rooms`
- upstream queue module REST resources expose `QueueResource`, `QueueRoomResource`, `RoomProviderMapResource`, and `QueueEntryTransitionRestController`
- upstream queue module README documents `queue.serviceConceptSetName`, `queue.statusConceptSetName`, `queue.priorityConceptSetName`, and `queue.sortWeightGenerator`

Important constraints:

- `routingRules` in the current manifest do not have a native queue-module persistence resource. The audited transition controller acts on live queue entries, not on durable route-policy definitions.
- `sortWeightGenerator` is a module global property, not a queue-level REST field. The current config model carries it per queue, but the audited live surface does not.
- `allowedStatuses` and `allowedPriorities` are derived from the queue concept sets. The writable queue fields are `statusConceptSet` and `priorityConceptSet`, not explicit arrays of allowed values.
- `queueRooms[].providerRoleIds` is not enough to drive `queue-room-provider` writes. The audited surface requires a concrete provider UUID, not a role ID.

Execution boundary for step 2:

- safe to implement queue upsert
- safe to implement queue-room upsert
- safe to implement room-provider upsert only after provider UUID resolution exists
- not safe to pretend `routingRules` are native queue metadata

## Billing And Pricing

Verdict: `live pricing apply is feasible, but the current manifest needs a tighter adapter contract`

Supported mutable surfaces:

- `ws/rest/v1/billing/billableService`
- `ws/rest/v1/billing/paymentMode`
- `ws/rest/v1/billing/cashPoint`
- `ws/rest/v1/billing/cashierItemPrice`

Supporting evidence:

- local billing module artifacts in `backups/default-port-validation/openmrs-data/.openmrs-lib-cache/billing/moduleApplicationContext.xml` and `liquibase.xml`
- upstream billing module REST resources expose `BillableServiceResource`, `PaymentModeResource`, `CashPointResource`, and `CashierItemPriceResource`
- local billing schema in `backups/default-port-validation/openmrs-data/.openmrs-lib-cache/billing/liquibase.xml` shows `cashier_item_price.service_id`, `cashier_item_price.item_id`, and `cashier_item_price.payment_mode` are nullable

What this means:

- the direct OpenMRS pack already handles billable services, payment modes, and cash points through Initializer CSV domains
- the highest-value live handler is `cashierItemPrice`
- service-level default prices are possible because `cashier_item_price` can point only at a billable service without forcing stock-item or payment-mode specificity

Important constraints:

- `pricingRules[].patientCategory` does not map to a first-class field on the audited price resource. If the field is kept, the adapter must encode it deliberately or split it into a separate policy model.
- `pricingRules[].requiresPaymentBeforeService` is not a billing REST field. It belongs more naturally to route policy than to stored price rows.
- the current manifest only carries `billableServiceId`, `amount`, `patientCategory`, and `requiresPaymentBeforeService`. It cannot currently express payment-mode-specific pricing or item-specific pricing.

Execution boundary for step 2:

- safe to implement service-level price upsert through `billing/cashierItemPrice`
- safe to continue letting the direct pack own billable services, payment modes, and cash points
- not safe to treat `requiresPaymentBeforeService` as a native billing field

## Stock And Pharmacy

Verdict: `stock rule execution is partially supported; operation-type mutation is not`

Supported mutable surfaces:

- `ws/rest/v1/stockmanagement/stockrule`
- `ws/rest/v1/stockmanagement/stockitem`
- `ws/rest/v1/stockmanagement/stockitempackaginguom`

Audited read-only or non-creatable surfaces:

- `ws/rest/v1/stockmanagement/stockoperationtype`
- `ws/rest/v1/stockmanagement/stockoperationtypelocationscope`
- `ws/rest/v1/stockmanagement/location`

Supporting evidence:

- local stockmanagement schema in `backups/default-port-validation/openmrs-data/.openmrs-lib-cache/stockmanagement/liquibase.xml`
- upstream stockmanagement REST resources expose `StockRuleResource`, `StockItemResource`, `StockItemPackagingUOMResource`, `StockOperationTypeResource`, `StockOperationTypeLocationScopeResource`, and `LocationResource`

Important constraints:

- `stockLocations` in the current manifest are bindings to OpenMRS locations that are already created elsewhere. The audited stockmanagement location resource does not provide a create/update path for new stock locations.
- `operationTypes` cannot be created or updated through the audited supported stockmanagement REST resources.
- the current `StockRule` model carries `stockItemName`, `locationId`, and `reorderLevel`, while the audited live resource expects `stockItemUuid`, `locationUuid`, `quantity`, and optional packaging/frequency fields. A live adapter would have to map `reorderLevel` to `quantity` and resolve UUIDs first.
- the current config model does not carry enough deterministic stock-item identity to create a missing stock item safely. `StockItemResource` can create stock items, but it needs fields such as `drugUuid` or `conceptUuid` that are absent from the current rule manifest.

Execution boundary for step 2:

- safe to implement stock-rule upsert only when the target stock item can be resolved unambiguously
- safe to treat stock locations as references to already-created OpenMRS locations
- not safe to implement operation-type creation/update through the audited supported surfaces

## Cross-Cutting Findings

- queue, billing, and stockmanagement all expose enough supported REST surface to justify a bounded OpenMRS REST client in the apply layer
- the current manifest shapes are closest to execution for queue metadata and service-level pricing
- the current manifest shapes are not yet strong enough for native routing-policy persistence, provider-room assignments, or deterministic stock-item creation

## Go Or No-Go For Step 2

Go:

- queue upsert
- queue-room upsert
- service-level billing price upsert
- stock-rule upsert against resolvable existing stock items

Do not fake as native:

- routing-rule persistence inside the queue module
- stock operation-type creation/update
- room-provider assignment without provider UUID resolution
- stock-rule creation from ambiguous stock-item names alone

## Required Shape Changes Before Or During Step 2

- resolve `queueRooms[].providerRoleIds` into concrete provider UUIDs before room-provider writes
- treat `routingRules` as adapter-owned policy rather than as queue-module metadata
- decide whether `pricingRules[].patientCategory` stays as adapter metadata or moves into a separate billing/exemption policy model
- extend stock execution inputs if missing stock items must be created deterministically
