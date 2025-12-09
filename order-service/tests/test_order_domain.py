import pytest

from domain.order import ItemLine, Order, ValidationError


def test_order_create_sets_pending_and_version_one():
    order = Order.create(
        order_id="ord-1",
        customer_id="cust-1",
        items=[ItemLine(sku="sku-1", quantity=2, price=10.0)],
    )

    assert order.status == "pending"
    assert order.version == 1
    assert order.total_amount == 20.0


@pytest.mark.parametrize(
    "items",
    [
        [],
        [ItemLine(sku="sku-1", quantity=0, price=1.0)],
        [ItemLine(sku="sku-1", quantity=1, price=0.0)],
    ],
)
def test_order_create_rejects_invalid_items(items):
    with pytest.raises(ValidationError):
        Order.create(order_id="ord-1", customer_id="cust-1", items=items)
