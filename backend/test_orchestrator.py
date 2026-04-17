import unittest

import agent


class TestProviderOrchestration(unittest.TestCase):
    def test_falls_back_to_second_provider_when_first_fails(self):
        original_call_provider = agent._call_provider

        calls: list[str] = []

        def fake_call_provider(provider, messages):
            calls.append(provider.name)
            if provider.name == "groq":
                raise agent.ProviderCallError("groq", "HTTP 429: rate limit")
            return '{"thought":"ok","response":"done","command":null}'

        agent._call_provider = fake_call_provider
        try:
            raw, provider_used = agent._orchestrated_llm_call(
                messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
            )
            self.assertEqual(provider_used, "together")
            self.assertIn('"response":"done"', raw)
            self.assertEqual(calls[:2], ["groq", "together"])
        finally:
            agent._call_provider = original_call_provider

    def test_parse_json_fallback_when_invalid(self):
        result = agent._parse_json_or_fallback("this is not json")
        self.assertIsNone(result["command"])
        self.assertIn("this is not json", result["response"])


if __name__ == "__main__":
    unittest.main()
