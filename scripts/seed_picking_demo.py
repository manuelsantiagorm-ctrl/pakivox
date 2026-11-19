from decimal import Decimal

from inventory.models import (
    Category,
    Brand,
    Product,
    ProductVariant,
    Warehouse,
    Stock,
)
from sales.models import Order, OrderItem
from warehouse.models import (
    WarehouseZone,
    WarehouseLocation,
    VariantLocation,
    PickingOrder,
)

ORDER_ID = 1


order = Order.objects.select_related("company", "customer").get(id=ORDER_ID)
company = order.company

print(f"Pedido encontrado: {order.folio}")
print(f"Empresa: {company.name}")

category, _ = Category.objects.get_or_create(
    company=company,
    name="Textil Demo",
    defaults={
        "description": "Categoría demo para pruebas de picking",
        "is_active": True,
    },
)

brand, _ = Brand.objects.get_or_create(
    company=company,
    name="WMS360 Demo",
    defaults={
        "is_active": True,
    },
)

product, _ = Product.objects.get_or_create(
    company=company,
    base_sku="TEXTIL-DEMO",
    defaults={
        "category": category,
        "brand": brand,
        "name": "Caja de ropa demo",
        "description": "Producto demo para probar ruta de picking",
        "product_type": "variant",
        "barcode": "750000000001",
        "cost_price": Decimal("120.00"),
        "sale_price": Decimal("250.00"),
        "wholesale_price": Decimal("200.00"),
        "is_active": True,
    },
)

variant_1, _ = ProductVariant.objects.get_or_create(
    company=company,
    product=product,
    sku="PLAYERA-NEGRA-M",
    defaults={
        "barcode": "750000000101",
        "color": "Negro",
        "size": "M",
        "presentation": "Caja",
        "cost_price": Decimal("120.00"),
        "sale_price": Decimal("250.00"),
        "wholesale_price": Decimal("200.00"),
        "is_active": True,
    },
)

variant_2, _ = ProductVariant.objects.get_or_create(
    company=company,
    product=product,
    sku="SUDADERA-GRIS-L",
    defaults={
        "barcode": "750000000102",
        "color": "Gris",
        "size": "L",
        "presentation": "Caja",
        "cost_price": Decimal("180.00"),
        "sale_price": Decimal("310.00"),
        "wholesale_price": Decimal("260.00"),
        "is_active": True,
    },
)

warehouse, _ = Warehouse.objects.get_or_create(
    company=company,
    name="Bodega Principal",
    defaults={
        "address": "Bodega demo WMS360",
        "is_active": True,
    },
)

zone, _ = WarehouseZone.objects.get_or_create(
    company=company,
    warehouse=warehouse,
    name="Zona Mayoreo",
    defaults={
        "description": "Zona demo para picking",
        "is_active": True,
    },
)

location_1, _ = WarehouseLocation.objects.get_or_create(
    company=company,
    warehouse=warehouse,
    bin_code="A-01-01",
    defaults={
        "zone": zone,
        "aisle": "A",
        "rack": "01",
        "level": "01",
        "is_active": True,
    },
)

location_2, _ = WarehouseLocation.objects.get_or_create(
    company=company,
    warehouse=warehouse,
    bin_code="A-02-01",
    defaults={
        "zone": zone,
        "aisle": "A",
        "rack": "02",
        "level": "01",
        "is_active": True,
    },
)

VariantLocation.objects.update_or_create(
    company=company,
    variant=variant_1,
    location=location_1,
    defaults={
        "quantity": 20,
        "is_primary": True,
    },
)

VariantLocation.objects.update_or_create(
    company=company,
    variant=variant_2,
    location=location_2,
    defaults={
        "quantity": 15,
        "is_primary": True,
    },
)

Stock.objects.update_or_create(
    company=company,
    warehouse=warehouse,
    variant=variant_1,
    defaults={
        "physical_stock": 20,
        "reserved_stock": 0,
        "committed_stock": 0,
    },
)

Stock.objects.update_or_create(
    company=company,
    warehouse=warehouse,
    variant=variant_2,
    defaults={
        "physical_stock": 15,
        "reserved_stock": 0,
        "committed_stock": 0,
    },
)

OrderItem.objects.update_or_create(
    order=order,
    variant=variant_1,
    defaults={
        "quantity": 3,
        "unit_price": Decimal("250.00"),
    },
)

OrderItem.objects.update_or_create(
    order=order,
    variant=variant_2,
    defaults={
        "quantity": 2,
        "unit_price": Decimal("310.00"),
    },
)

order.calculate_totals()

PickingOrder.objects.filter(order=order).delete()

print("Demo listo.")
print(f"Pedido: {order.folio}")
print("Productos agregados:")
print("- PLAYERA-NEGRA-M x 3 en A-01-01")
print("- SUDADERA-GRIS-L x 2 en A-02-01")
print("Ahora entra a:")
print("http://127.0.0.1:8000/warehouse/picking/generar/1/")
