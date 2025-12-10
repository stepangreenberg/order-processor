# Система Обработки Заказов

Распределенная микросервисная система для обработки заказов с событийно-ориентированной архитектурой.

## Архитектура

Система состоит из двух микросервисов:

- **Order Service** - Управляет заказами, предоставляет HTTP API
- **Processor Service** - Обрабатывает заказы асинхронно со случайным успехом/неудачей

Коммуникация через **RabbitMQ** с использованием паттернов Outbox/Inbox для надежности.

```
┌─────────────────┐         ┌──────────────┐         ┌──────────────────┐
│  Order Service  │────────▶│   RabbitMQ   │────────▶│ Processor Service│
│  (HTTP API)     │         │   (Events)   │         │  (Processing)    │
└─────────────────┘         └──────────────┘         └──────────────────┘
        │                            ▲                         │
        │                            │                         │
        ▼                            └─────────────────────────┘
  PostgreSQL                                            PostgreSQL
```

## Возможности

 - ✅ **Событийно-Ориентированная Архитектура** - Асинхронная коммуникация через RabbitMQ
 - ✅ **Паттерн Outbox** - Надежная публикация событий с транзакционными гарантиями
 - ✅ **Паттерн Inbox** - Дедупликация событий для идемпотентности
 - ✅ **Оптимистичная Конкурентность** - Разрешение конфликтов на основе версий
 - ✅ **Логика Повторов** - Автоматические повторы с экспоненциальной задержкой (max 3 попытки)
 - ✅ **Dead Letter Queue (DLQ)** - Обработка failed событий после превышения лимита повторов
 - ✅ **Database Migrations** - Alembic для версионирования схемы БД с полным покрытием тестами
 - ✅ **Prometheus Metrics** - Простой /metrics endpoint для мониторинга
 - ✅ **Чистая Архитектура** - Разделение Domain/Application/Infrastructure
 - ✅ **Полное Покрытие Тестами** - 45+ тестов (Unit + Integration) с Testcontainers

## Быстрый Старт

### Требования

- Docker & Docker Compose
- Git

### 1. Клонирование Репозитория

```bash
git clone https://github.com/stepangreenberg/order-processor.git
cd order-processor
```

### 2. Запуск Всех Сервисов через Docker Compose

```bash
cd infra
docker-compose up -d
```

Это запустит:
- **RabbitMQ** на портах 5672 (AMQP) и 15672 (Management UI: http://localhost:15672)
- **PostgreSQL (order-db)** на порту 5433 (orderdb)
- **PostgreSQL (processor-db)** на порту 5434 (processor_db)
- **Order Service** на порту 8000 (http://localhost:8000)
- **Processor Service** на порту 8001 (http://localhost:8001)

### 3. Проверка Статуса Сервисов

```bash
docker-compose ps
```

Все сервисы должны быть в статусе "Up" (healthy).

**Просмотр логов:**
```bash
# Все сервисы
docker-compose logs -f

# Конкретный сервис
docker-compose logs -f order-service
docker-compose logs -f processor-service
```

**Остановка сервисов:**
```bash
docker-compose down
```

**Остановка с удалением данных:**
```bash
docker-compose down -v
```

**Запуск миграций базы данных:**
```bash
# Order Service
docker exec -it order_processor-order-service alembic upgrade head

# Processor Service
docker exec -it order_processor-processor-service alembic upgrade head
```

> **Примечание:** Миграции запускаются автоматически при старте сервисов, но вы можете запустить их вручную при необходимости.

### 4. Тестирование Системы

**Создать заказ:**
```bash
curl -X POST http://localhost:8000/orders \
  -H "Content-Type: application/json" \
  -d '{
    "order_id": "ord-001",
    "customer_id": "cust-123",
    "items": [
      {"sku": "laptop", "quantity": 1, "price": 1200.0}
    ]
  }'
```

**Проверить статус заказа:**
```bash
curl http://localhost:8000/orders/ord-001
```

Заказ будет:
1. Создан со статусом `pending`
2. Отправлен в процессор через RabbitMQ
3. Обработан (случайный успех или неудача)
4. Статус обновлен на `done` или `failed`

Проверяйте статус несколько раз, чтобы увидеть прогресс!

## Справка по API

### Order Service

**POST /orders** - Создать новый заказ
```json
{
  "order_id": "ord-001",
  "customer_id": "cust-123",
  "items": [
    {"sku": "laptop", "quantity": 1, "price": 1200.0}
  ]
}
```

**GET /orders/{order_id}** - Получить статус заказа
```json
{
  "order_id": "ord-001",
  "customer_id": "cust-123",
  "status": "done",
  "total_amount": 1200.0,
  "version": 2
}
```

**GET /health** - Проверка здоровья сервиса

**GET /metrics** - Prometheus-совместимые метрики
```
# HELP events_published_total Total number of events successfully published
# TYPE events_published_total counter
events_published_total 42

# HELP events_failed_total Total number of events that failed to publish
# TYPE events_failed_total counter
events_failed_total 3

# HELP events_moved_to_dlq_total Total number of events moved to DLQ
# TYPE events_moved_to_dlq_total counter
events_moved_to_dlq_total 2

# HELP orders_created_total Total number of orders created
# TYPE orders_created_total counter
orders_created_total 15

# HELP orders_processed_total Total number of orders processed
# TYPE orders_processed_total counter
orders_processed_total 12
```

### Processor Service

**GET /health** - Проверка здоровья сервиса

**GET /metrics** - Prometheus-совместимые метрики (аналогично Order Service)

## Запуск Тестов

### Запуск тестов внутри Docker контейнеров:

**Order Service:**
```bash
docker exec -it order_processor-order-service pytest tests/ -v
```

**Processor Service:**
```bash
docker exec -it order_processor-processor-service pytest tests/ -v
```

### Запуск тестов локально (требует Python 3.12+):

**Order Service:**
```bash
cd order-service
python3 -m pip install -r requirements.txt
python3 -m pytest tests/ -v
```

**Processor Service:**
```bash
cd processor-service
python3 -m pip install -r requirements.txt
python3 -m pytest tests/ -v
```

Тесты используют Testcontainers для интеграционного тестирования с реальными PostgreSQL и RabbitMQ.

## Структура Проекта

```
order_processor/
├── order-service/
│   ├── domain/          # Модели домена (Order, ItemLine)
│   ├── application/     # Use cases (CreateOrder, ApplyProcessed)
│   ├── infrastructure/  # БД, RabbitMQ, Outbox/Inbox
│   ├── app/            # FastAPI приложение
│   └── tests/          # Unit + Integration тесты
│
├── processor-service/
│   ├── domain/          # Модели домена (ProcessingState)
│   ├── application/     # Use cases (HandleOrderCreated)
│   ├── infrastructure/  # БД, RabbitMQ, Outbox/Inbox
│   ├── app/            # FastAPI приложение
│   └── tests/          # Unit + Integration тесты
│
├── infra/
│   └── docker-compose.yml
│
└── README.md
```

## Поток Событий

1. **Заказ Создан** (`POST /orders`)
   - Заказ сохранен в БД со статусом `pending`
   - Событие записано в таблицу outbox

2. **Outbox Publisher** (фоновая задача)
   - Опрашивает outbox каждые 5 секунд
   - Публикует события в RabbitMQ
   - Помечает как опубликованные

3. **Processor Потребляет** (`order.created`)
   - Получает событие из RabbitMQ
   - Обрабатывает заказ (случайный успех/неудача)
   - Записывает результат в outbox

4. **Processor Публикует** (`order.processed`)
   - Outbox publisher отправляет результат в RabbitMQ

5. **Order Service Потребляет** (`order.processed`)
   - Обновляет статус заказа на `done` или `failed`
   - Версия инкрементируется

## Ключевые Паттерны

### Паттерн Outbox
Гарантирует атомарную публикацию событий вместе с изменениями в БД:
```
BEGIN TRANSACTION
  INSERT INTO orders ...
  INSERT INTO outbox (event_type, payload, ...)
COMMIT

-- Позже, фоновая задача:
SELECT * FROM outbox WHERE published_at IS NULL
Publish to RabbitMQ
UPDATE outbox SET published_at = NOW()
```

### Паттерн Inbox
Дедуплицирует события для обеспечения идемпотентности:
```
event_key = f"order.processed:{order_id}:{version}"
IF event_key IN inbox THEN
  SKIP (уже обработано)
ELSE
  Process event
  INSERT INTO inbox (event_key)
END
```

### Оптимистичная Конкурентность
Предотвращает конфликты используя номера версий:
```python
if cmd.version <= order.version:
    return None  # Устаревшее событие, игнорируем
order.version = cmd.version
```

## Конфигурация

Переменные окружения:

- `APP__DB_DSN` - Строка подключения к PostgreSQL
- `APP__RABBITMQ_URL` - URL подключения к RabbitMQ
- `APP__SERVICE_NAME` - Имя сервиса для логирования

## Устранение Неполадок

**"Connection refused" к PostgreSQL:**
- Проверьте Docker контейнеры: `docker ps`
- Убедитесь, что порты 5432/5433 не заняты

**"Connection refused" к RabbitMQ:**
- Проверьте RabbitMQ: `docker logs infra-rabbitmq-1`
- Откройте UI управления: http://localhost:15672 (guest/guest)

**События не обрабатываются:**
- Проверьте, что фоновые воркеры запущены (смотрите вывод в консоли)
- Проверьте очереди RabbitMQ: http://localhost:15672/#/queues
- Проверьте таблицы outbox на наличие необработанных событий

## Технологический Стек

- **FastAPI** - HTTP фреймворк
- **SQLAlchemy 2.0** - ORM с async поддержкой
- **asyncpg** - PostgreSQL async драйвер
- **Alembic** - Миграции базы данных
- **aio-pika** - RabbitMQ async клиент
- **Pydantic** - Валидация данных
- **pytest** - Фреймворк для тестирования
- **Testcontainers** - Интеграционное тестирование
- **nest-asyncio** - Поддержка вложенных event loops
- **Docker & Docker Compose** - Контейнеризация и оркестрация

## Лицензия

MIT
