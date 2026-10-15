University: [ITMO University](https://itmo.ru/ru/)<br>
Faculty: [FTMI](https://ftmi.itmo.ru/)<br>
Course: [Vibe Coding: AI-боты для бизнеса](https://ex-itmo-ict-faculty.github.io/vibe-coding-for-business)<br>
Year: 2025/2026<br>
Group: U4125<br>
Author: Shishkina Sofya Anatolievna<br>
Lab: Lab3<br>
Date of create: 15.10.2026<br>
Date of finished: -<br>

# Описание

В этой лабораторной работе бот будет развёрнут так, чтобы им могли пользоваться реальные люди.

# Цель работы

Научиться деплоить бота и собирать обратную связь от реальных пользователей для улучшения продукта.

# Ход работы

## Шаг 1: Выбор способа деплоя

Выберем рекомендованный способ - Облачный хостинг (Railway).

## Шаг 2: Подготовка к деплою

- Создан `.env`-файл (на этапе Лаборатоной №1), где хранится в том числе токен бота.
- `requirements.txt` - уже есть
- логирование - уже есть:
```
LOGGER = logging.getLogger("you_art")
```

Шаг 3: Деплой

- Авторизация на [Railway](https://railway.com) через GitHub.
- Выберем "Deploy from GitHub repo" и репозиторий `2026_2027-chatbots-U4125-shishkina_s_a`.
- Railway автоматически подхватил нужную папку:
  ![Railway](images/1.png)
- В разделе Variables добавим содержимое .env:
  ![Variables](images/2.png)