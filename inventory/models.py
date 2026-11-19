from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Sum, IntegerField, ExpressionWrapper
from django.conf import settings
from django.core.validators import RegexValidator
from companies.models import Company


class Category(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="categories",
    )

    name = models.CharField("Nombre", max_length=150)
    description = models.TextField("Descripción", blank=True, null=True)
    is_active = models.BooleanField("Activa", default=True)

    created_at = models.DateTimeField("Fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField("Última actualización", auto_now=True)

    class Meta:
        verbose_name = "Categoría"
        verbose_name_plural = "Categorías"
        ordering = ["name"]
        unique_together = ("company", "name")

    def __str__(self):
        return f"{self.company.name} - {self.name}"


class CategoryAttribute(models.Model):
    FIELD_TYPE_CHOICES = [
        ("text", "Texto"),
        ("number", "Número"),
        ("decimal", "Decimal"),
        ("boolean", "Sí / No"),
        ("date", "Fecha"),
        ("select", "Selección única"),
        ("multiselect", "Selección múltiple"),
    ]

    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="attributes",
    )

    name = models.CharField("Nombre visible", max_length=120)
    key = models.SlugField("Clave técnica", max_length=120)

    field_type = models.CharField(
        "Tipo de campo",
        max_length=30,
        choices=FIELD_TYPE_CHOICES,
        default="text",
    )

    is_required = models.BooleanField("Obligatorio", default=False)
    is_filterable = models.BooleanField("Usable como filtro", default=True)
    is_bundle = models.BooleanField(
        "Es combo virtual",
        default=False,
        db_index=True,
        help_text=(
            "El stock de este SKU no se almacena directamente. "
            "Se calcula con las existencias de sus componentes."
            ),
        )
    is_active = models.BooleanField(
        "Activo",
        default=True,
        )
    is_active = models.BooleanField("Activo", default=True)

    options = models.JSONField(
        "Opciones",
        default=list,
        blank=True,
        help_text="Solo aplica para select o multiselect. Ejemplo: ['S', 'M', 'L', 'XL']",
    )

    help_text = models.CharField(
        "Texto de ayuda",
        max_length=255,
        blank=True,
        null=True,
    )

    display_order = models.PositiveIntegerField("Orden", default=0)

    created_at = models.DateTimeField("Fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField("Última actualización", auto_now=True)

    class Meta:
        verbose_name = "Atributo de categoría"
        verbose_name_plural = "Atributos de categorías"
        ordering = ["category__name", "display_order", "name"]
        unique_together = ("category", "key")

    def save(self, *args, **kwargs):
        if self.key:
            self.key = self.key.lower().strip()

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.category.name} - {self.name}"


class Brand(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="brands",
    )

    name = models.CharField("Nombre", max_length=150)
    is_active = models.BooleanField("Activa", default=True)

    created_at = models.DateTimeField("Fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField("Última actualización", auto_now=True)

    class Meta:
        verbose_name = "Marca"
        verbose_name_plural = "Marcas"
        ordering = ["name"]
        unique_together = ("company", "name")

    def __str__(self):
        return f"{self.company.name} - {self.name}"


class Product(models.Model):
    PRODUCT_TYPE_CHOICES = [
        ("simple", "Producto simple"),
        ("variant", "Producto con variantes"),
    ]

    STOCK_MODE_CHOICES = [
        ("unit", "Producto unitario"),
        ("bulk", "Producto por volumen"),
    ]

    UNIT_CHOICES = [
        ("pza", "Pieza"),
        ("pqt", "Paquete"),
        ("cja", "Caja"),
        ("kg", "Kilogramo"),
        ("lt", "Litro"),
        ("saco", "Saco / Bulto"),
        ("tarima", "Tarima / Pallet"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="products",
    )

    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        related_name="products",
        blank=True,
        null=True,
    )

    brand = models.ForeignKey(
        Brand,
        on_delete=models.SET_NULL,
        related_name="products",
        blank=True,
        null=True,
    )

    name = models.CharField("Nombre del producto", max_length=200)
    description = models.TextField("Descripción", blank=True, null=True)

    product_type = models.CharField(
        "Tipo de producto",
        max_length=20,
        choices=PRODUCT_TYPE_CHOICES,
        default="simple",
    )

    stock_mode = models.CharField(
        "Modo de manejo",
        max_length=20,
        choices=STOCK_MODE_CHOICES,
        default="unit",
        help_text="Define si el producto se maneja por pieza o por volumen.",
    )

    base_unit = models.CharField(
        "Unidad base de control",
        max_length=20,
        choices=UNIT_CHOICES,
        default="pza",
        help_text="Unidad mínima interna en la que WMS360 controla el stock.",
    )

    commercial_unit = models.CharField(
        "Unidad comercial",
        max_length=20,
        choices=UNIT_CHOICES,
        default="pza",
        help_text="Unidad en la que normalmente se compra, vende o mueve el producto.",
    )

    conversion_factor = models.DecimalField(
        "Contenido por unidad comercial",
        max_digits=12,
        decimal_places=3,
        default=1,
        help_text="Ejemplo: si 1 paquete trae 12 piezas, escribe 12.",
    )

    base_sku = models.CharField("SKU base", max_length=100)

    barcode = models.CharField(
        "Código de barras",
        max_length=100,
        blank=True,
        null=True,
    )

    image = models.ImageField(
        "Imagen del producto",
        upload_to="products/",
        blank=True,
        null=True,
    )

    cost_price = models.DecimalField(
        "Costo",
        max_digits=16,
        decimal_places=6,
        default=0,
    )

    sale_price = models.DecimalField(
        "Precio venta",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    wholesale_price = models.DecimalField(
        "Precio mayoreo",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    is_active = models.BooleanField("Activo", default=True)

    created_at = models.DateTimeField("Fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField("Última actualización", auto_now=True)

    class Meta:
        verbose_name = "Producto"
        verbose_name_plural = "Productos"
        ordering = ["name"]
        unique_together = ("company", "base_sku")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(conversion_factor__gt=0),
                name="product_conversion_factor_positive",
            ),
        ]

    def clean(self):
        errors = {}

        if self.category_id and self.company_id:
            if self.category.company_id != self.company_id:
                errors["category"] = "La categoría no pertenece a esta empresa."

        if self.brand_id and self.company_id:
            if self.brand.company_id != self.company_id:
                errors["brand"] = "La marca no pertenece a esta empresa."

        if self.conversion_factor is None or self.conversion_factor <= 0:
            errors["conversion_factor"] = (
                "El factor de conversión debe ser mayor a cero."
            )

        if self.stock_mode == "unit":
            if self.base_unit != "pza":
                errors["base_unit"] = (
                    "Un producto unitario debe manejarse con unidad base Pieza."
                )

            if self.commercial_unit != "pza":
                errors["commercial_unit"] = (
                    "Un producto unitario debe manejarse con unidad comercial Pieza."
                )

            if self.conversion_factor != Decimal("1"):
                errors["conversion_factor"] = (
                    "Un producto unitario debe tener factor de conversión 1."
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.stock_mode == "unit":
            self.base_unit = "pza"
            self.commercial_unit = "pza"
            self.conversion_factor = Decimal("1")

        super().save(*args, **kwargs)

    def to_base_quantity(self, commercial_quantity):
        quantity = Decimal(str(commercial_quantity or 0))
        factor = self.conversion_factor or Decimal("1")

        return quantity * factor

    def to_commercial_quantity(self, base_quantity):
        quantity = Decimal(str(base_quantity or 0))
        factor = self.conversion_factor or Decimal("1")

        if factor <= 0:
            return quantity

        return quantity / factor

    @property
    def is_bulk(self):
        return self.stock_mode == "bulk"

    @property
    def is_unit(self):
        return self.stock_mode == "unit"

    def __str__(self):
        return f"{self.name} ({self.base_sku})"


class ProductVariant(models.Model):
    INVENTORY_CONTROL_NORMAL = "normal"
    INVENTORY_CONTROL_LOT = "lot"
    INVENTORY_CONTROL_LOT_EXPIRY = "lot_expiry"

    INVENTORY_CONTROL_CHOICES = [
        (INVENTORY_CONTROL_NORMAL, "Normal / sin lote"),
        (INVENTORY_CONTROL_LOT, "Control por lote"),
        (INVENTORY_CONTROL_LOT_EXPIRY, "Lote + caducidad"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="product_variants",
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="variants",
    )

    sku = models.CharField("SKU", max_length=120)

    barcode = models.CharField(
        "Código de barras",
        max_length=120,
        blank=True,
        null=True,
    )

    color = models.CharField("Color", max_length=80, blank=True, null=True)
    size = models.CharField("Talla/Medida", max_length=80, blank=True, null=True)

    presentation = models.CharField(
        "Presentación",
        max_length=120,
        blank=True,
        null=True,
    )

    specifications = models.JSONField(
        "Especificaciones dinámicas",
        default=dict,
        blank=True,
        help_text="Atributos variables según la categoría. Ejemplo: talla, material, kilataje.",
    )

    cost_price = models.DecimalField(
        "Costo",
        max_digits=16,
        decimal_places=6,
        default=0,
    )

    sale_price = models.DecimalField(
        "Precio venta",
        max_digits=12,  
        decimal_places=2,
        default=0,
    )

    wholesale_price = models.DecimalField(
        "Precio mayoreo",
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    min_stock = models.PositiveIntegerField(
        "Stock mínimo",
        default=0,
        help_text=(
            "Cantidad mínima interna antes de disparar alerta de bajo inventario. "
            "Ejemplo: si vendes paquetes de 12 piezas, aquí se guarda en piezas internas."
        ),
    )
    is_bundle = models.BooleanField(
        "Es combo virtual",
        default=False,
        db_index=True,
        help_text=(
            "El stock de este SKU no se almacena directamente. "
            "Se calcula con las existencias de sus componentes."
            ),
        )
    inventory_control = models.CharField(
        "Control de inventario",
        max_length=20,
        choices=INVENTORY_CONTROL_CHOICES,
        default=INVENTORY_CONTROL_NORMAL,
        db_index=True,
        help_text=(
            "NORMAL mantiene el flujo actual. LOT exige lote. "
            "LOT_EXPIRY exige lote y fecha de caducidad."
        ),
    )

    is_active = models.BooleanField(
        "Activo",
        default=True,
    )

    created_at = models.DateTimeField("Fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField("Última actualización", auto_now=True)

    class Meta:
        verbose_name = "Variante / SKU"
        verbose_name_plural = "Variantes / SKUs"
        ordering = ["sku"]
        unique_together = ("company", "sku")

    @property
    def physical_stock_total(self):
        return self.stocks.aggregate(total=Sum("physical_stock"))["total"] or 0

    @property
    def reserved_stock_total(self):
        return self.stocks.aggregate(total=Sum("reserved_stock"))["total"] or 0

    @property
    def committed_stock_total(self):
        return self.stocks.aggregate(total=Sum("committed_stock"))["total"] or 0

    @property
    def available_stock_total(self):
        available_expr = ExpressionWrapper(
            F("physical_stock") - F("reserved_stock") - F("committed_stock"),
            output_field=IntegerField(),
        )

        return self.stocks.aggregate(total=Sum(available_expr))["total"] or 0

    def clean(self):
        errors = {}

        # LT-1B: los cambios de modo de inventario de un SKU existente
        # deben pasar por el flujo transaccional de adopción. Esto evita
        # activar/desactivar lotes desde admin, shell o formularios genéricos
        # sin clasificar el stock existente.
        if self.pk:
            previous_control = (
                type(self).objects.filter(pk=self.pk)
                .values_list("inventory_control", flat=True)
                .first()
            )

            if (
                previous_control
                and previous_control != self.inventory_control
                and not getattr(
                    self,
                    "_inventory_control_transition_authorized",
                    False,
                )
            ):
                errors["inventory_control"] = (
                    "El modo de inventario de un SKU existente solo puede "
                    "cambiarse mediante el flujo seguro de adopción de lotes."
                )


        if (
            self.is_bundle
            and self.inventory_control != self.INVENTORY_CONTROL_NORMAL
        ):
            errors["inventory_control"] = (
                "Un combo virtual no puede tener lote propio. "
                "El control por lote pertenece a sus componentes físicos."
            )

        if self.product_id and self.company_id:
            if self.product.company_id != self.company_id:
                errors["product"] = "El producto no pertenece a esta empresa."

        if not self.product_id:
            if errors:
                raise ValidationError(errors)
            return

        category = self.product.category

        if not category:
            if errors:
                raise ValidationError(errors)
            return

        specifications = self.specifications or {}

        if not isinstance(specifications, dict):
            raise ValidationError(
                {
                    "specifications": "Las especificaciones deben ser un objeto JSON válido."
                }
            )

        missing_fields = []
        attributes = category.attributes.filter(is_active=True)

        for attribute in attributes:
            value = specifications.get(attribute.key)

            if attribute.is_required and value in [None, "", [], {}]:
                missing_fields.append(attribute.name)

            if (
                attribute.field_type in ["select", "multiselect"]
                and value not in [None, "", [], {}]
                and attribute.options
            ):
                if attribute.field_type == "select":
                    if value not in attribute.options:
                        errors["specifications"] = (
                            f"El valor '{value}' no es válido para {attribute.name}. "
                            f"Opciones válidas: {', '.join(attribute.options)}"
                        )

                if attribute.field_type == "multiselect":
                    if not isinstance(value, list):
                        errors["specifications"] = (
                            f"El atributo {attribute.name} debe ser una lista."
                        )
                    else:
                        invalid_values = [
                            item for item in value if item not in attribute.options
                        ]

                        if invalid_values:
                            errors["specifications"] = (
                                f"Valores inválidos para {attribute.name}: "
                                f"{', '.join(invalid_values)}"
                            )

        if missing_fields:
            errors["specifications"] = (
                "Faltan especificaciones obligatorias: " + ", ".join(missing_fields)
            )

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.sku


class Warehouse(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="warehouses",
        verbose_name="Empresa",
    )

    # ======================================================
    # IDENTIFICACIÓN
    # ======================================================

    name = models.CharField(
        "Nombre de bodega",
        max_length=150,
    )

    code = models.CharField(
        "Código identificador",
        max_length=30,
        null=True,
        blank=True,
        validators=[
            RegexValidator(
                regex=r"^[A-Za-z0-9_-]+$",
                message=(
                    "El código solo puede contener letras, números, "
                    "guiones y guiones bajos."
                ),
            )
        ],
        help_text="Ejemplo: B-NORTE, CEDIS-01.",
    )

    # Se conserva para no romper código existente.
    # Se actualizará automáticamente con la dirección estructurada.
    address = models.TextField(
        "Dirección completa",
        blank=True,
        null=True,
    )

    # ======================================================
    # DIRECCIÓN ESTRUCTURADA
    # ======================================================

    street = models.CharField(
        "Calle",
        max_length=180,
        blank=True,
        default="",
    )

    exterior_number = models.CharField(
        "Número exterior",
        max_length=30,
        blank=True,
        default="",
    )

    interior_number = models.CharField(
        "Número interior",
        max_length=30,
        blank=True,
        default="",
    )

    postal_code = models.CharField(
        "Código postal",
        max_length=12,
        blank=True,
        default="",
        validators=[
            RegexValidator(
                regex=r"^[A-Za-z0-9 -]{3,12}$",
                message="Escribe un código postal válido.",
            )
        ],
    )

    neighborhood = models.CharField(
        "Colonia",
        max_length=150,
        blank=True,
        default="",
    )

    municipality = models.CharField(
        "Municipio o alcaldía",
        max_length=150,
        blank=True,
        default="",
    )

    city = models.CharField(
        "Ciudad",
        max_length=150,
        blank=True,
        default="",
    )

    state = models.CharField(
        "Estado",
        max_length=150,
        blank=True,
        default="",
    )

    country = models.CharField(
        "País",
        max_length=100,
        default="México",
    )

    country_code = models.CharField(
        "Código de país",
        max_length=2,
        default="MX",
        validators=[
            RegexValidator(
                regex=r"^[A-Za-z]{2}$",
                message="Utiliza un código ISO de dos letras. Ejemplo: MX.",
            )
        ],
    )

    # ======================================================
    # CONTACTO OPERATIVO
    # ======================================================

    contact_name = models.CharField(
        "Responsable de la bodega",
        max_length=150,
        blank=True,
        default="",
    )

    contact_email = models.EmailField(
        "Correo de contacto",
        blank=True,
        default="",
    )

    contact_phone = models.CharField(
        "Teléfono de contacto",
        max_length=30,
        blank=True,
        default="",
        validators=[
            RegexValidator(
                regex=r"^[0-9+\-()\s]{7,30}$",
                message="Escribe un teléfono válido.",
            )
        ],
    )

    # ======================================================
    # CONFIGURACIÓN LOGÍSTICA
    # ======================================================

    is_primary = models.BooleanField(
        "Bodega principal",
        default=False,
        help_text=(
            "Será la opción preseleccionada en compras, ventas "
            "y operaciones logísticas."
        ),
    )

    allows_direct_sales = models.BooleanField(
        "Permite ventas directas y picking",
        default=True,
        help_text=(
            "Desactívalo para bodegas de tránsito, reserva "
            "o almacenamiento sin despacho."
        ),
    )

    is_active = models.BooleanField(
        "Activa",
        default=True,
    )

    # ======================================================
    # CAPACIDAD
    # ======================================================

    area_sqm = models.DecimalField(
        "Superficie en metros cuadrados",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )

    capacity_positions = models.PositiveIntegerField(
        "Capacidad de posiciones",
        null=True,
        blank=True,
        help_text=(
            "Cantidad máxima estimada de posiciones, tarimas "
            "o espacios de almacenamiento."
        ),
    )

    created_at = models.DateTimeField(
        "Fecha de creación",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        "Última actualización",
        auto_now=True,
    )

    class Meta:
        verbose_name = "Bodega"
        verbose_name_plural = "Bodegas"
        ordering = [
            "-is_primary",
            "name",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "company",
                    "name",
                ],
                name="unique_warehouse_name_per_company",
            ),
            models.UniqueConstraint(
                fields=[
                    "company",
                    "code",
                ],
                condition=(models.Q(code__isnull=False) & ~models.Q(code="")),
                name="unique_warehouse_code_per_company",
            ),
            models.UniqueConstraint(
                fields=[
                    "company",
                ],
                condition=models.Q(is_primary=True),
                name="unique_primary_warehouse_per_company",
            ),
            models.CheckConstraint(
                condition=(models.Q(area_sqm__isnull=True) | models.Q(area_sqm__gte=0)),
                name="warehouse_area_non_negative",
            ),
        ]

    def clean(self):
        super().clean()

        errors = {}

        self.name = (self.name or "").strip()
        self.code = (self.code or "").strip().upper()
        self.country_code = (self.country_code or "MX").strip().upper()

        if not self.name:
            errors["name"] = "Escribe el nombre de la bodega."

        if not self.code:
            errors["code"] = "Escribe un código identificador para la bodega."

        if not (self.street or "").strip():
            errors["street"] = "Escribe la calle."

        if not (self.exterior_number or "").strip():
            errors["exterior_number"] = "Escribe el número exterior."

        if not (self.postal_code or "").strip():
            errors["postal_code"] = "Escribe el código postal."

        if not (self.neighborhood or "").strip():
            errors["neighborhood"] = "Escribe la colonia."

        if not ((self.city or "").strip() or (self.municipality or "").strip()):
            errors["city"] = "Escribe la ciudad o el municipio."

        if not (self.state or "").strip():
            errors["state"] = "Escribe el estado."

        if not (self.country or "").strip():
            errors["country"] = "Escribe el país."

        if not (self.contact_name or "").strip():
            errors["contact_name"] = "Escribe el responsable operativo."

        if not (self.contact_phone or "").strip():
            errors["contact_phone"] = "Escribe el teléfono de contacto."

        if self.is_primary and not self.is_active:
            errors["is_primary"] = "La bodega principal debe permanecer activa."

        if errors:
            raise ValidationError(errors)

    def build_full_address(self):
        street_line = " ".join(
            value
            for value in [
                (self.street or "").strip(),
                (self.exterior_number or "").strip(),
            ]
            if value
        )

        if (self.interior_number or "").strip():
            street_line = f"{street_line}, Interior " f"{self.interior_number.strip()}"

        locality_parts = []

        for value in [
            self.neighborhood,
            self.municipality,
            self.city,
        ]:
            value = (value or "").strip()

            if value and value.lower() not in {item.lower() for item in locality_parts}:
                locality_parts.append(value)

        region_line = ", ".join(
            value
            for value in [
                (self.state or "").strip(),
                (self.postal_code or "").strip(),
            ]
            if value
        )

        return ", ".join(
            value
            for value in [
                street_line,
                ", ".join(locality_parts),
                region_line,
                (self.country or "").strip(),
            ]
            if value
        )

    @property
    def full_address(self):
        return self.build_full_address() or self.address or ""

    def save(self, *args, **kwargs):
        self.name = (self.name or "").strip()
        self.code = (self.code or "").strip().upper() or None
        self.country_code = (self.country_code or "MX").strip().upper()

        generated_address = self.build_full_address()

        if generated_address:
            self.address = generated_address

        return super().save(*args, **kwargs)

    def __str__(self):
        code_label = self.code or "SIN-CÓDIGO"

        return f"{self.company.name} - " f"{self.name} ({code_label})"


class Stock(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="stocks",
    )

    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.CASCADE,
        related_name="stocks",
    )

    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        related_name="stocks",
    )

    physical_stock = models.IntegerField("Stock físico", default=0)
    reserved_stock = models.IntegerField("Stock reservado", default=0)
    committed_stock = models.IntegerField("Stock comprometido", default=0)

    created_at = models.DateTimeField("Fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField("Última actualización", auto_now=True)

    class Meta:
        verbose_name = "Stock"
        verbose_name_plural = "Stocks"
        unique_together = ("company", "warehouse", "variant")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(physical_stock__gte=0),
                name="stock_physical_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(reserved_stock__gte=0),
                name="stock_reserved_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(committed_stock__gte=0),
                name="stock_committed_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    physical_stock__gte=F("reserved_stock") + F("committed_stock")
                ),
                name="stock_reserved_committed_lte_physical",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "variant", "warehouse"],
                name="stock_co_var_wh_idx",
            ),
        ]

    @property
    def available_stock(self):
        return self.physical_stock - self.reserved_stock - self.committed_stock

    def clean(self):
        errors = {}

        if self.physical_stock < 0:
            errors["physical_stock"] = "El stock físico no puede ser negativo."

        if self.reserved_stock < 0:
            errors["reserved_stock"] = "El stock reservado no puede ser negativo."

        if self.committed_stock < 0:
            errors["committed_stock"] = "El stock comprometido no puede ser negativo."

        if self.reserved_stock + self.committed_stock > self.physical_stock:
            errors["reserved_stock"] = (
                "La suma de stock reservado y comprometido no puede superar el stock físico."
            )

        if self.warehouse_id and self.company_id:
            if self.warehouse.company_id != self.company_id:
                errors["warehouse"] = "La bodega no pertenece a esta empresa."

        if self.variant_id and self.company_id:
            if self.variant.company_id != self.company_id:
                errors["variant"] = "La variante no pertenece a esta empresa."

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.variant.sku} - Disponible: {self.available_stock}"



class ProductLot(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_BLOCKED = "blocked"
    STATUS_RECALLED = "recalled"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Activo"),
        (STATUS_BLOCKED, "Bloqueado"),
        (STATUS_RECALLED, "Retirado / Recall"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="product_lots",
    )
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="lots",
    )
    lot_number = models.CharField(
        "Número de lote",
        max_length=120,
    )
    manufactured_date = models.DateField(
        "Fecha de fabricación",
        blank=True,
        null=True,
    )
    expiration_date = models.DateField(
        "Fecha de caducidad",
        blank=True,
        null=True,
        db_index=True,
    )
    status = models.CharField(
        "Estado global",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        db_index=True,
    )
    status_reason = models.TextField(
        "Motivo del estado",
        blank=True,
        default="",
    )
    status_changed_at = models.DateTimeField(
        "Fecha del cambio de estado",
        blank=True,
        null=True,
    )
    status_changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="product_lot_status_changes",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="created_product_lots",
    )
    created_at = models.DateTimeField("Fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField("Última actualización", auto_now=True)

    class Meta:
        verbose_name = "Lote de producto"
        verbose_name_plural = "Lotes de producto"
        ordering = ["expiration_date", "lot_number", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "variant", "lot_number"],
                name="uniq_lot_co_var_number",
            ),
            models.CheckConstraint(
                condition=~models.Q(lot_number=""),
                name="lot_number_not_empty",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "variant", "status", "expiration_date"],
                name="lot_co_var_st_exp_idx",
            ),
            models.Index(
                fields=["company", "status"],
                name="lot_co_status_idx",
            ),
        ]

    @property
    def is_expired(self):
        if not self.expiration_date:
            return False
        from django.utils import timezone

        return self.expiration_date < timezone.localdate()

    @property
    def is_allocatable(self):
        return self.status == self.STATUS_ACTIVE and not self.is_expired

    @property
    def expiry_state(self):
        from inventory.expiry_services import classify_expiry

        return classify_expiry(self.expiration_date)

    @property
    def days_to_expiry(self):
        return self.expiry_state["days_to_expiry"]

    @property
    def expiry_severity(self):
        return self.expiry_state["severity"]

    @property
    def expiry_label(self):
        return self.expiry_state["label"]

    def clean(self):
        errors = {}

        if self.variant_id and self.company_id:
            if self.variant.company_id != self.company_id:
                errors["variant"] = "La variante no pertenece a esta empresa."

        if self.variant_id:
            inventory_control = self.variant.inventory_control

            if inventory_control == ProductVariant.INVENTORY_CONTROL_NORMAL:
                errors["variant"] = (
                    "No se pueden registrar lotes para una variante NORMAL."
                )

            if (
                inventory_control == ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY
                and not self.expiration_date
            ):
                errors["expiration_date"] = (
                    "La fecha de caducidad es obligatoria para LOT_EXPIRY."
                )

        if (
            self.manufactured_date
            and self.expiration_date
            and self.manufactured_date > self.expiration_date
        ):
            errors["expiration_date"] = (
                "La caducidad no puede ser anterior a la fecha de fabricación."
            )

        normalized_lot_number = (self.lot_number or "").strip().upper()
        if not normalized_lot_number:
            errors["lot_number"] = "El número de lote es obligatorio."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.lot_number = (self.lot_number or "").strip().upper()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.variant.sku} · {self.lot_number}"


class LotBalance(models.Model):
    STATUS_AVAILABLE = "available"
    STATUS_QUARANTINE = "quarantine"
    STATUS_BLOCKED = "blocked"
    STATUS_WASTE = "waste"

    STATUS_CHOICES = [
        (STATUS_AVAILABLE, "Disponible"),
        (STATUS_QUARANTINE, "Cuarentena"),
        (STATUS_BLOCKED, "Bloqueado"),
        (STATUS_WASTE, "Merma / Desecho"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="lot_balances",
    )
    lot = models.ForeignKey(
        ProductLot,
        on_delete=models.PROTECT,
        related_name="balances",
    )
    location = models.ForeignKey(
        "warehouse.WarehouseLocation",
        on_delete=models.PROTECT,
        related_name="lot_balances",
    )
    status = models.CharField(
        "Estado operativo",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_AVAILABLE,
        db_index=True,
    )
    physical_quantity = models.IntegerField("Cantidad física", default=0)
    reserved_quantity = models.IntegerField("Cantidad reservada", default=0)
    committed_quantity = models.IntegerField("Cantidad comprometida", default=0)
    created_at = models.DateTimeField("Fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField("Última actualización", auto_now=True)

    class Meta:
        verbose_name = "Saldo por lote"
        verbose_name_plural = "Saldos por lote"
        ordering = ["lot_id", "location_id", "status", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "lot", "location"],
                name="uniq_lotbal_co_lot_loc",
            ),
            models.CheckConstraint(
                condition=models.Q(physical_quantity__gte=0),
                name="lotbal_physical_nonneg",
            ),
            models.CheckConstraint(
                condition=models.Q(reserved_quantity__gte=0),
                name="lotbal_reserved_nonneg",
            ),
            models.CheckConstraint(
                condition=models.Q(committed_quantity__gte=0),
                name="lotbal_committed_nonneg",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    physical_quantity__gte=(
                        F("reserved_quantity") + F("committed_quantity")
                    )
                ),
                name="lotbal_res_comm_lte_phys",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="available")
                    | (
                        models.Q(reserved_quantity=0)
                        & models.Q(committed_quantity=0)
                    )
                ),
                name="lotbal_nonavail_unreserved",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "lot", "status"],
                name="lotbal_co_lot_st_idx",
            ),
            models.Index(
                fields=["company", "location", "status"],
                name="lotbal_co_loc_st_idx",
            ),
        ]

    @property
    def warehouse(self):
        return self.location.warehouse

    @property
    def available_quantity(self):
        return (
            self.physical_quantity
            - self.reserved_quantity
            - self.committed_quantity
        )

    @property
    def allocatable_quantity(self):
        if self.status != self.STATUS_AVAILABLE:
            return 0
        if not self.lot.is_allocatable:
            return 0
        if not self.location.is_pickable:
            return 0
        return self.available_quantity

    def clean(self):
        errors = {}

        if self.lot_id and self.company_id:
            if self.lot.company_id != self.company_id:
                errors["lot"] = "El lote no pertenece a esta empresa."

        if self.location_id and self.company_id:
            if self.location.company_id != self.company_id:
                errors["location"] = "La ubicación no pertenece a esta empresa."

        if self.lot_id and self.location_id:
            if self.lot.company_id != self.location.company_id:
                errors["location"] = (
                    "El lote y la ubicación pertenecen a empresas distintas."
                )

        if self.physical_quantity < 0:
            errors["physical_quantity"] = "La cantidad física no puede ser negativa."

        if self.reserved_quantity < 0:
            errors["reserved_quantity"] = (
                "La cantidad reservada no puede ser negativa."
            )

        if self.committed_quantity < 0:
            errors["committed_quantity"] = (
                "La cantidad comprometida no puede ser negativa."
            )

        if (
            self.reserved_quantity + self.committed_quantity
            > self.physical_quantity
        ):
            errors["reserved_quantity"] = (
                "Reservado + comprometido no puede superar la cantidad física."
            )

        if (
            self.status != self.STATUS_AVAILABLE
            and (self.reserved_quantity or self.committed_quantity)
        ):
            errors["status"] = (
                "Un saldo no disponible no puede conservar reservas o compromiso."
            )

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return (
            f"{self.lot.lot_number} · {self.location.bin_code} · "
            f"{self.available_quantity} disponibles"
        )


class LotMovement(models.Model):
    TYPE_RECEIPT = "receipt"
    TYPE_RESERVATION = "reservation"
    TYPE_RELEASE = "release"
    TYPE_COMMIT = "commit"
    TYPE_CONSUME = "consume"
    TYPE_TRANSFER_OUT = "transfer_out"
    TYPE_TRANSFER_IN = "transfer_in"
    TYPE_ADJUSTMENT = "adjustment"
    TYPE_RETURN_QUARANTINE = "return_quarantine"
    TYPE_QUARANTINE_RELEASE = "quarantine_release"
    TYPE_BLOCK = "block"
    TYPE_UNBLOCK = "unblock"
    TYPE_RECALL = "recall"
    TYPE_WASTE = "waste"
    TYPE_RECONCILIATION = "reconciliation"

    MOVEMENT_TYPE_CHOICES = [
        (TYPE_RECEIPT, "Recepción"),
        (TYPE_RESERVATION, "Reserva"),
        (TYPE_RELEASE, "Liberación"),
        (TYPE_COMMIT, "Compromiso"),
        (TYPE_CONSUME, "Consumo"),
        (TYPE_TRANSFER_OUT, "Transferencia - salida"),
        (TYPE_TRANSFER_IN, "Transferencia - entrada"),
        (TYPE_ADJUSTMENT, "Ajuste"),
        (TYPE_RETURN_QUARANTINE, "Devolución a cuarentena"),
        (TYPE_QUARANTINE_RELEASE, "Liberación de cuarentena"),
        (TYPE_BLOCK, "Bloqueo"),
        (TYPE_UNBLOCK, "Desbloqueo"),
        (TYPE_RECALL, "Recall / retiro"),
        (TYPE_WASTE, "Merma / desecho"),
        (TYPE_RECONCILIATION, "Conciliación"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="lot_movements",
    )
    lot = models.ForeignKey(
        ProductLot,
        on_delete=models.PROTECT,
        related_name="movements",
    )
    balance = models.ForeignKey(
        LotBalance,
        on_delete=models.PROTECT,
        related_name="movements",
        blank=True,
        null=True,
    )
    movement_type = models.CharField(
        "Tipo de movimiento",
        max_length=30,
        choices=MOVEMENT_TYPE_CHOICES,
    )
    quantity = models.PositiveIntegerField("Cantidad", default=0)
    reference_type = models.CharField(
        "Tipo de referencia",
        max_length=60,
        blank=True,
        default="",
    )
    reference_id = models.PositiveBigIntegerField(
        "ID de referencia",
        blank=True,
        null=True,
    )
    reference_code = models.CharField(
        "Referencia visible",
        max_length=150,
        blank=True,
        default="",
    )
    event_key = models.CharField(
        "Clave idempotente del evento",
        max_length=180,
        blank=True,
        null=True,
    )
    physical_before = models.IntegerField(blank=True, null=True, editable=False)
    physical_after = models.IntegerField(blank=True, null=True, editable=False)
    reserved_before = models.IntegerField(blank=True, null=True, editable=False)
    reserved_after = models.IntegerField(blank=True, null=True, editable=False)
    committed_before = models.IntegerField(blank=True, null=True, editable=False)
    committed_after = models.IntegerField(blank=True, null=True, editable=False)
    notes = models.TextField("Notas", blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="created_lot_movements",
    )
    created_at = models.DateTimeField("Fecha", auto_now_add=True)

    class Meta:
        verbose_name = "Movimiento de lote"
        verbose_name_plural = "Movimientos de lote"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "event_key"],
                condition=models.Q(event_key__isnull=False),
                name="uniq_lotmove_co_eventkey",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(physical_before__isnull=True)
                    | models.Q(physical_before__gte=0)
                ),
                name="lotmove_phys_before_nonneg",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(physical_after__isnull=True)
                    | models.Q(physical_after__gte=0)
                ),
                name="lotmove_phys_after_nonneg",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(reserved_before__isnull=True)
                    | models.Q(reserved_before__gte=0)
                ),
                name="lotmove_res_before_nonneg",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(reserved_after__isnull=True)
                    | models.Q(reserved_after__gte=0)
                ),
                name="lotmove_res_after_nonneg",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(committed_before__isnull=True)
                    | models.Q(committed_before__gte=0)
                ),
                name="lotmove_com_before_nonneg",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(committed_after__isnull=True)
                    | models.Q(committed_after__gte=0)
                ),
                name="lotmove_com_after_nonneg",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "lot", "created_at"],
                name="lotmove_co_lot_dt_idx",
            ),
            models.Index(
                fields=["company", "reference_type", "reference_id"],
                name="lotmove_co_ref_idx",
            ),
        ]

    def clean(self):
        errors = {}

        if self.lot_id and self.company_id:
            if self.lot.company_id != self.company_id:
                errors["lot"] = "El lote no pertenece a esta empresa."

        if self.balance_id:
            if self.lot_id and self.balance.lot_id != self.lot_id:
                errors["balance"] = "El saldo no pertenece a este lote."
            if self.company_id and self.balance.company_id != self.company_id:
                errors["balance"] = "El saldo no pertenece a esta empresa."

        for field_name in (
            "physical_before",
            "physical_after",
            "reserved_before",
            "reserved_after",
            "committed_before",
            "committed_after",
        ):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                errors[field_name] = "El snapshot del movimiento no puede ser negativo."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.event_key is not None:
            self.event_key = self.event_key.strip() or None
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError(
                "Los movimientos de lote son inmutables; crea un movimiento compensatorio."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "Los movimientos de lote son auditables e inmutables; no se eliminan."
        )

    def __str__(self):
        return f"{self.lot} · {self.movement_type} · {self.quantity}"


class LotExpiryAlert(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_RESOLVED = "resolved"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Activa"),
        (STATUS_RESOLVED, "Resuelta"),
    ]

    SEVERITY_INFO = "info"
    SEVERITY_NOTICE = "notice"
    SEVERITY_WARNING = "warning"
    SEVERITY_CRITICAL = "critical"
    SEVERITY_EXPIRED = "expired"

    SEVERITY_CHOICES = [
        (SEVERITY_INFO, "Informativa"),
        (SEVERITY_NOTICE, "Aviso"),
        (SEVERITY_WARNING, "Advertencia"),
        (SEVERITY_CRITICAL, "Crítica"),
        (SEVERITY_EXPIRED, "Vencida"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="lot_expiry_alerts",
    )
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.CASCADE,
        related_name="lot_expiry_alerts",
    )
    lot = models.ForeignKey(
        ProductLot,
        on_delete=models.CASCADE,
        related_name="expiry_alerts",
    )
    severity = models.CharField(
        "Severidad",
        max_length=20,
        choices=SEVERITY_CHOICES,
        db_index=True,
    )
    status = models.CharField(
        "Estado",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        db_index=True,
    )
    expiration_date = models.DateField("Caducidad")
    days_to_expiry = models.IntegerField("Días para vencer")
    affected_quantity = models.PositiveIntegerField(
        "Cantidad física afectada",
        default=0,
    )
    is_read = models.BooleanField("Leída", default=False)
    message = models.TextField("Mensaje", blank=True, default="")
    first_detected_at = models.DateTimeField(
        "Primera detección",
        auto_now_add=True,
    )
    updated_at = models.DateTimeField(
        "Última actualización",
        auto_now=True,
    )
    resolved_at = models.DateTimeField(
        "Fecha de resolución",
        blank=True,
        null=True,
    )

    class Meta:
        verbose_name = "Alerta de caducidad de lote"
        verbose_name_plural = "Alertas de caducidad de lotes"
        ordering = ["days_to_expiry", "expiration_date", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "warehouse", "lot"],
                condition=models.Q(status="active"),
                name="uniq_active_lotexp_co_wh_lot",
            ),
            models.CheckConstraint(
                condition=models.Q(affected_quantity__gte=0),
                name="lotexp_qty_nonneg",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "status", "severity"],
                name="lotexp_co_st_sev_idx",
            ),
            models.Index(
                fields=["company", "warehouse", "status"],
                name="lotexp_co_wh_st_idx",
            ),
            models.Index(
                fields=["company", "lot"],
                name="lotexp_co_lot_idx",
            ),
        ]

    def clean(self):
        errors = {}

        if self.lot_id and self.company_id:
            if self.lot.company_id != self.company_id:
                errors["lot"] = "El lote no pertenece a esta empresa."

        if self.warehouse_id and self.company_id:
            if self.warehouse.company_id != self.company_id:
                errors["warehouse"] = "La bodega no pertenece a esta empresa."

        if self.affected_quantity < 0:
            errors["affected_quantity"] = "La cantidad afectada no puede ser negativa."

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return (
            f"{self.lot.lot_number} · {self.warehouse.name} · "
            f"{self.get_severity_display()}"
        )


class StockCriticalAlert(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_RESOLVED = "resolved"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Activa"),
        (STATUS_RESOLVED, "Resuelta"),
    ]

    TYPE_LOW_STOCK = "low_stock"
    TYPE_OUT_OF_STOCK = "out_of_stock"

    ALERT_TYPE_CHOICES = [
        (TYPE_LOW_STOCK, "Stock bajo"),
        (TYPE_OUT_OF_STOCK, "Sin stock"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="stock_critical_alerts",
    )

    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.CASCADE,
        related_name="stock_critical_alerts",
    )

    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        related_name="stock_critical_alerts",
    )

    stock = models.ForeignKey(
        Stock,
        on_delete=models.SET_NULL,
        related_name="critical_alerts",
        blank=True,
        null=True,
    )

    alert_type = models.CharField(
        "Tipo de alerta",
        max_length=20,
        choices=ALERT_TYPE_CHOICES,
        default=TYPE_LOW_STOCK,
    )

    status = models.CharField(
        "Estado",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
    )

    current_available = models.IntegerField(
        "Disponible actual",
        default=0,
    )

    min_stock = models.PositiveIntegerField(
        "Stock mínimo configurado",
        default=0,
    )

    source = models.CharField(
        "Origen",
        max_length=80,
        blank=True,
        null=True,
        help_text="Ejemplo: picking_reserved, order_shipped o goods_receipt.",
    )

    reference = models.CharField(
        "Referencia",
        max_length=150,
        blank=True,
        null=True,
        help_text="Ejemplo: folio de pedido, picking o recepción.",
    )

    message = models.TextField("Mensaje")
    is_read = models.BooleanField("Leída", default=False)

    created_at = models.DateTimeField(
        "Fecha de alerta",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        "Última actualización",
        auto_now=True,
    )

    resolved_at = models.DateTimeField(
        "Fecha de resolución",
        blank=True,
        null=True,
    )

    class Meta:
        verbose_name = "Alerta de stock crítico"
        verbose_name_plural = "Alertas de stock crítico"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "company",
                    "warehouse",
                    "variant",
                ],
                condition=models.Q(status="active"),
                name="uniq_active_stock_alert_company_wh_variant",
            ),
        ]
        indexes = [
            models.Index(
                fields=[
                    "company",
                    "status",
                    "is_read",
                ],
                name="stock_alert_company_status_idx",
            ),
        ]

    def __str__(self):
        return (
            f"{self.variant.sku} - "
            f"{self.get_alert_type_display()} - "
            f"{self.current_available}/{self.min_stock}"
        )


class StockMovement(models.Model):
    MOVEMENT_TYPE_CHOICES = [
        ("in", "Entrada"),
        ("out", "Salida"),
        ("transfer", "Transferencia"),
        ("adjustment", "Ajuste"),
        ("reservation", "Reserva"),
        ("release", "Liberación de reserva"),
        ("consume_reserved", "Consumo de reserva"),
        ("damage", "Merma / Daño"),
        ("quarantine", "Cuarentena"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="stock_movements",
    )

    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.CASCADE,
        related_name="stock_movements",
    )

    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        related_name="stock_movements",
    )

    movement_type = models.CharField(
        "Tipo de movimiento",
        max_length=30,
        choices=MOVEMENT_TYPE_CHOICES,
    )

    quantity = models.IntegerField("Cantidad")

    reference = models.CharField(
        "Referencia",
        max_length=150,
        blank=True,
        null=True,
        help_text="Ejemplo: pedido #10, picking #5, ajuste manual, etc.",
    )
    notes = models.TextField(
        "Notas",
        blank=True,
        null=True,
    )

    stock_before = models.IntegerField(
        "Stock físico anterior",
        blank=True,
        null=True,
        editable=False,
    )

    stock_after = models.IntegerField(
        "Stock físico posterior",
        blank=True,
        null=True,
        editable=False,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="created_stock_movements",
        verbose_name="Realizado por",
    )

    created_at = models.DateTimeField(
        "Fecha de movimiento",
        auto_now_add=True,
    )
    class Meta:
        verbose_name = "Movimiento de stock"
        verbose_name_plural = "Movimientos de stock"
        ordering = ["-created_at"]

    def clean(self):
        errors = {}

        if self.quantity <= 0:
            errors["quantity"] = "La cantidad debe ser mayor a cero."
        if (
            self.stock_before is not None
            and self.stock_before < 0
        ):
            errors["stock_before"] = (
                "El stock anterior no puede ser negativo."
            )

        if (
            self.stock_after is not None
            and self.stock_after < 0
        ):
            errors["stock_after"] = (
                "El stock posterior no puede ser negativo."
            )

        if (
            self.stock_before is not None
            and self.stock_after is not None
            and self.movement_type == "in"
            and self.stock_after
            != self.stock_before + self.quantity
        ):
            errors["stock_after"] = (
                "Una entrada debe incrementar el stock "
                "exactamente por la cantidad registrada."
            )

        if self.warehouse_id and self.company_id:
            if self.warehouse.company_id != self.company_id:
                errors["warehouse"] = "La bodega no pertenece a esta empresa."

        if self.variant_id and self.company_id:
            if self.variant.company_id != self.company_id:
                errors["variant"] = "La variante no pertenece a esta empresa."

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.variant.sku} - {self.movement_type} - {self.quantity}"


class ProductBundleComponent(models.Model):
    """
    Define qué productos y cantidades componen un combo virtual.

    La cantidad se guarda en unidades internas para conservar
    compatibilidad con productos por volumen.
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="product_bundle_components",
        verbose_name="Empresa",
    )

    bundle_variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.CASCADE,
        related_name="bundle_components",
        verbose_name="Combo",
    )

    component_variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="used_in_bundles",
        verbose_name="Producto componente",
    )

    quantity = models.PositiveIntegerField(
        "Cantidad interna requerida",
        default=1,
        help_text=("Cantidad de unidades internas consumidas por cada combo vendido."),
    )

    created_at = models.DateTimeField(
        "Fecha de creación",
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        "Última actualización",
        auto_now=True,
    )

    class Meta:
        verbose_name = "Componente de combo"
        verbose_name_plural = "Componentes de combos"
        ordering = [
            "bundle_variant__sku",
            "component_variant__sku",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "bundle_variant",
                    "component_variant",
                ],
                name="unique_component_per_bundle",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="bundle_component_quantity_positive",
            ),
            models.CheckConstraint(
                condition=~models.Q(bundle_variant=models.F("component_variant")),
                name="bundle_cannot_contain_itself",
            ),
        ]
        indexes = [
            models.Index(
                fields=[
                    "company",
                    "bundle_variant",
                ],
                name="bundle_company_variant_idx",
            ),
        ]

    def clean(self):
        super().clean()

        errors = {}

        if self.quantity is None or self.quantity <= 0:
            errors["quantity"] = "La cantidad del componente debe ser mayor a cero."

        if self.bundle_variant_id and self.component_variant_id:
            if self.bundle_variant_id == self.component_variant_id:
                errors["component_variant"] = "Un combo no puede contenerse a sí mismo."

        if self.company_id and self.bundle_variant_id:
            if self.bundle_variant.company_id != self.company_id:
                errors["bundle_variant"] = "El combo pertenece a otra empresa."

        if self.company_id and self.component_variant_id:
            if self.component_variant.company_id != self.company_id:
                errors["component_variant"] = (
                    "El producto componente pertenece a otra empresa."
                )

        if self.bundle_variant_id:
            if not self.bundle_variant.is_bundle:
                errors["bundle_variant"] = (
                    "La variante seleccionada no está marcada como combo."
                )

        if self.component_variant_id:
            if self.component_variant.is_bundle:
                errors["component_variant"] = (
                    "Por ahora un combo no puede contener otro combo."
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()

        return super().save(
            *args,
            **kwargs,
        )

    def __str__(self):
        return (
            f"{self.bundle_variant.sku} · "
            f"{self.quantity} x "
            f"{self.component_variant.sku}"
        )


class LotReconciliationRun(models.Model):
    MODE_DRY_RUN = "dry_run"
    MODE_APPLY = "apply"

    MODE_CHOICES = [
        (MODE_DRY_RUN, "Solo diagnóstico"),
        (MODE_APPLY, "Aplicar reparación"),
    ]

    STATUS_CLEAN = "clean"
    STATUS_ISSUES = "issues"
    STATUS_REPAIRED = "repaired"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_CLEAN, "Sin inconsistencias"),
        (STATUS_ISSUES, "Con inconsistencias"),
        (STATUS_REPAIRED, "Reparado"),
        (STATUS_FAILED, "Fallido"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="lot_reconciliation_runs",
        help_text="Empresa limitada por el run. NULL significa alcance global.",
    )
    mode = models.CharField(
        max_length=20,
        choices=MODE_CHOICES,
        default=MODE_DRY_RUN,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_CLEAN,
        db_index=True,
    )
    controlled_variants = models.PositiveIntegerField(default=0)
    lot_balances = models.PositiveIntegerField(default=0)
    issues_found = models.PositiveIntegerField(default=0)
    repairs_applied = models.PositiveIntegerField(default=0)
    report = models.JSONField(default=dict, blank=True)
    repairs = models.JSONField(default=list, blank=True)
    database_name = models.CharField(max_length=150, blank=True, default="")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        verbose_name = "Ejecución de conciliación de lotes"
        verbose_name_plural = "Ejecuciones de conciliación de lotes"
        ordering = ["-started_at", "-id"]
        indexes = [
            models.Index(
                fields=["company", "started_at"],
                name="lotrecon_co_started_idx",
            ),
            models.Index(
                fields=["status", "started_at"],
                name="lotrecon_st_started_idx",
            ),
        ]

    def __str__(self):
        scope = self.company_id or "GLOBAL"
        return f"LT-1G {scope} · {self.mode} · {self.status}"
