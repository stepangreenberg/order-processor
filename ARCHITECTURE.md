# Architecture Documentation

## System Overview

This is a distributed microservices system implementing event-driven architecture with strong consistency guarantees through the Outbox and Inbox patterns.

## Architecture Principles

### 1. Event-Driven Architecture (EDA)

Services communicate asynchronously via events published to RabbitMQ:
- **Loose Coupling** - Services don't know about each other directly
- **Scalability** - Services can be scaled independently
- **Resilience** - Failures in one service don't cascade

### 2. Clean Architecture

Each service follows Clean Architecture principles:

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

**Benefits:**
- Domain logic is independent of frameworks
- Easy to test (domain has no dependencies)
- Can swap infrastructure without changing business logic

### 3. Outbox Pattern

**Problem:** How to atomically update the database AND publish an event?

**Solution:** Write the event to an outbox table in the same transaction:

```python
async with uow:
    # Business logic
    order = Order.create(...)
    await uow.orders.add(order)

    # Outbox event
    await uow.outbox.put("order.created", {
        "order_id": order.order_id,
        "amount": order.total_amount
    })

    # Single atomic commit
    await uow.commit()
```

**Background Publisher** polls outbox and publishes to RabbitMQ:

```python
while True:
    events = SELECT * FROM outbox WHERE published_at IS NULL
    for event in events:
        publish_to_rabbitmq(event)
        UPDATE outbox SET published_at = NOW() WHERE id = event.id
    await asyncio.sleep(5)
```

**Guarantees:**
- ✅ Event is published AT LEAST ONCE
- ✅ No event is lost (survives crashes)
- ❌ May publish duplicates (handled by Inbox pattern)

### 4. Inbox Pattern

**Problem:** Consumer may receive duplicate events (network retries, etc.)

**Solution:** Track processed events in inbox table:

```python
event_key = f"order.processed:{order_id}:{version}"

async with uow:
    if await uow.inbox.exists(event_key):
        return  # Already processed, skip

    # Process event
    order.status = "done"
    await uow.orders.add(order)

    # Mark as processed
    await uow.inbox.add(event_key)

    await uow.commit()
```

**Guarantees:**
- ✅ Idempotent processing (safe to replay events)
- ✅ Exactly-once semantics at application level

### 5. Optimistic Concurrency Control

Uses version numbers to detect conflicts:

```python
# Event arrives with version=2
if event.version <= order.version:  # order.version=3
    return  # Stale event, ignore

# Event is newer, apply it
order.status = event.status
order.version = event.version
```

**Why not pessimistic locking?**
- No distributed locks needed
- Better performance (no lock contention)
- Works across different databases

## Data Flow

### Creating an Order

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

## Database Schema

### Order Service

**orders table:**
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

**outbox table:**
```sql
CREATE TABLE outbox (
    id SERIAL PRIMARY KEY,
    event_type VARCHAR NOT NULL,
    payload JSONB NOT NULL,
    published_at VARCHAR,
    retry_count INTEGER DEFAULT 0
);
```

**processed_inbox table:**
```sql
CREATE TABLE processed_inbox (
    event_key VARCHAR PRIMARY KEY
);
```

### Processor Service

**processing_states table:**
```sql
CREATE TABLE processing_states (
    order_id VARCHAR PRIMARY KEY,
    version INTEGER NOT NULL,
    status VARCHAR NOT NULL,
    attempt_count INTEGER NOT NULL,
    last_error VARCHAR
);
```

**outbox table:** (same as order-service)

**processed_inbox table:** (same as order-service)

## Event Schema

### order.created

Published by: Order Service
Consumed by: Processor Service

```json
{
  "order_id": "ord-001",
  "items": ["item1", "item2"],
  "amount": 1200.0,
  "version": 1
}
```

### order.processed

Published by: Processor Service
Consumed by: Order Service

```json
{
  "order_id": "ord-001",
  "status": "success",  // or "failed"
  "reason": null,       // or error message
  "version": 2
}
```

## Reliability Guarantees

### 1. At-Least-Once Delivery

**Outbox Pattern ensures:**
- Events are never lost (persisted in DB)
- Events are published even if service crashes
- Retries on failure

**Trade-off:** May deliver duplicates (handled by Inbox)

### 2. Idempotency

**Inbox Pattern ensures:**
- Same event processed only once
- Safe to replay events
- No duplicate side effects

### 3. Eventual Consistency

**System guarantees:**
- Orders will eventually be processed
- Status will eventually be updated
- Temporary inconsistency is acceptable

**Example timeline:**
```
t=0:  Order created (status=pending)
t=1:  Event published to RabbitMQ
t=2:  Processor receives event
t=3:  Processing completes
t=4:  Result published to RabbitMQ
t=5:  Order status updated (status=done)
```

Between t=0 and t=5, the order is in `pending` state even though it might already be processed. This is acceptable for most business cases.

## Error Handling

### 1. Transient Failures

**RabbitMQ connection lost:**
- Consumer automatically reconnects (aio-pika `connect_robust`)
- Retries every 5 seconds
- No data loss (outbox persists events)

**Database connection lost:**
- SQLAlchemy connection pool handles retries
- Failed transactions are rolled back
- Events remain in outbox for retry

### 2. Permanent Failures

**Processing fails (business logic):**
```python
try:
    result = process_order(order)
    state.status = "success"
except Exception as e:
    state.status = "failed"
    state.last_error = str(e)
    state.attempt_count += 1
```

**Poison messages (malformed events):**
- Currently: Logged and skipped
- Future: Move to Dead Letter Queue (DLQ)

### 3. Retry Strategy

**Outbox Publisher:**
```python
try:
    publish_to_rabbitmq(event)
    mark_as_published(event)
except Exception:
    increment_retry_count(event)
    # Retry on next poll (5s later)
```

**No exponential backoff yet** - fixed 5-second interval
Future improvement: Exponential backoff with max retries

## Performance Considerations

### Bottlenecks

1. **Outbox Polling** - Every 5 seconds
   - Could be optimized with PostgreSQL LISTEN/NOTIFY
   - Or use change data capture (CDC)

2. **Database Queries** - N+1 queries in some places
   - Could batch operations
   - Use connection pooling (already enabled)

3. **RabbitMQ Throughput** - Single consumer per service
   - Could scale horizontally (multiple instances)
   - Use prefetch to process multiple messages concurrently

### Scaling Strategy

**Horizontal Scaling:**
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

**Each instance:**
- Has its own database connection
- Polls its own outbox table
- Consumes from shared RabbitMQ queue (competing consumers)

**Inbox deduplication ensures** same event isn't processed twice even with multiple consumers.

## Testing Strategy

### Unit Tests
```python
# domain/order.py
def test_order_calculates_total():
    order = Order.create(...)
    assert order.total_amount == 1250.0
```

### Integration Tests
```python
# tests/test_api_create_order.py
@pytest.mark.asyncio
async def test_create_order_success(initialized_app):
    response = await client.post("/orders", json={...})
    assert response.status_code == 201
```

### Test Database
- Uses Testcontainers for real PostgreSQL
- Each test gets isolated database
- No mocks for infrastructure (test real behavior)

## Future Improvements

### 1. Dead Letter Queue (DLQ)
Move poison messages to DLQ after N retries

### 2. Observability
- Structured logging (JSON format)
- Distributed tracing (OpenTelemetry)
- Metrics (Prometheus)

### 3. Database Migrations
Use Alembic for schema versioning

### 4. API Versioning
Support backward-compatible API changes

### 5. Rate Limiting
Protect APIs from abuse

### 6. Circuit Breaker
Fail fast when downstream service is down

## Comparison with Alternatives

### SAGA Pattern
Our approach is similar to choreography-based SAGA:
- ✅ No central coordinator (simpler)
- ✅ Services react to events independently
- ❌ Harder to track overall flow (no single "SAGA log")

### Two-Phase Commit (2PC)
We don't use 2PC because:
- ❌ Requires distributed transaction coordinator
- ❌ Blocking (locks held during prepare phase)
- ❌ Availability suffers if coordinator fails

Our eventual consistency approach:
- ✅ Non-blocking
- ✅ Higher availability
- ✅ Better performance
- ❌ Temporary inconsistency (acceptable trade-off)

## References

- [Microservices Patterns](https://microservices.io/patterns/index.html) by Chris Richardson
- [Designing Data-Intensive Applications](https://dataintensive.net/) by Martin Kleppmann
- [Enterprise Integration Patterns](https://www.enterpriseintegrationpatterns.com/)
