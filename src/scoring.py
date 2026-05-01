from __future__ import annotations

from datetime import datetime, timezone

from src.models import ScoreResult, Vacancy


SEARCH_QUERIES = [
    "junior qa",
    "тестировщик без опыта",
    "стажер тестировщик",
    "manual qa",
    "техническая поддержка it",
    "helpdesk",
    "service desk",
    "junior support",
    "support engineer junior",
    "специалист технической поддержки it",
    "специалист поддержки saas",
    "crm специалист",
    "bitrix24 junior",
    "amoCRM",
    "специалист по интеграциям",
    "no-code",
    "low-code",
    "автоматизация процессов",
    "junior python",
    "стажер python",
    "python automation",
    "парсер данных",
    "оператор мониторинга it",
    "младший системный администратор",
]

HARD_REJECT_PHRASES = [
    "горячая линия",
    "оператор горячей линии",
    "call-центр",
    "call центр",
    "колл-центр",
    "колл центр",
    "контактный центр",
    "оператор контактного центра",
    "оператор входящих звонков",
    "оператор исходящих звонков",
    "входящие звонки",
    "исходящие звонки",
    "холодные звонки",
    "телемаркетинг",
    "продажи по телефону",
    "телефонные продажи",
    "работа по скрипту",
    "консультация по скрипту",
    "прием звонков",
    "прием звонков",
    "массовый обзвон",
    "обзвон клиентов",
    "назначение встреч",
    "лидогенерация звонками",
]

NEGATIVE_GROUPS: list[tuple[int, list[str], str]] = [
    (-50, ["поддержка покупателей", "поддержка курьеров", "поддержка водителей", "служба доставки"], "похоже на клиентскую поддержку без IT-задач"),
    (-40, ["офис", "только офис", "полный день в офисе"], "офисный формат без явной удаленки"),
    (-40, ["опыт от 3 лет", "от 3-х лет", "3 года опыта", "between3and6"], "требуется слишком большой опыт"),
    (-35, ["высшее образование обязательно", "обязательно высшее"], "обязательно высшее образование"),
    (-30, ["менеджер по работе с клиентами", "клиентский сервис", "только общение с клиентами"], "много клиентского сервиса без технической части"),
    (-25, ["2/2", "ночные смены", "ночь", "поток звонков", "высокая нагрузка"], "есть риск тяжелого сменного графика"),
    (-20, ["банк", "страховая", "микрозаймы", "маркетплейс"], "сфера часто дает псевдо-поддержку, нужна ручная проверка"),
]

POSITIVE_GROUPS: list[tuple[int, list[str], str]] = [
    (40, ["удаленная работа", "remote", "удаленно"], "удаленная работа"),
    (35, ["it-продукт", "it продукт", "saas", "software", "платформа", "программное обеспечение", "web-сервис", "веб-сервис"], "IT-продукт или техническая платформа"),
    (30, ["без опыта", "junior", "trainee", "стажер", "младший"], "можно начинать без сильного опыта"),
    (30, ["техническая поддержка it", "поддержка it-продукта", "поддержка it продукта", "техническая поддержка продукта"], "техническая поддержка IT-продукта"),
    (30, ["qa", "тестирование", "тестировщик", "manual testing", "баг-репорт", "баг репорт", "test case", "тест-кейс", "чек-лист"], "есть тестирование и работа с качеством"),
    (25, ["crm", "bitrix24", "битрикс24", "amocrm", "amo crm", "интеграции"], "есть CRM или интеграции"),
    (25, ["python", "sql", "api", "скрипты", "автоматизация"], "есть Python, SQL, API или автоматизация"),
    (25, ["тикеты", "jira", "confluence", "helpdesk", "service desk"], "есть тикеты, Helpdesk или Service Desk"),
    (20, ["настройка по", "диагностика", "логи", "баги", "администрирование", "мониторинг", "инциденты", "troubleshooting", "технические проблемы"], "есть диагностика, логи или технические проблемы"),
    (20, ["гибрид", "частично удал", "возможность удал"], "есть шанс на гибкий формат"),
    (15, ["обучение", "наставник", "ментор", "внутри компании"], "есть обучение внутри компании"),
    (15, ["портфолио не обязательно", "без портфолио"], "портфолио не выглядит обязательным"),
]


def calculate_score(vacancy: Vacancy) -> ScoreResult:
    text = _combined_text(vacancy)
    reasons_positive: list[str] = []
    reasons_negative: list[str] = []
    score = 0

    hard_matches = [phrase for phrase in HARD_REJECT_PHRASES if phrase in text]
    if hard_matches:
        return ScoreResult(
            score=0,
            status="REJECT",
            career_value=1,
            reasons_positive=[],
            reasons_negative=[f"стоп-фактор: {phrase}" for phrase in hard_matches[:3]],
        )

    for points, phrases, reason in POSITIVE_GROUPS:
        if any(phrase in text for phrase in phrases):
            score += points
            reasons_positive.append(reason)

    for points, phrases, reason in NEGATIVE_GROUPS:
        if any(phrase in text for phrase in phrases):
            score += points
            reasons_negative.append(reason)

    if vacancy.salary.salary_from or vacancy.salary.salary_to:
        score += 10
        reasons_positive.append("зарплата указана")
    else:
        reasons_negative.append("зарплата не указана")

    if _is_fresh(vacancy):
        score += 10
        reasons_positive.append("свежая вакансия")

    score += _salary_adjustment(vacancy, reasons_positive, reasons_negative)
    score = max(0, min(100, score))
    career_value = _career_value(score, reasons_positive, reasons_negative)

    if score >= 80:
        status = "HOT"
    elif score >= 60:
        status = "GOOD"
    elif score >= 40:
        status = "MAYBE"
    else:
        status = "REJECT"

    return ScoreResult(
        score=score,
        status=status,
        career_value=career_value,
        reasons_positive=_deduplicate(reasons_positive),
        reasons_negative=_deduplicate(reasons_negative),
    )


def _combined_text(vacancy: Vacancy) -> str:
    parts = [
        vacancy.title,
        vacancy.company,
        vacancy.description,
        vacancy.schedule,
        vacancy.experience,
        vacancy.employment,
        vacancy.area,
    ]
    return " ".join(part for part in parts if part).lower().replace("ё", "е")


def _is_fresh(vacancy: Vacancy) -> bool:
    published_at = vacancy.raw.get("published_at")
    if not published_at:
        return False
    try:
        published = datetime.fromisoformat(str(published_at))
    except ValueError:
        return False
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - published.astimezone(timezone.utc)).days <= 2


def _salary_adjustment(vacancy: Vacancy, positives: list[str], negatives: list[str]) -> int:
    salary_from = vacancy.salary.salary_from
    salary_to = vacancy.salary.salary_to
    best_salary = salary_from or salary_to
    if not best_salary:
        return 0
    title = vacancy.title.lower()
    is_junior = any(word in title.replace("ё", "е") for word in ["junior", "стажер", "младший", "без опыта"])
    if best_salary < 35000 and is_junior:
        positives.append("зарплата ниже текущей, но вакансия может дать вход в IT")
        return 0
    if best_salary < 35000:
        negatives.append("зарплата ниже 35 000 ₽")
        return -20
    if best_salary >= 45000:
        positives.append("зарплата выше текущего ориентира")
        return 10
    positives.append("зарплата не ниже текущего ориентира")
    return 5


def _career_value(score: int, positives: list[str], negatives: list[str]) -> int:
    value = round(score / 10)
    if any("Python" in item or "SQL" in item or "тестирование" in item for item in positives):
        value += 1
    if any("клиент" in item or "смен" in item for item in negatives):
        value -= 1
    return max(1, min(10, value))


def _deduplicate(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result
