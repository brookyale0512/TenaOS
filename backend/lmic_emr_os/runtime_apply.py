from __future__ import annotations

import base64
import json
import os
import re
import shlex
import shutil
import ssl
import subprocess
import tempfile
import time
import urllib.parse
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .adapters import OrthancAdapter
from .change_control import DEFAULT_PRODUCTS, ApplyRun, ChangeControlStateStore, ChangeRecord
from .config_model import ClinicConfigModel
from .verification_plan import VerificationPlan


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
            value = value[1:-1]
        values[key] = value
    return values


def load_runtime_env(repo_root: str | Path) -> dict[str, str]:
    root = Path(repo_root)
    values: dict[str, str] = {}
    values.update(_load_env_file(root / ".env"))
    values.update(_load_env_file(root / ".secrets.env"))
    host = values.get("CLINICDX_PUBLIC_HOST", "localhost").strip().strip("/")
    if "OPENMRS_PUBLIC_URL" not in values:
        values["OPENMRS_PUBLIC_URL"] = (
            f"{values.get('OPENMRS_PUBLIC_SCHEME', 'http')}://{host}:{values.get('OPENMRS_PORT', '8080')}/openmrs"
        )
    if "KEYCLOAK_PUBLIC_URL" not in values:
        values["KEYCLOAK_PUBLIC_URL"] = (
            f"{values.get('KEYCLOAK_PUBLIC_SCHEME', 'http')}://{host}:{values.get('KEYCLOAK_PORT', '8083')}"
        )
    if "OPENELIS_PUBLIC_URL" not in values:
        values["OPENELIS_PUBLIC_URL"] = (
            f"{values.get('OPENELIS_PUBLIC_SCHEME', 'https')}://{host}:{values.get('OPENELIS_HTTPS_PORT', '8444')}"
        )
    if "ORTHANC_PUBLIC_URL" not in values:
        values["ORTHANC_PUBLIC_URL"] = (
            f"{values.get('ORTHANC_PUBLIC_SCHEME', 'http')}://{host}:{values.get('ORTHANC_PORT', '8042')}"
        )
    values.setdefault("KEYCLOAK_REALM", "healthcare")
    values.setdefault("KEYCLOAK_ADMIN_USER", "admin")
    values.setdefault("CLINICDX_ADMIN_USERNAME", "emr-admin")
    values.setdefault("CLINICDX_DOCTOR_USERNAME", "dr-demo")
    values.setdefault("CLINICDX_LAB_USERNAME", "lab-demo")
    values.setdefault("CLINICDX_RAD_USERNAME", "rad-demo")
    values.setdefault("OPENMRS_CLIENT_ID", "openmrs-client")
    values.setdefault("OPENELIS_CLIENT_ID", "openelis-client")
    values.setdefault("ORTHANC_CLIENT_ID", "orthanc-client")
    values.setdefault("VERIFY_ADMIN_USER", values.get("CLINICDX_ADMIN_USERNAME", "emr-admin"))
    if "VERIFY_ADMIN_PASSWORD" not in values and values.get("CLINICDX_ADMIN_PASSWORD"):
        values["VERIFY_ADMIN_PASSWORD"] = values["CLINICDX_ADMIN_PASSWORD"]
    if "KEYCLOAK_REALM_URL" not in values:
        values["KEYCLOAK_REALM_URL"] = f"{values['KEYCLOAK_PUBLIC_URL']}/realms/{values['KEYCLOAK_REALM']}"
    values.setdefault("OPENELIS_FHIR_PORT", values.get("OPENELIS_FHIR_PORT", "8082"))
    return values


def _dict_to_properties(payload: dict[str, Any]) -> str:
    lines = [f"{key}={value}" for key, value in sorted(payload.items())]
    return "\n".join(lines) + "\n"


class RuntimeClient(Protocol):
    def copy_from_container(self, source_path: str, destination_path: str | Path) -> None:
        ...

    def copy_to_container(self, source_path: str | Path, destination_path: str) -> None:
        ...

    def path_exists(self, path: str) -> bool:
        ...

    def exec_shell(self, command: str) -> subprocess.CompletedProcess[str]:
        ...

    def restart_services(self, *services: str) -> list[str]:
        ...

    def run_verify_script(self, script_path: str | Path, *args: str) -> subprocess.CompletedProcess[str]:
        ...


class KeycloakClient(Protocol):
    def get_client_role(self, client_id: str, role_name: str) -> dict[str, Any] | None:
        ...

    def create_client_role(self, client_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def delete_client_role(self, client_id: str, role_name: str) -> None:
        ...

    def get_realm_role(self, role_name: str) -> dict[str, Any] | None:
        ...

    def create_realm_role(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def update_realm_role(self, role_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def delete_realm_role(self, role_name: str) -> None:
        ...

    def find_user_by_username(self, username: str) -> dict[str, Any] | None:
        ...

    def create_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def update_user(self, user_id: str, payload: dict[str, Any]) -> None:
        ...

    def delete_user(self, user_id: str) -> None:
        ...

    def get_user_realm_roles(self, user_id: str) -> list[dict[str, Any]]:
        ...

    def add_user_realm_roles(self, user_id: str, roles: list[dict[str, Any]]) -> None:
        ...

    def delete_user_realm_roles(self, user_id: str, roles: list[dict[str, Any]]) -> None:
        ...


class OpenMRSClient(Protocol):
    def wait_until_ready(self, timeout_seconds: int = 120) -> None:
        ...

    def find_location(self, name: str) -> dict[str, Any] | None:
        ...

    def find_encounter_type(self, name: str) -> dict[str, Any] | None:
        ...

    def find_queue(self, name: str, location_uuid: str) -> dict[str, Any] | None:
        ...

    def create_queue(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def update_queue(self, queue_uuid: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def delete_queue(self, queue_uuid: str, reason: str = "") -> None:
        ...

    def find_queue_room(self, name: str, queue_uuid: str) -> dict[str, Any] | None:
        ...

    def create_queue_room(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def update_queue_room(self, room_uuid: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def delete_queue_room(self, room_uuid: str, reason: str = "") -> None:
        ...

    def find_provider_by_username(self, username: str) -> dict[str, Any] | None:
        ...

    def find_queue_room_provider(self, queue_room_uuid: str, provider_uuid: str) -> dict[str, Any] | None:
        ...

    def create_queue_room_provider(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def delete_queue_room_provider(self, room_provider_uuid: str, reason: str = "") -> None:
        ...

    def find_billable_service(self, service_name: str) -> dict[str, Any] | None:
        ...

    def find_payment_mode(self, name: str) -> dict[str, Any] | None:
        ...

    def find_cash_point(self, name: str) -> dict[str, Any] | None:
        ...

    def find_cashier_item_price(self, name: str) -> dict[str, Any] | None:
        ...

    def create_cashier_item_price(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def update_cashier_item_price(self, price_uuid: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def delete_cashier_item_price(self, price_uuid: str, reason: str = "") -> None:
        ...

    def find_stock_item(self, stock_item_name: str) -> dict[str, Any] | None:
        ...

    def find_stock_rule(self, name: str, stock_item_uuid: str, location_uuid: str) -> dict[str, Any] | None:
        ...

    def create_stock_rule(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def update_stock_rule(self, rule_uuid: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def delete_stock_rule(self, rule_uuid: str, reason: str = "") -> None:
        ...


class DockerRuntimeClient:
    def __init__(
        self,
        container_name: str = "ClinicDx_backend",
        *,
        supervisor_config_path: str = "/opt/clinicDx/configs/supervisor/supervisord.conf",
        supervisor_socket_path: str = "/var/run/supervisor.sock",
        supervisor_wait_timeout_seconds: float = 120,
        supervisor_poll_interval_seconds: float = 2,
    ) -> None:
        self.container_name = container_name
        self.supervisor_config_path = supervisor_config_path
        self.supervisor_socket_path = supervisor_socket_path
        self.supervisor_wait_timeout_seconds = supervisor_wait_timeout_seconds
        self.supervisor_poll_interval_seconds = supervisor_poll_interval_seconds
        self.openmrs_restart_flag_path = "/opt/openmrs/data/.clinicdx-managed-restart"
        self.supervisor_service_ports: dict[str, tuple[int, ...]] = {
            "openmrs": (8080,),
            "openelis-webapp": (8081, 8444),
            "openelis-fhir": (8082,),
            "orthanc-auth": (8000,),
            "orthanc": (8042,),
            "keycloak": (8083,),
        }

    def _run(
        self,
        args: list[str],
        *,
        capture_output: bool = True,
        text: bool = True,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            capture_output=capture_output,
            text=text,
            check=check,
        )

    def copy_from_container(self, source_path: str, destination_path: str | Path) -> None:
        destination = Path(destination_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._run(["docker", "cp", f"{self.container_name}:{source_path}", str(destination)])

    def _container_owner_for_path(self, path: str) -> str | None:
        probe_command = (
            f"probe={shlex.quote(path)}; "
            'while [ ! -e "$probe" ] && [ "$probe" != "/" ]; do probe=$(dirname "$probe"); done; '
            'if [ -e "$probe" ]; then stat -c "%u:%g" "$probe"; fi'
        )
        result = self._run(
            ["docker", "exec", self.container_name, "sh", "-lc", probe_command],
            check=False,
        )
        if result.returncode != 0:
            return None
        owner = (result.stdout or "").strip()
        return owner or None

    def _container_mode_for_file(self, path: str) -> str | None:
        result = self._run(
            ["docker", "exec", self.container_name, "sh", "-lc", f"if [ -f {shlex.quote(path)} ]; then stat -c %a {shlex.quote(path)}; fi"],
            check=False,
        )
        if result.returncode != 0:
            return None
        mode = (result.stdout or "").strip()
        return mode or None

    def copy_to_container(self, source_path: str | Path, destination_path: str) -> None:
        source = Path(source_path)
        owner = self._container_owner_for_path(destination_path)
        mode = self._container_mode_for_file(destination_path) if source.is_file() else None
        self._run(["docker", "cp", str(source), f"{self.container_name}:{destination_path}"])
        if owner:
            self._run(
                [
                    "docker",
                    "exec",
                    self.container_name,
                    "sh",
                    "-lc",
                    f"chown -R {shlex.quote(owner)} {shlex.quote(destination_path)}",
                ]
            )
        if source.is_file():
            self._run(
                [
                    "docker",
                    "exec",
                    self.container_name,
                    "sh",
                    "-lc",
                    f"chmod {shlex.quote(mode or '0644')} {shlex.quote(destination_path)}",
                ]
            )

    def path_exists(self, path: str) -> bool:
        result = subprocess.run(
            ["docker", "exec", self.container_name, "sh", "-lc", f"[ -e '{path}' ]"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def exec_shell(self, command: str) -> subprocess.CompletedProcess[str]:
        return self._run(["docker", "exec", self.container_name, "sh", "-lc", command])

    def _supervisorctl(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self._run(
            [
                "docker",
                "exec",
                self.container_name,
                "supervisorctl",
                "-c",
                self.supervisor_config_path,
                *args,
            ],
            check=check,
        )

    def _wait_for_supervisor(self) -> None:
        deadline = time.monotonic() + self.supervisor_wait_timeout_seconds
        socket_check = f"[ -S '{self.supervisor_socket_path}' ]"
        last_error = ""
        while time.monotonic() < deadline:
            socket_status = self._run(
                ["docker", "exec", self.container_name, "sh", "-lc", socket_check],
                check=False,
            )
            if socket_status.returncode != 0:
                last_error = f"Supervisor socket {self.supervisor_socket_path} is not ready yet."
                time.sleep(self.supervisor_poll_interval_seconds)
                continue
            probe = self._supervisorctl("pid", check=False)
            if probe.returncode == 0:
                return
            last_error = (probe.stderr or probe.stdout).strip() or f"supervisorctl pid exited with status {probe.returncode}."
            time.sleep(self.supervisor_poll_interval_seconds)
        raise RuntimeError(
            "supervisorctl did not become ready in container "
            f"{self.container_name} within {self.supervisor_wait_timeout_seconds:g} seconds: "
            f"{last_error or 'control socket unavailable.'}"
        )

    def _wait_for_supervisor_service_state(self, service: str, desired_states: set[str]) -> None:
        deadline = time.monotonic() + self.supervisor_wait_timeout_seconds
        last_status = ""
        while time.monotonic() < deadline:
            result = self._supervisorctl("status", service, check=False)
            output = (result.stdout or result.stderr or "").strip()
            parts = output.split()
            status = parts[1] if len(parts) >= 2 else ""
            if status in desired_states:
                return
            last_status = output or f"exit status {result.returncode}"
            time.sleep(self.supervisor_poll_interval_seconds)
        raise RuntimeError(
            f"Service {service} did not reach one of {sorted(desired_states)} within "
            f"{self.supervisor_wait_timeout_seconds:g} seconds: {last_status or 'status unavailable.'}"
        )

    def _wait_for_ports_released(self, ports: tuple[int, ...]) -> None:
        if not ports:
            return
        deadline = time.monotonic() + self.supervisor_wait_timeout_seconds
        last_in_use = list(ports)
        while time.monotonic() < deadline:
            result = self._run(
                ["docker", "exec", self.container_name, "sh", "-lc", "ss -ltnH"],
                check=False,
            )
            output = result.stdout or ""
            in_use = [port for port in ports if re.search(rf":{port}\\s", output)]
            if not in_use:
                return
            last_in_use = in_use
            time.sleep(self.supervisor_poll_interval_seconds)
        raise RuntimeError(
            f"Ports {last_in_use} did not release within {self.supervisor_wait_timeout_seconds:g} seconds."
        )

    def restart_services(self, *services: str) -> list[str]:
        self._wait_for_supervisor()
        restarted: list[str] = []
        for service in services:
            ports = self.supervisor_service_ports.get(service, ())
            if ports:
                restart_flag_path = self.openmrs_restart_flag_path if service == "openmrs" else ""
                if restart_flag_path:
                    self._run(
                        [
                            "docker",
                            "exec",
                            self.container_name,
                            "sh",
                            "-lc",
                            f"touch {shlex.quote(restart_flag_path)}",
                        ]
                    )
                stop_result = self._supervisorctl("stop", service, check=False)
                stop_output = ((stop_result.stdout or "") + (stop_result.stderr or "")).strip()
                if stop_result.returncode != 0 and "not running" not in stop_output.lower():
                    raise RuntimeError(
                        f"Failed to stop {service} before restart: {stop_output or stop_result.returncode}"
                    )
                self._wait_for_supervisor_service_state(service, {"STOPPED", "EXITED", "FATAL"})
                self._wait_for_ports_released(ports)
                try:
                    start_result = self._supervisorctl("start", service, check=False)
                    start_output = ((start_result.stdout or "") + (start_result.stderr or "")).strip()
                    if start_result.returncode != 0:
                        raise RuntimeError(
                            f"Failed to start {service} after restart: {start_output or start_result.returncode}"
                        )
                finally:
                    if restart_flag_path:
                        self._run(
                            [
                                "docker",
                                "exec",
                                self.container_name,
                                "sh",
                                "-lc",
                                f"rm -f {shlex.quote(restart_flag_path)}",
                            ],
                            check=False,
                        )
            else:
                self._supervisorctl("restart", service)
            restarted.append(service)
        return restarted

    def run_verify_script(self, script_path: str | Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(script_path), *args],
            capture_output=True,
            text=True,
            check=False,
        )


class KeycloakAdminRestClient:
    def __init__(
        self,
        *,
        base_url: str,
        realm: str,
        admin_username: str,
        admin_password: str,
        client_id: str = "admin-cli",
        client_secret: str | None = None,
        admin_realm: str = "master",
        timeout_seconds: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.realm = realm
        self.admin_username = admin_username
        self.admin_password = admin_password
        self.client_id = client_id
        self.client_secret = client_secret
        self.admin_realm = admin_realm
        self.timeout_seconds = timeout_seconds
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0
        self._client_uuid_cache: dict[str, str] = {}

    def _invalidate_token(self) -> None:
        self._access_token = None
        self._access_token_expires_at = 0.0

    def _token(self) -> str:
        if self._access_token is not None and time.monotonic() < self._access_token_expires_at:
            return self._access_token
        payload = {
            "grant_type": "password",
            "client_id": self.client_id,
            "username": self.admin_username,
            "password": self.admin_password,
        }
        if self.client_secret:
            payload["client_secret"] = self.client_secret
        encoded = urllib.parse.urlencode(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/realms/{self.admin_realm}/protocol/openid-connect/token",
            data=encoded,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
        self._access_token = str(data["access_token"])
        expires_in = int(data.get("expires_in") or 300)
        self._access_token_expires_at = time.monotonic() + max(expires_in - 30, 0)
        return self._access_token

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | list[dict[str, Any]] | None = None,
        query: dict[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        for attempt in range(2):
            headers = {"Authorization": f"Bearer {self._token()}"}
            if payload is not None:
                headers["Content-Type"] = "application/json"
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    body = response.read().decode("utf-8")
                    return json.loads(body) if body else None
            except urllib.error.HTTPError as exc:
                if exc.code == 404 and allow_not_found:
                    return None
                if exc.code == 401 and attempt == 0:
                    self._invalidate_token()
                    continue
                raise

    def get_realm_role(self, role_name: str) -> dict[str, Any] | None:
        quoted = urllib.parse.quote(role_name, safe="")
        return self._request(
            "GET",
            f"/admin/realms/{self.realm}/roles/{quoted}",
            allow_not_found=True,
        )

    def _client_uuid(self, client_id: str) -> str:
        cached = self._client_uuid_cache.get(client_id)
        if cached:
            return cached
        clients = self._request(
            "GET",
            f"/admin/realms/{self.realm}/clients",
            query={"clientId": client_id, "max": 2},
        )
        for client in clients or []:
            if str(client.get("clientId", "")) == client_id:
                client_uuid = str(client["id"])
                self._client_uuid_cache[client_id] = client_uuid
                return client_uuid
        raise RuntimeError(f"Keycloak client '{client_id}' was not found in realm '{self.realm}'.")

    def get_client_role(self, client_id: str, role_name: str) -> dict[str, Any] | None:
        client_uuid = self._client_uuid(client_id)
        quoted = urllib.parse.quote(role_name, safe="")
        return self._request(
            "GET",
            f"/admin/realms/{self.realm}/clients/{client_uuid}/roles/{quoted}",
            allow_not_found=True,
        )

    def create_client_role(self, client_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        client_uuid = self._client_uuid(client_id)
        self._request("POST", f"/admin/realms/{self.realm}/clients/{client_uuid}/roles", payload=payload)
        role_name = str(payload["name"])
        role = self.get_client_role(client_id, role_name)
        if role is None:
            raise RuntimeError(f"Keycloak client role '{client_id}/{role_name}' was not found after creation.")
        return role

    def delete_client_role(self, client_id: str, role_name: str) -> None:
        client_uuid = self._client_uuid(client_id)
        quoted = urllib.parse.quote(role_name, safe="")
        self._request("DELETE", f"/admin/realms/{self.realm}/clients/{client_uuid}/roles/{quoted}")

    def create_realm_role(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._request("POST", f"/admin/realms/{self.realm}/roles", payload=payload)
        role_name = str(payload["name"])
        role = self.get_realm_role(role_name)
        if role is None:
            raise RuntimeError(f"Keycloak role '{role_name}' was not found after creation.")
        return role

    def delete_realm_role(self, role_name: str) -> None:
        quoted = urllib.parse.quote(role_name, safe="")
        self._request("DELETE", f"/admin/realms/{self.realm}/roles/{quoted}")

    def find_user_by_username(self, username: str) -> dict[str, Any] | None:
        users = self._request(
            "GET",
            f"/admin/realms/{self.realm}/users",
            query={"username": username, "exact": "true", "max": 2},
        )
        if not users:
            return None
        return dict(users[0])

    def create_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._request("POST", f"/admin/realms/{self.realm}/users", payload=payload)
        user = self.find_user_by_username(str(payload["username"]))
        if user is None:
            raise RuntimeError(f"Keycloak user '{payload['username']}' was not found after creation.")
        return user

    def update_user(self, user_id: str, payload: dict[str, Any]) -> None:
        self._request("PUT", f"/admin/realms/{self.realm}/users/{user_id}", payload=payload)

    def delete_user(self, user_id: str) -> None:
        self._request("DELETE", f"/admin/realms/{self.realm}/users/{user_id}")

    def get_user_realm_roles(self, user_id: str) -> list[dict[str, Any]]:
        roles = self._request("GET", f"/admin/realms/{self.realm}/users/{user_id}/role-mappings/realm")
        return [dict(role) for role in (roles or [])]

    def add_user_realm_roles(self, user_id: str, roles: list[dict[str, Any]]) -> None:
        if not roles:
            return
        self._request(
            "POST",
            f"/admin/realms/{self.realm}/users/{user_id}/role-mappings/realm",
            payload=roles,
        )

    def delete_user_realm_roles(self, user_id: str, roles: list[dict[str, Any]]) -> None:
        if not roles:
            return
        self._request(
            "DELETE",
            f"/admin/realms/{self.realm}/users/{user_id}/role-mappings/realm",
            payload=roles,
        )

    def update_realm_role(self, role_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        quoted = urllib.parse.quote(role_name, safe="")
        self._request("PUT", f"/admin/realms/{self.realm}/roles/{quoted}", payload=payload)
        role = self.get_realm_role(str(payload["name"]))
        if role is None:
            raise RuntimeError(f"Keycloak role '{payload['name']}' was not found after update.")
        return role


class OpenMRSRestClient:
    max_list_limit = 100

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        timeout_seconds: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout_seconds = timeout_seconds

    def _auth_header(self) -> str:
        token = base64.b64encode(f"{self.username}:{self.password}".encode("utf-8")).decode("ascii")
        return f"Basic {token}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {
            "Authorization": self._auth_header(),
            "Accept": "application/json",
        }
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and allow_not_found:
                return None
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenMRS request failed: {method} {url} -> {exc.code}: {body}") from exc

    def _results(self, resource_path: str, *, query: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        def normalize_results(payload: Any) -> list[dict[str, Any]]:
            if isinstance(payload, dict) and "results" in payload:
                return [dict(item) for item in payload["results"]]
            if isinstance(payload, list):
                return [dict(item) for item in payload]
            return []

        page_query = dict(query or {})
        raw_limit = page_query.get("limit")
        try:
            requested_limit = int(raw_limit) if raw_limit is not None else None
        except (TypeError, ValueError):
            requested_limit = None

        if requested_limit is None or requested_limit <= self.max_list_limit:
            payload = self._request("GET", resource_path, query=page_query or None)
            return normalize_results(payload)

        collected: list[dict[str, Any]] = []
        start_index = int(page_query.get("startIndex", 0) or 0)
        while len(collected) < requested_limit:
            current_query = dict(page_query)
            current_query["limit"] = min(self.max_list_limit, requested_limit - len(collected))
            current_query["startIndex"] = start_index
            payload = self._request("GET", resource_path, query=current_query)
            page_results = normalize_results(payload)
            collected.extend(page_results)
            if len(page_results) < int(current_query["limit"]):
                break
            start_index += len(page_results)
        return collected[:requested_limit]

    def _post_create(self, resource_path: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._request("POST", resource_path, payload=payload)
        return dict(result or {})

    def _post_update(self, resource_path: str, resource_uuid: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._request("POST", f"{resource_path}/{resource_uuid}", payload=payload)
        return dict(result or {})

    def _delete(self, resource_path: str, resource_uuid: str, *, reason: str = "") -> None:
        query = {"reason": reason} if reason else None
        self._request("DELETE", f"{resource_path}/{resource_uuid}", query=query, allow_not_found=True)

    def _ref_uuid(self, value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("uuid", ""))
        return str(value or "")

    def wait_until_ready(self, timeout_seconds: int = 120) -> None:
        deadline = datetime.now(timezone.utc).timestamp() + timeout_seconds
        last_error: Exception | None = None
        while datetime.now(timezone.utc).timestamp() < deadline:
            try:
                self._request("GET", "/ws/rest/v1/session")
                return
            except Exception as exc:  # pragma: no cover - best-effort polling path
                last_error = exc
                time.sleep(2)
        if last_error is not None:
            raise RuntimeError(f"OpenMRS did not become ready in time: {last_error}") from last_error
        raise RuntimeError("OpenMRS did not become ready in time.")

    def find_location(self, name: str) -> dict[str, Any] | None:
        for location in self._results("/ws/rest/v1/location", query={"q": name, "v": "full", "limit": 100}):
            if str(location.get("name", "")) == name:
                return location
        return None

    def find_encounter_type(self, name: str) -> dict[str, Any] | None:
        for encounter_type in self._results("/ws/rest/v1/encountertype", query={"q": name, "v": "full", "limit": 100}):
            if str(encounter_type.get("name", "")) == name:
                return encounter_type
        return None

    def find_queue(self, name: str, location_uuid: str) -> dict[str, Any] | None:
        for queue in self._results("/ws/rest/v1/queue", query={"v": "full", "limit": 500}):
            if str(queue.get("name", "")) == name and self._ref_uuid(queue.get("location")) == location_uuid:
                return queue
        return None

    def create_queue(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_create("/ws/rest/v1/queue", payload)

    def update_queue(self, queue_uuid: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_update("/ws/rest/v1/queue", queue_uuid, payload)

    def delete_queue(self, queue_uuid: str, reason: str = "") -> None:
        self._delete("/ws/rest/v1/queue", queue_uuid, reason=reason)

    def find_queue_room(self, name: str, queue_uuid: str) -> dict[str, Any] | None:
        for room in self._results("/ws/rest/v1/queue-room", query={"v": "full", "limit": 500}):
            if str(room.get("name", "")) == name and self._ref_uuid(room.get("queue")) == queue_uuid:
                return room
        return None

    def create_queue_room(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_create("/ws/rest/v1/queue-room", payload)

    def update_queue_room(self, room_uuid: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_update("/ws/rest/v1/queue-room", room_uuid, payload)

    def delete_queue_room(self, room_uuid: str, reason: str = "") -> None:
        self._delete("/ws/rest/v1/queue-room", room_uuid, reason=reason)

    def find_provider_by_username(self, username: str) -> dict[str, Any] | None:
        for provider in self._results("/ws/rest/v1/provider", query={"q": username, "v": "full", "limit": 100}):
            person = provider.get("person") or {}
            user = provider.get("user") or {}
            candidate_values = {
                str(provider.get("identifier", "")),
                str(provider.get("display", "")),
                str(person.get("display", "")),
                str(user.get("username", "")),
            }
            if username in candidate_values:
                return provider
        return None

    def find_queue_room_provider(self, queue_room_uuid: str, provider_uuid: str) -> dict[str, Any] | None:
        for assignment in self._results("/ws/rest/v1/queue-room-provider", query={"v": "full", "limit": 500}):
            if self._ref_uuid(assignment.get("queueRoom")) == queue_room_uuid and self._ref_uuid(
                assignment.get("provider")
            ) == provider_uuid:
                return assignment
        return None

    def create_queue_room_provider(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_create("/ws/rest/v1/queue-room-provider", payload)

    def delete_queue_room_provider(self, room_provider_uuid: str, reason: str = "") -> None:
        self._delete("/ws/rest/v1/queue-room-provider", room_provider_uuid, reason=reason)

    def find_billable_service(self, service_name: str) -> dict[str, Any] | None:
        for service in self._results("/ws/rest/v1/billing/billableService", query={"v": "full", "limit": 500}):
            if str(service.get("name", "")) == service_name:
                return service
        return None

    def find_payment_mode(self, name: str) -> dict[str, Any] | None:
        for payment_mode in self._results("/ws/rest/v1/billing/paymentMode", query={"v": "full", "limit": 500}):
            if str(payment_mode.get("name", "")) == name:
                return payment_mode
        return None

    def find_cash_point(self, name: str) -> dict[str, Any] | None:
        for cash_point in self._results("/ws/rest/v1/billing/cashPoint", query={"v": "full", "limit": 500}):
            if str(cash_point.get("name", "")) == name:
                return cash_point
        return None

    def find_cashier_item_price(self, name: str) -> dict[str, Any] | None:
        for item_price in self._results(
            "/ws/rest/v1/billing/cashierItemPrice",
            query={"v": "full", "includeAll": "true", "limit": 500},
        ):
            if str(item_price.get("name", "")) == name:
                return item_price
        return None

    def create_cashier_item_price(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_create("/ws/rest/v1/billing/cashierItemPrice", payload)

    def update_cashier_item_price(self, price_uuid: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_update("/ws/rest/v1/billing/cashierItemPrice", price_uuid, payload)

    def delete_cashier_item_price(self, price_uuid: str, reason: str = "") -> None:
        self._delete("/ws/rest/v1/billing/cashierItemPrice", price_uuid, reason=reason)

    def find_stock_item(self, stock_item_name: str) -> dict[str, Any] | None:
        results = self._results(
            "/ws/rest/v1/stockmanagement/stockitem",
            query={"q": stock_item_name, "v": "full", "limit": 100},
        )
        exact_matches: list[dict[str, Any]] = []
        search_value = stock_item_name.casefold()
        for item in results:
            candidate_values = {
                str(item.get("commonName", "")).casefold(),
                str(item.get("drugName", "")).casefold(),
                str(item.get("conceptName", "")).casefold(),
            }
            if search_value in candidate_values:
                exact_matches.append(item)
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            raise RuntimeError(f"Stock item '{stock_item_name}' is ambiguous in OpenMRS stockmanagement.")
        if len(results) == 1:
            return results[0]
        return None

    def find_stock_rule(self, name: str, stock_item_uuid: str, location_uuid: str) -> dict[str, Any] | None:
        results = self._results(
            "/ws/rest/v1/stockmanagement/stockrule",
            query={
                "locationUuid": location_uuid,
                "stockItemUuid": stock_item_uuid,
                "includeAll": "true",
                "limit": 200,
            },
        )
        for stock_rule in results:
            if str(stock_rule.get("name", "")) == name:
                return stock_rule
        if len(results) == 1:
            return results[0]
        return None

    def create_stock_rule(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_create("/ws/rest/v1/stockmanagement/stockrule", payload)

    def update_stock_rule(self, rule_uuid: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_update("/ws/rest/v1/stockmanagement/stockrule", rule_uuid, payload)

    def delete_stock_rule(self, rule_uuid: str, reason: str = "") -> None:
        self._delete("/ws/rest/v1/stockmanagement/stockrule", rule_uuid, reason=reason)


@dataclass(slots=True)
class ApplyOutcome:
    change_id: str
    run_id: str
    status: str
    results: dict[str, Any]
    warnings: list[str]
    errors: list[str]


class BundleApplier:
    def __init__(
        self,
        repo_root: str | Path,
        *,
        state_store: ChangeControlStateStore | None = None,
        runtime_client: RuntimeClient | None = None,
        keycloak_client: KeycloakClient | None = None,
        openmrs_client: OpenMRSClient | None = None,
        runtime_env: dict[str, str] | None = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.runtime_env = runtime_env if runtime_env is not None else load_runtime_env(self.repo_root)
        self.state_store = state_store or ChangeControlStateStore(self.repo_root / "data" / "emr-os-control-plane")
        self.runtime_client = runtime_client or DockerRuntimeClient(
            self.runtime_env.get("CLINICDX_CONTAINER_NAME", "ClinicDx_backend")
        )
        self.openmrs_client = openmrs_client
        self.keycloak_client = keycloak_client
        self.verify_script = self.repo_root / "scripts" / "verify-backend.sh"

    def _require_runtime_env(self, name: str, *, purpose: str) -> str:
        value = str(self.runtime_env.get(name, "") or "")
        if value:
            return value
        raise RuntimeError(
            f"{name} is required for {purpose} but was not found in the runtime environment or .secrets.env."
        )

    def _get_keycloak_client(self) -> KeycloakClient:
        if self.keycloak_client is None:
            self.keycloak_client = KeycloakAdminRestClient(
                base_url=self.runtime_env.get("KEYCLOAK_PUBLIC_URL", "http://localhost:8083"),
                realm=self.runtime_env.get("KEYCLOAK_REALM", "healthcare"),
                admin_username=self.runtime_env.get("KEYCLOAK_ADMIN_USER", "admin"),
                admin_password=self._require_runtime_env(
                    "KEYCLOAK_ADMIN_PASSWORD",
                    purpose="the Keycloak admin client",
                ),
                client_id=self.runtime_env.get("LMIC_EMR_OS_KEYCLOAK_ADMIN_CLIENT_ID", "admin-cli"),
                client_secret=self.runtime_env.get("LMIC_EMR_OS_KEYCLOAK_ADMIN_CLIENT_SECRET"),
                admin_realm=self.runtime_env.get("LMIC_EMR_OS_KEYCLOAK_ADMIN_REALM", "master"),
            )
        return self.keycloak_client

    def _get_openmrs_client(self) -> OpenMRSClient:
        if self.openmrs_client is None:
            self.openmrs_client = OpenMRSRestClient(
                base_url=self.runtime_env.get("OPENMRS_PUBLIC_URL", "http://localhost:8080/openmrs"),
                username=self.runtime_env.get("LMIC_EMR_OS_OPENMRS_ADMIN_USER", "admin"),
                password=self._require_runtime_env(
                    "OPENMRS_ADMIN_PASSWORD",
                    purpose="the OpenMRS admin client",
                ),
            )
        return self.openmrs_client

    def _openmrs_extensions_target(self) -> str:
        return "/opt/clinicDx/data/emr-os/openmrs-extensions"

    def _openmrs_managed_config_target(self) -> str:
        return "/opt/clinicDx/data/emr-os/openmrs-managed-config"

    def _wait_for_openmrs_imports_to_settle(
        self,
        timeout_seconds: int = 900,
        *,
        stable_polls_required: int = 3,
        poll_interval_seconds: int = 10,
    ) -> None:
        if not isinstance(self.runtime_client, DockerRuntimeClient):
            return
        password = str(self.runtime_env.get("OPENMRS_DB_PASSWORD", "") or "")
        if not password:
            return
        db_user = str(self.runtime_env.get("OPENMRS_DB_USER", "openmrs") or "openmrs")
        db_name = str(self.runtime_env.get("OPENMRS_DB_NAME", "openmrs") or "openmrs")
        query = (
            "SELECT CASE "
            "WHEN EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = 'openconceptlab_import') "
            "THEN (SELECT COUNT(*) FROM openconceptlab_import WHERE local_date_stopped IS NULL) "
            "ELSE 0 END;"
        )
        command = " ".join(
            [
                "mysql",
                "-N",
                shlex.quote(f"--user={db_user}"),
                shlex.quote(f"--password={password}"),
                shlex.quote(db_name),
                "-e",
                shlex.quote(query),
            ]
        )
        deadline = time.monotonic() + timeout_seconds
        last_count = 0
        zero_streak = 0
        while time.monotonic() < deadline:
            result = self.runtime_client.exec_shell(command)
            output = (result.stdout or "").strip().splitlines()
            try:
                last_count = int(output[-1]) if output else 0
            except ValueError:
                return
            if last_count == 0:
                zero_streak += 1
                if zero_streak >= stable_polls_required:
                    return
            else:
                zero_streak = 0
            time.sleep(poll_interval_seconds)
        raise RuntimeError(
            "OpenMRS still has in-progress OpenConceptLab imports after "
            f"{timeout_seconds} seconds ({last_count} active imports remain)."
        )

    def _selected_products(self, products: list[str] | None) -> list[str]:
        selected = products or DEFAULT_PRODUCTS
        ordered: list[str] = []
        seen: set[str] = set()
        for product in selected:
            normalized = str(product)
            if normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
        return ordered

    def _common_active_change_id(self, active_changes: dict[str, str]) -> str:
        unique_ids = {value for value in active_changes.values() if value}
        if len(unique_ids) == 1:
            return next(iter(unique_ids))
        return ""

    def get_current_config(
        self,
        products: list[str] | None = None,
        *,
        require_consistent: bool = True,
    ) -> ClinicConfigModel | dict[str, ClinicConfigModel] | None:
        selected_products = self._selected_products(products) if products is not None else DEFAULT_PRODUCTS
        selected_products = [product for product in selected_products if product in DEFAULT_PRODUCTS]
        active_change_ids = self.state_store.get_active_change_ids_by_product(selected_products)
        if not active_change_ids or not any(active_change_ids.values()):
            return None if require_consistent else {}

        if require_consistent:
            unique_ids = {change_id for change_id in active_change_ids.values() if change_id}
            if len(unique_ids) != 1 or any(not active_change_ids.get(product, "") for product in selected_products):
                raise RuntimeError(
                    "Current configuration is not consistent across the requested products. "
                    f"Active change ids: {active_change_ids}"
                )
            change_id = next(iter(unique_ids))
            record = self.state_store.get_change(change_id)
            if record is None or not Path(record.config_path).exists():
                return None
            return ClinicConfigModel.from_json_file(record.config_path)

        configs: dict[str, ClinicConfigModel] = {}
        config_cache: dict[str, ClinicConfigModel] = {}
        for product, change_id in active_change_ids.items():
            if not change_id:
                continue
            if change_id not in config_cache:
                record = self.state_store.get_change(change_id)
                if record is None or not Path(record.config_path).exists():
                    continue
                config_cache[change_id] = ClinicConfigModel.from_json_file(record.config_path)
            configs[product] = config_cache[change_id]
        return configs

    def _product_snapshot_ready(self, product: str, snapshot_path: Path) -> bool:
        if product == "keycloak":
            return (snapshot_path / "pre-apply.json").exists()
        return snapshot_path.exists()

    def _ensure_required_clients(self, selected_products: list[str]) -> None:
        if "keycloak" in selected_products:
            self._get_keycloak_client()
        if "openmrs" in selected_products:
            self._get_openmrs_client()

    def _expand_runtime_value(self, value: str) -> str:
        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            return self.runtime_env.get(key, match.group(0))

        return re.sub(r"\$\{([^}]+)\}", replace, value)

    def _expand_runtime_data(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._expand_runtime_value(value)
        if isinstance(value, list):
            return [self._expand_runtime_data(item) for item in value]
        if isinstance(value, dict):
            expanded: dict[Any, Any] = {}
            for key, item in value.items():
                expanded_key = self._expand_runtime_value(key) if isinstance(key, str) else key
                expanded[expanded_key] = self._expand_runtime_data(item)
            return expanded
        return value

    def _normalize_keycloak_role_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        attributes = normalized.get("attributes")
        if isinstance(attributes, dict):
            normalized_attributes: dict[str, list[str]] = {}
            for key, value in attributes.items():
                if isinstance(value, list):
                    normalized_attributes[str(key)] = [str(item) for item in value]
                elif value is None:
                    normalized_attributes[str(key)] = []
                else:
                    normalized_attributes[str(key)] = [str(value)]
            normalized["attributes"] = normalized_attributes
        return normalized

    def _required_keycloak_client_roles(self, realm_roles: list[dict[str, Any]]) -> dict[str, list[str]]:
        required: dict[str, set[str]] = {}
        for role in realm_roles:
            composites = role.get("composites")
            client_composites = composites.get("client", {}) if isinstance(composites, dict) else {}
            if not isinstance(client_composites, dict):
                continue
            for client_id, client_role_names in client_composites.items():
                if not isinstance(client_role_names, list):
                    continue
                names = required.setdefault(str(client_id), set())
                names.update(str(name) for name in client_role_names if str(name))
        return {client_id: sorted(names) for client_id, names in required.items()}

    def _expected_status_codes(self, expected: str) -> set[int]:
        return {int(match) for match in re.findall(r"\b(\d{3})\b", expected)}

    def _http_status(self, url: str, headers: dict[str, str] | None = None) -> int:
        handlers: list[Any] = [_NoRedirectHandler()]
        if urllib.parse.urlparse(url).scheme == "https":
            handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
        opener = urllib.request.build_opener(*handlers)
        request = urllib.request.Request(url, headers=headers or {}, method="GET")
        try:
            with opener.open(request, timeout=30) as response:
                return int(getattr(response, "status", response.getcode()))
        except urllib.error.HTTPError as exc:
            return int(exc.code)

    def _wait_for_http_statuses(
        self,
        url: str,
        expected_statuses: set[int],
        *,
        timeout_seconds: int = 180,
        headers: dict[str, str] | None = None,
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        last_status = 0
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                last_status = self._http_status(url, headers=headers)
                if last_status in expected_statuses:
                    return
                last_error = None
            except Exception as exc:  # pragma: no cover - polling network failures are timing-sensitive
                last_error = exc
            time.sleep(2)

        detail = f"last status {last_status}" if last_status else "no HTTP response"
        if last_error is not None:
            detail = f"{detail}; last error: {last_error}"
        raise RuntimeError(
            f"Endpoint '{url}' did not reach one of {sorted(expected_statuses)} "
            f"within {timeout_seconds} seconds ({detail})."
        )

    def _fetch_access_token(
        self,
        *,
        client_id_env: str,
        client_secret_env: str,
        username_env: str,
        password_env: str,
    ) -> str:
        client_id = self.runtime_env.get(client_id_env, "")
        client_secret = self.runtime_env.get(client_secret_env, "")
        username = self.runtime_env.get(username_env, "")
        password = self.runtime_env.get(password_env, "")
        if not client_id or not client_secret or not username or not password:
            raise RuntimeError(
                "Missing runtime environment for token fetch "
                f"({client_id_env}, {client_secret_env}, {username_env}, {password_env})."
            )
        payload = urllib.parse.urlencode(
            {
                "grant_type": "password",
                "client_id": client_id,
                "client_secret": client_secret,
                "username": username,
                "password": password,
            }
        ).encode("utf-8")
        token_url = (
            f"{self.runtime_env.get('KEYCLOAK_PUBLIC_URL', 'http://localhost:8083')}"
            f"/realms/{self.runtime_env.get('KEYCLOAK_REALM', 'healthcare')}/protocol/openid-connect/token"
        )
        request = urllib.request.Request(
            token_url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        token = str(data.get("access_token", ""))
        if not token:
            raise RuntimeError(f"Token endpoint did not return an access token for user '{username}'.")
        return token

    def _verify_orthanc_dicomweb_access(self, url: str, config: ClinicConfigModel) -> dict[str, Any]:
        planned_role_names = set(OrthancAdapter().build_plan(config).payload.get("permissions", {}).get("roles", {}).keys())
        managed_realm_roles = {
            role_name
            for role in config.identity_model.roles
            for role_name in (role.keycloak_roles or [role.id])
        }
        actor_specs = [
            {
                "id": "admin",
                "roleName": "admin",
                "client_id_env": "ORTHANC_CLIENT_ID",
                "client_secret_env": "ORTHANC_CLIENT_SECRET",
                "username_env": "VERIFY_ADMIN_USER",
                "password_env": "VERIFY_ADMIN_PASSWORD",
            },
            {
                "id": "doctor",
                "roleName": "doctor",
                "client_id_env": "ORTHANC_CLIENT_ID",
                "client_secret_env": "ORTHANC_CLIENT_SECRET",
                "username_env": "CLINICDX_DOCTOR_USERNAME",
                "password_env": "CLINICDX_DOCTOR_PASSWORD",
            },
            {
                "id": "lab-technician",
                "roleName": "lab-technician",
                "client_id_env": "ORTHANC_CLIENT_ID",
                "client_secret_env": "ORTHANC_CLIENT_SECRET",
                "username_env": "CLINICDX_LAB_USERNAME",
                "password_env": "CLINICDX_LAB_PASSWORD",
            },
            {
                "id": "radiologist",
                "roleName": "radiologist",
                "client_id_env": "ORTHANC_CLIENT_ID",
                "client_secret_env": "ORTHANC_CLIENT_SECRET",
                "username_env": "CLINICDX_RAD_USERNAME",
                "password_env": "CLINICDX_RAD_PASSWORD",
            },
        ]
        actors: list[dict[str, Any]] = []
        for spec in actor_specs:
            role_name = str(spec["roleName"])
            if role_name == "admin":
                expected = {200}
            elif role_name in planned_role_names:
                expected = {200}
            elif role_name in managed_realm_roles:
                expected = {401, 403}
            else:
                continue
            actors.append({**spec, "expected": expected})
        results: list[dict[str, Any]] = []
        failures: list[str] = []
        for actor in actors:
            token = self._fetch_access_token(
                client_id_env=actor["client_id_env"],
                client_secret_env=actor["client_secret_env"],
                username_env=actor["username_env"],
                password_env=actor["password_env"],
            )
            status = self._http_status(url, headers={"Authorization": f"Bearer {token}"})
            actor_result = {
                "actor": actor["id"],
                "statusCode": status,
                "expected": sorted(actor["expected"]),
                "ok": status in actor["expected"],
            }
            results.append(actor_result)
            if not actor_result["ok"]:
                failures.append(str(actor["id"]))
        return {"ok": not failures, "actors": results, "failures": failures}

    def _copy_container_file_to_text(self, path: str) -> str:
        if not self.runtime_client.path_exists(path):
            raise FileNotFoundError(path)
        with tempfile.TemporaryDirectory() as tmpdir:
            destination = Path(tmpdir) / Path(path).name
            self.runtime_client.copy_from_container(path, destination)
            return destination.read_text(encoding="utf-8")

    def _write_snapshot_artifact(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _sync_openmrs_simulated_state(
        self,
        configuration_dir: Path,
        *,
        extensions_dir: Path | None = None,
    ) -> None:
        client = self._get_openmrs_client()
        sync_method = getattr(client, "load_configuration_directory", None)
        if callable(sync_method):
            sync_method(configuration_dir, extensions_dir=extensions_dir)

    def _apply_product(
        self,
        product: str,
        record: ChangeRecord,
        snapshot_dir: Path,
        restart_services: bool,
    ) -> dict[str, Any]:
        if product == "openmrs":
            return self._apply_openmrs(record, snapshot_dir, restart_services)
        if product == "openelis":
            return self._apply_openelis(record, snapshot_dir, restart_services)
        if product == "orthanc":
            return self._apply_orthanc(record, snapshot_dir, restart_services)
        if product == "keycloak":
            return self._apply_keycloak(record, snapshot_dir)
        raise ValueError(f"Unknown product '{product}'.")

    def _restore_product(
        self,
        product: str,
        snapshot_dir: Path,
        restart_services: bool,
    ) -> dict[str, Any]:
        if product == "openmrs":
            return self._restore_openmrs(snapshot_dir, restart_services)
        if product == "openelis":
            return self._restore_openelis(snapshot_dir, restart_services)
        if product == "orthanc":
            return self._restore_orthanc(snapshot_dir, restart_services)
        if product == "keycloak":
            return self._restore_keycloak(snapshot_dir)
        raise ValueError(f"Unknown product '{product}'.")

    def _auto_rollback_products(
        self,
        snapshot_dir: Path,
        applied_products: list[str],
    ) -> tuple[dict[str, Any], list[str]]:
        rollback_results: dict[str, Any] = {}
        rollback_errors: list[str] = []
        for product in reversed(applied_products):
            try:
                rollback_results[product] = self._restore_product(
                    product,
                    snapshot_dir / product,
                    restart_services=product in {"openmrs", "openelis", "orthanc"},
                )
            except Exception as exc:  # pragma: no cover - exercised through fake clients
                rollback_errors.append(f"{product}: {exc}")
        return (
            {
                "rolledBackProducts": list(reversed(applied_products)),
                "results": rollback_results,
                "forcedRestarts": [product for product in applied_products if product in {"openmrs", "openelis", "orthanc"}],
            },
            rollback_errors,
        )

    def _check_products_for_verification(self, selected_products: list[str], check_product: str) -> bool:
        normalized = str(check_product).lower()
        if normalized == "workflow":
            return "openmrs" in selected_products
        if normalized == "openmrs":
            return "openmrs" in selected_products
        if normalized == "keycloak":
            return "keycloak" in selected_products
        if normalized == "openelis":
            return "openelis" in selected_products
        if normalized == "orthanc":
            return "orthanc" in selected_products
        return True

    def _run_verification_plan(
        self,
        record: ChangeRecord,
        config: ClinicConfigModel,
        selected_products: list[str],
        *,
        smoke_script_passed: bool,
    ) -> dict[str, Any]:
        plan_payload = json.loads(Path(record.verification_plan_path).read_text(encoding="utf-8"))
        plan = VerificationPlan.from_dict(plan_payload)
        executed_checks: list[dict[str, Any]] = []
        failures: list[str] = []
        for check in plan.checks:
            result = self._execute_verification_check(
                check,
                record,
                config,
                selected_products,
                smoke_script_passed=smoke_script_passed,
            )
            executed_checks.append(result)
            if result["status"] == "failed" and check.required:
                failures.append(check.check_id)

        return {
            "ok": not failures,
            "failedRequiredChecks": failures,
            "executedChecks": len([item for item in executed_checks if item["status"] != "skipped"]),
            "checks": executed_checks,
        }

    def _execute_verification_check(
        self,
        check: Any,
        record: ChangeRecord,
        config: ClinicConfigModel,
        selected_products: list[str],
        *,
        smoke_script_passed: bool,
    ) -> dict[str, Any]:
        result = {
            "checkId": check.check_id,
            "product": check.product,
            "title": check.title,
            "status": "passed",
            "details": {},
        }
        if not self._check_products_for_verification(selected_products, check.product):
            result["status"] = "skipped"
            result["details"] = {"reason": "product not selected for this run"}
            return result

        if check.method == "http-get":
            target = self._expand_runtime_value(check.target)
            expected_codes = self._expected_status_codes(check.expected)
            status_code = self._http_status(target)
            result["status"] = "passed" if status_code in expected_codes else "failed"
            result["details"] = {
                "target": target,
                "statusCode": status_code,
                "expectedStatusCodes": sorted(expected_codes),
                "smokeScriptPassed": smoke_script_passed,
            }
            return result

        if check.method == "http-get-auth":
            target = self._expand_runtime_value(check.target)
            if check.check_id == "orthanc.dicomweb.studies":
                authz_details = self._verify_orthanc_dicomweb_access(target, config)
                result["status"] = "passed" if authz_details["ok"] else "failed"
                result["details"] = authz_details | {"target": target}
                return result
            result["status"] = "skipped"
            result["details"] = {"reason": f"unsupported authenticated HTTP check '{check.check_id}'"}
            return result

        if check.method == "analysis-check":
            analysis = json.loads(Path(record.operational_analysis_path).read_text(encoding="utf-8"))
            error_issues = [issue for issue in analysis.get("issues", []) if issue.get("severity") == "error"]
            result["status"] = "passed" if not error_issues else "failed"
            result["details"] = {"errorIssueCount": len(error_issues)}
            return result

        if check.method == "artifact-check":
            artifact_path = Path(record.bundle_dir) / check.target
            result["status"] = "passed" if artifact_path.exists() else "failed"
            result["details"] = {"path": str(artifact_path), "exists": artifact_path.exists()}
            return result

        if check.method == "file-check":
            target = self._expand_runtime_value(check.target)
            if target == self._openmrs_managed_config_target():
                exists = self.runtime_client.path_exists(target)
                direct_domains = self._direct_openmrs_domains(Path(record.openmrs_pack_dir))
                expected_directories = [domain.name for domain in direct_domains]
                present_directories = [
                    domain
                    for domain in expected_directories
                    if self.runtime_client.path_exists(f"{target}/{domain}")
                ]
                result["status"] = "passed" if exists and present_directories == expected_directories else "failed"
                result["details"] = {
                    "target": target,
                    "expectedDirectories": expected_directories,
                    "presentDirectories": present_directories,
                }
                return result

            if not self.runtime_client.path_exists(target):
                result["status"] = "failed"
                result["details"] = {"target": target, "reason": "path missing"}
                return result

            content = self._copy_container_file_to_text(target)
            if check.check_id == "orthanc.permissions.file":
                permissions = json.loads(content)
                role_map = permissions.get("roles", {})
                result["status"] = "passed" if bool(role_map) else "failed"
                result["details"] = {"target": target, "roleCount": len(role_map)}
                return result
            expected = self._expand_runtime_value(check.expected)
            result["status"] = "passed" if expected in content else "failed"
            result["details"] = {"target": target, "containsExpected": expected in content}
            return result

        if check.method == "routing-policy-check":
            target = self._expand_runtime_value(check.target)
            if not self.runtime_client.path_exists(target):
                result["status"] = "failed"
                result["details"] = {"target": target, "reason": "path missing"}
                return result
            payload = json.loads(self._copy_container_file_to_text(target))
            contract = payload.get("contract", {})
            routing_rules = payload.get("routingRules", [])
            ok = (
                contract.get("contractId") == "openmrs-routing-policy-v1"
                and contract.get("mode") == "adapter-owned"
                and contract.get("nativePersistence") is False
                and len(routing_rules) == len(config.routing_rules)
            )
            result["status"] = "passed" if ok else "failed"
            result["details"] = {
                "target": target,
                "contract": contract,
                "ruleCount": len(routing_rules),
                "expectedRuleCount": len(config.routing_rules),
            }
            return result

        if check.method == "admin-api-get":
            role_name = str(check.target).rstrip("/").split("/")[-1]
            role_name = self._expand_runtime_value(role_name)
            role = self._get_keycloak_client().get_realm_role(role_name)
            result["status"] = "passed" if role is not None else "failed"
            result["details"] = {"roleName": role_name}
            return result

        if check.method == "admin-api-query":
            parsed = urllib.parse.urlparse(self._expand_runtime_value(check.target))
            query = urllib.parse.parse_qs(parsed.query)
            username = str(query.get("username", [""])[0])
            user = self._get_keycloak_client().find_user_by_username(username) if username else None
            result["status"] = "passed" if user is not None else "failed"
            result["details"] = {"username": username}
            return result

        if check.method == "metadata-check":
            billing_details = self._verify_openmrs_billing_metadata(config)
            result["status"] = "passed" if billing_details["ok"] else "failed"
            result["details"] = billing_details
            return result

        if check.method == "openmrs-runtime-check":
            runtime_details = self._verify_openmrs_runtime_bundle(config)
            result["status"] = "passed" if runtime_details["ok"] else "failed"
            result["details"] = runtime_details
            return result

        result["status"] = "skipped"
        result["details"] = {"reason": f"unsupported verification method '{check.method}'"}
        return result

    def _verify_openmrs_billing_metadata(self, config: ClinicConfigModel) -> dict[str, Any]:
        client = self._get_openmrs_client()
        missing_billable_services = [
            service.service_name
            for service in config.billing_model.billable_services
            if client.find_billable_service(service.service_name) is None
        ]
        missing_payment_modes = [
            payment_mode.name
            for payment_mode in config.billing_model.payment_modes
            if client.find_payment_mode(payment_mode.name) is None
        ]
        missing_cash_points = [
            cash_point.name
            for cash_point in config.billing_model.cash_points
            if client.find_cash_point(cash_point.name) is None
        ]
        return {
            "ok": not (missing_billable_services or missing_payment_modes or missing_cash_points),
            "missingBillableServices": missing_billable_services,
            "missingPaymentModes": missing_payment_modes,
            "missingCashPoints": missing_cash_points,
        }

    def _verify_openmrs_runtime_bundle(self, config: ClinicConfigModel) -> dict[str, Any]:
        client = self._get_openmrs_client()
        users_by_role = self._config_users_by_role(config)
        location_uuid_by_id: dict[str, str] = {}
        queue_uuid_by_id: dict[str, str] = {}
        queue_room_uuid_by_id: dict[str, str] = {}
        failures: list[str] = []
        details: dict[str, Any] = {
            "locations": [],
            "encounterTypes": [],
            "billingMetadata": [],
            "queues": [],
            "queueRooms": [],
            "queueRoomProviders": [],
            "pricingRules": [],
            "stockRules": [],
        }

        for location in config.locations:
            live_location = client.find_location(location.name)
            if live_location is None:
                failures.append(f"location:{location.name}")
                details["locations"].append({"name": location.name, "status": "missing"})
                continue
            location_uuid_by_id[location.id] = str(live_location["uuid"])
            details["locations"].append({"name": location.name, "status": "present", "uuid": str(live_location["uuid"])})

        for encounter_type in config.encounter_types:
            live_encounter = client.find_encounter_type(encounter_type.name)
            if live_encounter is None:
                failures.append(f"encounterType:{encounter_type.name}")
                details["encounterTypes"].append({"name": encounter_type.name, "status": "missing"})
                continue
            details["encounterTypes"].append(
                {"name": encounter_type.name, "status": "present", "uuid": str(live_encounter["uuid"])}
            )

        billing_metadata = self._verify_openmrs_billing_metadata(config)
        details["billingMetadata"] = billing_metadata
        if not billing_metadata["ok"]:
            failures.append("billingMetadata")

        for queue in config.queues:
            location_uuid = location_uuid_by_id.get(queue.location_id, "")
            if not location_uuid:
                failures.append(f"queue-location:{queue.id}")
                details["queues"].append({"id": queue.id, "status": "missing-location"})
                continue
            live_queue = client.find_queue(queue.name, location_uuid)
            if live_queue is None:
                failures.append(f"queue:{queue.id}")
                details["queues"].append({"id": queue.id, "status": "missing"})
                continue
            queue_uuid = str(live_queue["uuid"])
            queue_uuid_by_id[queue.id] = queue_uuid
            allowed_statuses = live_queue.get("allowedStatuses") or []
            allowed_priorities = live_queue.get("allowedPriorities") or []
            status_present = (
                bool(self._manifest_value(live_queue.get("statusConceptSet"))) or bool(allowed_statuses)
            ) if queue.status_concept_set else True
            priority_present = (
                bool(self._manifest_value(live_queue.get("priorityConceptSet"))) or bool(allowed_priorities)
            ) if queue.priority_concept_set else True
            if not status_present:
                failures.append(f"queue-status:{queue.id}")
            if not priority_present:
                failures.append(f"queue-priority:{queue.id}")
            details["queues"].append(
                {
                    "id": queue.id,
                    "status": "present",
                    "uuid": queue_uuid,
                    "statusConceptSetPresent": status_present,
                    "priorityConceptSetPresent": priority_present,
                    "allowedStatusesCount": len(allowed_statuses),
                    "allowedPrioritiesCount": len(allowed_priorities),
                }
            )

        for room in config.queue_rooms:
            queue_uuid = queue_uuid_by_id.get(room.queue_id, "")
            if not queue_uuid:
                failures.append(f"queueRoom-queue:{room.id}")
                details["queueRooms"].append({"id": room.id, "status": "missing-queue"})
                continue
            live_room = client.find_queue_room(room.name, queue_uuid)
            if live_room is None:
                failures.append(f"queueRoom:{room.id}")
                details["queueRooms"].append({"id": room.id, "status": "missing"})
                continue
            room_uuid = str(live_room["uuid"])
            queue_room_uuid_by_id[room.id] = room_uuid
            details["queueRooms"].append({"id": room.id, "status": "present", "uuid": room_uuid})
            try:
                resolved_providers = self._resolve_queue_room_provider_uuids(asdict(room), client, users_by_role)
            except Exception as exc:
                failures.append(f"queueRoomProviders:{room.id}")
                details["queueRoomProviders"].append({"roomId": room.id, "status": "resolution-failed", "reason": str(exc)})
                continue
            for provider in resolved_providers:
                assignment = client.find_queue_room_provider(room_uuid, provider["providerUuid"])
                if assignment is None:
                    failures.append(f"queueRoomProvider:{room.id}:{provider['providerUsername']}")
                    details["queueRoomProviders"].append(
                        {
                            "roomId": room.id,
                            "providerUsername": provider["providerUsername"],
                            "status": "missing",
                        }
                    )
                else:
                    details["queueRoomProviders"].append(
                        {
                            "roomId": room.id,
                            "providerUsername": provider["providerUsername"],
                            "status": "present",
                            "uuid": str(assignment["uuid"]),
                        }
                    )

        service_uuid_by_id: dict[str, str] = {}
        for service in config.billing_model.billable_services:
            live_service = client.find_billable_service(service.service_name)
            if live_service is not None:
                service_uuid_by_id[service.id] = str(live_service["uuid"])

        for pricing_rule in config.billing_model.pricing_rules:
            live_price = client.find_cashier_item_price(pricing_rule.id)
            service_uuid = service_uuid_by_id.get(pricing_rule.billable_service_id, "")
            if live_price is None or not service_uuid:
                failures.append(f"pricingRule:{pricing_rule.id}")
                details["pricingRules"].append({"id": pricing_rule.id, "status": "missing"})
                continue
            amount_matches = float(live_price.get("price", 0.0)) == float(pricing_rule.amount)
            service_matches = self._manifest_value(live_price.get("billableService")) == service_uuid
            if not amount_matches or not service_matches:
                failures.append(f"pricingRule:{pricing_rule.id}")
            details["pricingRules"].append(
                {
                    "id": pricing_rule.id,
                    "status": "present" if amount_matches and service_matches else "mismatch",
                    "amountMatches": amount_matches,
                    "serviceMatches": service_matches,
                }
            )

        for stock_rule in config.stock_pharmacy_model.rules:
            stock_item = client.find_stock_item(stock_rule.stock_item_name)
            location_uuid = location_uuid_by_id.get(stock_rule.location_id, "")
            if stock_item is None or not location_uuid:
                details["stockRules"].append({"id": stock_rule.id, "status": "missing-inputs"})
                continue
            live_rule = client.find_stock_rule(stock_rule.id, str(stock_item["uuid"]), location_uuid)
            if live_rule is None:
                failures.append(f"stockRule:{stock_rule.id}")
                details["stockRules"].append({"id": stock_rule.id, "status": "missing"})
                continue
            quantity_matches = float(live_rule.get("quantity", 0.0)) == float(stock_rule.reorder_level)
            if not quantity_matches:
                failures.append(f"stockRule:{stock_rule.id}")
            details["stockRules"].append(
                {
                    "id": stock_rule.id,
                    "status": "present" if quantity_matches else "mismatch",
                    "quantityMatches": quantity_matches,
                }
            )

        details["ok"] = not failures
        details["failures"] = failures
        return details

    def _verify_snapshot_file_restore(self, snapshot_path: Path, live_path: str) -> dict[str, Any]:
        live_exists = self.runtime_client.path_exists(live_path)
        if snapshot_path.exists():
            if not live_exists:
                return {
                    "ok": False,
                    "livePath": live_path,
                    "snapshotPath": str(snapshot_path),
                    "reason": "live path missing",
                }
            snapshot_text = snapshot_path.read_text(encoding="utf-8")
            live_text = self._copy_container_file_to_text(live_path)
            return {
                "ok": snapshot_text == live_text,
                "livePath": live_path,
                "snapshotPath": str(snapshot_path),
                "matches": snapshot_text == live_text,
            }
        return {
            "ok": not live_exists,
            "livePath": live_path,
            "snapshotPath": str(snapshot_path),
            "matches": not live_exists,
        }

    def _verify_openmrs_restored_state(self, snapshot_dir: Path) -> dict[str, Any]:
        config_snapshot = snapshot_dir / "configuration"
        extensions_snapshot = snapshot_dir / "extensions-live"
        live_config_path = self._openmrs_managed_config_target()
        live_extensions_path = self._openmrs_extensions_target()
        expected_domains = sorted(child.name for child in config_snapshot.iterdir()) if config_snapshot.exists() else []
        present_domains = [
            domain for domain in expected_domains if self.runtime_client.path_exists(f"{live_config_path}/{domain}")
        ]
        expected_extension_manifests = (
            sorted(child.name for child in extensions_snapshot.iterdir()) if extensions_snapshot.exists() else []
        )
        present_extension_manifests = [
            name for name in expected_extension_manifests if self.runtime_client.path_exists(f"{live_extensions_path}/{name}")
        ]

        client = self._get_openmrs_client()
        runtime_failures: list[str] = []
        runtime_details: dict[str, Any] = {
            "queues": [],
            "queueRooms": [],
            "pricingRules": [],
            "stockRules": [],
        }
        rest_snapshot_path = snapshot_dir / "rest-snapshot.json"
        if rest_snapshot_path.exists():
            rest_snapshot = json.loads(rest_snapshot_path.read_text(encoding="utf-8"))

            for item in rest_snapshot.get("queues", []):
                before = item.get("before")
                if before:
                    live_queue = client.find_queue(
                        str(before.get("name", "")),
                        self._manifest_value(before.get("location")),
                    )
                    ok = live_queue is not None
                else:
                    live_queue = client.find_queue(str(item.get("name", "")), str(item.get("locationUuid", "")))
                    ok = live_queue is None
                runtime_details["queues"].append({"id": item.get("manifestId", ""), "ok": ok})
                if not ok:
                    runtime_failures.append(f"queue:{item.get('manifestId', item.get('name', 'unknown'))}")

            for item in rest_snapshot.get("queueRooms", []):
                before = item.get("before")
                if before:
                    live_room = client.find_queue_room(
                        str(before.get("name", "")),
                        self._manifest_value(before.get("queue")),
                    )
                    ok = live_room is not None
                else:
                    live_room = client.find_queue_room(str(item.get("name", "")), str(item.get("queueUuid", "")))
                    ok = live_room is None
                runtime_details["queueRooms"].append({"id": item.get("manifestId", ""), "ok": ok})
                if not ok:
                    runtime_failures.append(f"queueRoom:{item.get('manifestId', item.get('name', 'unknown'))}")

            for item in rest_snapshot.get("cashierItemPrices", []):
                before = item.get("before")
                if before:
                    live_price = client.find_cashier_item_price(str(before.get("name", "")))
                    ok = live_price is not None and bool(live_price.get("retired", False)) == bool(before.get("retired", False))
                else:
                    live_price = client.find_cashier_item_price(str(item.get("ruleId", "")))
                    ok = live_price is None or bool(live_price.get("retired", False))
                runtime_details["pricingRules"].append(
                    {
                        "id": item.get("ruleId", ""),
                        "ok": ok,
                        "retired": bool(live_price.get("retired", False)) if live_price else False,
                    }
                )
                if not ok:
                    runtime_failures.append(f"pricingRule:{item.get('ruleId', 'unknown')}")

            for item in rest_snapshot.get("stockRules", []):
                before = item.get("before")
                if before:
                    live_rule = client.find_stock_rule(
                        str(before.get("name", "")),
                        str(before.get("stockItemUuid", "")),
                        str(before.get("locationUuid", "")),
                    )
                    ok = live_rule is not None
                else:
                    live_rule = client.find_stock_rule(
                        str(item.get("ruleId", "")),
                        str(item.get("stockItemUuid", "")),
                        str(item.get("locationUuid", "")),
                    )
                    ok = live_rule is None
                runtime_details["stockRules"].append({"id": item.get("ruleId", ""), "ok": ok})
                if not ok:
                    runtime_failures.append(f"stockRule:{item.get('ruleId', 'unknown')}")

        ok = (
            present_domains == expected_domains
            and present_extension_manifests == expected_extension_manifests
            and not runtime_failures
        )
        return {
            "ok": ok,
            "expectedDomains": expected_domains,
            "presentDomains": present_domains,
            "expectedExtensionManifests": expected_extension_manifests,
            "presentExtensionManifests": present_extension_manifests,
            "runtime": runtime_details,
            "runtimeFailures": runtime_failures,
        }

    def _verify_keycloak_snapshot_restored(self, snapshot_path: Path) -> dict[str, Any]:
        if not snapshot_path.exists():
            return {"ok": False, "reason": f"missing snapshot '{snapshot_path}'"}
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        failures: list[str] = []
        details: dict[str, Any] = {"users": [], "roles": []}

        for created_user in snapshot.get("createdUsers", []):
            username = str(created_user.get("username", ""))
            exists = self._get_keycloak_client().find_user_by_username(username) is not None
            details["users"].append({"username": username, "createdByChange": True, "present": exists})
            if exists:
                failures.append(f"user:{username}")

        for username, user_snapshot in snapshot.get("users", {}).items():
            if username in {str(item.get("username", "")) for item in snapshot.get("createdUsers", [])}:
                continue
            current_user = self._get_keycloak_client().find_user_by_username(username)
            previous_user = user_snapshot.get("user") or {}
            expected_roles = {str(role["name"]) for role in user_snapshot.get("realmRoles", [])}
            current_roles = (
                {str(role["name"]) for role in self._get_keycloak_client().get_user_realm_roles(str(current_user["id"]))}
                if current_user
                else set()
            )
            matches = bool(current_user) and current_roles == expected_roles
            details["users"].append(
                {
                    "username": username,
                    "createdByChange": False,
                    "present": current_user is not None,
                    "matchesRoles": current_roles == expected_roles,
                    "matchesProfile": bool(current_user)
                    and current_user.get("firstName") == previous_user.get("firstName")
                    and current_user.get("lastName") == previous_user.get("lastName")
                    and current_user.get("email") == previous_user.get("email"),
                }
            )
            if not matches:
                failures.append(f"user:{username}")

        for role_name in snapshot.get("createdRoleNames", []):
            exists = self._get_keycloak_client().get_realm_role(str(role_name)) is not None
            details["roles"].append({"name": str(role_name), "createdByChange": True, "present": exists})
            if exists:
                failures.append(f"role:{role_name}")

        for role_name in snapshot.get("updatedRoleNames", []):
            previous_role = snapshot.get("roles", {}).get(role_name) or {}
            current_role = self._get_keycloak_client().get_realm_role(str(role_name))
            matches = bool(current_role)
            if previous_role and current_role:
                matches = (
                    current_role.get("attributes") == previous_role.get("attributes")
                    and current_role.get("composites") == previous_role.get("composites")
                )
            details["roles"].append(
                {
                    "name": str(role_name),
                    "createdByChange": False,
                    "present": current_role is not None,
                    "matches": matches,
                }
            )
            if not matches:
                failures.append(f"role:{role_name}")

        return {"ok": not failures, "failures": failures, "details": details}

    def _verify_cleared_products(self, target_run: ApplyRun, products: list[str]) -> dict[str, Any]:
        failures: list[str] = []
        details: dict[str, Any] = {}
        for product in products:
            if product == "openmrs":
                outcome = self._verify_openmrs_restored_state(Path(target_run.snapshots[product]))
            elif product == "openelis":
                snapshot_dir = Path(target_run.snapshots[product])
                outcome = {
                    "auth": self._verify_snapshot_file_restore(snapshot_dir / "extra.properties", "/run/secrets/extra.properties"),
                    "common": self._verify_snapshot_file_restore(
                        snapshot_dir / "common.properties",
                        "/var/lib/openelis-global/properties/common.properties",
                    ),
                    "workflowProfile": self._verify_snapshot_file_restore(
                        snapshot_dir / "lmic-emr-os-plan.json",
                        "/var/lib/openelis-global/configuration/backend/lmic-emr-os-plan.json",
                    ),
                }
                outcome["ok"] = all(item["ok"] for item in outcome.values())
            elif product == "orthanc":
                snapshot_dir = Path(target_run.snapshots[product])
                outcome = {
                    "permissions": self._verify_snapshot_file_restore(
                        snapshot_dir / "permissions.json",
                        "/opt/clinicDx/configs/orthanc-auth/permissions.json",
                    ),
                    "accessProfiles": self._verify_snapshot_file_restore(
                        snapshot_dir / "access-profiles.json",
                        "/opt/clinicDx/configs/orthanc-auth/access-profiles.json",
                    ),
                    "orthancConfig": self._verify_snapshot_file_restore(
                        snapshot_dir / "orthanc.json",
                        "/opt/clinicDx/configs/orthanc/orthanc.json",
                    ),
                }
                outcome["ok"] = all(item["ok"] for item in outcome.values())
            elif product == "keycloak":
                outcome = self._verify_keycloak_snapshot_restored(Path(target_run.snapshots[product]) / "pre-apply.json")
            else:
                outcome = {"ok": True, "reason": "unsupported product"}
            details[product] = outcome
            if not outcome.get("ok", False):
                failures.append(product)
        return {"ok": not failures, "failures": failures, "products": details}

    def _run_rollback_verification(
        self,
        target_run: ApplyRun,
        previous_active_changes: dict[str, str],
        selected_products: list[str],
        *,
        smoke_script_passed: bool,
    ) -> dict[str, Any]:
        grouped_products: dict[str, list[str]] = {}
        cleared_products: list[str] = []
        for product in selected_products:
            previous_change_id = str(previous_active_changes.get(product, "") or "")
            if previous_change_id:
                grouped_products.setdefault(previous_change_id, []).append(product)
            else:
                cleared_products.append(product)

        bundle_results: list[dict[str, Any]] = []
        failures: list[str] = []
        for change_id, products in grouped_products.items():
            record = self.state_store.require_change(change_id)
            config = ClinicConfigModel.from_json_file(record.config_path)
            plan_result = self._run_verification_plan(
                record,
                config,
                products,
                smoke_script_passed=smoke_script_passed,
            )
            bundle_results.append(
                {
                    "changeId": change_id,
                    "products": products,
                    "verification": plan_result,
                }
            )
            if not plan_result["ok"]:
                failures.extend(f"{change_id}:{product}" for product in products)

        cleared_result = self._verify_cleared_products(target_run, cleared_products) if cleared_products else {"ok": True}
        if not cleared_result.get("ok", True):
            failures.extend(f"cleared:{product}" for product in cleared_result.get("failures", []))

        return {
            "ok": not failures,
            "failures": failures,
            "restoredBundles": bundle_results,
            "clearedProducts": cleared_result,
        }

    def register_bundle(self, bundle_dir: str | Path) -> ChangeRecord:
        return self.state_store.register_bundle(bundle_dir)

    def approve_bundle(
        self,
        change_ref: str | Path,
        approver: str,
        note: str = "",
        environment: str = "",
    ) -> ChangeRecord:
        record = self._resolve_change(change_ref)
        return self.state_store.approve_change(record.change_id, approver, note, environment=environment)

    def promote_bundle(
        self,
        change_ref: str | Path,
        *,
        from_environment: str,
        to_environment: str,
        promoted_by: str,
        note: str = "",
    ) -> ChangeRecord:
        record = self._resolve_change(change_ref)
        config = ClinicConfigModel.from_json_file(record.config_path)
        allowed_environments = set(config.governance.promotion_environments)
        if allowed_environments and to_environment not in allowed_environments:
            raise PermissionError(
                f"Change '{record.change_id}' is not configured for promotion into environment '{to_environment}'."
            )
        return self.state_store.promote_change(
            record.change_id,
            from_environment=from_environment,
            to_environment=to_environment,
            promoted_by=promoted_by,
            note=note,
        )

    def _approval_satisfied(
        self,
        record: ChangeRecord,
        config: ClinicConfigModel,
        *,
        environment: str,
    ) -> bool:
        if not config.governance.approval_required:
            return True
        if not record.approvals:
            return False
        gated_environments = set(config.governance.approval_environments)
        if not environment or (gated_environments and environment not in gated_environments):
            return True
        return any(not approval.environment or approval.environment == environment for approval in record.approvals)

    def apply_change(
        self,
        change_ref: str | Path,
        *,
        dry_run: bool = False,
        products: list[str] | None = None,
        restart_services: bool = False,
        run_verify: bool = False,
        environment: str = "",
    ) -> ApplyOutcome:
        record = self._resolve_change(change_ref)
        config = ClinicConfigModel.from_json_file(record.config_path)
        requested_products = self._selected_products(products)
        selected_products = [product for product in requested_products if product in DEFAULT_PRODUCTS]
        run_id = f"run-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
        warnings: list[str] = []
        errors: list[str] = []
        results: dict[str, Any] = {}
        previous_active_changes = self.state_store.get_active_change_ids_by_product(selected_products)
        previous_active = self._common_active_change_id(previous_active_changes)
        for product in requested_products:
            if product not in DEFAULT_PRODUCTS:
                warnings.append(f"Unknown product '{product}' was skipped.")

        if not dry_run and not self._approval_satisfied(record, config, environment=environment):
            raise PermissionError(
                f"Change '{record.change_id}' requires approval before apply"
                + (f" in environment '{environment}'." if environment else ".")
            )

        if dry_run:
            results["preview"] = json.loads(Path(record.preview_path).read_text(encoding="utf-8"))
            run = ApplyRun(
                run_id=run_id,
                executed_at=_utc_now(),
                mode="dry-run",
                environment=environment,
                restart_services=restart_services,
                run_verify=run_verify,
                status="dry-run",
                products=selected_products,
                bundle_dir=record.bundle_dir,
                results=results,
                warnings=warnings,
                errors=errors,
                previous_active_change_id=previous_active,
                previous_active_change_ids=previous_active_changes,
            )
            self.state_store.append_apply_run(record.change_id, run)
            return ApplyOutcome(record.change_id, run_id, run.status, results, warnings, errors)

        self._ensure_required_clients(selected_products)
        snapshot_dir = self.state_store.create_snapshot_dir(record.change_id, run_id)
        applied_products: list[str] = []
        for product in selected_products:
            try:
                results[product] = self._apply_product(product, record, snapshot_dir, restart_services)
                applied_products.append(product)
            except Exception as exc:  # pragma: no cover - error path exercised through fake clients
                current_snapshot_path = snapshot_dir / product
                if self._product_snapshot_ready(product, current_snapshot_path):
                    applied_products.append(product)
                errors.append(f"{product}: {exc}")
                break

        verification: dict[str, Any] = {}
        status = "applied"

        if run_verify and not errors:
            verification_result = self.runtime_client.run_verify_script(self.verify_script, "--healthcheck")
            verification = {
                "smokeScript": {
                    "path": str(self.verify_script),
                    "returncode": verification_result.returncode,
                    "stdout": verification_result.stdout,
                    "stderr": verification_result.stderr,
                },
                "returncode": verification_result.returncode,
                "stdout": verification_result.stdout,
                "stderr": verification_result.stderr,
            }
            if verification_result.returncode != 0:
                errors.append("verify-backend.sh failed after apply.")
            else:
                try:
                    plan_verification = self._run_verification_plan(
                        record,
                        config,
                        selected_products,
                        smoke_script_passed=True,
                    )
                    verification["verificationPlan"] = plan_verification
                    if not plan_verification["ok"]:
                        errors.append("verification-plan checks failed after apply.")
                except Exception as exc:
                    verification["verificationPlanError"] = str(exc)
                    errors.append(f"verification-plan error after apply: {exc}")

        if errors:
            if applied_products:
                rollback_summary, rollback_errors = self._auto_rollback_products(snapshot_dir, applied_products)
                rollback_summary["manualRepairRequired"] = bool(rollback_errors)
                results["automaticRollback"] = rollback_summary
                if rollback_errors:
                    errors.extend(f"auto-rollback {message}" for message in rollback_errors)
                    warnings.append("Automatic rollback could not fully restore all mutated products; manual repair is required.")
                    status = "failed-rollback-failed"
                else:
                    warnings.append("Applied products were automatically rolled back after failure.")
                    status = "failed-rolled-back"
            else:
                status = "failed"

        run = ApplyRun(
            run_id=run_id,
            executed_at=_utc_now(),
            mode="apply",
            environment=environment,
            restart_services=restart_services,
            run_verify=run_verify,
            status=status,
            products=selected_products,
            bundle_dir=record.bundle_dir,
            snapshots={product: str(snapshot_dir / product) for product in selected_products},
            results=results,
            verification=verification,
            warnings=warnings,
            errors=errors,
            previous_active_change_id=previous_active,
            previous_active_change_ids=previous_active_changes,
        )
        self.state_store.append_apply_run(record.change_id, run)
        return ApplyOutcome(record.change_id, run_id, status, results, warnings, errors)

    def rollback_change(
        self,
        change_ref: str | Path,
        *,
        run_id: str | None = None,
        products: list[str] | None = None,
        restart_services: bool = False,
        run_verify: bool = False,
        environment: str = "",
    ) -> ApplyOutcome:
        record = self._resolve_change(change_ref)
        requested_products = self._selected_products(products)
        selected_products = [product for product in requested_products if product in DEFAULT_PRODUCTS]
        self._ensure_required_clients(selected_products)
        target_run = self._select_apply_run(record, run_id, selected_products)
        warnings: list[str] = []
        errors: list[str] = []
        results: dict[str, Any] = {}
        rollback_run_id = f"rollback-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
        previous_active_changes = dict(target_run.previous_active_change_ids)
        if not previous_active_changes and target_run.previous_active_change_id:
            previous_active_changes = {
                product: target_run.previous_active_change_id
                for product in target_run.products
            }
        for product in requested_products:
            if product not in DEFAULT_PRODUCTS:
                warnings.append(f"Unknown product '{product}' was skipped during rollback.")

        for product in selected_products:
            try:
                snapshot_path = Path(target_run.snapshots[product])
                results[product] = self._restore_product(product, snapshot_path, restart_services)
            except Exception as exc:  # pragma: no cover - error path exercised through fake clients
                errors.append(f"{product}: {exc}")

        verification: dict[str, Any] = {}
        status = "rolled-back" if not errors else "rollback-failed"
        if run_verify and not errors:
            verification_result = self.runtime_client.run_verify_script(self.verify_script, "--healthcheck")
            verification = {
                "smokeScript": {
                    "path": str(self.verify_script),
                    "returncode": verification_result.returncode,
                    "stdout": verification_result.stdout,
                    "stderr": verification_result.stderr,
                },
                "returncode": verification_result.returncode,
                "stdout": verification_result.stdout,
                "stderr": verification_result.stderr,
            }
            if verification_result.returncode != 0:
                status = "rollback-failed"
                errors.append("verify-backend.sh failed after rollback.")
            else:
                try:
                    rollback_verification = self._run_rollback_verification(
                        target_run,
                        previous_active_changes,
                        selected_products,
                        smoke_script_passed=True,
                    )
                    verification["rollbackVerification"] = rollback_verification
                    if not rollback_verification["ok"]:
                        status = "rollback-failed"
                        errors.append("rollback verification checks failed after rollback.")
                except Exception as exc:
                    verification["rollbackVerificationError"] = str(exc)
                    status = "rollback-failed"
                    errors.append(f"rollback verification error after rollback: {exc}")

        rollback_run = ApplyRun(
            run_id=rollback_run_id,
            executed_at=_utc_now(),
            mode="rollback",
            environment=environment,
            restart_services=restart_services,
            run_verify=run_verify,
            status=status,
            products=selected_products,
            bundle_dir=record.bundle_dir,
            snapshots=target_run.snapshots,
            results=results,
            verification=verification,
            warnings=warnings,
            errors=errors,
            previous_active_change_id=self._common_active_change_id(previous_active_changes),
            previous_active_change_ids=previous_active_changes,
        )
        self.state_store.append_apply_run(record.change_id, rollback_run)
        return ApplyOutcome(record.change_id, rollback_run_id, status, results, warnings, errors)

    def _resolve_change(self, change_ref: str | Path) -> ChangeRecord:
        path = Path(change_ref)
        if path.exists():
            return self.state_store.register_bundle(path)
        return self.state_store.require_change(str(change_ref))

    def _select_apply_run(
        self,
        record: ChangeRecord,
        run_id: str | None,
        selected_products: list[str],
    ) -> ApplyRun:
        def supports_products(run: ApplyRun) -> bool:
            for product in selected_products:
                if product not in run.products or not str(run.snapshots.get(product, "")):
                    return False
            return True

        if run_id:
            for run in record.apply_runs:
                if run.run_id == run_id:
                    if run.mode != "apply":
                        raise ValueError(
                            f"Run '{run_id}' for change '{record.change_id}' is a '{run.mode}' run, not an apply run."
                        )
                    if run.status != "applied":
                        raise ValueError(
                            f"Run '{run_id}' for change '{record.change_id}' has status '{run.status}' and cannot be rolled back."
                        )
                    if not supports_products(run):
                        raise ValueError(
                            f"Run '{run_id}' for change '{record.change_id}' does not contain snapshots for products {selected_products}."
                        )
                    return run
            raise FileNotFoundError(f"Run '{run_id}' was not found for change '{record.change_id}'.")
        for run in reversed(record.apply_runs):
            if run.mode == "apply" and run.status == "applied" and supports_products(run):
                return run
        raise FileNotFoundError(f"Change '{record.change_id}' has no successful apply run to roll back.")

    def _direct_openmrs_domains(self, openmrs_pack_dir: Path) -> list[Path]:
        domains: list[Path] = []
        for path in sorted(openmrs_pack_dir.iterdir()):
            if path.name in {"extensions"}:
                continue
            if path.is_dir():
                domains.append(path)
        return domains

    def _clear_container_directory(self, path: str) -> None:
        self.runtime_client.exec_shell(
            f"mkdir -p {path} && rm -rf {path}/* {path}/.[!.]* {path}/..?*"
        )

    def _build_openmrs_managed_overlay(self, openmrs_pack_dir: Path, destination: Path) -> list[str]:
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True, exist_ok=True)
        applied_domains: list[str] = []
        for domain_path in self._direct_openmrs_domains(openmrs_pack_dir):
            wrapped_domain = destination / domain_path.name / "clinicdx-managed"
            wrapped_domain.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(domain_path, wrapped_domain)
            applied_domains.append(domain_path.name)
        return applied_domains

    def _manifest_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return value.get("uuid") or value.get("display") or value.get("name") or value
        return value

    def _clean_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        cleaned: dict[str, Any] = {}
        for key, value in payload.items():
            if value in ("", None, [], {}):
                continue
            cleaned[key] = value
        return cleaned

    def _queue_payload_from_manifest(self, queue: dict[str, Any], location_uuid: str) -> dict[str, Any]:
        return self._clean_payload(
            {
                "name": queue["name"],
                "description": queue.get("description", ""),
                "location": location_uuid,
                "service": queue.get("service_concept", ""),
                "priorityConceptSet": queue.get("priority_concept_set", ""),
                "statusConceptSet": queue.get("status_concept_set", ""),
            }
        )

    def _queue_payload_from_snapshot(self, queue: dict[str, Any]) -> dict[str, Any]:
        return self._clean_payload(
            {
                "name": queue.get("name", ""),
                "description": queue.get("description", ""),
                "location": self._manifest_value(queue.get("location")),
                "service": self._manifest_value(queue.get("service")),
                "priorityConceptSet": self._manifest_value(queue.get("priorityConceptSet")),
                "statusConceptSet": self._manifest_value(queue.get("statusConceptSet")),
            }
        )

    def _is_retryable_openmrs_queue_error(self, exc: Exception) -> bool:
        message = str(exc)
        return "QueueEntry.service.null" in message or "The property service should not be null" in message

    def _upsert_openmrs_queue_with_retry(
        self,
        client: OpenMRSClient,
        queue: dict[str, Any],
        location_uuid: str,
        *,
        timeout_seconds: int = 180,
        poll_interval_seconds: float = 2.0,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            existing_queue = client.find_queue(str(queue["name"]), location_uuid)
            payload = self._queue_payload_from_manifest(queue, location_uuid)
            try:
                current_queue = (
                    client.update_queue(str(existing_queue["uuid"]), payload)
                    if existing_queue is not None
                    else client.create_queue(payload)
                )
                return current_queue, existing_queue
            except Exception as exc:
                if not self._is_retryable_openmrs_queue_error(exc):
                    raise
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"OpenMRS queue metadata for '{queue['id']}' did not become ready in time: {exc}"
                    ) from exc
                time.sleep(poll_interval_seconds)

    def _queue_room_payload_from_manifest(self, room: dict[str, Any], queue_uuid: str) -> dict[str, Any]:
        return self._clean_payload(
            {
                "name": room["name"],
                "description": room.get("description", ""),
                "queue": queue_uuid,
            }
        )

    def _queue_room_payload_from_snapshot(self, room: dict[str, Any]) -> dict[str, Any]:
        return self._clean_payload(
            {
                "name": room.get("name", ""),
                "description": room.get("description", ""),
                "queue": self._manifest_value(room.get("queue")),
            }
        )

    def _cashier_item_price_payload_from_manifest(
        self,
        pricing_rule: dict[str, Any],
        billable_service_uuid: str,
    ) -> dict[str, Any]:
        return self._clean_payload(
            {
                "name": pricing_rule["id"],
                "price": pricing_rule["amount"],
                "billableService": billable_service_uuid,
            }
        )

    def _cashier_item_price_payload_from_snapshot(self, price: dict[str, Any]) -> dict[str, Any]:
        return self._clean_payload(
            {
                "name": price.get("name", ""),
                "price": price.get("price"),
                "paymentMode": self._manifest_value(price.get("paymentMode")),
                "item": self._manifest_value(price.get("item")),
                "billableService": self._manifest_value(price.get("billableService")),
            }
        )

    def _stock_rule_payload_from_manifest(
        self,
        stock_rule: dict[str, Any],
        *,
        location_uuid: str,
        stock_item_uuid: str,
    ) -> dict[str, Any]:
        return self._clean_payload(
            {
                "name": stock_rule["id"],
                "locationUuid": location_uuid,
                "stockItemUuid": stock_item_uuid,
                "quantity": stock_rule.get("reorder_level", 0.0),
                "enabled": True,
            }
        )

    def _stock_rule_payload_from_snapshot(self, stock_rule: dict[str, Any]) -> dict[str, Any]:
        return self._clean_payload(
            {
                "name": stock_rule.get("name", ""),
                "description": stock_rule.get("description", ""),
                "locationUuid": stock_rule.get("locationUuid", ""),
                "stockItemUuid": stock_rule.get("stockItemUuid", ""),
                "quantity": stock_rule.get("quantity"),
                "stockItemPackagingUOMUuid": stock_rule.get("stockItemPackagingUOMUuid"),
                "enabled": stock_rule.get("enabled"),
                "evaluationFrequency": stock_rule.get("evaluationFrequency"),
                "actionFrequency": stock_rule.get("actionFrequency"),
                "alertRole": stock_rule.get("alertRole", ""),
                "mailRole": stock_rule.get("mailRole", ""),
                "enableDescendants": stock_rule.get("enableDescendants"),
            }
        )

    def _queue_room_provider_payload(self, queue_room_uuid: str, provider_uuid: str) -> dict[str, Any]:
        return {"queueRoom": queue_room_uuid, "provider": provider_uuid}

    def _identity_users_by_role(self, identity_manifest_path: Path) -> dict[str, list[str]]:
        if not identity_manifest_path.exists():
            return {}
        identity_manifest = json.loads(identity_manifest_path.read_text(encoding="utf-8"))
        users_by_role: dict[str, list[str]] = {}
        for user in identity_manifest.get("users", []):
            username = str(user.get("username", ""))
            if not username:
                continue
            for role_id in user.get("role_ids", []):
                users_by_role.setdefault(str(role_id), []).append(username)
        return users_by_role

    def _config_users_by_role(self, config: ClinicConfigModel) -> dict[str, list[str]]:
        users_by_role: dict[str, list[str]] = {}
        for user in config.identity_model.users:
            for role_id in user.role_ids:
                users_by_role.setdefault(str(role_id), []).append(user.username)
        return users_by_role

    def _resolve_queue_room_provider_uuids(
        self,
        room: dict[str, Any],
        client: OpenMRSClient,
        users_by_role: dict[str, list[str]],
    ) -> list[dict[str, str]]:
        resolved: list[dict[str, str]] = []
        seen_provider_uuids: set[str] = set()
        for role_id in [str(value) for value in room.get("provider_role_ids", [])]:
            usernames = users_by_role.get(role_id, [])
            if not usernames:
                raise RuntimeError(
                    f"Queue room '{room['id']}' provider role '{role_id}' does not resolve to any configured clinic user."
                )
            for username in usernames:
                provider = client.find_provider_by_username(username)
                if provider is None:
                    raise RuntimeError(
                        f"Queue room '{room['id']}' user '{username}' for provider role '{role_id}' does not resolve to an OpenMRS provider."
                    )
                provider_uuid = str(provider["uuid"])
                if provider_uuid in seen_provider_uuids:
                    continue
                seen_provider_uuids.add(provider_uuid)
                resolved.append(
                    {
                        "providerUuid": provider_uuid,
                        "providerUsername": username,
                        "roleId": role_id,
                    }
                )
        return resolved

    def _install_openmrs_extension_manifests(self, record: ChangeRecord, product_dir: Path) -> dict[str, Any]:
        extensions_dir = Path(record.openmrs_pack_dir) / "extensions"
        live_target = self._openmrs_extensions_target()
        snapshot_path = product_dir / "extensions-live"
        if self.runtime_client.path_exists(live_target):
            self.runtime_client.copy_from_container(live_target, snapshot_path)
        installed_manifests: list[str] = []
        self._clear_container_directory(live_target)
        if extensions_dir.exists():
            for manifest_path in sorted(extensions_dir.glob("*.json")):
                self.runtime_client.copy_to_container(manifest_path, f"{live_target}/{manifest_path.name}")
                installed_manifests.append(manifest_path.name)
        return {
            "source": str(extensions_dir),
            "target": live_target,
            "snapshot": str(snapshot_path),
            "installedManifests": installed_manifests,
        }

    def _apply_openmrs_extensions(self, record: ChangeRecord, product_dir: Path) -> dict[str, Any]:
        client = self._get_openmrs_client()
        extensions_dir = Path(record.openmrs_pack_dir) / "extensions"
        if not extensions_dir.exists():
            return {
                "appliedQueues": [],
                "appliedQueueRooms": [],
                "appliedQueueRoomProviders": [],
                "appliedPrices": [],
                "appliedStockRules": [],
                "warnings": [],
                "routingPolicy": {
                    "installed": False,
                    "nativePersistence": False,
                    "ruleCount": 0,
                    "policyPath": "",
                },
                "identityPolicyInstalled": False,
                "restSnapshot": "",
            }

        snapshot: dict[str, Any] = {
            "queues": [],
            "queueRooms": [],
            "queueRoomProviders": [],
            "cashierItemPrices": [],
            "stockRules": [],
        }
        results: dict[str, Any] = {
            "appliedQueues": [],
            "appliedQueueRooms": [],
            "appliedQueueRoomProviders": [],
            "appliedPrices": [],
            "appliedStockRules": [],
            "warnings": [],
            "routingPolicy": {
                "installed": False,
                "nativePersistence": False,
                "ruleCount": 0,
                "policyPath": str(extensions_dir / "queue-routing.json"),
            },
            "identityPolicyInstalled": False,
        }
        snapshot_path = product_dir / "rest-snapshot.json"

        def persist_snapshot() -> None:
            self._write_snapshot_artifact(snapshot_path, snapshot)
            results["restSnapshot"] = str(snapshot_path)

        persist_snapshot()

        queue_manifest_path = extensions_dir / "queue-routing.json"
        if queue_manifest_path.exists():
            queue_manifest = json.loads(queue_manifest_path.read_text(encoding="utf-8"))
            users_by_role = self._identity_users_by_role(extensions_dir / "identity.json")
            location_uuid_map = {
                str(key): str(value)
                for key, value in queue_manifest.get("locationUuidMap", {}).items()
            }
            queue_uuid_by_id: dict[str, str] = {}
            for queue in queue_manifest.get("queues", []):
                location_uuid = location_uuid_map.get(str(queue["location_id"]), "")
                if not location_uuid:
                    raise RuntimeError(
                        f"Queue '{queue['id']}' references location '{queue['location_id']}' with no resolved UUID."
                    )
                current_queue, existing_queue = self._upsert_openmrs_queue_with_retry(client, queue, location_uuid)
                current_uuid = str(current_queue["uuid"])
                queue_uuid_by_id[str(queue["id"])] = current_uuid
                snapshot["queues"].append(
                    {
                        "manifestId": str(queue["id"]),
                        "name": str(queue["name"]),
                        "locationUuid": location_uuid,
                        "currentUuid": current_uuid,
                        "before": existing_queue,
                    }
                )
                persist_snapshot()
                results["appliedQueues"].append(
                    {
                        "id": queue["id"],
                        "uuid": current_uuid,
                        "mode": "updated" if existing_queue is not None else "created",
                    }
                )

            for room in queue_manifest.get("queueRooms", []):
                queue_uuid = queue_uuid_by_id.get(str(room["queue_id"]), "")
                if not queue_uuid:
                    raise RuntimeError(
                        f"Queue room '{room['id']}' references queue '{room['queue_id']}' that was not resolved."
                    )
                existing_room = client.find_queue_room(str(room["name"]), queue_uuid)
                payload = self._queue_room_payload_from_manifest(room, queue_uuid)
                current_room = (
                    client.update_queue_room(str(existing_room["uuid"]), payload)
                    if existing_room is not None
                    else client.create_queue_room(payload)
                )
                current_uuid = str(current_room["uuid"])
                snapshot["queueRooms"].append(
                    {
                        "manifestId": str(room["id"]),
                        "name": str(room["name"]),
                        "queueUuid": queue_uuid,
                        "currentUuid": current_uuid,
                        "before": existing_room,
                    }
                )
                persist_snapshot()
                results["appliedQueueRooms"].append(
                    {
                        "id": room["id"],
                        "uuid": current_uuid,
                        "mode": "updated" if existing_room is not None else "created",
                    }
                )
                for resolved_provider in self._resolve_queue_room_provider_uuids(room, client, users_by_role):
                    existing_assignment = client.find_queue_room_provider(current_uuid, resolved_provider["providerUuid"])
                    if existing_assignment is None:
                        assignment = client.create_queue_room_provider(
                            self._queue_room_provider_payload(current_uuid, resolved_provider["providerUuid"])
                        )
                        snapshot["queueRoomProviders"].append(
                            {
                                "roomId": str(room["id"]),
                                "providerUuid": resolved_provider["providerUuid"],
                                "providerUsername": resolved_provider["providerUsername"],
                                "currentUuid": str(assignment["uuid"]),
                                "before": None,
                            }
                        )
                        persist_snapshot()
                        results["appliedQueueRoomProviders"].append(
                            {
                                "roomId": room["id"],
                                "uuid": str(assignment["uuid"]),
                                "providerUuid": resolved_provider["providerUuid"],
                                "providerUsername": resolved_provider["providerUsername"],
                                "mode": "created",
                            }
                        )
                    else:
                        snapshot["queueRoomProviders"].append(
                            {
                                "roomId": str(room["id"]),
                                "providerUuid": resolved_provider["providerUuid"],
                                "providerUsername": resolved_provider["providerUsername"],
                                "currentUuid": str(existing_assignment["uuid"]),
                                "before": existing_assignment,
                            }
                        )
                        persist_snapshot()
                        results["appliedQueueRoomProviders"].append(
                            {
                                "roomId": room["id"],
                                "uuid": str(existing_assignment["uuid"]),
                                "providerUuid": resolved_provider["providerUuid"],
                                "providerUsername": resolved_provider["providerUsername"],
                                "mode": "existing",
                            }
                        )

            routing_rules = queue_manifest.get("routingRules", [])
            results["routingPolicy"] = {
                "installed": bool(routing_rules),
                "nativePersistence": False,
                "ruleCount": len(routing_rules),
                "policyPath": str(queue_manifest_path),
            }
            if routing_rules:
                results["warnings"].append(
                    "Routing rules are installed as adapter-owned policy in the live OpenMRS extension directory; there is still no native Queue-module persistence resource for durable route-policy definitions."
                )

        pricing_manifest_path = extensions_dir / "billing-pricing.json"
        if pricing_manifest_path.exists():
            pricing_manifest = json.loads(pricing_manifest_path.read_text(encoding="utf-8"))
            service_name_by_id = {
                str(service["id"]): str(service["service_name"])
                for service in pricing_manifest.get("billableServices", [])
            }
            for pricing_rule in pricing_manifest.get("pricingRules", []):
                billable_service_name = service_name_by_id.get(str(pricing_rule["billable_service_id"]), "")
                if not billable_service_name:
                    raise RuntimeError(
                        f"Pricing rule '{pricing_rule['id']}' references unknown billable service '{pricing_rule['billable_service_id']}'."
                    )
                billable_service = client.find_billable_service(billable_service_name)
                if billable_service is None:
                    raise RuntimeError(
                        f"Billable service '{billable_service_name}' was not found in OpenMRS after metadata load."
                    )
                existing_price = client.find_cashier_item_price(str(pricing_rule["id"]))
                payload = self._cashier_item_price_payload_from_manifest(
                    pricing_rule,
                    str(billable_service["uuid"]),
                )
                current_price = (
                    client.update_cashier_item_price(str(existing_price["uuid"]), payload)
                    if existing_price is not None
                    else client.create_cashier_item_price(payload)
                )
                current_uuid = str(current_price["uuid"])
                snapshot["cashierItemPrices"].append(
                    {
                        "ruleId": str(pricing_rule["id"]),
                        "currentUuid": current_uuid,
                        "before": existing_price,
                    }
                )
                persist_snapshot()
                results["appliedPrices"].append(
                    {
                        "id": pricing_rule["id"],
                        "uuid": current_uuid,
                        "mode": "updated" if existing_price is not None else "created",
                    }
                )
                if pricing_rule.get("patient_category") or pricing_rule.get("requires_payment_before_service"):
                    results["warnings"].append(
                        f"Pricing rule '{pricing_rule['id']}' retains patient-category and payment-gating semantics in the live extension policy file; the billing REST resource only stores the price row."
                    )

        stock_manifest_path = extensions_dir / "stock-pharmacy.json"
        if stock_manifest_path.exists():
            stock_manifest = json.loads(stock_manifest_path.read_text(encoding="utf-8"))
            location_uuid_map = {
                str(key): str(value)
                for key, value in stock_manifest.get("locationUuidMap", {}).items()
            }
            for stock_rule in stock_manifest.get("rules", []):
                location_uuid = location_uuid_map.get(str(stock_rule["location_id"]), "")
                if not location_uuid:
                    raise RuntimeError(
                        f"Stock rule '{stock_rule['id']}' references location '{stock_rule['location_id']}' with no resolved UUID."
                    )
                stock_item = client.find_stock_item(str(stock_rule["stock_item_name"]))
                if stock_item is None:
                    results["warnings"].append(
                        f"Stock rule '{stock_rule['id']}' was skipped because stock item "
                        f"'{stock_rule['stock_item_name']}' was not found in OpenMRS stockmanagement."
                    )
                    continue
                stock_item_uuid = str(stock_item["uuid"])
                existing_rule = client.find_stock_rule(str(stock_rule["id"]), stock_item_uuid, location_uuid)
                payload = self._stock_rule_payload_from_manifest(
                    stock_rule,
                    location_uuid=location_uuid,
                    stock_item_uuid=stock_item_uuid,
                )
                current_rule = (
                    client.update_stock_rule(str(existing_rule["uuid"]), payload)
                    if existing_rule is not None
                    else client.create_stock_rule(payload)
                )
                current_uuid = str(current_rule["uuid"])
                snapshot["stockRules"].append(
                    {
                        "ruleId": str(stock_rule["id"]),
                        "stockItemUuid": stock_item_uuid,
                        "locationUuid": location_uuid,
                        "currentUuid": current_uuid,
                        "before": existing_rule,
                    }
                )
                persist_snapshot()
                results["appliedStockRules"].append(
                    {
                        "id": stock_rule["id"],
                        "uuid": current_uuid,
                        "mode": "updated" if existing_rule is not None else "created",
                    }
                )

            if stock_manifest.get("stockLocations") or stock_manifest.get("operationTypes"):
                results["warnings"].append(
                    "Stock locations and operation types were installed to the live OpenMRS extension policy directory, but only stock rules are mutable through the audited stockmanagement REST surfaces."
                )

        identity_manifest_path = extensions_dir / "identity.json"
        if identity_manifest_path.exists():
            results["identityPolicyInstalled"] = True
            results["warnings"].append(
                "Identity extension data was installed to the live OpenMRS extension policy directory; Keycloak remains the authoritative runtime identity provisioning surface."
            )

        return results

    def _apply_openmrs(self, record: ChangeRecord, snapshot_dir: Path, restart_services: bool) -> dict[str, Any]:
        product_dir = snapshot_dir / "openmrs"
        product_dir.mkdir(parents=True, exist_ok=True)
        target_path = self._openmrs_managed_config_target()
        live_extensions_target = self._openmrs_extensions_target()
        managed_overlay_dir = product_dir / "managed-overlay"

        if self.runtime_client.path_exists(target_path):
            self.runtime_client.copy_from_container(target_path, product_dir / "configuration")
        extension_manifest_result = self._install_openmrs_extension_manifests(record, product_dir)
        applied_domains = self._build_openmrs_managed_overlay(Path(record.openmrs_pack_dir), managed_overlay_dir)
        self._clear_container_directory(target_path)
        for child in sorted(managed_overlay_dir.iterdir()):
            self.runtime_client.copy_to_container(child, f"{target_path}/{child.name}")

        extensions_dir = Path(record.openmrs_pack_dir) / "extensions"
        extension_manifests_present = extensions_dir.exists() and any(extensions_dir.glob("*.json"))
        forced_restart = not restart_services
        self._wait_for_openmrs_imports_to_settle()
        restarted = self.runtime_client.restart_services("openmrs")
        self._sync_openmrs_simulated_state(Path(record.openmrs_pack_dir), extensions_dir=extensions_dir if extensions_dir.exists() else None)
        self._get_openmrs_client().wait_until_ready()
        extension_results: dict[str, Any] = {
            "appliedQueues": [],
            "appliedQueueRooms": [],
            "appliedQueueRoomProviders": [],
            "appliedPrices": [],
            "appliedStockRules": [],
            "warnings": [],
            "routingPolicy": {
                "installed": False,
                "nativePersistence": False,
                "ruleCount": 0,
                "policyPath": "",
            },
            "identityPolicyInstalled": False,
            "restSnapshot": "",
        }
        if extension_manifests_present:
            extension_results = self._apply_openmrs_extensions(record, product_dir)

        return {
            "target": target_path,
            "liveExtensionTarget": live_extensions_target,
            "snapshot": str(product_dir),
            "appliedDomains": applied_domains,
            "extensionManifests": extension_manifest_result["installedManifests"],
            "extensionResults": extension_results,
            "extensionWarnings": extension_results.get("warnings", []),
            "forcedRestartForMetadata": forced_restart,
            "restartedServices": restarted,
        }

    def _apply_openelis(self, record: ChangeRecord, snapshot_dir: Path, restart_services: bool) -> dict[str, Any]:
        product_dir = snapshot_dir / "openelis"
        product_dir.mkdir(parents=True, exist_ok=True)
        plan = self._expand_runtime_data(
            json.loads((Path(record.bundle_dir) / "plans" / "openelis-plan.json").read_text(encoding="utf-8"))
        )
        auth_target_path = "/run/secrets/extra.properties"
        target_path = "/var/lib/openelis-global/properties/common.properties"
        workflow_profile_target = "/var/lib/openelis-global/configuration/backend/lmic-emr-os-plan.json"

        if self.runtime_client.path_exists(auth_target_path):
            self.runtime_client.copy_from_container(auth_target_path, product_dir / "extra.properties")
        if self.runtime_client.path_exists(target_path):
            self.runtime_client.copy_from_container(target_path, product_dir / "common.properties")
        if self.runtime_client.path_exists(workflow_profile_target):
            self.runtime_client.copy_from_container(workflow_profile_target, product_dir / "lmic-emr-os-plan.json")

        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
            handle.write(_dict_to_properties(plan["authProperties"]))
            auth_temp_path = Path(handle.name)
        try:
            self.runtime_client.copy_to_container(auth_temp_path, auth_target_path)
        finally:
            auth_temp_path.unlink(missing_ok=True)

        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
            handle.write(_dict_to_properties(plan["commonProperties"]))
            common_temp_path = Path(handle.name)
        try:
            self.runtime_client.copy_to_container(common_temp_path, target_path)
        finally:
            common_temp_path.unlink(missing_ok=True)

        self.runtime_client.exec_shell("mkdir -p /var/lib/openelis-global/configuration/backend")
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
            json.dump(
                {
                    "workflowProfile": plan.get("workflowProfile", {}),
                    "labModel": plan.get("labModel", {}),
                    "supportedSurfaces": plan.get("supportedSurfaces", []),
                },
                handle,
                indent=2,
            )
            handle.write("\n")
            workflow_profile_temp_path = Path(handle.name)
        try:
            self.runtime_client.copy_to_container(workflow_profile_temp_path, workflow_profile_target)
        finally:
            workflow_profile_temp_path.unlink(missing_ok=True)

        restarted = self.runtime_client.restart_services("openelis-webapp", "openelis-fhir") if restart_services else []
        if restarted:
            openelis_web_base = self.runtime_env.get("OPENELIS_PUBLIC_URL", "https://localhost:8444")
            openelis_fhir_base = f"http://localhost:{self.runtime_env.get('OPENELIS_FHIR_PORT', '8082')}"
            self._wait_for_http_statuses(openelis_web_base, {200, 302})
            self._wait_for_http_statuses(f"{openelis_fhir_base}/fhir/metadata", {200})
        return {
            "authTarget": auth_target_path,
            "target": target_path,
            "workflowProfileTarget": workflow_profile_target,
            "snapshot": str(product_dir),
            "restartedServices": restarted,
        }

    def _apply_orthanc(self, record: ChangeRecord, snapshot_dir: Path, restart_services: bool) -> dict[str, Any]:
        product_dir = snapshot_dir / "orthanc"
        product_dir.mkdir(parents=True, exist_ok=True)
        permissions_path = "/opt/clinicDx/configs/orthanc-auth/permissions.json"
        access_profiles_path = "/opt/clinicDx/configs/orthanc-auth/access-profiles.json"
        orthanc_config_path = "/opt/clinicDx/configs/orthanc/orthanc.json"

        if self.runtime_client.path_exists(permissions_path):
            self.runtime_client.copy_from_container(permissions_path, product_dir / "permissions.json")
        if self.runtime_client.path_exists(access_profiles_path):
            self.runtime_client.copy_from_container(access_profiles_path, product_dir / "access-profiles.json")
        if self.runtime_client.path_exists(orthanc_config_path):
            self.runtime_client.copy_from_container(orthanc_config_path, product_dir / "orthanc.json")

        plan = self._expand_runtime_data(
            json.loads((Path(record.bundle_dir) / "plans" / "orthanc-plan.json").read_text(encoding="utf-8"))
        )
        if not bool(plan.get("serviceProfile", {}).get("enabled")):
            return {
                "permissionsTarget": permissions_path,
                "accessProfilesTarget": access_profiles_path,
                "orthancConfigTarget": orthanc_config_path,
                "snapshot": str(product_dir),
                "overlayApplied": False,
                "managed": False,
                "restartedServices": [],
            }
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
            json.dump(plan["permissions"], handle, indent=2)
            handle.write("\n")
            permissions_temp = Path(handle.name)
        try:
            self.runtime_client.copy_to_container(permissions_temp, permissions_path)
        finally:
            permissions_temp.unlink(missing_ok=True)

        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
            json.dump(plan.get("accessProfiles", {}), handle, indent=2)
            handle.write("\n")
            access_profiles_temp = Path(handle.name)
        try:
            self.runtime_client.exec_shell("mkdir -p /opt/clinicDx/configs/orthanc-auth")
            self.runtime_client.copy_to_container(access_profiles_temp, access_profiles_path)
        finally:
            access_profiles_temp.unlink(missing_ok=True)

        overlay_applied = False
        if self.runtime_client.path_exists(orthanc_config_path):
            current_config_path = product_dir / "orthanc-current.json"
            self.runtime_client.copy_from_container(orthanc_config_path, current_config_path)
            current_config = json.loads(current_config_path.read_text(encoding="utf-8"))
            current_config.setdefault("Authorization", {}).update(
                plan.get("orthancConfigOverlay", {}).get("Authorization", {})
            )
            with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as handle:
                json.dump(current_config, handle, indent=2)
                handle.write("\n")
                orthanc_temp = Path(handle.name)
            try:
                self.runtime_client.copy_to_container(orthanc_temp, orthanc_config_path)
                overlay_applied = True
            finally:
                orthanc_temp.unlink(missing_ok=True)

        restarted = self.runtime_client.restart_services("orthanc-auth", "orthanc") if restart_services else []
        return {
            "permissionsTarget": permissions_path,
            "accessProfilesTarget": access_profiles_path,
            "orthancConfigTarget": orthanc_config_path,
            "snapshot": str(product_dir),
            "overlayApplied": overlay_applied,
            "managed": True,
            "restartedServices": restarted,
        }

    def _apply_keycloak(self, record: ChangeRecord, snapshot_dir: Path) -> dict[str, Any]:
        product_dir = snapshot_dir / "keycloak"
        product_dir.mkdir(parents=True, exist_ok=True)
        client = self._get_keycloak_client()
        plan = self._expand_runtime_data(
            json.loads((Path(record.bundle_dir) / "plans" / "keycloak-plan.json").read_text(encoding="utf-8"))
        )
        realm_roles = [self._normalize_keycloak_role_payload(dict(role)) for role in plan.get("realmRoles", [])]
        managed_role_names = [str(role["name"]) for role in realm_roles]
        managed_users = [dict(user) for user in plan.get("users", [])]

        snapshot: dict[str, Any] = {
            "createdClientRoles": [],
            "roles": {},
            "users": {},
            "createdRoleNames": [],
            "updatedRoleNames": [],
            "createdUsers": [],
            "managedRoleNames": managed_role_names,
        }
        snapshot_path = product_dir / "pre-apply.json"

        for role_payload in realm_roles:
            role_name = str(role_payload["name"])
            snapshot["roles"][role_name] = client.get_realm_role(role_name)

        for user_payload in managed_users:
            username = str(user_payload["username"])
            existing_user = client.find_user_by_username(username)
            if existing_user is None:
                snapshot["users"][username] = {
                    "existed": False,
                    "id": "",
                    "user": None,
                    "realmRoles": [],
                }
            else:
                user_id = str(existing_user["id"])
                snapshot["users"][username] = {
                    "existed": True,
                    "id": user_id,
                    "user": existing_user,
                    "realmRoles": client.get_user_realm_roles(user_id),
                }
        self._write_snapshot_artifact(snapshot_path, snapshot)

        for client_id, client_role_names in self._required_keycloak_client_roles(realm_roles).items():
            for client_role_name in client_role_names:
                if client.get_client_role(client_id, client_role_name) is not None:
                    continue
                client.create_client_role(client_id, {"name": client_role_name})
                snapshot["createdClientRoles"].append({"clientId": client_id, "roleName": client_role_name})
                self._write_snapshot_artifact(snapshot_path, snapshot)

        role_representations: dict[str, dict[str, Any]] = {}
        for role_payload in realm_roles:
            role_name = str(role_payload["name"])
            existing_role = snapshot["roles"][role_name]
            if existing_role is None:
                role_representations[role_name] = client.create_realm_role(role_payload)
                snapshot["createdRoleNames"].append(role_name)
            else:
                role_representations[role_name] = client.update_realm_role(role_name, role_payload)
                snapshot["updatedRoleNames"].append(role_name)
            self._write_snapshot_artifact(snapshot_path, snapshot)

        for user_payload in managed_users:
            username = str(user_payload["username"])
            existing_snapshot = snapshot["users"][username]
            existing_user = existing_snapshot.get("user")
            if existing_user is None:
                created_user = client.create_user(user_payload)
                user_id = str(created_user["id"])
                snapshot["createdUsers"].append({"username": username, "id": user_id})
                snapshot["users"][username] = {
                    "existed": False,
                    "id": user_id,
                    "user": None,
                    "realmRoles": [],
                }
                current_user = created_user
            else:
                user_id = str(existing_snapshot["id"])
                update_payload = {key: value for key, value in user_payload.items() if key != "realmRoles"}
                client.update_user(user_id, update_payload)
                current_user = existing_user
            self._write_snapshot_artifact(snapshot_path, snapshot)

            desired_role_names = [str(name) for name in user_payload.get("realmRoles", [])]
            current_roles = client.get_user_realm_roles(str(current_user["id"]))
            current_role_names = {str(role["name"]) for role in current_roles}
            removable_roles = [
                role
                for role in current_roles
                if str(role["name"]) in managed_role_names and str(role["name"]) not in desired_role_names
            ]
            missing_roles = [role_representations[name] for name in desired_role_names if name not in current_role_names]
            client.delete_user_realm_roles(str(current_user["id"]), removable_roles)
            client.add_user_realm_roles(str(current_user["id"]), missing_roles)

        return {
            "snapshot": str(snapshot_path),
            "managedRoles": managed_role_names,
            "managedUsers": [user["username"] for user in managed_users],
            "createdRoleNames": snapshot["createdRoleNames"],
            "updatedRoleNames": snapshot["updatedRoleNames"],
            "createdUsers": [user["username"] for user in snapshot["createdUsers"]],
        }

    def _restore_openmrs_extension_state(self, snapshot_path: Path) -> dict[str, Any]:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        client = self._get_openmrs_client()
        restored: dict[str, Any] = {
            "queues": [],
            "queueRooms": [],
            "queueRoomProviders": [],
            "cashierItemPrices": [],
            "stockRules": [],
        }

        for item in reversed(snapshot.get("stockRules", [])):
            current_uuid = str(item.get("currentUuid", ""))
            before = item.get("before")
            if before:
                target_uuid = current_uuid or str(before.get("uuid", ""))
                client.update_stock_rule(target_uuid, self._stock_rule_payload_from_snapshot(before))
                restored["stockRules"].append({"uuid": target_uuid, "mode": "restored"})
            elif current_uuid:
                client.delete_stock_rule(current_uuid, reason="Rollback LMIC EMR OS change")
                restored["stockRules"].append({"uuid": current_uuid, "mode": "deleted"})

        for item in reversed(snapshot.get("cashierItemPrices", [])):
            current_uuid = str(item.get("currentUuid", ""))
            before = item.get("before")
            if before:
                target_uuid = current_uuid or str(before.get("uuid", ""))
                client.update_cashier_item_price(target_uuid, self._cashier_item_price_payload_from_snapshot(before))
                restored["cashierItemPrices"].append({"uuid": target_uuid, "mode": "restored"})
            elif current_uuid:
                client.delete_cashier_item_price(current_uuid, reason="Rollback LMIC EMR OS change")
                restored["cashierItemPrices"].append({"uuid": current_uuid, "mode": "deleted"})

        for item in reversed(snapshot.get("queueRoomProviders", [])):
            current_uuid = str(item.get("currentUuid", ""))
            before = item.get("before")
            if before:
                restored["queueRoomProviders"].append({"uuid": str(before.get("uuid", current_uuid)), "mode": "preserved"})
            elif current_uuid:
                client.delete_queue_room_provider(current_uuid, reason="Rollback LMIC EMR OS change")
                restored["queueRoomProviders"].append({"uuid": current_uuid, "mode": "deleted"})

        for item in reversed(snapshot.get("queueRooms", [])):
            current_uuid = str(item.get("currentUuid", ""))
            before = item.get("before")
            if before:
                target_uuid = current_uuid or str(before.get("uuid", ""))
                client.update_queue_room(target_uuid, self._queue_room_payload_from_snapshot(before))
                restored["queueRooms"].append({"uuid": target_uuid, "mode": "restored"})
            elif current_uuid:
                client.delete_queue_room(current_uuid, reason="Rollback LMIC EMR OS change")
                restored["queueRooms"].append({"uuid": current_uuid, "mode": "deleted"})

        for item in reversed(snapshot.get("queues", [])):
            current_uuid = str(item.get("currentUuid", ""))
            before = item.get("before")
            if before:
                target_uuid = current_uuid or str(before.get("uuid", ""))
                client.update_queue(target_uuid, self._queue_payload_from_snapshot(before))
                restored["queues"].append({"uuid": target_uuid, "mode": "restored"})
            elif current_uuid:
                client.delete_queue(current_uuid, reason="Rollback LMIC EMR OS change")
                restored["queues"].append({"uuid": current_uuid, "mode": "deleted"})

        return restored

    def _restore_openmrs(self, snapshot_dir: Path, restart_services: bool) -> dict[str, Any]:
        snapshot_path = snapshot_dir / "configuration"
        target_path = self._openmrs_managed_config_target()
        live_extensions_snapshot = snapshot_dir / "extensions-live"
        live_extensions_target = self._openmrs_extensions_target()
        self._clear_container_directory(target_path)
        if snapshot_path.exists():
            for child in snapshot_path.iterdir():
                self.runtime_client.copy_to_container(child, f"{target_path}/{child.name}")
        self._clear_container_directory(live_extensions_target)
        if live_extensions_snapshot.exists():
            for child in live_extensions_snapshot.iterdir():
                self.runtime_client.copy_to_container(child, f"{live_extensions_target}/{child.name}")
        self._wait_for_openmrs_imports_to_settle()
        restarted = self.runtime_client.restart_services("openmrs")
        self._sync_openmrs_simulated_state(snapshot_path, extensions_dir=live_extensions_snapshot if live_extensions_snapshot.exists() else None)
        self._get_openmrs_client().wait_until_ready()
        restored_extensions: dict[str, Any] = {}
        rest_snapshot_path = snapshot_dir / "rest-snapshot.json"
        if rest_snapshot_path.exists():
            restored_extensions = self._restore_openmrs_extension_state(rest_snapshot_path)
        return {
            "restoredFrom": str(snapshot_path),
            "restoredLiveExtensions": str(live_extensions_snapshot),
            "restoredExtensionState": restored_extensions,
            "forcedRestartForMetadataRestore": not restart_services,
            "restartedServices": restarted,
        }

    def _restore_openelis(self, snapshot_dir: Path, restart_services: bool) -> dict[str, Any]:
        auth_snapshot_path = snapshot_dir / "extra.properties"
        auth_target_path = "/run/secrets/extra.properties"
        snapshot_path = snapshot_dir / "common.properties"
        target_path = "/var/lib/openelis-global/properties/common.properties"
        workflow_profile_snapshot = snapshot_dir / "lmic-emr-os-plan.json"
        workflow_profile_target = "/var/lib/openelis-global/configuration/backend/lmic-emr-os-plan.json"
        if auth_snapshot_path.exists():
            self.runtime_client.copy_to_container(auth_snapshot_path, auth_target_path)
        if snapshot_path.exists():
            self.runtime_client.copy_to_container(snapshot_path, target_path)
        if workflow_profile_snapshot.exists():
            self.runtime_client.copy_to_container(workflow_profile_snapshot, workflow_profile_target)
        else:
            self.runtime_client.exec_shell(f"rm -f {workflow_profile_target}")
        restarted = self.runtime_client.restart_services("openelis-webapp", "openelis-fhir") if restart_services else []
        if restarted:
            openelis_web_base = self.runtime_env.get("OPENELIS_PUBLIC_URL", "https://localhost:8444")
            openelis_fhir_base = f"http://localhost:{self.runtime_env.get('OPENELIS_FHIR_PORT', '8082')}"
            self._wait_for_http_statuses(openelis_web_base, {200, 302})
            self._wait_for_http_statuses(f"{openelis_fhir_base}/fhir/metadata", {200})
        return {
            "restoredAuthFrom": str(auth_snapshot_path),
            "restoredFrom": str(snapshot_path),
            "restoredWorkflowProfileFrom": str(workflow_profile_snapshot),
            "restartedServices": restarted,
        }

    def _restore_orthanc(self, snapshot_dir: Path, restart_services: bool) -> dict[str, Any]:
        permissions_snapshot = snapshot_dir / "permissions.json"
        access_profiles_snapshot = snapshot_dir / "access-profiles.json"
        orthanc_snapshot = snapshot_dir / "orthanc.json"
        if permissions_snapshot.exists():
            self.runtime_client.copy_to_container(
                permissions_snapshot,
                "/opt/clinicDx/configs/orthanc-auth/permissions.json",
            )
        if access_profiles_snapshot.exists():
            self.runtime_client.copy_to_container(
                access_profiles_snapshot,
                "/opt/clinicDx/configs/orthanc-auth/access-profiles.json",
            )
        else:
            self.runtime_client.exec_shell("rm -f /opt/clinicDx/configs/orthanc-auth/access-profiles.json")
        if orthanc_snapshot.exists():
            self.runtime_client.copy_to_container(
                orthanc_snapshot,
                "/opt/clinicDx/configs/orthanc/orthanc.json",
            )
        restarted = self.runtime_client.restart_services("orthanc-auth", "orthanc") if restart_services else []
        return {
            "restoredPermissions": str(permissions_snapshot),
            "restoredAccessProfiles": str(access_profiles_snapshot),
            "restoredOrthancConfig": str(orthanc_snapshot),
            "restartedServices": restarted,
        }

    def _restore_keycloak(self, snapshot_dir: Path) -> dict[str, Any]:
        snapshot_path = snapshot_dir / "pre-apply.json"
        if not snapshot_path.exists():
            raise FileNotFoundError(f"Keycloak snapshot not found at '{snapshot_path}'.")
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        client = self._get_keycloak_client()

        for created_user in snapshot.get("createdUsers", []):
            client.delete_user(str(created_user["id"]))

        created_usernames = {str(item["username"]) for item in snapshot.get("createdUsers", [])}
        for username, user_snapshot in snapshot.get("users", {}).items():
            if username in created_usernames:
                continue
            if not user_snapshot.get("existed") and not user_snapshot.get("user"):
                continue
            user_id = str(user_snapshot["id"])
            if user_snapshot.get("user"):
                restore_payload = dict(user_snapshot["user"])
                client.update_user(user_id, restore_payload)
            previous_roles = [dict(role) for role in user_snapshot.get("realmRoles", [])]
            current_roles = client.get_user_realm_roles(user_id)
            previous_role_names = {str(role["name"]) for role in previous_roles}
            current_role_names = {str(role["name"]) for role in current_roles}
            removable = [
                role
                for role in current_roles
                if str(role["name"]) not in previous_role_names
                and str(role["name"]) in set(snapshot.get("managedRoleNames", []))
            ]
            addable = []
            role_lookup = {str(role["name"]): role for role in previous_roles}
            for role_name in previous_role_names - current_role_names:
                addable.append(role_lookup[role_name])
            client.delete_user_realm_roles(user_id, removable)
            client.add_user_realm_roles(user_id, addable)

        for role_name in reversed(snapshot.get("createdRoleNames", [])):
            client.delete_realm_role(str(role_name))
        for role_name in snapshot.get("updatedRoleNames", []):
            previous_role = snapshot.get("roles", {}).get(role_name)
            if previous_role:
                client.update_realm_role(str(role_name), dict(previous_role))
        for item in reversed(snapshot.get("createdClientRoles", [])):
            client.delete_client_role(str(item["clientId"]), str(item["roleName"]))

        return {
            "restoredFrom": str(snapshot_path),
            "deletedUsers": [str(item["username"]) for item in snapshot.get("createdUsers", [])],
            "deletedRoles": [str(name) for name in snapshot.get("createdRoleNames", [])],
        }
