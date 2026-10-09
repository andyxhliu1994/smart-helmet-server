from __future__ import annotations

import unittest

from app.services.knowledge import knowledge_service


class KnowledgeMatchingTest(unittest.TestCase):
    def test_matches_keyword_inside_natural_sentence(self) -> None:
        flow = knowledge_service.match_check_flow("我要检查配电箱。")

        self.assertIsNotNone(flow)
        self.assertIn("配电箱", flow.get("object_name") or flow["name"])

    def test_does_not_match_unrelated_request(self) -> None:
        self.assertIsNone(knowledge_service.match_check_flow("今天天气怎么样"))


if __name__ == "__main__":
    unittest.main()
