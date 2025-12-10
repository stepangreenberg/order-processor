# Order Processing System

Distributed microservices system for processing orders with event-driven architecture.

## Architecture

The system consists of two microservices:

- **Order Service** - Manages orders, exposes HTTP API
- **Processor Service** - Processes orders asynchronously with random success/failure

Communication via **RabbitMQ** using Outbox/Inbox patterns for reliability.

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

## Features

✅ **Event-Driven Architecture** - Asynchronous communication via RabbitMQ
✅ **Outbox Pattern** - Reliable event publishing with transactional guarantees
✅ **Inbox Pattern** - Event deduplication for idempotency
✅ **Optimistic Concurrency** - Version-based conflict resolution
✅ **Retry Logic** - Automatic retry with exponential backoff
✅ **Clean Architecture** - Domain/Application/Infrastructure separation
✅ **Full Test Coverage** - Unit tests + Integration tests with Testcontainers

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.12+

### 1. Start Infrastructure

```bash
docker-compose up -d
```

This starts:
- PostgreSQL (order-service DB) on port 5432
- PostgreSQL (processor-service DB) on port 5433
- RabbitMQ on port 5672 (management UI: http://localhost:15672)

### 2. Install Dependencies

**Order Service:**
```bash
cd order-service
python3 -m pip install -r requirements.txt
```

**Processor Service:**
```bash
cd processor-service
python3 -m pip install -r requirements.txt
```

### 3. Run Services

**Terminal 1 - Order Service:**
```bash
cd order-service
export APP__DB_DSN="postgresql+asyncpg://postgres:postgres@localhost:5432/orders"
export APP__RABBITMQ_URL="amqp://guest:guest@localhost:5672/"
uvicorn app.main:app --reload --port 8001
```

**Terminal 2 - Processor Service:**
```bash
cd processor-service
export APP__DB_DSN="postgresql+asyncpg://postgres:postgres@localhost:5433/processor"
export APP__RABBITMQ_URL="amqp://guest:guest@localhost:5672/"
uvicorn app.main:app --reload --port 8002
```

### 4. Test the System

**Create an order:**
```bash
curl -X POST http://localhost:8001/orders \
  -H "Content-Type: application/json" \
  -d '{
    "order_id": "ord-001",
    "customer_id": "cust-123",
    "items": [
      {"sku": "laptop", "quantity": 1, "price": 1200.0}
    ]
  }'
```

**Check order status:**
```bash
curl http://localhost:8001/orders/ord-001
```

The order will be:
1. Created with status `pending`
2. Sent to processor via RabbitMQ
3. Processed (randomly succeeds or fails)
4. Status updated to `done` or `failed`

Check status multiple times to see the progression!

## API Reference

### Order Service

**POST /orders** - Create a new order
```json
{
  "order_id": "ord-001",
  "customer_id": "cust-123",
  "items": [
    {"sku": "laptop", "quantity": 1, "price": 1200.0}
  ]
}
```

**GET /orders/{order_id}** - Get order status
```json
{
  "order_id": "ord-001",
  "customer_id": "cust-123",
  "status": "done",
  "total_amount": 1200.0,
  "version": 2
}
```

**GET /health** - Health check

### Processor Service

**GET /health** - Health check

## Running Tests

**Order Service:**
```bash
cd order-service
python3 -m pytest tests/ -v
```

**Processor Service:**
```bash
cd processor-service
python3 -m pytest tests/ -v
```

Tests use Testcontainers for integration testing with real PostgreSQL and RabbitMQ.

## Project Structure

```
order_processor/
├── order-service/
│   ├── domain/          # Domain models (Order, ItemLine)
│   ├── application/     # Use cases (CreateOrder, ApplyProcessed)
│   ├── infrastructure/  # DB, RabbitMQ, Outbox/Inbox
│   ├── app/            # FastAPI application
│   └── tests/          # Unit + Integration tests
│
├── processor-service/
│   ├── domain/          # Domain models (ProcessingState)
│   ├── application/     # Use cases (HandleOrderCreated)
│   ├── infrastructure/  # DB, RabbitMQ, Outbox/Inbox
│   ├── app/            # FastAPI application
│   └── tests/          # Unit + Integration tests
│
├── infra/
│   └── docker-compose.yml
│
└── README.md
```

## Event Flow

1. **Order Created** (`POST /orders`)
   - Order saved to DB with status `pending`
   - Event written to outbox table

2. **Outbox Publisher** (background task)
   - Polls outbox every 5 seconds
   - Publishes events to RabbitMQ
   - Marks as published

3. **Processor Consumes** (`order.created`)
   - Receives event from RabbitMQ
   - Processes order (random success/failure)
   - Writes result to outbox

4. **Processor Publishes** (`order.processed`)
   - Outbox publisher sends result to RabbitMQ

5. **Order Service Consumes** (`order.processed`)
   - Updates order status to `done` or `failed`
   - Version incremented

## Key Patterns

### Outbox Pattern
Ensures events are published atomically with database changes:
```
BEGIN TRANSACTION
  INSERT INTO orders ...
  INSERT INTO outbox (event_type, payload, ...)
COMMIT

-- Later, background task:
SELECT * FROM outbox WHERE published_at IS NULL
Publish to RabbitMQ
UPDATE outbox SET published_at = NOW()
```

### Inbox Pattern
Deduplicates events to ensure idempotency:
```
event_key = f"order.processed:{order_id}:{version}"
IF event_key IN inbox THEN
  SKIP (already processed)
ELSE
  Process event
  INSERT INTO inbox (event_key)
END
```

### Optimistic Concurrency
Prevents conflicts using version numbers:
```python
if cmd.version <= order.version:
    return None  # Stale event, ignore
order.version = cmd.version
```

## Configuration

Environment variables:

- `APP__DB_DSN` - PostgreSQL connection string
- `APP__RABBITMQ_URL` - RabbitMQ connection URL
- `APP__SERVICE_NAME` - Service name for logging

## Troubleshooting

**"Connection refused" to PostgreSQL:**
- Check Docker containers: `docker ps`
- Ensure ports 5432/5433 are not in use

**"Connection refused" to RabbitMQ:**
- Check RabbitMQ: `docker logs infra-rabbitmq-1`
- Access management UI: http://localhost:15672 (guest/guest)

**Events not processing:**
- Check background workers are running (see console output)
- Check RabbitMQ queues: http://localhost:15672/#/queues
- Check outbox tables for pending events

## Technology Stack

- **FastAPI** - HTTP framework
- **SQLAlchemy 2.0** - ORM with async support
- **asyncpg** - PostgreSQL async driver
- **aio-pika** - RabbitMQ async client
- **Pydantic** - Data validation
- **pytest** - Testing framework
- **Testcontainers** - Integration testing

## License

MIT
