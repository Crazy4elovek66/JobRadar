import React from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const contactText = "Контакт для связи указывается владельцем проекта при деплое.";

function App() {
  const path = window.location.pathname;

  if (path === "/privacy") {
    return <PrivacyPage />;
  }

  if (path === "/terms") {
    return <TermsPage />;
  }

  if (path === "/auth/hh/callback") {
    return <CallbackPage />;
  }

  return <HomePage />;
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="page">
      <header className="topbar">
        <a className="brand" href="/" aria-label="На главную">
          <span className="brandMark" aria-hidden="true" />
          <span>JobRadar</span>
        </a>
        <nav className="nav" aria-label="Навигация">
          <a href="/privacy">Конфиденциальность</a>
          <a href="/terms">Условия</a>
        </nav>
      </header>
      {children}
      <footer className="footer">
        <span>JobRadar</span>
        <span>{contactText}</span>
      </footer>
    </div>
  );
}

function HomePage() {
  return (
    <Shell>
      <main>
        <section className="hero">
          <div className="heroText">
            <p className="eyebrow">Личный MVP-инструмент</p>
            <h1>JobRadar</h1>
            <p className="lead">Личный помощник для поиска подходящих IT-вакансий на hh.ru</p>
            <p className="description">
              JobRadar помогает находить удалённые вакансии начального уровня в IT и около-IT направлениях:
              QA, helpdesk, техническая поддержка, CRM, low-code и автоматизация.
            </p>
          </div>
          <div className="productPanel" aria-label="Пример результата JobRadar">
            <div className="panelHeader">
              <span>Найдено</span>
              <strong>87/100</strong>
            </div>
            <div className="vacancyPreview">
              <span className="status">Подходит</span>
              <h2>Junior QA Manual</h2>
              <p>Удалённо, без опыта, есть тест-кейсы и баг-репорты.</p>
            </div>
            <div className="signals">
              <span>Удалёнка</span>
              <span>QA</span>
              <span>Junior</span>
            </div>
          </div>
        </section>

        <section className="section">
          <h2>Что делает бот</h2>
          <div className="grid">
            {[
              "ищет вакансии через официальный API hh.ru",
              "фильтрует неподходящие предложения",
              "исключает колл-центры, горячие линии и продажи",
              "выделяет вакансии с реальными техническими задачами",
              "отправляет подходящие варианты владельцу в Telegram",
            ].map((item) => (
              <article className="card" key={item}>
                <span className="dot" aria-hidden="true" />
                <p>{item}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="splitSection">
          <div>
            <h2>Для кого</h2>
            <ul className="list">
              <li>для личного поиска работы</li>
              <li>для входа в IT</li>
              <li>для отслеживания удалённых junior-вакансий</li>
            </ul>
          </div>
          <div>
            <h2>Статус</h2>
            <p className="muted">Проект находится в стадии MVP.</p>
            <h2>Контакты</h2>
            <p className="muted">{contactText}</p>
          </div>
        </section>
      </main>
    </Shell>
  );
}

function PrivacyPage() {
  return (
    <Shell>
      <main className="document">
        <h1>Политика конфиденциальности</h1>
        <p>JobRadar используется как личный инструмент для поиска вакансий и оценки их полезности.</p>
        <p>
          Проект может обрабатывать данные вакансий, полученные через API hh.ru: название, компанию, ссылку,
          зарплату, регион, формат работы и описание.
        </p>
        <p>JobRadar не продаёт данные третьим лицам и не предназначен для массовой рассылки или автокликов.</p>
        <p>
          Пользовательские токены, client secret и другие секреты не должны храниться в публичном коде. Если в
          будущем появится авторизация hh.ru, access token будет использоваться только для работы с API hh.ru.
        </p>
        <p>{contactText}</p>
      </main>
    </Shell>
  );
}

function TermsPage() {
  return (
    <Shell>
      <main className="document">
        <h1>Условия использования</h1>
        <p>JobRadar не является официальным продуктом hh.ru и не представляет hh.ru.</p>
        <p>Проект не гарантирует трудоустройство, приглашения на собеседование или ответы работодателей.</p>
        <p>
          Результаты поиска зависят от доступности API hh.ru, параметров поиска, качества вакансий и настроек
          фильтрации.
        </p>
        <p>Владелец проекта самостоятельно принимает решение об отклике на вакансии.</p>
        <p>Автоматические отклики в текущей MVP-версии не являются основной функцией.</p>
      </main>
    </Shell>
  );
}

function CallbackPage() {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  const error = params.get("error");

  return (
    <Shell>
      <main className="document callback">
        <h1>Авторизация hh.ru завершена</h1>
        <p>Этот экран используется для технической проверки OAuth-подключения JobRadar.</p>
        {code ? <p className="stateOk">Код авторизации получен. Серверная обработка будет добавлена позже.</p> : null}
        {error ? <p className="stateError">hh.ru вернул ошибку авторизации: {error}</p> : null}
        {!code && !error ? <p className="muted">В URL нет параметров `code` или `error`.</p> : null}
        {/* TODO: обменивать code на токен только на сервере, без сохранения секретов в браузере. */}
      </main>
    </Shell>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
