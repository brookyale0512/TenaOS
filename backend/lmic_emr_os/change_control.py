from __future__ import annotations

import fcntl
import json
import os
import shutil
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_PRODUCTS = ["openmrs", "keycloak", "openelis", "orthanc"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ApprovalEntry:
    approver: str
    note: str
    environment: str = ""
    approved_at: str = field(default_factory=_utc_now)


@dataclass(slots=True)
class PromotionEntry:
    from_environment: str
    to_environment: str
    promoted_by: str
    note: str = ""
    promoted_at: str = field(default_factory=_utc_now)
    artifacts: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ApplyRun:
    run_id: str
    executed_at: str
    mode: str
    environment: str
    restart_services: bool
    run_verify: bool
    status: str
    products: list[str]
    bundle_dir: str
    snapshots: dict[str, str] = field(default_factory=dict)
    results: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    previous_active_change_id: str = ""
    previous_active_change_ids: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ChangeRecord:
    change_id: str
    bundle_dir: str
    config_path: str
    preview_path: str
    operational_analysis_path: str
    workflow_graph_path: str
    verification_plan_path: str
    openmrs_pack_dir: str
    apply_order_path: str
    rollback_plan_path: str
    created_at: str = field(default_factory=_utc_now)
    status: str = "registered"
    active: bool = False
    active_products: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    approvals: list[ApprovalEntry] = field(default_factory=list)
    apply_runs: list[ApplyRun] = field(default_factory=list)
    promotions: list[PromotionEntry] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ChangeRecord":
        return cls(
            change_id=str(payload["change_id"]),
            bundle_dir=str(payload["bundle_dir"]),
            config_path=str(payload["config_path"]),
            preview_path=str(payload["preview_path"]),
            operational_analysis_path=str(payload.get("operational_analysis_path", "")),
            workflow_graph_path=str(payload.get("workflow_graph_path", "")),
            verification_plan_path=str(payload.get("verification_plan_path", "")),
            openmrs_pack_dir=str(payload["openmrs_pack_dir"]),
            apply_order_path=str(payload["apply_order_path"]),
            rollback_plan_path=str(payload["rollback_plan_path"]),
            created_at=str(payload.get("created_at") or _utc_now()),
            status=str(payload.get("status") or "registered"),
            active=bool(payload.get("active", False)),
            active_products=[str(value) for value in payload.get("active_products", payload.get("activeProducts", []))],
            artifacts={str(key): str(value) for key, value in payload.get("artifacts", {}).items()},
            approvals=[
                ApprovalEntry(
                    approver=str(item["approver"]),
                    note=str(item.get("note", "")),
                    environment=str(item.get("environment", "")),
                    approved_at=str(item.get("approved_at") or _utc_now()),
                )
                for item in payload.get("approvals", [])
            ],
            apply_runs=[
                ApplyRun(
                    run_id=str(item["run_id"]),
                    executed_at=str(item["executed_at"]),
                    mode=str(item["mode"]),
                    environment=str(item.get("environment", "")),
                    restart_services=bool(item.get("restart_services", False)),
                    run_verify=bool(item.get("run_verify", False)),
                    status=str(item.get("status") or "unknown"),
                    products=[str(product) for product in item.get("products", [])],
                    bundle_dir=str(item.get("bundle_dir", "")),
                    snapshots={str(key): str(value) for key, value in item.get("snapshots", {}).items()},
                    results=dict(item.get("results", {})),
                    verification=dict(item.get("verification", {})),
                    warnings=[str(value) for value in item.get("warnings", [])],
                    errors=[str(value) for value in item.get("errors", [])],
                    previous_active_change_id=str(item.get("previous_active_change_id", "")),
                    previous_active_change_ids={
                        str(key): str(value)
                        for key, value in item.get("previous_active_change_ids", item.get("previousActiveChangeIds", {})).items()
                    },
                )
                for item in payload.get("apply_runs", [])
            ],
            promotions=[
                PromotionEntry(
                    from_environment=str(item["from_environment"]),
                    to_environment=str(item["to_environment"]),
                    promoted_by=str(item["promoted_by"]),
                    note=str(item.get("note", "")),
                    promoted_at=str(item.get("promoted_at") or _utc_now()),
                    artifacts={str(key): str(value) for key, value in item.get("artifacts", {}).items()},
                )
                for item in payload.get("promotions", [])
            ],
            notes=[str(value) for value in payload.get("notes", [])],
        )


class ChangeControlStateStore:
    def __init__(self, root_dir: str | Path, *, max_runs_per_change: int = 25) -> None:
        self.root_dir = Path(root_dir)
        self.changes_dir = self.root_dir / "changes"
        self.snapshots_dir = self.root_dir / "snapshots"
        self.active_path = self.root_dir / "active-change.json"
        self.active_products_path = self.root_dir / "active-products.json"
        self.lock_path = self.root_dir / ".state.lock"
        self.max_runs_per_change = max_runs_per_change
        self._thread_lock = threading.RLock()
        self._lock_handle: Any | None = None
        self._lock_depth = 0
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.changes_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _locked(self):
        with self._thread_lock:
            if self._lock_depth == 0:
                self.lock_path.parent.mkdir(parents=True, exist_ok=True)
                self._lock_handle = self.lock_path.open("a+", encoding="utf-8")
                fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_EX)
            self._lock_depth += 1
            try:
                yield
            finally:
                self._lock_depth -= 1
                if self._lock_depth == 0 and self._lock_handle is not None:
                    fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
                    self._lock_handle.close()
                    self._lock_handle = None

    def _record_path(self, change_id: str) -> Path:
        return self.changes_dir / change_id / "record.json"

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        ) as handle:
            handle.write(json.dumps(payload, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        try:
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)

    def _default_artifacts(self, bundle_root: Path) -> dict[str, str]:
        return {
            "artifactManifest": str(bundle_root / "artifact-manifest.json"),
            "clinicConfig": str(bundle_root / "clinic-config.json"),
            "preview": str(bundle_root / "preview.json"),
            "operationalAnalysis": str(bundle_root / "operational-analysis.json"),
            "workflowGraph": str(bundle_root / "workflow-graph.mmd"),
            "verificationPlan": str(bundle_root / "verification-plan.json"),
            "openmrsPack": str(bundle_root / "openmrs"),
            "plansDir": str(bundle_root / "plans"),
            "applyOrder": str(bundle_root / "apply-order.json"),
            "rollbackPlan": str(bundle_root / "rollback.json"),
        }

    def register_bundle(self, bundle_dir: str | Path) -> ChangeRecord:
        with self._locked():
            bundle_root = Path(bundle_dir)
            apply_order = json.loads((bundle_root / "apply-order.json").read_text(encoding="utf-8"))
            change_id = str(apply_order["changeId"])
            existing = self.get_change(change_id)
            if existing is not None:
                return existing
            artifact_manifest_path = bundle_root / "artifact-manifest.json"
            if artifact_manifest_path.exists():
                manifest_artifacts = {
                    str(key): str(value)
                    for key, value in json.loads(artifact_manifest_path.read_text(encoding="utf-8")).get(
                        "artifacts",
                        {},
                    ).items()
                }
                artifacts = {**self._default_artifacts(bundle_root), **manifest_artifacts}
            else:
                artifacts = self._default_artifacts(bundle_root)
            record = ChangeRecord(
                change_id=change_id,
                bundle_dir=str(bundle_root),
                config_path=str(bundle_root / "clinic-config.json"),
                preview_path=str(bundle_root / "preview.json"),
                operational_analysis_path=str(bundle_root / "operational-analysis.json"),
                workflow_graph_path=str(bundle_root / "workflow-graph.mmd"),
                verification_plan_path=str(bundle_root / "verification-plan.json"),
                openmrs_pack_dir=str(bundle_root / "openmrs"),
                apply_order_path=str(bundle_root / "apply-order.json"),
                rollback_plan_path=str(bundle_root / "rollback.json"),
                artifacts=artifacts,
            )
            self.save_change(record)
            return record

    def save_change(self, record: ChangeRecord) -> None:
        with self._locked():
            self._write_json(self._record_path(record.change_id), record.to_dict())

    def get_change(self, change_id: str) -> ChangeRecord | None:
        with self._locked():
            path = self._record_path(change_id)
            if not path.exists():
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            return ChangeRecord.from_dict(payload)

    def list_changes(self) -> list[ChangeRecord]:
        with self._locked():
            records: list[ChangeRecord] = []
            for path in sorted(self.changes_dir.glob("*/record.json")):
                payload = json.loads(path.read_text(encoding="utf-8"))
                records.append(ChangeRecord.from_dict(payload))
            return records

    def approve_change(
        self,
        change_id: str,
        approver: str,
        note: str = "",
        environment: str = "",
    ) -> ChangeRecord:
        with self._locked():
            record = self.require_change(change_id)
            record.approvals.append(ApprovalEntry(approver=approver, note=note, environment=environment))
            record.status = "approved"
            self.save_change(record)
            return record

    def promote_change(
        self,
        change_id: str,
        *,
        from_environment: str,
        to_environment: str,
        promoted_by: str,
        note: str = "",
    ) -> ChangeRecord:
        with self._locked():
            record = self.require_change(change_id)
            record.promotions.append(
                PromotionEntry(
                    from_environment=from_environment,
                    to_environment=to_environment,
                    promoted_by=promoted_by,
                    note=note,
                    artifacts=dict(record.artifacts),
                )
            )
            self.save_change(record)
            return record

    def append_apply_run(self, change_id: str, run: ApplyRun) -> ChangeRecord:
        with self._locked():
            record = self.require_change(change_id)
            record.apply_runs.append(run)
            record.status = run.status
            self.save_change(record)
            if run.status == "applied":
                self.set_active_change_for_products(change_id, run.products)
            elif run.status in {"rolled-back", "failed-rolled-back"}:
                self.restore_active_changes(run.previous_active_change_ids, run.products)
            else:
                self._sync_record_activity(self.get_active_change_ids_by_product())
            self._prune_change_history(change_id)
            return self.require_change(change_id)

    def set_active_change(self, change_id: str) -> None:
        self.set_active_change_for_products(change_id, DEFAULT_PRODUCTS)

    def set_active_change_for_products(self, change_id: str, products: list[str]) -> None:
        with self._locked():
            if not products:
                return
            active_changes = self.get_active_change_ids_by_product()
            for product in products:
                active_changes[str(product)] = change_id
            self._write_active_changes(active_changes)

    def get_active_change_id(self) -> str:
        active_changes = self.get_active_change_ids_by_product()
        if not active_changes:
            return ""
        if any(not active_changes.get(product, "") for product in DEFAULT_PRODUCTS):
            return ""
        unique_ids = {change_id for change_id in active_changes.values() if change_id}
        if len(unique_ids) == 1:
            return next(iter(unique_ids))
        return ""

    def get_active_change_ids_by_product(self, products: list[str] | None = None) -> dict[str, str]:
        with self._locked():
            if self.active_products_path.exists():
                payload = json.loads(self.active_products_path.read_text(encoding="utf-8"))
                stored = {
                    str(key): str(value)
                    for key, value in payload.get("products", {}).items()
                    if value
                }
            elif self.active_path.exists():
                payload = json.loads(self.active_path.read_text(encoding="utf-8"))
                change_id = str(payload.get("change_id", ""))
                stored = {product: change_id for product in DEFAULT_PRODUCTS if change_id}
            else:
                stored = {}
            if products is None:
                return dict(stored)
            return {str(product): stored.get(str(product), "") for product in products}

    def clear_active_change(self) -> None:
        self.clear_active_changes(DEFAULT_PRODUCTS)

    def clear_active_changes(self, products: list[str]) -> None:
        with self._locked():
            if not products:
                return
            active_changes = self.get_active_change_ids_by_product()
            for product in products:
                active_changes.pop(str(product), None)
            self._write_active_changes(active_changes)

    def restore_active_changes(self, previous_active_changes: dict[str, str], products: list[str]) -> None:
        with self._locked():
            active_changes = self.get_active_change_ids_by_product()
            for product in products:
                change_id = str(previous_active_changes.get(product, "") or "")
                if change_id:
                    active_changes[str(product)] = change_id
                else:
                    active_changes.pop(str(product), None)
            self._write_active_changes(active_changes)

    def create_snapshot_dir(self, change_id: str, run_id: str) -> Path:
        with self._locked():
            path = self.snapshots_dir / change_id / run_id
            path.mkdir(parents=True, exist_ok=True)
            return path

    def require_change(self, change_id: str) -> ChangeRecord:
        record = self.get_change(change_id)
        if record is None:
            raise FileNotFoundError(f"Unknown change id '{change_id}'.")
        return record

    def _prune_change_history(self, change_id: str) -> None:
        if self.max_runs_per_change <= 0:
            return
        record = self.require_change(change_id)
        if len(record.apply_runs) <= self.max_runs_per_change:
            return
        prune_runs = record.apply_runs[:-self.max_runs_per_change]
        record.apply_runs = record.apply_runs[-self.max_runs_per_change :]
        for run in prune_runs:
            shutil.rmtree(self.snapshots_dir / change_id / run.run_id, ignore_errors=True)
        self._write_json(self._record_path(record.change_id), record.to_dict())

    def _write_active_changes(self, active_changes: dict[str, str]) -> None:
        active_changes = {str(key): str(value) for key, value in active_changes.items() if value}
        if active_changes:
            self._write_json(
                self.active_products_path,
                {"products": active_changes, "updated_at": _utc_now()},
            )
        else:
            self.active_products_path.unlink(missing_ok=True)

        if len({value for value in active_changes.values() if value}) == 1 and all(
            active_changes.get(product, "") for product in DEFAULT_PRODUCTS
        ):
            change_id = next(iter(active_changes.values()))
            self._write_json(self.active_path, {"change_id": change_id, "updated_at": _utc_now()})
        else:
            self.active_path.unlink(missing_ok=True)

        self._sync_record_activity(active_changes)

    def _sync_record_activity(self, active_changes: dict[str, str]) -> None:
        active_products_by_change_id: dict[str, list[str]] = {}
        for product, change_id in active_changes.items():
            active_products_by_change_id.setdefault(str(change_id), []).append(str(product))

        for record in self.list_changes():
            record.active_products = sorted(active_products_by_change_id.get(record.change_id, []))
            record.active = bool(record.active_products)
            if record.active_products and set(record.active_products) == set(DEFAULT_PRODUCTS):
                record.status = "applied"
            elif record.active_products:
                record.status = "partially-applied"
            self.save_change(record)
