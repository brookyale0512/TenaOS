from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config_model import ClinicConfigModel, ClinicRole


@dataclass(slots=True)
class AdapterPlan:
    product: str
    payload: dict[str, Any]

    def write_json(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.payload, indent=2) + "\n", encoding="utf-8")


class OpenELISAdapter:
    def __init__(
        self,
        *,
        keycloak_realm_url: str = "${KEYCLOAK_REALM_URL}",
        client_id: str = "${OPENELIS_CLIENT_ID}",
        client_secret: str = "${OPENELIS_CLIENT_SECRET}",
        internal_fhir_uri: str = "http://localhost:8082/fhir/",
    ) -> None:
        self.keycloak_realm_url = keycloak_realm_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.internal_fhir_uri = internal_fhir_uri

    def build_plan(self, config: ClinicConfigModel) -> AdapterPlan:
        enabled = config.lab_model.enabled
        if not enabled:
            workflow_profile = {
                "profileId": "lab-disabled",
                "enabled": False,
                "orderingMode": "disabled",
                "specimenHandoffMode": "disabled",
                "resultDeliveryMode": "disabled",
            }
        elif config.lab_model.integrated_ordering:
            workflow_profile = {
                "profileId": "integrated-fhir-lab",
                "enabled": True,
                "orderingMode": "fhir-integrated",
                "specimenHandoffMode": config.lab_model.specimen_handoff_mode,
                "resultDeliveryMode": config.lab_model.result_delivery_mode,
            }
        else:
            workflow_profile = {
                "profileId": "manual-lab-intake",
                "enabled": True,
                "orderingMode": "manual",
                "specimenHandoffMode": config.lab_model.specimen_handoff_mode,
                "resultDeliveryMode": config.lab_model.result_delivery_mode,
            }
        auth_properties = {
            "org.itech.login.form": "true",
            "org.itech.login.oauth": "true" if enabled else "false",
            "org.itech.login.oauth.config": self.keycloak_realm_url,
            "org.itech.login.oauth.clientID": self.client_id,
            "org.itech.login.oauth.clientSecret": self.client_secret,
        }
        common_properties = {
            "org.openelisglobal.fhirstore.uri": self.internal_fhir_uri if enabled else "",
            "org.openelisglobal.fhir.subscriber.allowHTTP": "true",
            "org.openelisglobal.remote.source.updateStatus": "false",
            "org.openelisglobal.remote.poll.frequency": "120000",
            "org.openelisglobal.task.useBasedOn": "true",
            "org.openelisglobal.fhir.subscriber.resources": "Task,Patient,ServiceRequest,DiagnosticReport,Observation,Specimen,Practitioner,Encounter",
            "org.openelisglobal.program.autocreate": "true",
            "org.openelisglobal.configuration.autocreate": "false",
            "org.openelisglobal.configuration.dir": "/var/lib/openelis-global/configuration/backend",
            "org.openelisglobal.facility.id": config.facility_profile.name,
        }
        if config.lab_model.result_delivery_mode != "fhir":
            common_properties["org.openelisglobal.fhir.subscriber.resources"] = ""
        payload = {
            "product": "OpenELIS",
            "supportedSurfaces": [
                "extra.properties oauth settings",
                "common.properties integration settings",
                "existing runtime config renderer inputs",
            ],
            "assumptions": [
                "The lab engine remains specialized and is configured through supported properties and integration settings.",
                "Deeper LIS workflow changes should remain outside the conversational agent until backed by a supported admin surface.",
            ],
            "labModel": asdict(config.lab_model),
            "workflowProfile": workflow_profile,
            "authProperties": auth_properties,
            "commonProperties": common_properties,
            "manualChecks": [
                "Verify OpenELIS authentication redirect and token exchange after apply.",
                "Verify ServiceRequest and DiagnosticReport round-trip through the FHIR interface.",
                "Verify lab reviewer roles against OpenELIS native permissions.",
            ],
        }
        return AdapterPlan(product="OpenELIS", payload=payload)


class OrthancAdapter:
    DEFAULT_ROLE_PERMISSIONS = {
        "admin": ["all"],
        "radiologist": ["view", "download", "send", "share", "upload", "modify", "api-view", "worklists"],
        "doctor": ["view", "download", "send", "share", "api-view"],
    }

    def build_plan(self, config: ClinicConfigModel) -> AdapterPlan:
        role_policies: dict[str, dict[str, Any]] = {}
        access_profiles: dict[str, dict[str, Any]] = {}

        for role in config.identity_model.roles:
            permissions = self._resolve_permissions(role, config)
            if not permissions:
                continue
            labels = config.imaging_model.authorized_labels.get(role.id, ["*"])
            orthanc_role_names = list(dict.fromkeys([*(role.keycloak_roles or []), role.id]))
            for orthanc_role_name in orthanc_role_names:
                role_policies[orthanc_role_name] = {
                    "permissions": permissions,
                    "authorized-labels": labels,
                }
                access_profiles[orthanc_role_name] = self._build_access_profile(
                    orthanc_role_name,
                    permissions,
                    labels,
                )

        payload = {
            "product": "Orthanc",
            "supportedSurfaces": [
                "permissions.json role map",
                "orthanc-auth-service policy file",
                "generated orthanc.json authorization plugin settings",
            ],
            "imagingModel": asdict(config.imaging_model),
            "serviceProfile": {
                "profileId": "imaging-disabled"
                if not config.imaging_model.enabled
                else "shared-imaging-service",
                "enabled": config.imaging_model.enabled,
                "defaultViewer": config.imaging_model.default_viewer,
                "studyShareEnabled": config.imaging_model.study_share_enabled,
                "departmentLocationId": config.imaging_model.department_location_id,
            },
            "accessProfiles": access_profiles,
            "permissions": {
                "roles": role_policies,
                "authorized-clients": ["share-user"] if config.imaging_model.study_share_enabled else [],
            },
            "orthancConfigOverlay": {
                "Authorization": {
                    "Enable": True,
                    "StandardConfigurations": ["orthanc-explorer-2"],
                    "CheckedLevel": "studies",
                    "UncheckedResources": ["token-validation", "user-profile", "shares"],
                    "TokenHttpHeaders": ["Authorization"],
                    "TokenGetArguments": ["token"],
                }
            },
            "manualChecks": [
                "Verify the auth service returns expected profile/permission payloads.",
                "Verify upload and viewing access for each clinic role.",
                "Verify share policy behavior before enabling external sharing in production.",
            ],
        }
        return AdapterPlan(product="Orthanc", payload=payload)

    def _build_access_profile(
        self,
        role_id: str,
        permissions: list[str],
        labels: list[str],
    ) -> dict[str, Any]:
        permission_set = set(permissions)
        if "all" in permission_set:
            profile_id = "admin-full-control"
        elif {"upload", "modify"} & permission_set:
            profile_id = "radiology-production"
        elif {"share", "api-view"} & permission_set:
            profile_id = "clinical-review"
        else:
            profile_id = "restricted-view"
        return {
            "profileId": profile_id,
            "roleId": role_id,
            "permissions": permissions,
            "authorizedLabels": labels,
        }

    def _resolve_permissions(self, role: ClinicRole, config: ClinicConfigModel) -> list[str]:
        if role.orthanc_permissions:
            return role.orthanc_permissions
        if role.id in config.imaging_model.role_permissions:
            return config.imaging_model.role_permissions[role.id]
        if role.name.lower() in self.DEFAULT_ROLE_PERMISSIONS:
            return self.DEFAULT_ROLE_PERMISSIONS[role.name.lower()]
        if role.id in self.DEFAULT_ROLE_PERMISSIONS:
            return self.DEFAULT_ROLE_PERMISSIONS[role.id]
        return []


class KeycloakAdapter:
    def __init__(
        self,
        *,
        openmrs_client_id: str = "${OPENMRS_CLIENT_ID}",
        openelis_client_id: str = "${OPENELIS_CLIENT_ID}",
        orthanc_client_id: str = "${ORTHANC_CLIENT_ID}",
    ) -> None:
        self.openmrs_client_id = openmrs_client_id
        self.openelis_client_id = openelis_client_id
        self.orthanc_client_id = orthanc_client_id

    def build_plan(self, config: ClinicConfigModel) -> AdapterPlan:
        realm_roles: list[dict[str, Any]] = []
        clinic_role_to_realm_roles: dict[str, list[str]] = {}
        for role in config.identity_model.roles:
            role_names = role.keycloak_roles or [role.id]
            clinic_role_to_realm_roles[role.id] = role_names
            composites = {
                "client": {
                    self.openmrs_client_id: role.openmrs_roles,
                    self.openelis_client_id: role.openelis_roles,
                }
            }
            for role_name in role_names:
                realm_roles.append(
                    {
                        "name": role_name,
                        "description": role.description or role.name,
                        "composite": True if role.openmrs_roles or role.openelis_roles else False,
                        "composites": composites if role.openmrs_roles or role.openelis_roles else {},
                        "attributes": {
                            "orthanc_permissions": [str(permission) for permission in role.orthanc_permissions],
                            "clinic_role_id": [role.id],
                            "clinic_role_name": [role.name],
                        },
                    }
                )

        users = [
            {
                "username": user.username,
                "firstName": user.first_name,
                "lastName": user.last_name,
                "email": user.email,
                "enabled": True,
                "emailVerified": True,
                "realmRoles": self._resolve_user_realm_roles(user.role_ids, clinic_role_to_realm_roles),
            }
            for user in config.identity_model.users
        ]

        payload = {
            "product": "Keycloak",
            "supportedSurfaces": ["realm roles", "client role composites", "bounded admin API provisioning"],
            "leastPrivilegeNotes": [
                "Use a dedicated control-plane service account for provisioning.",
                "Do not grant the conversational runtime agent unrestricted realm-management privileges.",
            ],
            "realmRoles": realm_roles,
            "users": users,
        }
        return AdapterPlan(product="Keycloak", payload=payload)

    def _resolve_user_realm_roles(
        self,
        clinic_role_ids: list[str],
        clinic_role_to_realm_roles: dict[str, list[str]],
    ) -> list[str]:
        resolved: list[str] = []
        seen: set[str] = set()
        for clinic_role_id in clinic_role_ids:
            for realm_role in clinic_role_to_realm_roles.get(clinic_role_id, [clinic_role_id]):
                if realm_role in seen:
                    continue
                seen.add(realm_role)
                resolved.append(realm_role)
        return resolved
