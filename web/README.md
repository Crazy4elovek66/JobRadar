# Лендинг JobRadar

Это отдельный минимальный сайт JobRadar для Vercel. Он нужен как публичная страница проекта; подключение HH через dev.hh.ru больше не используется.

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

4. После деплоя используйте URL главной страницы как публичную страницу проекта.

## Маршруты

- `/` - главная страница.
- `/privacy` - политика конфиденциальности.
- `/terms` - условия использования.
