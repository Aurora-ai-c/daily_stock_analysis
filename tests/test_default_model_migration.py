"""Lock-in test: DeepSeek default model migrated from deepseek-chat to deepseek-v4-flash.

Keeps the four inference sites aligned so a regression back to the deprecated
deepseek-chat default is caught immediately.
"""

import os
import unittest
from unittest import mock

from src.services.system_config_service import SystemConfigService
from src.services.generation_backend_status_service import (
    GenerationBackendStatusService,
)
from src.services.screening import config as screening_config


class TestDeepSeekDefaultModelMigration(unittest.TestCase):
    def test_system_config_service_legacy_primary_default(self):
        model = SystemConfigService._infer_setup_legacy_primary_model(
            {"DEEPSEEK_API_KEY": "sk-test"}
        )
        self.assertEqual(model, "deepseek/deepseek-v4-flash")

    def test_generation_backend_status_legacy_default(self):
        model = GenerationBackendStatusService._infer_legacy_litellm_model(
            {"DEEPSEEK_API_KEY": "sk-test"}
        )
        self.assertEqual(model, "deepseek/deepseek-v4-flash")

    def test_screening_config_default_with_deepseek_key(self):
        env = {"DEEPSEEK_API_KEY": "sk-test"}
        with mock.patch.dict(os.environ, env, clear=True):
            model = screening_config._resolve_llm_model([])
        self.assertEqual(model, "deepseek/deepseek-v4-flash")


if __name__ == "__main__":
    unittest.main()
