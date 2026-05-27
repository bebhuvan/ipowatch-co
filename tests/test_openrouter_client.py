import json

from ipo_portal.openrouter import OpenRouterClient, _build_messages, _pdf_data_url


def test_openrouter_cached_json_response(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    client = OpenRouterClient(cache_dir=tmp_path / "cache", usage_log=tmp_path / "usage.jsonl")
    payload = {
        "model": "qwen/qwen3.5-flash-02-23",
        "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"ok": True})}}],
    }
    response, parse_error = client._try_build_response(payload, "json_object", "abc", 0, cached=True)
    assert parse_error is None
    assert response is not None
    assert response.json_content == {"ok": True}
    assert response.estimated_cost_usd > 0


def test_openrouter_pdf_message_parts(tmp_path) -> None:
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%test\n")

    data_url = _pdf_data_url(pdf)
    messages = _build_messages(
        "system",
        [
            {"type": "text", "text": "Return json."},
            {"type": "file", "file": {"filename": "sample.pdf", "file_data": data_url}},
        ],
    )

    assert messages[0] == {"role": "system", "content": "system"}
    assert messages[1]["content"][1]["type"] == "file"
    assert messages[1]["content"][1]["file"]["file_data"].startswith("data:application/pdf;base64,")
