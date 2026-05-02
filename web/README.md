# Лендинг JobRadar

Это отдельный минимальный сайт JobRadar для Vercel. Он нужен как публичный URL приложения при регистрации в dev.hh.ru.

Telegram-бот не запускается на Vercel. Бот остаётся отдельным Python-процессом и работает через long polling.

## Локальный запуск

```bash
npm install
npm run dev
```

После запуска откройте адрес, который покажет Vite.

## Проверка сборки

```bash
npm run build
```

## Деплой на Vercel

1. Создайте проект в Vercel из репозитория.
2. В настройках проекта укажите:

```text
Root Directory = web
```

3. Команды Vercel:

```text
Install Command = npm install
Build Command = npm run build
Output Directory = dist
```

4. После деплоя используйте URL главной страницы как URL сайта приложения в dev.hh.ru.
5. В Redirect URL укажите:

```text
https://your-project.vercel.app/auth/hh/callback
```

## Маршруты

- `/` - главная страница.
- `/privacy` - политика конфиденциальности.
- `/terms` - условия использования.
- `/auth/hh/callback` - callback-заглушка для OAuth hh.ru.

Callback-страница не сохраняет токены и секреты. Полноценный обмен `code` на токен нужно делать только на сервере.
