import pytest
from src.models import Vacancy, Salary
from src.scoring import calculate_score

def test_reject_stop_phrases():
    # Test that a vacancy with a stop-phrase is rejected
    vacancy = Vacancy(
        source="hh",
        external_id="123",
        title="Оператор call-центра",
        company="Test Company",
        url="http://example.com",
        description="Прием входящих звонков и обзвон клиентов.",
        salary=Salary(salary_from=30000, salary_to=40000)
    )
    result = calculate_score(vacancy)
    assert result.status == "REJECT"
    assert any("колл-центр" in r or "stop" in r or "звонк" in r or "call-центр" in r for r in result.reasons_negative)

def test_hot_vacancy():
    # Test that a good vacancy yields a hot/good status
    vacancy = Vacancy(
        source="hh",
        external_id="124",
        title="Junior QA Engineer (Manual)",
        company="Tech Corp",
        url="http://example.com",
        description="Ищем junior qa для ручного тестирования веб-сервиса. Удаленная работа, api, python, sql, тикеты в jira.",
        salary=Salary(salary_from=50000, salary_to=60000),
        raw={"published_at": "2026-07-07T12:00:00Z"}  # fresh
    )
    result = calculate_score(vacancy)
    assert result.status in ["HOT", "GOOD"]
    assert any("тестирование" in r or "qa" in r for r in result.reasons_positive)
    assert any("удаленная работа" in r for r in result.reasons_positive)

def test_maybe_vacancy():
    # Test that a vacancy with positive and negative groups yields a maybe/reject status
    vacancy = Vacancy(
        source="hh",
        external_id="125",
        title="Junior Support Analyst",
        company="Finance Inc",
        url="http://example.com",
        description="Техническая поддержка IT в офисе. Опыт от 3 лет.",
        salary=Salary(salary_from=30000)
    )
    result = calculate_score(vacancy)
    assert result.status in ["MAYBE", "REJECT"]
