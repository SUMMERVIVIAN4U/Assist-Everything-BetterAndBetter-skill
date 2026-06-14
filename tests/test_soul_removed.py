import unittest

from evalharness.agent import HarnessAgent


class _CapturingClient:
    def __init__(self):
        self.messages = None

    def chat(self, messages, temperature=0.3):
        self.messages = messages
        return "rewritten"


class SoulRemovedTest(unittest.TestCase):
    def test_mimo_rewrite_does_not_load_soul_md(self):
        client = _CapturingClient()
        agent = HarnessAgent(llm_mode="mimo", llm_client=client, persist_memory=False)

        agent.reply("帮我规划一个周末任务")

        system_prompt = client.messages[0]["content"]
        self.assertNotIn("靠谱兄弟", system_prompt)
        self.assertNotIn("僚机", system_prompt)
        self.assertNotIn("礼物/关系建议", system_prompt)


if __name__ == "__main__":
    unittest.main()
