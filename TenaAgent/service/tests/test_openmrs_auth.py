from __future__ import annotations

import base64
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tena_agent_service import openmrs, openmrs_writer  # noqa: E402


def _decoded_token(header: str | None) -> str:
    assert header is not None
    scheme, token = header.split(" ", 1)
    assert scheme == "Basic"
    return base64.b64decode(token.encode("ascii")).decode("utf-8")


class OpenMrsAuthFallbackTests(unittest.TestCase):
    def test_writer_uses_service_credentials_when_legacy_pair_is_absent(self) -> None:
        env = {
            "OPENMRS_SERVICE_USER": "admin",
            "OPENMRS_SERVICE_PASSWORD": "Admin123",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(_decoded_token(openmrs_writer._basic_auth_from_env()), "admin:Admin123")

    def test_openmrs_client_uses_service_credentials_when_legacy_pair_is_absent(self) -> None:
        env = {
            "OPENMRS_SERVICE_USER": "admin",
            "OPENMRS_SERVICE_PASSWORD": "Admin123",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(_decoded_token(openmrs._basic_auth_from_env()), "admin:Admin123")

    def test_legacy_credentials_still_take_precedence(self) -> None:
        env = {
            "OPENMRS_USERNAME": "legacy",
            "OPENMRS_PASSWORD": "secret",
            "OPENMRS_SERVICE_USER": "admin",
            "OPENMRS_SERVICE_PASSWORD": "Admin123",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(_decoded_token(openmrs_writer._basic_auth_from_env()), "legacy:secret")
            self.assertEqual(_decoded_token(openmrs._basic_auth_from_env()), "legacy:secret")


if __name__ == "__main__":
    unittest.main()
