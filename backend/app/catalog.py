from datetime import datetime, timezone
from fastapi import HTTPException
from .domain import discount_fields


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def catalog(scenario: str = "ready") -> dict:
    if scenario == "error":
        raise HTTPException(503, "Синтетическая ошибка чтения MOCK")
    actions = [dict(id=f"mock-action-{i}", name=name, auto_add=auto, status=status,
                    start_at=start, end_at=end)
               for i, name, auto, status, start, end in [
                   (1, "Автодобавление — Осень", True, "active", "2026-10-01T00:00:00Z", "2026-10-31T23:59:59Z"),
                   (2, "Автодобавление — Ноябрь", True, "upcoming", "2026-11-01T00:00:00Z", "2026-11-30T23:59:59Z"),
                   (3, "Автодобавление — Сентябрь", True, "ended", "2026-09-01T00:00:00Z", "2026-09-30T23:59:59Z"),
                   (4, "Обычная — Дом", False, "active", "2026-10-01T00:00:00Z", "2026-10-31T23:59:59Z"),
                   (5, "Обычная — Свет", False, "upcoming", "2026-11-01T00:00:00Z", "2026-11-30T23:59:59Z")]]
    for action in actions:
        action['auto_add_dates']=[action['start_at']] if action['auto_add'] else []
    products = []
    for i, action in enumerate(actions, 1):
        for j, (name, price) in enumerate([("Термокружка MOCK", "1000.00"), ("Лампа MOCK", "1999.00"), ("Блокнот MOCK", "500.00")], 1):
            products.append(dict(id=f"mock-product-{i}-{j}", action_id=action["id"], offer_id=f"MOCK-{i}-{j}",
                                 name=name, role="member" if j == 1 else "candidate", price=price,
                                 action_price="950.00" if j == 1 else "1899.05" if action['auto_add'] and j == 2 else "475.00" if action['auto_add'] else None, min_price="400.00" if j == 3 else "800.00",
                                 max_price="2200.00", elastic=j == 3,
                                 auto_add_at=action["start_at"] if action["auto_add"] else None,
                                 sku=str(20000+i*10+j),product_id=str(10000+i*10+j),
                                 currency='RUB',currency_assumed=False,max_action_price=price,max_discount_price=price,min_seller_price='400.00',
                                 price_min_elastic='2200.00' if j == 3 and not action['auto_add'] else None,
                                 price_max_elastic='400.00' if j == 3 and not action['auto_add'] else None))
    for product in products:
        product.update(discount_fields(product))
    stocks = [dict(id=f"mock-stock-{i}", offer_id=f"MOCK-STOCK-{i}", name=name,
                   warehouse_id=f"mock-warehouse-{i % 2}", warehouse_name="Синтетический FBS" if i % 2 else "Синтетический rFBS",
                   available=i * 10, reserved=i, present=i * 11,sku=str(30000+i),product_id=str(40000+i))
              for i, name in enumerate(["Термокружка MOCK", "Лампа MOCK", "Блокнот MOCK", "Сумка MOCK"], 1)]
    if scenario == "empty":
        actions, products, stocks = [], [], []
    return dict(mode="MOCK", fetched_at=timestamp(), complete=scenario != "partial",
                warnings=["Неполная синтетическая загрузка: сохранение заблокировано"] if scenario == "partial" else [],
                actions=actions, products=products, stocks=stocks)
