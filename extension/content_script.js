// content_script.js — JobRadar: скрапинг вакансии и UI оверлея

(function () {
  'use strict';

  // ── Кэш последнего результата ──────────────────────────────────────
  let lastResult = null;
  let lastVacancyId = null;

  // ── Утилиты ────────────────────────────────────────────────────────

  /**
   * Очистка HTML → plain text.
   * Конвертирует <li> в "- item\n", убирает теги, невидимые символы,
   * схлопывает пустые строки.
   */
  function cleanVacancyText(html) {
    if (!html) return '';

    let text = html;

    // <br> → перенос строки
    text = text.replace(/<br\s*\/?>/gi, '\n');

    // <li> → "- содержимое\n"
    text = text.replace(/<li[^>]*>([\s\S]*?)<\/li>/gi, (_, inner) => {
      return '- ' + inner.replace(/<[^>]*>/g, '').trim() + '\n';
    });

    // Остальные блочные теги → перенос строки
    text = text.replace(/<\/(p|div|h[1-6]|ul|ol|tr)>/gi, '\n');
    text = text.replace(/<(p|div|h[1-6]|ul|ol|tr)[^>]*>/gi, '\n');

    // Убираем все оставшиеся HTML-теги
    text = text.replace(/<[^>]*>/g, '');

    // Декодируем HTML-сущности
    const textarea = document.createElement('textarea');
    textarea.innerHTML = text;
    text = textarea.value;

    // Убираем невидимые Unicode-символы
    text = text.replace(/[\u200b\u200c\u200d\u200e\u200f\ufeff\u00ad\u2060\u2061\u2062\u2063\u2064]/g, '');

    // Схлопываем 3+ переноса строк в 2
    text = text.replace(/\n{3,}/g, '\n\n');

    // Убираем пробелы в начале/конце каждой строки
    text = text.split('\n').map(line => line.trim()).join('\n');

    return text.trim();
  }

  /**
   * Извлечь текст элемента по data-qa селектору.
   */
  function getTextByQA(qa) {
    const el = document.querySelector(`[data-qa="${qa}"]`);
    return el ? el.textContent.trim() : '';
  }

  /**
   * Извлечь innerHTML элемента по data-qa селектору.
   */
  function getHtmlByQA(qa) {
    const el = document.querySelector(`[data-qa="${qa}"]`);
    return el ? el.innerHTML : '';
  }

  // ── Скрапинг вакансии ──────────────────────────────────────────────

  function scrapeVacancyPage() {
    // ID из URL
    const idMatch = window.location.pathname.match(/\/vacancy\/(\d+)/);
    const id = idMatch ? idMatch[1] : '';

    // Название
    let title = getTextByQA('vacancy-title');
    if (!title) {
      title = document.title.replace(/—.*$/, '').replace(/\|.*$/, '').trim();
    }

    // Зарплата
    let salary = getTextByQA('vacancy-salary') || getTextByQA('vacancy-salary-compensation-type-net') || getTextByQA('vacancy-salary-compensation-type-gross');
    if (!salary) {
      // Фоллбек: ищем текст с ₽/руб/USD/EUR рядом с заголовком
      const headerArea = document.querySelector('.vacancy-title, .bloko-header-section-1, h1');
      if (headerArea && headerArea.parentElement) {
        const parentText = headerArea.parentElement.textContent;
        const salaryMatch = parentText.match(/[\d\s]+[–—-][\d\s]*(₽|руб|USD|EUR|$)/i) ||
                            parentText.match(/(от|до)\s*[\d\s]+(₽|руб|USD|EUR|$)/i);
        if (salaryMatch) salary = salaryMatch[0].trim();
      }
    }

    // Опыт
    let experience = getTextByQA('vacancy-experience');
    if (!experience) {
      const bodyText = document.body.textContent;
      const expPatterns = ['Нет опыта', 'Не требуется', 'От 1 года', 'От 3 лет', 'От 6 лет', '1–3 года', '3–6 лет', 'Более 6 лет'];
      for (const pattern of expPatterns) {
        if (bodyText.includes(pattern)) {
          experience = pattern;
          break;
        }
      }
    }

    // Формат работы (занятость, график, удалёнка)
    let employmentMode = getTextByQA('vacancy-view-employment-mode');
    if (!employmentMode) {
      const bodyText = document.body.textContent;
      const modes = [];
      const modePatterns = ['Полная занятость', 'Частичная занятость', 'Полный день', 'Гибкий график',
        'Удалённая работа', 'Можно из дома', 'Офис', 'Гибрид', 'Проектная работа', 'Стажировка',
        'Сменный график', 'Вахтовый метод'];
      for (const pattern of modePatterns) {
        if (bodyText.includes(pattern)) modes.push(pattern);
      }
      employmentMode = modes.join(', ');
    }

    // Описание вакансии
    let descriptionHtml = getHtmlByQA('vacancy-description');
    if (!descriptionHtml) {
      // Фоллбек: ищем самый большой текстовый блок
      const blocks = document.querySelectorAll('div, section, article');
      let maxLen = 0;
      let maxBlock = null;
      blocks.forEach(block => {
        const len = block.textContent.trim().length;
        if (len > maxLen && len > 200) {
          maxLen = len;
          maxBlock = block;
        }
      });
      if (maxBlock) descriptionHtml = maxBlock.innerHTML;
    }

    // Обрезаем описание на блоке «Ключевые навыки»
    if (descriptionHtml) {
      const cutIdx = descriptionHtml.search(/Ключевые\s+навыки/i);
      if (cutIdx > 0) {
        descriptionHtml = descriptionHtml.substring(0, cutIdx);
      }
    }

    const description = cleanVacancyText(descriptionHtml);

    // Ключевые навыки (теги)
    const skillTags = document.querySelectorAll('[data-qa="bloko-tag__text"], [data-qa="skills-element"]');
    const skills = [];
    skillTags.forEach(tag => {
      const text = tag.textContent.trim();
      if (text && !skills.includes(text)) skills.push(text);
    });

    // Компания
    const company = getTextByQA('vacancy-company-name') ||
                     getTextByQA('vacancy-company-name-wrapper') || '';

    return {
      id,
      url: window.location.href.split('?')[0],
      title,
      salary,
      experience,
      employment_mode: employmentMode,
      description,
      skills,
      company,
    };
  }

  // ── UI: Плавающая кнопка ───────────────────────────────────────────

  function injectFloatingButton() {
    if (document.getElementById('jobradar-fab')) return;

    const btn = document.createElement('button');
    btn.id = 'jobradar-fab';
    btn.textContent = '🎯 Анализ';
    btn.addEventListener('click', onButtonClick);
    document.body.appendChild(btn);
  }

  // ── UI: Оверлей ────────────────────────────────────────────────────

  function showOverlay(state, data) {
    // Удаляем предыдущий оверлей если есть
    const existing = document.getElementById('jobradar-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'jobradar-overlay';
    overlay.className = 'jobradar-overlay';

    const card = document.createElement('div');
    card.className = 'jobradar-card';

    // Кнопка закрытия
    const closeBtn = document.createElement('button');
    closeBtn.className = 'jobradar-close';
    closeBtn.textContent = '✕';
    closeBtn.addEventListener('click', () => overlay.remove());
    card.appendChild(closeBtn);

    if (state === 'loading') {
      const title = document.createElement('h2');
      title.className = 'jobradar-title';
      title.textContent = 'Анализ вакансии…';
      card.appendChild(title);

      const sub = document.createElement('p');
      sub.className = 'jobradar-subtitle';
      sub.textContent = 'Отправляем данные на анализ, подождите';
      card.appendChild(sub);

      const spinner = document.createElement('div');
      spinner.className = 'jobradar-spinner';
      spinner.textContent = '⏳';
      card.appendChild(spinner);
    } else if (state === 'error') {
      const title = document.createElement('h2');
      title.className = 'jobradar-title';
      title.textContent = 'Ошибка';
      card.appendChild(title);

      const msg = document.createElement('p');
      msg.className = 'jobradar-error-text';
      msg.textContent = data.error || 'Неизвестная ошибка';
      card.appendChild(msg);
    } else if (state === 'result') {
      const result = data;
      const isFit = result.fit === true;

      card.classList.add(isFit ? 'jobradar-card--fit' : 'jobradar-card--nofit');

      // Заголовок
      const title = document.createElement('h2');
      title.className = 'jobradar-title';
      title.textContent = isFit ? 'Вакансия подходит' : 'Вакансия не подходит';
      card.appendChild(title);

      // Причины
      if (result.reasons && result.reasons.length > 0) {
        const reasonsTitle = document.createElement('p');
        reasonsTitle.className = 'jobradar-reasons-title';
        reasonsTitle.textContent = 'Причины:';
        card.appendChild(reasonsTitle);

        const list = document.createElement('ul');
        list.className = 'jobradar-reasons';
        result.reasons.forEach(reason => {
          const li = document.createElement('li');
          li.textContent = reason;
          list.appendChild(li);
        });
        card.appendChild(list);
      }

      // Сопроводительное письмо (только если подходит)
      if (isFit && result.cover_letter) {
        const letterTitle = document.createElement('p');
        letterTitle.className = 'jobradar-letter-title';
        letterTitle.textContent = 'Сопроводительное письмо:';
        card.appendChild(letterTitle);

        const textarea = document.createElement('textarea');
        textarea.className = 'jobradar-textarea';
        textarea.readOnly = true;
        textarea.value = result.cover_letter;
        textarea.rows = 10;
        card.appendChild(textarea);

        const copyBtn = document.createElement('button');
        copyBtn.className = 'jobradar-copy-btn';
        copyBtn.textContent = 'Скопировать текст отклика';
        copyBtn.addEventListener('click', () => {
          navigator.clipboard.writeText(result.cover_letter).then(() => {
            copyBtn.textContent = 'Скопировано ✓';
            setTimeout(() => {
              copyBtn.textContent = 'Скопировать текст отклика';
            }, 1500);
          });
        });
        card.appendChild(copyBtn);
      }
    }

    overlay.appendChild(card);

    // Закрытие по клику на backdrop
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) overlay.remove();
    });

    // Закрытие по Escape
    const onEscape = (e) => {
      if (e.key === 'Escape') {
        overlay.remove();
        document.removeEventListener('keydown', onEscape);
      }
    };
    document.addEventListener('keydown', onEscape);

    document.body.appendChild(overlay);
  }

  // ── Обработка клика по кнопке ──────────────────────────────────────

  async function onButtonClick() {
    const vacancy = scrapeVacancyPage();

    // Если есть кэш для этой вакансии — показываем без запроса
    if (lastResult && lastVacancyId === vacancy.id) {
      showOverlay('result', lastResult);
      return;
    }

    if (!vacancy.title) {
      showOverlay('error', { error: 'Не удалось найти вакансию на странице' });
      return;
    }

    showOverlay('loading');

    // Формируем payload по формату, который ожидает бэкенд
    const employmentParts = (vacancy.employment_mode || '').split(',').map(s => s.trim());
    const payload = {
      hh_vacancy_id: vacancy.id,
      url: vacancy.url,
      title: vacancy.title,
      conditions: {
        salary: vacancy.salary || null,
        employment: employmentParts[0] || null,
        schedule: employmentParts[1] || null,
        work_format: employmentParts[2] || null,
        experience: vacancy.experience || null,
      },
      description: vacancy.description || null,
      scraped_at: new Date().toISOString(),
    };

    chrome.runtime.sendMessage(
      { type: 'analyzeVacancy', payload },
      (response) => {
        if (chrome.runtime.lastError) {
          showOverlay('error', { error: `Ошибка расширения: ${chrome.runtime.lastError.message}` });
          return;
        }

        if (!response) {
          showOverlay('error', { error: 'Нет ответа от фонового процесса' });
          return;
        }

        if (response.success) {
          lastResult = response.data;
          lastVacancyId = vacancy.id;
          showOverlay('result', response.data);
        } else {
          showOverlay('error', { error: response.error });
        }
      }
    );
  }

  // ── Инициализация ──────────────────────────────────────────────────

  injectFloatingButton();
})();
