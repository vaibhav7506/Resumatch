import copy
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import routes
from app.main import app


class Database:
    """Transactional storage double; tests cover the public HTTP contract."""
    def __init__(self):
        self.materials = {"material": "Force is mass times acceleration. Momentum is mass times velocity."}
        self.sessions = {}

    def connect(self, *_args, **_kwargs):
        db = self

        class Connection:
            def __enter__(self):
                self.snapshot = copy.deepcopy(db.sessions)
                return self

            def __exit__(self, exc_type, *_args):
                if exc_type:
                    db.sessions = self.snapshot

            def execute(self, sql, values):
                self.row = None
                if sql.startswith("SELECT content"):
                    if values[0] in db.materials:
                        self.row = (db.materials[values[0]],)
                elif sql.startswith("SELECT state"):
                    if values[0] in db.sessions:
                        self.row = (copy.deepcopy(db.sessions[values[0]]),)
                elif sql.startswith("INSERT INTO quiz_sessions"):
                    db.sessions[values[0]] = copy.deepcopy(values[1].obj)
                elif sql.startswith("UPDATE quiz_sessions"):
                    db.sessions[values[1]] = copy.deepcopy(values[0].obj)
                return self

            def fetchone(self):
                return self.row

        return Connection()


class StudyQuizTests(unittest.TestCase):
    def setUp(self):
        self.db = Database()
        self.client = TestClient(app)
        self.db_patch = patch.object(routes.psycopg, "connect", self.db.connect)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.llm_patch = patch.object(routes, "complete", return_value=json.dumps({
            "question": "What is force?", "choices": ["Mass times acceleration", "Mass times velocity"],
            "answer": "Mass times acceleration", "explanation": "Newton's second law is F = ma.", "topic": "Force",
        }))
        self.llm = self.llm_patch.start()
        self.addCleanup(self.llm_patch.stop)
        self.validator_patch = patch.object(routes, "_validate_quiz_question")
        self.validator = self.validator_patch.start()
        self.addCleanup(self.validator_patch.stop)

    def start(self, count=2):
        response = self.client.post("/quiz/start", json={"material_document_id": "material", "question_count": count})
        self.assertEqual(response.status_code, 200, response.text)
        generated = json.loads(self.llm.return_value)
        generated["question"] = "Which formula calculates force for the next example?"
        self.llm.return_value = json.dumps(generated)
        return response.json()

    def answer_body(self, started, answer="Mass times acceleration"):
        return {"session_id": started["session_id"], "question_id": started["question"]["question_id"], "answer": answer}

    def test_correct_answer_adapts_and_retry_does_not_double_count(self):
        started = self.start()
        self.assertNotIn("answer", started["question"])
        first = self.client.post("/quiz/answer", json=self.answer_body(started)).json()
        self.assertTrue(first["correct"])
        self.assertEqual(first["next_question"]["difficulty"], "hard")
        calls = self.llm.call_count
        replay = self.client.post("/quiz/answer", json=self.answer_body(started)).json()
        self.assertEqual(first, replay)
        self.assertEqual(self.llm.call_count, calls)

    def test_wrong_answer_adapts_to_easy_then_completes(self):
        started = self.start()
        first = self.client.post("/quiz/answer", json=self.answer_body(started, "Mass times velocity")).json()
        self.assertFalse(first["correct"])
        self.assertEqual(first["next_question"]["difficulty"], "easy")
        second = self.client.post("/quiz/answer", json={"session_id": started["session_id"], "question_id": first["next_question"]["question_id"], "answer": "Mass times acceleration"}).json()
        self.assertIsNone(second["next_question"])
        self.assertEqual(second["answered_count"], 2)
        self.assertEqual(second["correct_count"], 1)

    def test_provider_failure_can_be_retried_without_losing_progress(self):
        started = self.start()
        self.llm.return_value = "invalid JSON"
        failed = self.client.post("/quiz/answer", json=self.answer_body(started))
        self.assertEqual(failed.status_code, 502)
        self.assertEqual(self.db.sessions[started["session_id"]]["answered_count"], 0)

    def test_invalid_choice_stale_question_and_unknown_material_are_rejected(self):
        started = self.start()
        self.assertEqual(self.client.post("/quiz/answer", json=self.answer_body(started, "arbitrary answer")).status_code, 422)
        body = self.answer_body(started)
        body["question_id"] = "stale"
        self.assertEqual(self.client.post("/quiz/answer", json=body).status_code, 409)
        self.assertEqual(self.client.post("/quiz/start", json={"material_document_id": "missing"}).status_code, 404)

    def test_bad_pdf_returns_readable_error(self):
        response = self.client.post("/ingest-study-material", files={"file": ("bad.pdf", b"not a PDF", "application/pdf")})
        self.assertEqual(response.status_code, 422)
        self.assertIn("Couldn't read", response.json()["detail"])

    def test_repeated_question_is_regenerated(self):
        started = self.start()
        repeated = json.loads(self.llm.return_value)
        repeated["question"] = started["question"]["question"]
        self.llm.side_effect = [json.dumps(repeated), self.llm.return_value]
        response = self.client.post("/quiz/answer", json=self.answer_body(started))
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.json()["next_question"]["question"], started["question"]["question"])

    def test_question_with_incorrect_answer_is_regenerated(self):
        self.validator.side_effect = [ValueError("No correct choice"), None]
        self.start()
        self.assertEqual(self.validator.call_count, 2)

    def test_answer_key_repair_is_independently_rechecked(self):
        self.validator_patch.stop()
        question = {"question": "What is 2 times 3?", "choices": ["4", "5"], "answer": "4", "explanation": "Incorrect explanation"}
        self.llm.side_effect = [
            json.dumps({"valid": False, "grounded": True, "correct_answer": "6", "explanation": "2 times 3 is 6."}),
            json.dumps({"valid": True}),
        ]
        routes._validate_quiz_question(question, "Force equals mass times acceleration. For mass 2 and acceleration 3, force is 6.")
        self.assertEqual(question["answer"], "6")
        self.assertIn("6", question["choices"])
        self.assertEqual(self.llm.call_count, 2)


if __name__ == "__main__":
    unittest.main()
