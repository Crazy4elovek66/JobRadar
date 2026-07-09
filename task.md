# Патч для src/extension_service.py

Промпт ужесточён, но маленькая модель (gemini-2.5-flash-lite) не даёт 100%
гарантии соблюдения инструкции. Добавляем защитный пост-процессинг как
второй рубеж - если модель всё равно вставит приветствие или markdown,
код сам их вырежет перед сохранением в БД.

## 1. Добавить функцию (рядом с `_parse_ai_response`)

```python
_GREETING_PATTERNS = [
    r'^\s*(здравствуйте|добрый\s+день|добрый\s+вечер|доброе\s+утро|приветствую|привет)[!,.\s-]*',
    r'^\s*меня\s+зовут[^.\n]*[.\n]\s*',
    r'^\s*разрешите\s+представиться[^.\n]*[.\n]\s*',
]

_SIGNOFF_PATTERNS = [
    r'\s*с\s+уважением[,.\s]*[^\n]*$',
    r'\s*заранее\s+спасибо[^\n]*$',
    r'\s*буду\s+рад[а]?\s+сотрудничеству[^\n]*$',
    r'\s*жду\s+обратной\s+связи[^\n]*$',
]


def _sanitize_cover_letter(text: str | None) -> str | None:
    """Защитный пост-процессинг: вырезает приветствия, представления по
    имени, прощальные клише и markdown-разметку, если модель всё же их
    вставила вопреки промпту."""
    if not text:
        return text

    cleaned = text.strip()

    # Приветствия и самопредставление в начале (возможно, несколько подряд)
    for _ in range(3):
        before = cleaned
        for pattern in _GREETING_PATTERNS:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip()
        if cleaned == before:
            break

    # Прощание/подпись в конце
    for pattern in _SIGNOFF_PATTERNS:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)

    # Markdown-разметка
    cleaned = re.sub(r'\*\*(.+?)\*\*', r'\1', cleaned)   # **жирный**
    cleaned = re.sub(r'__(.+?)__', r'\1', cleaned)        # __жирный__
    cleaned = re.sub(r'^#{1,6}\s*', '', cleaned, flags=re.MULTILINE)  # ## заголовки
    cleaned = cleaned.replace('—', '-')                   # длинное тире

    return cleaned.strip()
```

## 2. Применить в `analyze_vacancy`, сразу после парсинга ответа

Найти в текущем коде:

```python
    # Build result
    result = {
        "hh_vacancy_id": hh_vacancy_id,
        "url": vacancy_data.get("url", ""),
        "title": vacancy_data.get("title"),
        "fit": bool(parsed.get("fit", False)),
        "confidence": parsed.get("confidence", "низкая"),
        "reasons": parsed.get("reasons", []),
        "cover_letter": parsed.get("cover_letter"),
    }
```

Заменить последнюю строку на:

```python
    # Build result
    result = {
        "hh_vacancy_id": hh_vacancy_id,
        "url": vacancy_data.get("url", ""),
        "title": vacancy_data.get("title"),
        "fit": bool(parsed.get("fit", False)),
        "confidence": parsed.get("confidence", "низкая"),
        "reasons": parsed.get("reasons", []),
        "cover_letter": _sanitize_cover_letter(parsed.get("cover_letter")),
    }
```

`re` уже импортирован в файле (используется в `_parse_ai_response`) - новых
зависимостей не требуется.

## 3. Как проверить, что сработало

1. Заменить `src/extension_prompt.py` на версию из `extension_prompt.py`
   (готовый файл рядом с этим патчем).
2. Внести патч выше в `src/extension_service.py`.
3. Перезапустить `python main.py`.
4. Прогнать 3-5 разных вакансий через расширение (junior-техподдержка,
   QA, Python-backend) - проверить, что `cover_letter` начинается сразу
   с содержательного предложения, без "Здравствуйте"/"Меня зовут", и
   заканчивается без "С уважением".
5. Если модель всё равно проскочит через оба рубежа на какой-то формулировке -
   скинь мне конкретный проблемный ответ, дожму паттерны точечно под него.