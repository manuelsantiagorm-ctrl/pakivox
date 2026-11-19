from decimal import Decimal

from django import forms

from inventory.models import (
    Brand,
    Category,
    Product,
    ProductVariant,
)


class ProductCreateForm(forms.ModelForm):
    variant_sku = forms.CharField(
        label="SKU de venta",
        max_length=120,
        help_text=(
            "Código único utilizado para vender y controlar " "esta presentación."
        ),
    )

    variant_barcode = forms.CharField(
        label="Código de barras del SKU",
        max_length=120,
        required=False,
    )

    color = forms.CharField(
        label="Color",
        max_length=80,
        required=False,
    )

    size = forms.CharField(
        label="Talla o medida",
        max_length=80,
        required=False,
    )

    presentation = forms.CharField(
        label="Presentación",
        max_length=120,
        required=False,
        help_text="Ejemplo: Unidad, paquete de 12 o caja.",
    )

    specifications = forms.JSONField(
        label="Especificaciones adicionales",
        required=False,
        initial=dict,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": ('{"material": "Algodón", "modelo": "Clásico"}'),
            }
        ),
        help_text=(
            "Opcional. Utiliza un objeto JSON para atributos "
            "especiales de la categoría."
        ),
    )

    min_stock = forms.IntegerField(
        label="Stock mínimo",
        min_value=0,
        initial=0,
        help_text=(
            "Cantidad interna a partir de la cual WMS360 " "generará una alerta."
        ),
    )

    class Meta:
        model = Product
        fields = [
            "name",
            "description",
            "category",
            "brand",
            "stock_mode",
            "base_unit",
            "commercial_unit",
            "conversion_factor",
            "base_sku",
            "barcode",
            "image",
            "cost_price",
            "sale_price",
            "wholesale_price",
            "is_active",
        ]
        widgets = {
            "description": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": ("Descripción comercial y operativa del producto"),
                }
            ),
            "conversion_factor": forms.NumberInput(
                attrs={
                    "min": "0.001",
                    "step": "0.001",
                }
            ),
            "cost_price": forms.NumberInput(
                attrs={
                    "min": "0",
                    "step": "0.01",
                }
            ),
            "sale_price": forms.NumberInput(
                attrs={
                    "min": "0",
                    "step": "0.01",
                }
            ),
            "wholesale_price": forms.NumberInput(
                attrs={
                    "min": "0",
                    "step": "0.01",
                }
            ),
        }

    def __init__(
        self,
        *args,
        company=None,
        **kwargs,
    ):
        super().__init__(
            *args,
            **kwargs,
        )

        self.company = company

        self.fields["category"].queryset = Category.objects.none()

        self.fields["brand"].queryset = Brand.objects.none()

        if company:
            self.fields["category"].queryset = Category.objects.filter(
                company=company,
            ).order_by("name")

            self.fields["brand"].queryset = Brand.objects.filter(
                company=company,
            ).order_by("name")

        self.fields["category"].required = False
        self.fields["brand"].required = False
        self.fields["image"].required = False
        self.fields["description"].required = False
        self.fields["barcode"].required = False

        self.fields["stock_mode"].initial = "unit"
        self.fields["base_unit"].initial = "pza"
        self.fields["commercial_unit"].initial = "pza"
        self.fields["conversion_factor"].initial = Decimal("1")
        self.fields["is_active"].initial = True

        for field_name, field in self.fields.items():
            current_class = field.widget.attrs.get(
                "class",
                "",
            )

            field.widget.attrs["class"] = (
                f"{current_class} product-form-control"
            ).strip()

    def clean_name(self):
        return " ".join(
            str(
                self.cleaned_data.get(
                    "name",
                    "",
                )
            )
            .strip()
            .split()
        )

    def clean_base_sku(self):
        sku = (
            str(
                self.cleaned_data.get(
                    "base_sku",
                    "",
                )
            )
            .strip()
            .upper()
        )

        if not self.company:
            raise forms.ValidationError("No se pudo identificar la empresa.")

        if Product.objects.filter(
            company=self.company,
            base_sku__iexact=sku,
        ).exists():
            raise forms.ValidationError("Ya existe un producto con este SKU base.")

        return sku

    def clean_variant_sku(self):
        sku = (
            str(
                self.cleaned_data.get(
                    "variant_sku",
                    "",
                )
            )
            .strip()
            .upper()
        )

        if not self.company:
            raise forms.ValidationError("No se pudo identificar la empresa.")

        if ProductVariant.objects.filter(
            company=self.company,
            sku__iexact=sku,
        ).exists():
            raise forms.ValidationError("Ya existe una variante con este SKU.")

        return sku

    def clean_barcode(self):
        value = str(
            self.cleaned_data.get(
                "barcode",
                "",
            )
            or ""
        ).strip()

        return value or None

    def clean_variant_barcode(self):
        value = str(
            self.cleaned_data.get(
                "variant_barcode",
                "",
            )
            or ""
        ).strip()

        return value or None

    def clean(self):
        cleaned_data = super().clean()

        stock_mode = cleaned_data.get("stock_mode")

        base_unit = cleaned_data.get("base_unit")

        commercial_unit = cleaned_data.get("commercial_unit")

        conversion_factor = cleaned_data.get("conversion_factor")

        cost_price = cleaned_data.get("cost_price") or Decimal("0")

        sale_price = cleaned_data.get("sale_price") or Decimal("0")

        wholesale_price = cleaned_data.get("wholesale_price") or Decimal("0")

        if stock_mode == "unit":
            cleaned_data["base_unit"] = "pza"
            cleaned_data["commercial_unit"] = "pza"
            cleaned_data["conversion_factor"] = Decimal("1")

            self.instance.base_unit = "pza"
            self.instance.commercial_unit = "pza"
            self.instance.conversion_factor = Decimal("1")

        elif conversion_factor is None or conversion_factor <= 0:
            self.add_error(
                "conversion_factor",
                "El contenido debe ser mayor que cero.",
            )

        if sale_price < 0:
            self.add_error(
                "sale_price",
                "El precio de venta no puede ser negativo.",
            )

        if cost_price < 0:
            self.add_error(
                "cost_price",
                "El costo no puede ser negativo.",
            )

        if wholesale_price < 0:
            self.add_error(
                "wholesale_price",
                "El precio de mayoreo no puede ser negativo.",
            )

        if wholesale_price > 0 and sale_price > 0 and wholesale_price > sale_price:
            self.add_error(
                "wholesale_price",
                ("El precio de mayoreo no debería superar " "el precio de venta."),
            )

        if not base_unit and stock_mode != "unit":
            self.add_error(
                "base_unit",
                "Selecciona la unidad base.",
            )

        if not commercial_unit and stock_mode != "unit":
            self.add_error(
                "commercial_unit",
                "Selecciona la unidad comercial.",
            )

        return cleaned_data


class ManualStockEntryForm(forms.Form):
    variant = forms.ModelChoiceField(
        label="SKU",
        queryset=ProductVariant.objects.none(),
        empty_label="Selecciona un SKU",
    )

    quantity = forms.IntegerField(
        label="Cantidad de entrada",
        min_value=1,
        initial=1,
        help_text=("Cantidad expresada en unidades internas de inventario."),
    )

    reference = forms.CharField(
        label="Referencia",
        max_length=150,
        required=False,
        initial="Carga inicial",
        help_text=("Ejemplo: inventario inicial, factura, compra o ajuste autorizado."),
    )

    notes = forms.CharField(
        label="Notas",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": ("Explica el origen de esta entrada de inventario."),
            }
        ),
    )

    def __init__(
        self,
        *args,
        company=None,
        product=None,
        **kwargs,
    ):
        super().__init__(
            *args,
            **kwargs,
        )

        self.company = company
        self.product = product

        variants = ProductVariant.objects.none()

        if company and product:
            variants = ProductVariant.objects.filter(
                company=company,
                product=product,
                product__company=company,
                is_active=True,
                is_bundle=False,
                inventory_control=ProductVariant.INVENTORY_CONTROL_NORMAL,
            ).order_by("sku")

        self.fields["variant"].queryset = variants

        if variants.count() == 1:
            self.fields["variant"].initial = variants.first()

        for field in self.fields.values():
            current_class = field.widget.attrs.get(
                "class",
                "",
            )

            field.widget.attrs["class"] = (
                f"{current_class} stock-entry-control"
            ).strip()

    def clean_variant(self):
        variant = self.cleaned_data.get("variant")

        if not variant:
            return variant

        if not self.company or not self.product:
            raise forms.ValidationError("No se pudo validar el producto.")

        if (
            variant.company_id != self.company.id
            or variant.product_id != self.product.id
            or self.product.company_id != self.company.id
        ):
            raise forms.ValidationError(
                "El SKU seleccionado no pertenece " "a este producto o empresa."
            )

        if variant.is_bundle:
            raise forms.ValidationError("Un combo virtual no recibe stock directo.")

        if (
            variant.inventory_control
            != ProductVariant.INVENTORY_CONTROL_NORMAL
        ):
            raise forms.ValidationError(
                "Los SKU con control por lote no admiten entradas manuales "
                "sin identidad de lote."
            )

        return variant


class ProductEditForm(forms.ModelForm):
    """
    Edición de los datos comerciales de un producto existente.

    No se editan aquí el SKU base, la forma de controlar existencias ni las
    unidades/factor de conversión: cambiarlos con stock ya registrado
    alteraría el significado de las existencias y de los movimientos.
    """

    remove_image = forms.BooleanField(
        label="Eliminar imagen actual",
        required=False,
    )

    class Meta:
        model = Product
        fields = [
            "name",
            "category",
            "brand",
            "barcode",
            "cost_price",
            "sale_price",
            "wholesale_price",
            "description",
            "image",
            "is_active",
        ]
        labels = {
            "name": "Nombre del producto",
            "category": "Categoría",
            "brand": "Marca",
            "barcode": "Código de barras",
            "cost_price": "Costo",
            "sale_price": "Precio de venta",
            "wholesale_price": "Precio de mayoreo",
            "description": "Descripción",
            "image": "Imagen",
            "is_active": "Producto activo (se puede vender)",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "cost_price": forms.NumberInput(attrs={"min": "0", "step": "0.01", "inputmode": "decimal"}),
            "sale_price": forms.NumberInput(attrs={"min": "0", "step": "0.01", "inputmode": "decimal"}),
            "wholesale_price": forms.NumberInput(attrs={"min": "0", "step": "0.01", "inputmode": "decimal"}),
        }

    def __init__(self, *args, company, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company
        self.fields["category"].queryset = Category.objects.filter(company=company).order_by("name")
        self.fields["brand"].queryset = Brand.objects.filter(company=company).order_by("name")
        self.fields["category"].empty_label = "Sin categoría"
        self.fields["brand"].empty_label = "Sin marca"
        for name in ("category", "brand", "barcode", "description", "image"):
            self.fields[name].required = False
        # Los precios se guardan con 6 decimales; se muestran con 2.
        if not self.is_bound:
            for name in ("cost_price", "sale_price", "wholesale_price"):
                valor = getattr(self.instance, name, None)
                if valor is not None:
                    self.initial[name] = Decimal(valor).quantize(Decimal("0.01"))

    def clean_name(self):
        value = " ".join(str(self.cleaned_data.get("name") or "").split())
        if not value:
            raise forms.ValidationError("Escribe el nombre del producto.")
        return value

    def clean_barcode(self):
        value = str(self.cleaned_data.get("barcode") or "").strip()
        if not value:
            return None
        repetido = (
            Product.objects.filter(company=self.company, barcode__iexact=value)
            .exclude(pk=self.instance.pk)
            .exists()
        )
        if repetido:
            raise forms.ValidationError("Ese código de barras ya lo usa otro producto de tu empresa.")
        return value

    def clean(self):
        cleaned_data = super().clean()
        cost_price = cleaned_data.get("cost_price") or Decimal("0")
        sale_price = cleaned_data.get("sale_price") or Decimal("0")
        wholesale_price = cleaned_data.get("wholesale_price") or Decimal("0")
        if cost_price < 0:
            self.add_error("cost_price", "El costo no puede ser negativo.")
        if sale_price < 0:
            self.add_error("sale_price", "El precio de venta no puede ser negativo.")
        if wholesale_price < 0:
            self.add_error("wholesale_price", "El precio de mayoreo no puede ser negativo.")
        if wholesale_price > 0 and sale_price > 0 and wholesale_price > sale_price:
            self.add_error("wholesale_price", "El precio de mayoreo no debería superar el precio de venta.")
        return cleaned_data
