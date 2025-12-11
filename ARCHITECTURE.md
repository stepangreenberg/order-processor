# Документация по Архитектуре

## Обзор Системы

Это распределенная микросервисная система, реализующая событийно-ориентированную архитектуру с гарантиями строгой согласованности через паттерны Outbox и Inbox.

## Принципы Архитектуры

### 1. Событийно-Ориентированная Архитектура (EDA)

Сервисы общаются асинхронно через события, публикуемые в RabbitMQ:
- **Слабая Связанность** - Сервисы не знают друг о друге напрямую
- **Масштабируемость** - Сервисы можно масштабировать независимо
- **Устойчивость** - Сбои в одном сервисе не каскадируются

### 2. Чистая Архитектура

Каждый сервис следует принципам Чистой Архитектуры:

```
┌─────────────────────────────────────┐
│           API Layer (FastAPI)        │
├─────────────────────────────────────┤
│     Application Layer (Use Cases)    │
├─────────────────────────────────────┤
│      Domain Layer (Business Logic)   │
├─────────────────────────────────────┤
│   Infrastructure (DB, RabbitMQ)      │
└─────────────────────────────────────┘
```

**Преимущества:**
- Доменная логика независима от фреймворков
- Легко тестировать (домен не имеет зависимостей)
- Можно менять инфраструктуру без изменения бизнес-логики

### 3. Паттерн Outbox

**Проблема:** Как атомарно обновить БД И опубликовать событие?

**Решение:** Записать событие в таблицу outbox в той же транзакции:

```python
async with uow:
    # Бизнес-логика
    order = Order.create(...)
    await uow.orders.add(order)

    # Событие в outbox
    await uow.outbox.put("order.created", {
        "order_id": order.order_id,
        "amount": order.total_amount
    })

    # Единый атомарный коммит
    await uow.commit()
```

**Background Publisher** опрашивает outbox и публикует в RabbitMQ:

```python
while True:
    events = SELECT * FROM outbox WHERE published_at IS NULL
    for event in events:
        publish_to_rabbitmq(event)
        UPDATE outbox SET published_at = NOW() WHERE id = event.id
    await asyncio.sleep(5)
```

**Гарантии:**
- ✅ Событие публикуется КАК МИНИМУМ ОДИН РАЗ
- ✅ Ни одно событие не теряется (переживает крахи)
- ⚠️ Может публиковать дубликаты (обрабатывается паттерном Inbox)

### 4. Паттерн Inbox

**Проблема:** Потребитель может получить дубликаты событий (повторы сети и т.д.)

**Решение:** Отслеживать обработанные события в таблице inbox:

```python
event_key = f"order.processed:{order_id}:{version}"

async with uow:
    if await uow.inbox.exists(event_key):
        return  # Уже обработано, пропускаем

    # Обработка события
    order.status = "done"
    await uow.orders.add(order)

    # Отметить как обработанное
    await uow.inbox.add(event_key)

    await uow.commit()
```

**Гарантии:**
- ✅ Идемпотентная обработка (безопасно повторять события)
- ✅ Семантика exactly-once на уровне приложения

### 5. Оптимистичный Контроль Конкурентности

Использует номера версий для обнаружения конфликтов:

```python
# Приходит событие с version=2
if event.version <= order.version:  # order.version=3
    return  # Устаревшее событие, игнорируем

# Событие новее, применяем его
order.status = event.status
order.version = event.version
```

**Почему не пессимистическая блокировка?**
- Не нужны распределенные блокировки
- Лучшая производительность (нет конкуренции за блокировки)
- Работает на разных базах данных

## Поток Данных

### Создание Заказа

```
┌──────────┐
│  Client  │
└─────┬────┘
      │ POST /orders
      ▼
┌─────────────────────────────────────────┐
│         Order Service                   │
│                                         │
│  ┌──────────────────────────────────┐  │
│  │  CreateOrderUseCase              │  │
│  │  1. Validate order               │  │
│  │  2. Save to orders table         │  │
│  │  3. Write to outbox table        │  │
│  │  4. Commit transaction           │  │
│  └──────────────────────────────────┘  │
│                                         │
│  ┌──────────────────────────────────┐  │
│  │  OutboxPublisher (background)    │  │
│  │  5. Poll outbox every 5s         │  │
│  │  6. Publish to RabbitMQ          │  │
│  │  7. Mark as published            │  │
│  └──────────────────────────────────┘  │
└─────────────────┬───────────────────────┘
                  │ order.created
                  ▼
           ┌──────────────┐
           │   RabbitMQ   │
           └──────┬───────┘
                  │ order.created
                  ▼
┌─────────────────────────────────────────┐
│       Processor Service                 │
│                                         │
│  ┌──────────────────────────────────┐  │
│  │  OrderCreatedConsumer            │  │
│  │  8. Consume from RabbitMQ        │  │
│  │  9. Check inbox (dedup)          │  │
│  │  10. Process order (random)      │  │
│  │  11. Write to outbox             │  │
│  │  12. Mark in inbox               │  │
│  └──────────────────────────────────┘  │
│                                         │
│  ┌──────────────────────────────────┐  │
│  │  OutboxPublisher (background)    │  │
│  │  13. Poll outbox                 │  │
│  │  14. Publish to RabbitMQ         │  │
│  └──────────────────────────────────┘  │
└─────────────────┬───────────────────────┘
                  │ order.processed
                  ▼
           ┌──────────────┐
           │   RabbitMQ   │
           └──────┬───────┘
                  │ order.processed
                  ▼
┌─────────────────────────────────────────┐
│         Order Service                   │
│                                         │
│  ┌──────────────────────────────────┐  │
│  │  OrderProcessedConsumer          │  │
│  │  15. Consume from RabbitMQ       │  │
│  │  16. Check inbox (dedup)         │  │
│  │  17. Update order status         │  │
│  │  18. Increment version           │  │
│  │  19. Mark in inbox               │  │
│  └──────────────────────────────────┘  │
└─────────────────────────────────────────┘
```

## Схема Базы Данных

### Order Service

**Таблица orders:**
```sql
CREATE TABLE orders (
    order_id VARCHAR PRIMARY KEY,
    customer_id VARCHAR NOT NULL,
    items JSONB NOT NULL,
    amount DECIMAL NOT NULL,
    status VARCHAR NOT NULL,
    version INTEGER NOT NULL
);
```

**Таблица outbox:**
```sql
CREATE TABLE outbox (
    id SERIAL PRIMARY KEY,
    event_type VARCHAR NOT NULL,
    payload JSONB NOT NULL,
    published_at VARCHAR,
    retry_count INTEGER DEFAULT 0
);
```

**Таблица processed_inbox:**
```sql
CREATE TABLE processed_inbox (
    event_key VARCHAR PRIMARY KEY
);
```

### Processor Service

**Таблица processing_states:**
```sql
CREATE TABLE processing_states (
    order_id VARCHAR PRIMARY KEY,
    version INTEGER NOT NULL,
    status VARCHAR NOT NULL,
    attempt_count INTEGER NOT NULL,
    last_error VARCHAR
);
```

**Таблица outbox:** (такая же, как в order-service)

**Таблица processed_inbox:** (такая же, как в order-service)

## Схема Событий

### order.created

Публикуется: Order Service
Потребляется: Processor Service

```json
{
  "order_id": "ord-001",
  "items": ["item1", "item2"],
  "amount": 1200.0,
  "version": 1
}
```

### order.processed

Публикуется: Processor Service
Потребляется: Order Service

```json
{
  "order_id": "ord-001",
  "status": "success",  // или "failed"
  "fail_reason": null,  // или сообщение об ошибке
  "version": 2
}
```

## Гарантии Надежности

### 1. Доставка Как Минимум Один Раз

**Паттерн Outbox гарантирует:**
- События никогда не теряются (сохраняются в БД)
- События публикуются даже при краше сервиса
- Повторы при сбоях

**Компромисс:** Может доставлять дубликаты (обрабатывается Inbox)

### 2. Идемпотентность

**Паттерн Inbox гарантирует:**
- Одно и то же событие обрабатывается только один раз
- Безопасно повторять события
- Нет дублирующихся побочных эффектов

### 3. Eventual Consistency (Согласованность в Конечном Счете)

**Система гарантирует:**
- Заказы в конечном итоге будут обработаны
- Статус в конечном итоге будет обновлен
- Временная несогласованность приемлема

**Пример временной шкалы:**
```
t=0:  Заказ создан (status=pending)
t=1:  Событие опубликовано в RabbitMQ
t=2:  Процессор получает событие
t=3:  Обработка завершена
t=4:  Результат опубликован в RabbitMQ
t=5:  Статус заказа обновлен (status=done)
```

Между t=0 и t=5 заказ находится в состоянии `pending`, хотя он может уже быть обработан. Это приемлемо для большинства бизнес-случаев.

## Обработка Ошибок

### 1. Временные Сбои

**Потеряно соединение с RabbitMQ:**
- Потребитель автоматически переподключается (aio-pika `connect_robust`)
- Повторы каждые 5 секунд
- Нет потери данных (outbox сохраняет события)

**Потеряно соединение с БД:**
- Пул подключений SQLAlchemy обрабатывает повторы
- Неудачные транзакции откатываются
- События остаются в outbox для повтора

### 2. Постоянные Сбои

**Сбой обработки (бизнес-логика):**
```python
try:
    result = process_order(order)
    state.status = "success"
except Exception as e:
    state.status = "failed"
    state.last_error = str(e)
    state.attempt_count += 1
```

**Poison messages (некорректные события):**
- Текущее: Логируются и пропускаются
- Будущее: Перемещение в Dead Letter Queue (DLQ)

### 3. Стратегия Повторов

**Outbox Publisher:**
```python
try:
    publish_to_rabbitmq(event)
    mark_as_published(event)
except Exception:
    increment_retry_count(event)
    # Повтор при следующем опросе (через 5с)
```

**Пока нет экспоненциальной задержки** - фиксированный интервал 5 секунд
Будущее улучшение: Экспоненциальная задержка с максимальным количеством повторов

## Соображения Производительности

### Узкие Места

1. **Опрос Outbox** - Каждые 5 секунд
   - Можно оптимизировать с PostgreSQL LISTEN/NOTIFY
   - Или использовать change data capture (CDC)

2. **Запросы к БД** - N+1 запросы в некоторых местах
   - Можно пакетировать операции
   - Использовать пул подключений (уже включено)

3. **Пропускная способность RabbitMQ** - Один потребитель на сервис
   - Можно масштабировать горизонтально (несколько инстансов)
   - Использовать prefetch для параллельной обработки нескольких сообщений

### Стратегия Масштабирования

**Горизонтальное Масштабирование:**
```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  Order-1     │  │  Order-2     │  │  Order-3     │
│  (instance)  │  │  (instance)  │  │  (instance)  │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │
       └─────────────────┴─────────────────┘
                         │
                    ┌────▼─────┐
                    │ RabbitMQ │
                    │ (fanout) │
                    └──────────┘
```

**Каждый инстанс:**
- Имеет свое подключение к БД
- Опрашивает свою таблицу outbox
- Потребляет из общей очереди RabbitMQ (конкурирующие потребители)

**Дедупликация Inbox гарантирует**, что одно и то же событие не обрабатывается дважды даже с несколькими потребителями.

## Стратегия Тестирования

### Unit Тесты
```python
# domain/order.py
def test_order_calculates_total():
    order = Order.create(...)
    assert order.total_amount == 1250.0
```

### Integration Тесты
```python
# tests/test_api_create_order.py
@pytest.mark.asyncio
async def test_create_order_success(initialized_app):
    response = await client.post("/orders", json={...})
    assert response.status_code == 201
```

### Тестовая БД
- Использует Testcontainers для реального PostgreSQL
- Каждый тест получает изолированную БД
- Нет моков для инфраструктуры (тестируем реальное поведение)

## Будущие Улучшения

### 1. Dead Letter Queue (DLQ)
Перемещение poison messages в DLQ после N повторов

### 2. Observability (Наблюдаемость)
- Структурированное логирование (JSON формат)
- Распределенная трассировка (OpenTelemetry)
- Метрики (Prometheus)

### 3. Database Migrations
Использовать Alembic для версионирования схемы

### 4. API Versioning
Поддержка обратно совместимых изменений API

### 5. Rate Limiting
Защита API от злоупотреблений

### 6. Circuit Breaker
Быстрый отказ когда downstream сервис недоступен

## Сравнение с Альтернативами

### Паттерн SAGA
Наш подход похож на SAGA на основе хореографии:
- ✅ Нет центрального координатора (проще)
- ✅ Сервисы реагируют на события независимо
- ❌ Сложнее отслеживать общий поток (нет единого "SAGA лога")

### Two-Phase Commit (2PC)
Мы не используем 2PC потому что:
- ❌ Требует распределенного координатора транзакций
- ❌ Блокирующий (блокировки удерживаются во время фазы подготовки)
- ❌ Доступность страдает при отказе координатора

Наш подход eventual consistency:
- ✅ Неблокирующий
- ✅ Более высокая доступность
- ✅ Лучшая производительность
- ❌ Временная несогласованность (приемлемый компромисс)

## Ссылки

- [Microservices Patterns](https://microservices.io/patterns/index.html) by Chris Richardson
- [Designing Data-Intensive Applications](https://dataintensive.net/) by Martin Kleppmann
- [Enterprise Integration Patterns](https://www.enterpriseintegrationpatterns.com/)
