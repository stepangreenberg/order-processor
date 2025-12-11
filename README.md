# Система обработки заказов

Минимальный HTTP API + асинхронная обработка через RabbitMQ. Два сервиса: **order-service** и **processor-service**.

## 1. Быстрый запуск (Docker Compose)

Требования: Docker, Docker Compose, Git.

```bash
git clone https://github.com/stepangreenberg/order-processor.git
cd order-processor/infra
docker-compose up -d
docker-compose ps
```

Компоненты: RabbitMQ (5672/15672), order-db (5433), processor-db (5434), order-service (8000), processor-service (8001).

Логи:
```bash
docker-compose logs -f           # все
docker-compose logs -f order-service
docker-compose logs -f processor-service
```

Миграции (order-service, при необходимости):
```bash
docker exec -it order_processor-order-service alembic upgrade head
```

Остановка:
```bash
docker-compose down          # без удаления данных
docker-compose down -v       # с удалением данных
```

## 2. HTTP API

- POST /orders — создать заказ (идемпотентно)
- GET /orders/{order_id} — статус заказа (поле `fail_reason` присутствует при failed)
- GET /health — healthcheck (оба сервиса)
- GET /metrics — простые метрики (оба сервиса)

Примеры пейлоада:

Успех:
```json
{
  "order_id": "ord-sample-success",
  "customer_id": "cust-123",
  "items": [
    {"sku": "laptop", "quantity": 1, "price": 1200.0},
    {"sku": "mouse", "quantity": 2, "price": 25.0}
  ]
}
```

Запрещённые SKU (уйдёт в failed с причиной эмбарго):
```json
{
  "order_id": "ord-embargo-1",
  "customer_id": "cust-embargo",
  "items": [
    {"sku": "pineapple_pizza", "quantity": 1, "price": 15.0},
    {"sku": "teapot", "quantity": 1, "price": 30.0}
  ]
}
```

## 3. Тесты

Внутри контейнеров:
```bash
docker exec -it order_processor-order-service pytest tests/ -v
docker exec -it order_processor-processor-service pytest tests/ -v
```

Локально (Python 3.12+):
```bash
cd order-service && python3 -m pip install -r requirements.txt && python3 -m pytest tests/ -v
cd processor-service && python3 -m pip install -r requirements.txt && python3 -m pytest tests/ -v
```
Тесты используют Testcontainers для интеграции с реальными PostgreSQL и RabbitMQ.

## 4. Миграции
- Order service: Alembic (`order-service/alembic/versions/*`). Применение: `alembic upgrade head` (см. выше).
- Processor service: схема создаётся через SQLAlchemy `metadata.create_all()` при старте.

## 5. Архитектура
- Краткое описание и паттерны: [ARCHITECTURE.md](ARCHITECTURE.md)
- Ключевые идеи: Event-driven, Outbox/Inbox, retries+DLQ, оптимистичная версионность, чистая архитектура (Domain/Application/Infrastructure).

## 6. Структура проекта
```
order-service/      # HTTP API, БД, outbox/inbox, Alembic
processor-service/  # Консьюмер, обработка, outbox/inbox
infra/              # docker-compose.yml
ARCHITECTURE.md     # краткое архитектурное описание
README.md
```
