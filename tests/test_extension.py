import json
import pytest
from pathlib import Path
import tempfile
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from src.database import Database
from src.extension_prompt import build_user_prompt
from src.extension_service import _parse_ai_response, analyze_vacancy
from src.oauth_server import handle_extension_analyze
from src.config import Settings


def test_build_user_prompt():
    vacancy = {
        "title": "Junior Python Developer",
        "conditions": {
            "salary": "100 000 руб.",
            "employment": "Полная занятость",
            "schedule": "Полный день",
            "work_format": "Удаленка",
            "experience": "Без опыта",
        },
        "description": "Разработка на Django/FastAPI."
    }
    prompt = build_user_prompt(vacancy)
    assert "Junior Python Developer" in prompt
    assert "100 000 руб." in prompt
    assert "Полная занятость" in prompt
    assert "Разработка на Django/FastAPI." in prompt


def test_parse_ai_response_clean():
    raw = '{"fit": true, "confidence": "высокая", "reasons": ["A", "B"], "cover_letter": "Hello"}'
    parsed = _parse_ai_response(raw)
    assert parsed["fit"] is True
    assert parsed["confidence"] == "высокая"
    assert parsed["reasons"] == ["A", "B"]
    assert parsed["cover_letter"] == "Hello"


def test_parse_ai_response_markdown():
    raw = '```json\n{"fit": false, "confidence": "средняя", "reasons": ["C"], "cover_letter": null}\n```'
    parsed = _parse_ai_response(raw)
    assert parsed["fit"] is False
    assert parsed["reasons"] == ["C"]


def test_parse_ai_response_with_text():
    raw = 'Some text before\n{"fit": true, "reasons": ["D"]}\nSome text after'
    parsed = _parse_ai_response(raw)
    assert parsed["fit"] is True
    assert parsed["reasons"] == ["D"]


def test_database_extension():
    # Use a temporary database file
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    
    try:
        db = Database(db_path)
        db.init()

        # Cache check when empty
        assert db.get_extension_analysis("111") is None

        # Save analysis
        data = {
            "hh_vacancy_id": "111",
            "url": "https://hh.ru/vacancy/111",
            "title": "Test",
            "fit": True,
            "confidence": "высокая",
            "reasons": ["Reason 1", "Reason 2"],
            "cover_letter": "Letter text",
            "raw_vacancy_json": '{"id": "111"}',
            "created_at": "2026-07-09T18:00:00Z"
        }
        db.save_extension_analysis(data)

        # Retrieve and verify
        retrieved = db.get_extension_analysis("111")
        assert retrieved is not None
        assert retrieved["hh_vacancy_id"] == "111"
        assert retrieved["title"] == "Test"
        assert retrieved["fit"] is True
        assert retrieved["confidence"] == "высокая"
        assert retrieved["reasons"] == ["Reason 1", "Reason 2"]
        assert retrieved["cover_letter"] == "Letter text"
        assert "raw_vacancy_json" not in retrieved
    finally:
        if db_path.exists():
            db_path.unlink()


@pytest.mark.asyncio
async def test_handle_extension_analyze_unauthorized():
    app = web.Application()
    settings = Settings(
        telegram_bot_token="token",
        telegram_user_id=123,
        extension_endpoint_secret="test_secret"
    )
    app["settings"] = settings
    
    # Request without secret header
    req = make_mocked_request("POST", "/api/extension/analyze", app=app)
    resp = await handle_extension_analyze(req)
    assert resp.status == 403
    assert "forbidden" in resp.body.decode()

    # Request with wrong secret header
    headers = {"X-Extension-Secret": "wrong_secret"}
    req = make_mocked_request("POST", "/api/extension/analyze", headers=headers, app=app)
    resp = await handle_extension_analyze(req)
    assert resp.status == 403
