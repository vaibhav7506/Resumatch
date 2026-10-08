"""Exercise the deployed study flow with an original, non-personal PDF."""
import argparse
import json

import httpx


def study_pdf():
    lines = [
        "Physics Study Guide: Forces, Energy and Motion",
        "Force equals mass times acceleration: F = ma. A 2 kg mass at 3 m/s2 needs 6 N.",
        "Momentum equals mass times velocity: p = mv. Total momentum is conserved in an isolated system.",
        "Kinetic energy is one half times mass times velocity squared: KE = 0.5 m v squared.",
        "Work equals force times distance in the direction of the force. Power is work divided by time.",
        "A body at rest stays at rest unless acted on by a net external force.",
        "Action and reaction forces are equal in magnitude and opposite in direction.",
    ]
    stream = "BT /F1 10 Tf 40 550 Td "
    for index, line in enumerate(lines):
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream += ("0 -30 Td " if index else "") + f"({escaped}) Tj "
    stream += "ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 792 612] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
    ]
    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(content))
        content.extend(f"{number} 0 obj\n{obj}\nendobj\n".encode())
    xref = len(content)
    content.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode())
    content.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return bytes(content)


def run(base_url):
    with httpx.Client(base_url=base_url, timeout=120) as client:
        schema = client.get("/openapi.json").json()
        assert "question_id" in schema["components"]["schemas"]["SubmitQuizAnswerRequest"]["required"], "Latest API is not deployed"
        response = client.post("/ingest-study-material", files={"file": ("physics-study.pdf", study_pdf(), "application/pdf")})
        response.raise_for_status()
        material = response.json()
        assert "Force equals mass" in material["material_text"]
        print("PDF ingestion:", material["extraction_method"], flush=True)
        response = client.post("/quiz/start", json={"material_document_id": material["document_id"], "question_count": 5})
        response.raise_for_status()
        session = response.json()
        question = session["question"]
        assert "answer" not in question
        for number in range(1, 6):
            payload = {"session_id": session["session_id"], "question_id": question["question_id"], "answer": question["choices"][0]}
            response = client.post("/quiz/answer", json=payload)
            response.raise_for_status()
            result = response.json()
            assert result["answered_count"] == number
            assert result["feedback"]
            if number == 1:
                replay = client.post("/quiz/answer", json=payload)
                replay.raise_for_status()
                assert replay.json() == result, "Retry altered the score or next question"
            if number < 5:
                question = result["next_question"]
                assert question["difficulty"] == ("hard" if result["correct"] else "easy")
            else:
                assert result["next_question"] is None
            print(f"Question {number}: correct={result['correct']}; answered={result['answered_count']}", flush=True)
        print("Final score:", result["correct_count"], "/ 5", flush=True)
        bad = client.post("/ingest-study-material", files={"file": ("bad.pdf", b"invalid", "application/pdf")})
        assert bad.status_code == 422
        print("Invalid PDF and duplicate answer checks: passed", flush=True)
        print(json.dumps({"session_id": session["session_id"], "document_id": material["document_id"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    run(parser.parse_args().base_url)
