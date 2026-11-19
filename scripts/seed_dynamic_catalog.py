from django.db import transaction

from companies.models import Company, BusinessLine, CompanyBusinessLine
from inventory.models import Category, CategoryAttribute, ProductVariant

COMPANY_SLUG = "demo-mayorista"


BUSINESS_LINES = [
    "Textil",
    "Ferretería",
    "Abarrotes",
    "Electrónicos",
    "Joyería",
    "Calzado",
    "Papelería",
    "Refacciones",
    "Muebles",
    "Belleza",
    "Limpieza",
    "Construcción",
    "Importación",
    "Mayoreo general",
]


COMPANY_BUSINESS_LINES = [
    ("Calzado", True),
    ("Textil", False),
    ("Joyería", False),
    ("Abarrotes", False),
    ("Electrónicos", False),
]


CATEGORY_ATTRIBUTES = {
    "Pantalones": [
        {
            "name": "Talla",
            "key": "talla",
            "field_type": "select",
            "is_required": True,
            "is_filterable": True,
            "options": ["28", "30", "32", "34", "36", "38", "40"],
            "display_order": 1,
        },
        {
            "name": "Color",
            "key": "color",
            "field_type": "text",
            "is_required": True,
            "is_filterable": True,
            "options": [],
            "display_order": 2,
        },
        {
            "name": "Corte",
            "key": "corte",
            "field_type": "select",
            "is_required": False,
            "is_filterable": True,
            "options": ["Regular", "Slim", "Skinny", "Recto", "Cargo"],
            "display_order": 3,
        },
    ],
    "Playeras": [
        {
            "name": "Talla",
            "key": "talla",
            "field_type": "select",
            "is_required": True,
            "is_filterable": True,
            "options": ["XS", "S", "M", "L", "XL", "XXL"],
            "display_order": 1,
        },
        {
            "name": "Color",
            "key": "color",
            "field_type": "text",
            "is_required": True,
            "is_filterable": True,
            "options": [],
            "display_order": 2,
        },
        {
            "name": "Tela",
            "key": "tela",
            "field_type": "text",
            "is_required": False,
            "is_filterable": True,
            "options": [],
            "display_order": 3,
        },
        {
            "name": "Corte",
            "key": "corte",
            "field_type": "select",
            "is_required": False,
            "is_filterable": True,
            "options": ["Regular", "Oversize", "Slim Fit"],
            "display_order": 4,
        },
    ],
    "Zapatos": [
        {
            "name": "Número",
            "key": "numero",
            "field_type": "decimal",
            "is_required": True,
            "is_filterable": True,
            "options": [],
            "display_order": 1,
        },
        {
            "name": "Material",
            "key": "material",
            "field_type": "text",
            "is_required": True,
            "is_filterable": True,
            "options": [],
            "display_order": 2,
        },
        {
            "name": "Tipo de suela",
            "key": "tipo_suela",
            "field_type": "text",
            "is_required": False,
            "is_filterable": True,
            "options": [],
            "display_order": 3,
        },
    ],
    "Joyería": [
        {
            "name": "Kilataje",
            "key": "kilataje",
            "field_type": "select",
            "is_required": True,
            "is_filterable": True,
            "options": ["10k", "14k", "18k", "24k"],
            "display_order": 1,
        },
        {
            "name": "Metal",
            "key": "metal",
            "field_type": "select",
            "is_required": True,
            "is_filterable": True,
            "options": ["Oro", "Plata", "Acero", "Titanio"],
            "display_order": 2,
        },
        {
            "name": "Peso gramos",
            "key": "peso_gramos",
            "field_type": "decimal",
            "is_required": False,
            "is_filterable": True,
            "options": [],
            "display_order": 3,
        },
    ],
    "Electrónicos": [
        {
            "name": "Modelo",
            "key": "modelo",
            "field_type": "text",
            "is_required": True,
            "is_filterable": True,
            "options": [],
            "display_order": 1,
        },
        {
            "name": "Voltaje",
            "key": "voltaje",
            "field_type": "text",
            "is_required": False,
            "is_filterable": True,
            "options": [],
            "display_order": 2,
        },
        {
            "name": "Garantía meses",
            "key": "garantia_meses",
            "field_type": "number",
            "is_required": False,
            "is_filterable": True,
            "options": [],
            "display_order": 3,
        },
    ],
    "Abarrotes": [
        {
            "name": "Caducidad",
            "key": "caducidad",
            "field_type": "date",
            "is_required": False,
            "is_filterable": True,
            "options": [],
            "display_order": 1,
        },
        {
            "name": "Contenido",
            "key": "contenido",
            "field_type": "text",
            "is_required": False,
            "is_filterable": True,
            "options": [],
            "display_order": 2,
        },
        {
            "name": "Lote",
            "key": "lote",
            "field_type": "text",
            "is_required": False,
            "is_filterable": True,
            "options": [],
            "display_order": 3,
        },
    ],
}


@transaction.atomic
def run():
    company = Company.objects.filter(slug=COMPANY_SLUG).first()

    if not company:
        company = Company.objects.first()

    if not company:
        raise Exception("No existe ninguna empresa. Primero crea una empresa.")

    print(f"Empresa usada: {company.name}")

    print("\nCreando giros comerciales...")
    business_line_map = {}

    for name in BUSINESS_LINES:
        obj, created = BusinessLine.objects.get_or_create(name=name)
        business_line_map[name] = obj
        print(f" - {obj.name}: {'CREADO' if created else 'YA EXISTE'}")

    print("\nAsignando giros a la empresa...")

    CompanyBusinessLine.objects.filter(company=company).update(is_primary=False)

    for name, is_primary in COMPANY_BUSINESS_LINES:
        business_line = business_line_map[name]

        link, created = CompanyBusinessLine.objects.update_or_create(
            company=company,
            business_line=business_line,
            defaults={
                "is_primary": is_primary,
            },
        )

        print(
            f" - {company.name} -> {business_line.name}: "
            f"{'CREADO' if created else 'ACTUALIZADO'} "
            f"{'(PRINCIPAL)' if is_primary else ''}"
        )

    print("\nCreando categorías y atributos dinámicos...")

    category_map = {}

    for category_name, attributes in CATEGORY_ATTRIBUTES.items():
        category, created = Category.objects.get_or_create(
            company=company,
            name=category_name,
            defaults={
                "description": f"Categoría dinámica para {category_name}",
                "is_active": True,
            },
        )

        category_map[category_name] = category

        print(f"\nCategoría {category.name}: {'CREADA' if created else 'YA EXISTE'}")

        for attr in attributes:
            attribute, attr_created = CategoryAttribute.objects.update_or_create(
                category=category,
                key=attr["key"],
                defaults={
                    "name": attr["name"],
                    "field_type": attr["field_type"],
                    "is_required": attr["is_required"],
                    "is_filterable": attr["is_filterable"],
                    "is_active": True,
                    "options": attr["options"],
                    "display_order": attr["display_order"],
                },
            )

            print(
                f"   - {attribute.name} ({attribute.key}): "
                f"{'CREADO' if attr_created else 'ACTUALIZADO'}"
            )

    print("\nActualizando variante demo PANTALON-AZUL-32 si existe...")

    variant = (
        ProductVariant.objects.filter(
            company=company,
            sku__iexact="PANTALON-AZUL-32",
        )
        .select_related("product")
        .first()
    )

    if variant:
        variant.specifications = {
            "talla": "32",
            "color": "Azul",
            "corte": "Slim",
        }

        variant.color = variant.color or "Azul"
        variant.size = variant.size or "32"

        variant.save(
            update_fields=[
                "specifications",
                "color",
                "size",
                "updated_at",
            ]
        )

        if variant.product and "Pantalones" in category_map:
            variant.product.category = category_map["Pantalones"]
            variant.product.save(update_fields=["category", "updated_at"])

        print(" - Variante PANTALON-AZUL-32 actualizada correctamente.")
    else:
        print(" - No existe la variante PANTALON-AZUL-32. Se omite actualización.")

    print("\nRESUMEN")
    print(f"Empresa: {company.name}")
    print(f"Giros comerciales: {BusinessLine.objects.count()}")
    print(f"Categorías empresa: {Category.objects.filter(company=company).count()}")
    print(
        "Atributos dinámicos empresa:",
        CategoryAttribute.objects.filter(category__company=company).count(),
    )

    print("\nSeed completado correctamente.")


run()
