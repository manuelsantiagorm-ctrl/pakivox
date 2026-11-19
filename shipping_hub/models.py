import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from companies.scoping import CompanyScopedModel, CompanyScopedQuerySet  # noqa: F401

# ============================================================
# BASE MULTIEMPRESA
# ============================================================

# ============================================================
# BASE MULTIEMPRESA
# ============================================================


class CompanyScopedQuerySet(models.QuerySet):
    def for_company(self, company):
        """
        Devuelve únicamente registros de la empresa indicada.

        Regla crítica SaaS:
        si no existe una empresa válida, no devuelve información.
        """
        if company is None:
            return self.none()

        return self.filter(company=company)


class CompanyScopedModel(models.Model):
    """Modelo abstracto para registros aislados por empresa."""

    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_records",
    )

    objects = CompanyScopedQuerySet.as_manager()

    class Meta:
        abstract = True


class CompanyScopedModel(models.Model):
    """Modelo abstracto para registros aislados por empresa."""

    company = models.ForeignKey(
        "companies.Company",
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_records",
    )

    objects = CompanyScopedQuerySet.as_manager()

    class Meta:
        abstract = True


# ============================================================
# INTEGRACIÓN LOGÍSTICA POR EMPRESA
# ============================================================


class CompanyShippingIntegration(CompanyScopedModel):
    """
    Configuración de un proveedor logístico por empresa.

    Permite trabajar con:
    - Cuenta propia de Envia.com.
    - Cuenta maestra de WMS360 en Envia.com.
    - Contrato directo con Estafeta.
    - Contrato directo con FedEx.
    - Contrato directo con DHL.
    - Otros proveedores futuros.

    Las credenciales nunca se guardan en campos visibles.
    Se almacenan cifradas en credentials_encrypted.
    """

    # =========================================================
    # PROVEEDORES
    # =========================================================

    PROVIDER_ENVIA = "envia"
    PROVIDER_ESTAFETA_DIRECT = "estafeta_direct"
    PROVIDER_FEDEX_DIRECT = "fedex_direct"
    PROVIDER_DHL_DIRECT = "dhl_direct"
    PROVIDER_SKYDROPX = "skydropx"

    PROVIDER_CHOICES = [
        (
            PROVIDER_ENVIA,
            "Envia.com",
        ),
        (
            PROVIDER_ESTAFETA_DIRECT,
            "Estafeta — contrato directo",
        ),
        (
            PROVIDER_FEDEX_DIRECT,
            "FedEx — contrato directo",
        ),
        (
            PROVIDER_DHL_DIRECT,
            "DHL — contrato directo",
        ),
        (
            PROVIDER_SKYDROPX,
            "Skydropx",
        ),
    ]

    # =========================================================
    # MODO DE CUENTA
    # =========================================================

    ACCOUNT_MODE_MASTER = "master"
    ACCOUNT_MODE_COMPANY = "company"

    ACCOUNT_MODE_CHOICES = [
        (
            ACCOUNT_MODE_MASTER,
            "Cuenta maestra de WMS360",
        ),
        (
            ACCOUNT_MODE_COMPANY,
            "Cuenta propia de la empresa",
        ),
    ]

    # =========================================================
    # ENTORNOS
    # =========================================================

    ENVIRONMENT_SANDBOX = "sandbox"
    ENVIRONMENT_PRODUCTION = "production"

    ENVIRONMENT_CHOICES = [
        (
            ENVIRONMENT_SANDBOX,
            "Sandbox",
        ),
        (
            ENVIRONMENT_PRODUCTION,
            "Producción",
        ),
    ]

    # =========================================================
    # FORMATOS DE ETIQUETA
    # =========================================================

    FORMAT_PDF = "pdf"
    FORMAT_ZPL = "zpl"

    FORMAT_CHOICES = [
        (
            FORMAT_PDF,
            "PDF",
        ),
        (
            FORMAT_ZPL,
            "ZPL",
        ),
    ]

    # =========================================================
    # CONFIGURACIÓN GENERAL
    # =========================================================

    provider = models.CharField(
        max_length=30,
        choices=PROVIDER_CHOICES,
    )

    account_mode = models.CharField(
        max_length=20,
        choices=ACCOUNT_MODE_CHOICES,
        default=ACCOUNT_MODE_COMPANY,
        help_text=(
            "Indica si se usará una cuenta propia de la empresa "
            "o una cuenta maestra administrada por WMS360."
        ),
    )

    environment = models.CharField(
        max_length=20,
        choices=ENVIRONMENT_CHOICES,
        default=ENVIRONMENT_SANDBOX,
    )

    is_active = models.BooleanField(
        default=False,
        help_text=(
            "Permite utilizar esta integración para cotizar, "
            "generar guías y consultar rastreo."
        ),
    )

    is_primary = models.BooleanField(
        default=False,
        help_text=(
            "Integración seleccionada por defecto para esta " "empresa y entorno."
        ),
    )

    account_name = models.CharField(
        max_length=150,
        blank=True,
        help_text=(
            "Nombre interno para identificar el contrato " "o cuenta logística."
        ),
    )

    preferred_label_format = models.CharField(
        max_length=10,
        choices=FORMAT_CHOICES,
        default=FORMAT_PDF,
    )

    fallback_label_format = models.CharField(
        max_length=10,
        choices=FORMAT_CHOICES,
        default=FORMAT_PDF,
    )

    priority = models.PositiveSmallIntegerField(
        default=100,
        help_text="Orden multi-proveedor; el número menor se consulta primero.",
    )
    warehouse = models.ForeignKey(
        "inventory.Warehouse",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="shipping_integrations",
        help_text=(
            "Si se deja vacío, esta integración aplica como respaldo "
            "para toda la empresa cuando la bodega no tiene una propia."
        ),
    )

    allow_quote_fallback = models.BooleanField(
        default=True,
        help_text="Continúa con otro agregador si esta cotización falla.",
    )

    # =========================================================
    # CREDENCIALES CIFRADAS
    # =========================================================

    credentials_encrypted = models.TextField(
        blank=True,
        help_text=(
            "Credenciales cifradas del proveedor logístico. "
            "Nunca deben almacenarse secretos en texto plano."
        ),
    )

    webhook_secret_encrypted = models.TextField(
        blank=True,
        help_text=("Secreto cifrado utilizado para validar webhooks."),
    )

    # =========================================================
    # ESTADO DE CONEXIÓN
    # =========================================================

    last_connection_test_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    last_connection_test_success = models.BooleanField(
        default=False,
    )

    last_connection_error = models.TextField(
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "-is_primary",
            "provider",
            "environment",
            "account_mode",
        ]

        constraints = [
            # Permite, por ejemplo:
            # Envia company + producción
            # Envia master + producción
            # Estafeta directa + producción
            models.UniqueConstraint(
                fields=[
                    "company",
                    "provider",
                    "environment",
                    "account_mode",
                ],
                name="uniq_ship_integration_mode",
            ),
            # Solo puede existir una integración principal
            # por empresa y entorno.
            models.UniqueConstraint(
                fields=[
                    "company",
                    "environment",
                ],
                condition=Q(
                    is_primary=True,
                ),
                name="uniq_primary_ship_integration",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "company",
                    "environment",
                    "is_active",
                ],
                name="shipint_comp_env_active_idx",
            ),
            models.Index(
                fields=[
                    "company",
                    "is_primary",
                ],
                name="shipint_comp_primary_idx",
            ),
            models.Index(
                fields=[
                    "company",
                    "provider",
                    "account_mode",
                ],
                name="shipint_comp_prov_mode_idx",
            ),
        ]

    def clean(self):
        super().clean()

        direct_providers = {
            self.PROVIDER_ESTAFETA_DIRECT,
            self.PROVIDER_FEDEX_DIRECT,
            self.PROVIDER_DHL_DIRECT,
        }

        # Los contratos directos siempre pertenecen
        # a la empresa cliente.
        if (
            self.provider in direct_providers
            and self.account_mode == self.ACCOUNT_MODE_MASTER
        ):
            raise ValidationError(
                {
                    "account_mode": (
                        "Los contratos directos deben utilizar "
                        "la cuenta propia de la empresa."
                    )
                }
            )

        # Una integración principal debe estar habilitada.
        if self.is_primary and not self.is_active:
            raise ValidationError(
                {"is_primary": ("Una integración principal debe estar activa.")}
            )

        # Las credenciales propias son obligatorias
        # cuando la integración está activa.
        if (
            self.is_active
            and self.account_mode == self.ACCOUNT_MODE_COMPANY
            and not self.credentials_encrypted
        ):
            raise ValidationError(
                {
                    "credentials_encrypted": (
                        "La cuenta propia de la empresa necesita "
                        "credenciales configuradas."
                    )
                }
            )

        # La cuenta maestra se obtiene desde configuración
        # segura del servidor, no desde la fila de la empresa.
        if self.account_mode == self.ACCOUNT_MODE_MASTER and self.provider not in {
            self.PROVIDER_ENVIA,
            self.PROVIDER_SKYDROPX,
        }:
            raise ValidationError(
                {
                    "provider": (
                        "Este proveedor no admite una cuenta "
                        "maestra administrada por WMS360."
                    )
                }
            )

    @property
    def uses_master_account(self):
        return self.account_mode == self.ACCOUNT_MODE_MASTER

    @property
    def uses_company_account(self):
        return self.account_mode == self.ACCOUNT_MODE_COMPANY

    @property
    def is_direct_contract(self):
        return self.provider in {
            self.PROVIDER_ESTAFETA_DIRECT,
            self.PROVIDER_FEDEX_DIRECT,
            self.PROVIDER_DHL_DIRECT,
        }

    @property
    def strategy_key(self):
        """
        Identificador utilizado por el resolver
        del Strategy Pattern.
        """

        return self.provider

    def __str__(self):
        primary_text = " · Principal" if self.is_primary else ""

        return (
            f"{self.company} - "
            f"{self.get_provider_display()} - "
            f"{self.get_account_mode_display()} "
            f"({self.get_environment_display()})"
            f"{primary_text}"
        )


# ============================================================
# COTIZACIONES
# ============================================================


class ShippingQuote(CompanyScopedModel):
    """
    Tarifa devuelta por Envia.com, Skydropx u otro proveedor.

    La cotización pertenece primero a un pedido de ventas, porque debe
    generarse y seleccionarse antes del pago, picking, packing y creación
    del envío operativo.

    Después puede vincularse opcionalmente con ShippingOrder.
    """

    order = models.ForeignKey(
        "sales.Order",
        on_delete=models.CASCADE,
        related_name="shipping_quotes",
    )

    shipping_order = models.ForeignKey(
        "warehouse.ShippingOrder",
        on_delete=models.CASCADE,
        related_name="shipping_quotes",
        null=True,
        blank=True,
    )

    provider = models.CharField(
        max_length=30,
        choices=CompanyShippingIntegration.PROVIDER_CHOICES,
    )

    integration = models.ForeignKey(
        CompanyShippingIntegration,
        on_delete=models.PROTECT,
        related_name="shipping_quotes",
        null=True,
        blank=True,
        help_text="Cuenta exacta que originó la tarifa.",
    )

    external_rate_id = models.CharField(
        max_length=200,
        blank=True,
    )

    carrier_code = models.CharField(
        max_length=100,
        blank=True,
    )

    carrier_name = models.CharField(
        max_length=150,
    )

    service_code = models.CharField(
        max_length=100,
        blank=True,
    )

    service_name = models.CharField(
        max_length=150,
    )

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    currency = models.CharField(
        max_length=10,
        default="MXN",
    )

    estimated_delivery_days = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    estimated_delivery_text = models.CharField(
        max_length=150,
        blank=True,
    )

    is_selected = models.BooleanField(
        default=False,
    )

    expires_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    response_data = models.JSONField(
        default=dict,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "amount",
            "created_at",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "company",
                    "order",
                ],
                condition=Q(is_selected=True),
                name="uniq_selected_quote_order",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "company",
                    "order",
                ],
                name="quote_comp_order_idx",
            ),
            models.Index(
                fields=[
                    "company",
                    "shipping_order",
                ],
                name="quote_comp_ship_idx",
            ),
            models.Index(
                fields=[
                    "company",
                    "provider",
                ],
                name="quote_comp_prov_idx",
            ),
        ]

    def clean(self):
        super().clean()

        errors = {}

        if self.order_id:
            if self.order.company_id != self.company_id:
                errors["order"] = (
                    "La cotización y el pedido pertenecen " "a empresas diferentes."
                )

        if self.shipping_order_id:
            if self.shipping_order.company_id != self.company_id:
                errors["shipping_order"] = (
                    "La cotización y la orden de envío pertenecen "
                    "a empresas diferentes."
                )

            shipping_order_sales_order_id = getattr(
                self.shipping_order,
                "order_id",
                None,
            )

            if (
                self.order_id
                and shipping_order_sales_order_id
                and shipping_order_sales_order_id != self.order_id
            ):
                errors["shipping_order"] = (
                    "La orden de envío no corresponde al pedido " "de esta cotización."
                )

        if self.integration_id:
            if self.integration.company_id != self.company_id:
                errors["integration"] = "La cuenta logística pertenece a otra empresa."
            elif self.integration.provider != self.provider:
                errors["integration"] = "La cuenta logística no corresponde al proveedor de la tarifa."

        if self.amount is not None and self.amount < 0:
            errors["amount"] = "El costo de la cotización no puede ser negativo."

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return (
            f"{self.carrier_name} - "
            f"{self.service_name} - "
            f"${self.amount} {self.currency}"
        )


# ============================================================
# COMPRA Y CONTROL DE LA GUÍA
# ============================================================
class CarrierShipment(CompanyScopedModel):
    STATUS_DRAFT = "draft"
    STATUS_GENERATING = "generating"
    STATUS_RECONCILING = "reconciling"
    STATUS_LABEL_READY = "label_ready"
    STATUS_PRINTED = "printed"
    STATUS_READY = "ready"
    STATUS_SHIPPED = "shipped"
    STATUS_IN_TRANSIT = "in_transit"
    STATUS_OUT_FOR_DELIVERY = "out_for_delivery"
    STATUS_DELIVERED = "delivered"
    STATUS_EXCEPTION = "exception"
    STATUS_CANCELLED = "cancelled"
    STATUS_FAILED = "failed"

    SOURCE_AUTOMATIC = "automatic"
    SOURCE_EXTERNAL = "external"
    SOURCE_LOCAL = "local"
    SOURCE_CUSTOM = "custom"
    SOURCE_DIRECT = "direct"

    SOURCE_CHOICES = [
        (SOURCE_AUTOMATIC, "Guía automática"),
        (SOURCE_EXTERNAL, "Guía externa"),
        (SOURCE_LOCAL, "Entrega propia / local"),
        (SOURCE_CUSTOM, "Transportista personalizado"),
        (SOURCE_DIRECT, "Convenio directo"),
    ]

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Borrador"),
        (STATUS_GENERATING, "Generando guía"),
        (STATUS_RECONCILING, "Verificando con proveedor"),
        (STATUS_LABEL_READY, "Guía generada"),
        (STATUS_PRINTED, "Etiqueta impresa"),
        (STATUS_READY, "Listo para salida"),
        (STATUS_SHIPPED, "Enviado"),
        (STATUS_IN_TRANSIT, "En tránsito"),
        (STATUS_OUT_FOR_DELIVERY, "En reparto"),
        (STATUS_DELIVERED, "Entregado"),
        (STATUS_EXCEPTION, "Incidencia"),
        (STATUS_CANCELLED, "Cancelado"),
        (STATUS_FAILED, "Fallido"),
    ]

    shipping_order = models.ForeignKey(
        "warehouse.ShippingOrder",
        on_delete=models.CASCADE,
        related_name="carrier_shipments",
    )

    selected_quote = models.ForeignKey(
        ShippingQuote,
        on_delete=models.SET_NULL,
        related_name="carrier_shipments",
        null=True,
        blank=True,
    )

    source = models.CharField(
        "Origen del envío",
        max_length=20,
        choices=SOURCE_CHOICES,
        default=SOURCE_AUTOMATIC,
        db_index=True,
    )

    provider = models.CharField(
        max_length=30,
        choices=[
            *CompanyShippingIntegration.PROVIDER_CHOICES,
            ("manual", "Guía externa"),
            ("local", "Entrega propia / local"),
            ("custom", "Transportista personalizado"),
            ("direct", "Convenio directo"),
        ],
    )

    carrier_code = models.CharField(
        max_length=100,
        blank=True,
    )

    carrier_name = models.CharField(
        max_length=150,
        blank=True,
    )

    service_code = models.CharField(
        max_length=100,
        blank=True,
    )

    service_name = models.CharField(
        max_length=150,
        blank=True,
    )

    external_shipment_id = models.CharField(
        max_length=200,
        blank=True,
        db_index=True,
    )

    internal_reference = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )

    idempotency_key = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )

    master_tracking_number = models.CharField(
        max_length=200,
        blank=True,
        db_index=True,
    )

    master_tracking_url = models.URLField(
        max_length=1000,
        blank=True,
    )

    total_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    currency = models.CharField(
        max_length=10,
        default="MXN",
    )

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
    )

    generation_attempts = models.PositiveIntegerField(
        default=0,
    )

    purchase_quote_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Importe que el Owner confirmó antes de comprar la guía.",
    )

    purchase_currency = models.CharField(max_length=10, blank=True)

    purchase_provider = models.CharField(max_length=30, blank=True)

    purchase_environment = models.CharField(max_length=20, blank=True)

    purchase_confirmed_at = models.DateTimeField(null=True, blank=True)

    purchase_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="confirmed_shipping_purchases",
        null=True,
        blank=True,
    )

    purchase_started_at = models.DateTimeField(null=True, blank=True)

    last_error_type = models.CharField(
        max_length=50,
        blank=True,
    )

    last_error_code = models.CharField(
        max_length=100,
        blank=True,
    )

    last_error_message = models.TextField(
        blank=True,
    )

    request_data = models.JSONField(
        default=dict,
        blank=True,
    )

    response_data = models.JSONField(
        default=dict,
        blank=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_carrier_shipments",
        null=True,
        blank=True,
    )

    generated_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    external_registered_at = models.DateTimeField(
        "Fecha de registro externo",
        null=True,
        blank=True,
    )

    printed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )
    class Meta:
        ordering = ["-created_at"]

        constraints = [
            models.UniqueConstraint(
                fields=["company", "shipping_order"],
                condition=Q(
                    status__in=[
                        "generating",
                        "reconciling",
                        "label_ready",
                        "printed",
                        "ready",
                        "shipped",
                        "in_transit",
                        "out_for_delivery",
                        "delivered",
                    ]
                ),
                name="uniq_active_carrier_ship",
            )
        ]

        indexes = [
            models.Index(
                fields=["company", "status"],
                name="cship_comp_status_idx",
            ),
            models.Index(
                fields=["company", "provider"],
                name="cship_comp_prov_idx",
            ),
            models.Index(
                fields=["company", "shipping_order"],
                name="cship_comp_ship_idx",
            ),
        ]

    def clean(self):
        super().clean()

        # Seguridad multiempresa:
        # la orden de envío debe pertenecer a la misma empresa.
        if self.shipping_order_id:
            if self.shipping_order.company_id != self.company_id:
                raise ValidationError(
                    {
                        "shipping_order": (
                            "La guía y la orden de envío pertenecen "
                            "a empresas diferentes."
                        )
                    }
                )

        # La cotización debe pertenecer a la misma empresa,
        # orden de envío y proveedor logístico.
        if self.selected_quote_id:
            if self.selected_quote.company_id != self.company_id:
                raise ValidationError(
                    {
                        "selected_quote": (
                            "La cotización seleccionada pertenece " "a otra empresa."
                        )
                    }
                )

            if self.selected_quote.shipping_order_id != self.shipping_order_id:
                raise ValidationError(
                    {
                        "selected_quote": (
                            "La cotización seleccionada no pertenece "
                            "a esta orden de envío."
                        )
                    }
                )

            if self.selected_quote.provider != self.provider:
                raise ValidationError(
                    {
                        "selected_quote": (
                            "La cotización seleccionada pertenece "
                            "a otro proveedor logístico."
                        )
                    }
                )

        # Una guía externa requiere paquetería y rastreo.
        if self.source == self.SOURCE_EXTERNAL:
            if not self.carrier_name or not self.carrier_name.strip():
                raise ValidationError(
                    {
                        "carrier_name": (
                            "La paquetería es obligatoria "
                            "para registrar una guía externa."
                        )
                    }
                )

            if (
                not self.master_tracking_number
                or not self.master_tracking_number.strip()
            ):
                raise ValidationError(
                    {
                        "master_tracking_number": (
                            "El número de rastreo es obligatorio "
                            "para registrar una guía externa."
                        )
                    }
                )

        # Una entrega propia/local no utiliza guía de paquetería.
        if self.source == self.SOURCE_LOCAL:
            if self.master_tracking_number:
                raise ValidationError(
                    {
                        "master_tracking_number": (
                            "Una entrega propia no debe tener "
                            "número de guía de paquetería."
                        )
                    }
                )

            if self.master_tracking_url:
                raise ValidationError(
                    {
                        "master_tracking_url": (
                            "Una entrega propia no debe tener "
                            "un enlace de rastreo de paquetería."
                        )
                    }
                )

        if self.total_cost is not None and self.total_cost < 0:
            raise ValidationError(
                {"total_cost": ("El costo total del envío no puede ser negativo.")}
            )

    @property
    def has_label(self):
        return self.packages.filter(
            status__in=[
                ShipmentPackage.STATUS_LABEL_READY,
                ShipmentPackage.STATUS_PRINTED,
                ShipmentPackage.STATUS_SHIPPED,
                ShipmentPackage.STATUS_IN_TRANSIT,
                ShipmentPackage.STATUS_OUT_FOR_DELIVERY,
                ShipmentPackage.STATUS_DELIVERED,
            ]
        ).exists()

    @property
    def package_count(self):
        return self.packages.count()

    def __str__(self):
        reference = (
            self.master_tracking_number
            or self.external_shipment_id
            or str(self.internal_reference)
        )

        carrier = self.carrier_name or self.get_provider_display() or self.provider

        return f"{carrier} - {reference}"


# ============================================================
# PAQUETES / BULTOS
# ============================================================


class ShipmentPackage(CompanyScopedModel):
    STATUS_DRAFT = "draft"
    STATUS_GENERATING = "generating"
    STATUS_LABEL_READY = "label_ready"
    STATUS_PRINTED = "printed"
    STATUS_SHIPPED = "shipped"
    STATUS_IN_TRANSIT = "in_transit"
    STATUS_OUT_FOR_DELIVERY = "out_for_delivery"
    STATUS_DELIVERED = "delivered"
    STATUS_EXCEPTION = "exception"
    STATUS_CANCELLED = "cancelled"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Borrador"),
        (STATUS_GENERATING, "Generando"),
        (STATUS_LABEL_READY, "Etiqueta lista"),
        (STATUS_PRINTED, "Etiqueta impresa"),
        (STATUS_SHIPPED, "Enviado"),
        (STATUS_IN_TRANSIT, "En tránsito"),
        (STATUS_OUT_FOR_DELIVERY, "En reparto"),
        (STATUS_DELIVERED, "Entregado"),
        (STATUS_EXCEPTION, "Incidencia"),
        (STATUS_CANCELLED, "Cancelado"),
        (STATUS_FAILED, "Fallido"),
    ]

    LABEL_FORMAT_PDF = "pdf"
    LABEL_FORMAT_PNG = "png"
    LABEL_FORMAT_ZPL = "zpl"

    LABEL_FORMAT_CHOICES = [
        (LABEL_FORMAT_PDF, "PDF"),
        (LABEL_FORMAT_PNG, "PNG"),
        (LABEL_FORMAT_ZPL, "ZPL"),
    ]

    carrier_shipment = models.ForeignKey(
        CarrierShipment,
        on_delete=models.CASCADE,
        related_name="packages",
    )

    package_number = models.PositiveIntegerField(default=1)

    description = models.CharField(
        max_length=200,
        blank=True,
    )

    weight_kg = models.DecimalField(
        max_digits=10,
        decimal_places=3,
    )

    length_cm = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    width_cm = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    height_cm = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    external_package_id = models.CharField(
        max_length=200,
        blank=True,
    )

    tracking_number = models.CharField(
        max_length=200,
        blank=True,
        db_index=True,
    )

    tracking_url = models.URLField(
        max_length=1000,
        blank=True,
    )

    label_format = models.CharField(
        max_length=10,
        choices=LABEL_FORMAT_CHOICES,
        default=LABEL_FORMAT_PDF,
    )

    label_url = models.URLField(
        max_length=1000,
        blank=True,
    )

    label_file = models.FileField(
        upload_to="private/shipping_labels/%Y/%m/",
        null=True,
        blank=True,
    )

    label_zpl = models.TextField(
        blank=True,
    )

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
    )

    last_error_message = models.TextField(
        blank=True,
    )

    printed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    external_label_original_name = models.CharField(max_length=255, blank=True)
    external_label_content_type = models.CharField(max_length=100, blank=True)
    external_registered_at = models.DateTimeField(null=True, blank=True)
    external_registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="registered_external_package_labels",
        null=True,
        blank=True,
    )
    print_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["package_number"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "company",
                    "carrier_shipment",
                    "package_number",
                ],
                name="uniq_package_number_ship",
            ),
            models.CheckConstraint(
                condition=Q(package_number__gt=0),
                name="pkg_number_positive",
            ),
            models.CheckConstraint(
                condition=Q(weight_kg__gt=0),
                name="pkg_weight_positive",
            ),
            models.CheckConstraint(
                condition=Q(length_cm__gt=0),
                name="pkg_length_positive",
            ),
            models.CheckConstraint(
                condition=Q(width_cm__gt=0),
                name="pkg_width_positive",
            ),
            models.CheckConstraint(
                condition=Q(height_cm__gt=0),
                name="pkg_height_positive",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "tracking_number"],
                name="pkg_comp_track_idx",
            ),
            models.Index(
                fields=["company", "carrier_shipment"],
                name="pkg_comp_cship_idx",
            ),
        ]

    def clean(self):
        super().clean()

        if self.carrier_shipment_id:
            if self.carrier_shipment.company_id != self.company_id:
                raise ValidationError(
                    {
                        "carrier_shipment": (
                            "El paquete y la guía pertenecen " "a empresas diferentes."
                        )
                    }
                )

        if self.package_number is not None and self.package_number <= 0:
            raise ValidationError(
                {"package_number": ("El número de paquete debe ser mayor a cero.")}
            )

        dimensions = {
            "weight_kg": self.weight_kg,
            "length_cm": self.length_cm,
            "width_cm": self.width_cm,
            "height_cm": self.height_cm,
        }

        errors = {}

        for field_name, value in dimensions.items():
            if value is not None and value <= 0:
                errors[field_name] = "El valor debe ser mayor a cero."

        if errors:
            raise ValidationError(errors)

        if self.tracking_number:
            duplicate = ShipmentPackage.objects.filter(
                company_id=self.company_id,
                tracking_number__iexact=self.tracking_number.strip(),
            ).exclude(pk=self.pk).exists()
            if duplicate:
                raise ValidationError(
                    {"tracking_number": "Este rastreo ya está asignado a otro bulto de la empresa."}
                )

    def __str__(self):
        return (
            f"Paquete {self.package_number} - "
            f"{self.tracking_number or 'Sin tracking'}"
        )


class LocalDeliveryDriver(CompanyScopedModel):
    """Chofer de reparto propio, aislado por empresa."""

    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    employee_number = models.CharField(max_length=60, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "employee_number"],
                condition=~Q(employee_number=""),
                name="uniq_driver_employee_company",
            )
        ]

    def __str__(self):
        return self.name


class LocalDeliveryVehicle(CompanyScopedModel):
    """Vehículo que pertenece a la flotilla de una sola empresa."""

    name = models.CharField(max_length=120)
    plate = models.CharField(max_length=30)
    vehicle_type = models.CharField(max_length=80, blank=True)
    max_weight_kg = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "plate"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "plate"], name="uniq_vehicle_plate_company"
            )
        ]

    def __str__(self):
        return f"{self.name} · {self.plate}"


class LocalDeliveryRoute(CompanyScopedModel):
    """Ruta programada para una bodega concreta."""

    STATUS_PLANNED = "planned"
    STATUS_LOADING = "loading"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PLANNED, "Programada"),
        (STATUS_LOADING, "Cargando"),
        (STATUS_IN_PROGRESS, "En ruta"),
        (STATUS_COMPLETED, "Completada"),
        (STATUS_CANCELLED, "Cancelada"),
    ]

    warehouse = models.ForeignKey(
        "inventory.Warehouse", on_delete=models.PROTECT, related_name="local_routes"
    )
    name = models.CharField(max_length=150)
    scheduled_date = models.DateField(db_index=True)
    driver = models.ForeignKey(
        LocalDeliveryDriver, on_delete=models.PROTECT, related_name="routes"
    )
    vehicle = models.ForeignKey(
        LocalDeliveryVehicle, on_delete=models.PROTECT, related_name="routes"
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PLANNED, db_index=True
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_local_delivery_routes",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["scheduled_date", "name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "warehouse", "scheduled_date", "name"],
                name="uniq_local_route_day_name",
            )
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.warehouse_id and self.warehouse.company_id != self.company_id:
            errors["warehouse"] = "La bodega de la ruta pertenece a otra empresa."
        if self.driver_id and self.driver.company_id != self.company_id:
            errors["driver"] = "El chofer pertenece a otra empresa."
        if self.vehicle_id and self.vehicle.company_id != self.company_id:
            errors["vehicle"] = "El vehículo pertenece a otra empresa."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.name} · {self.scheduled_date}"


class LocalDeliveryAssignment(CompanyScopedModel):
    """Asignación única del envío a una ruta de reparto propio."""

    STATUS_READY = "ready"
    STATUS_LOADED = "loaded"
    STATUS_IN_TRANSIT = "in_transit"
    STATUS_FAILED_ATTEMPT = "failed_attempt"
    STATUS_DELIVERED = "delivered"
    STATUS_CHOICES = [
        (STATUS_READY, "Listo para carga"),
        (STATUS_LOADED, "Cargado"),
        (STATUS_IN_TRANSIT, "En ruta"),
        (STATUS_FAILED_ATTEMPT, "Intento fallido"),
        (STATUS_DELIVERED, "Entregado"),
    ]

    carrier_shipment = models.OneToOneField(
        CarrierShipment, on_delete=models.CASCADE, related_name="local_assignment"
    )
    route = models.ForeignKey(
        LocalDeliveryRoute, on_delete=models.PROTECT, related_name="assignments"
    )
    stop_order = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=25, choices=STATUS_CHOICES, default=STATUS_READY, db_index=True
    )
    loaded_at = models.DateTimeField(null=True, blank=True)
    departed_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    failed_attempts = models.PositiveIntegerField(default=0)
    last_failure_reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["route", "stop_order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(stop_order__gt=0), name="local_stop_order_positive"
            )
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.carrier_shipment_id:
            if self.carrier_shipment.company_id != self.company_id:
                errors["carrier_shipment"] = "El envío pertenece a otra empresa."
            if self.carrier_shipment.source != CarrierShipment.SOURCE_LOCAL:
                errors["carrier_shipment"] = "La asignación requiere entrega propia."
        if self.route_id and self.route.company_id != self.company_id:
            errors["route"] = "La ruta pertenece a otra empresa."
        if errors:
            raise ValidationError(errors)


class CustomCarrier(CompanyScopedModel):
    """Transportista regional o personalizado sin integración API."""

    name = models.CharField(max_length=150)
    contact_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    tracking_url_template = models.URLField(max_length=1000, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "name"], name="uniq_custom_carrier_company"
            )
        ]

    def __str__(self):
        return self.name


class ShippingAuditEvent(CompanyScopedModel):
    """Bitácora inmutable de acciones sensibles del Shipping Hub."""

    shipping_order = models.ForeignKey(
        "warehouse.ShippingOrder",
        on_delete=models.CASCADE,
        related_name="shipping_hub_audit_events",
    )
    carrier_shipment = models.ForeignKey(
        CarrierShipment,
        on_delete=models.SET_NULL,
        related_name="audit_events",
        null=True,
        blank=True,
    )

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="shipping_hub_audit_events",
        null=True,
        blank=True,
    )
    action = models.CharField(max_length=60, db_index=True)
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["company", "shipping_order", "created_at"],
                name="shipaudit_comp_order_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if self.shipping_order_id and self.shipping_order.company_id != self.company_id:
            raise ValidationError(
                {"shipping_order": "El evento y el envío pertenecen a empresas diferentes."}
            )
        if self.carrier_shipment_id:
            if self.carrier_shipment.company_id != self.company_id:
                raise ValidationError(
                    {"carrier_shipment": "La guía pertenece a otra empresa."}
                )
            if self.carrier_shipment.shipping_order_id != self.shipping_order_id:
                raise ValidationError(
                    {"carrier_shipment": "La guía no corresponde a este envío."}
                )

    def __str__(self):
        return f"{self.action} - envío {self.shipping_order_id}"


class ShippingPrintJob(CompanyScopedModel):
    """Trabajo inmutable que agrupa una impresión individual o masiva."""

    STATUS_CREATED = "created"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_CREATED, "Creado"),
        (STATUS_COMPLETED, "Completado"),
        (STATUS_CANCELLED, "Cancelado"),
    ]

    MODE_SINGLE = "single"
    MODE_BATCH = "batch"
    MODE_CHOICES = [(MODE_SINGLE, "Individual"), (MODE_BATCH, "Masiva")]

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="shipping_print_jobs",
        null=True,
        blank=True,
    )
    mode = models.CharField(max_length=10, choices=MODE_CHOICES)
    status = models.CharField(
        max_length=15, choices=STATUS_CHOICES, default=STATUS_CREATED, db_index=True
    )
    warehouse_id_snapshot = models.PositiveBigIntegerField(db_index=True)
    reason = models.CharField(max_length=250, blank=True)
    item_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["company", "warehouse_id_snapshot", "created_at"],
                name="printjob_comp_wh_idx",
            )
        ]

    def clean(self):
        super().clean()
        if self.item_count < 0:
            raise ValidationError({"item_count": "La cantidad no puede ser negativa."})

    def __str__(self):
        return f"Impresión {self.pk or 'nueva'} ({self.item_count})"


class ShippingPrintItem(CompanyScopedModel):
    """Evidencia por bulto, incluso para reimpresiones."""

    job = models.ForeignKey(
        ShippingPrintJob, on_delete=models.CASCADE, related_name="items"
    )
    package = models.ForeignKey(
        ShipmentPackage, on_delete=models.PROTECT, related_name="print_items"
    )
    carrier_shipment = models.ForeignKey(
        CarrierShipment, on_delete=models.PROTECT, related_name="print_items"
    )
    sequence = models.PositiveIntegerField()
    label_format_snapshot = models.CharField(max_length=10)
    source_snapshot = models.CharField(max_length=20)
    was_reprint = models.BooleanField(default=False)
    previous_print_count = models.PositiveIntegerField(default=0)
    printed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "package"], name="uniq_print_job_package"
            ),
            models.CheckConstraint(
                condition=Q(sequence__gt=0), name="print_item_sequence_positive"
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.job_id and self.job.company_id != self.company_id:
            errors["job"] = "El trabajo de impresión pertenece a otra empresa."
        if self.package_id and self.package.company_id != self.company_id:
            errors["package"] = "El bulto pertenece a otra empresa."
        if self.carrier_shipment_id:
            if self.carrier_shipment.company_id != self.company_id:
                errors["carrier_shipment"] = "El envío pertenece a otra empresa."
            if self.package_id and self.package.carrier_shipment_id != self.carrier_shipment_id:
                errors["package"] = "El bulto no corresponde al envío."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.job_id} - bulto {self.package_id}"


# ============================================================
# EVENTOS DE TRACKING
# ============================================================


class ShipmentTrackingEvent(CompanyScopedModel):
    carrier_shipment = models.ForeignKey(
        CarrierShipment,
        on_delete=models.CASCADE,
        related_name="tracking_events",
    )

    package = models.ForeignKey(
        ShipmentPackage,
        on_delete=models.SET_NULL,
        related_name="tracking_events",
        null=True,
        blank=True,
    )

    provider_event_id = models.CharField(
        max_length=200,
        blank=True,
    )

    event_code = models.CharField(
        max_length=100,
    )

    event_name = models.CharField(
        max_length=150,
    )

    description = models.TextField(
        blank=True,
    )

    location = models.CharField(
        max_length=250,
        blank=True,
    )

    occurred_at = models.DateTimeField()

    raw_data = models.JSONField(
        default=dict,
        blank=True,
    )

    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "company",
                    "carrier_shipment",
                    "provider_event_id",
                ],
                condition=~Q(provider_event_id=""),
                name="uniq_provider_tracking_evt",
            )
        ]
        indexes = [
            models.Index(
                fields=["company", "carrier_shipment"],
                name="tevt_comp_cship_idx",
            ),
            models.Index(
                fields=["company", "occurred_at"],
                name="tevt_comp_date_idx",
            ),
        ]

    def clean(self):
        super().clean()

        if self.carrier_shipment_id:
            if self.carrier_shipment.company_id != self.company_id:
                raise ValidationError(
                    {
                        "carrier_shipment": (
                            "El evento y la guía pertenecen " "a empresas diferentes."
                        )
                    }
                )

        if self.package_id:
            if self.package.carrier_shipment_id != self.carrier_shipment_id:
                raise ValidationError(
                    {"package": ("El paquete no pertenece a esta guía.")}
                )

            if self.package.company_id != self.company_id:
                raise ValidationError(
                    {
                        "package": (
                            "El paquete y el evento pertenecen "
                            "a empresas diferentes."
                        )
                    }
                )

    def __str__(self):
        return f"{self.event_name} - {self.occurred_at}"


class TrackingWebhookReceipt(CompanyScopedModel):
    """Sobre de auditoría idempotente para webhooks logísticos."""

    STATUS_RECEIVED = "received"
    STATUS_PROCESSED = "processed"
    STATUS_IGNORED = "ignored"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_RECEIVED, "Recibido"),
        (STATUS_PROCESSED, "Procesado"),
        (STATUS_IGNORED, "Ignorado"),
        (STATUS_FAILED, "Fallido"),
    ]

    integration = models.ForeignKey(
        CompanyShippingIntegration,
        on_delete=models.PROTECT,
        related_name="tracking_webhook_receipts",
    )
    carrier_shipment = models.ForeignKey(
        CarrierShipment,
        on_delete=models.SET_NULL,
        related_name="tracking_webhook_receipts",
        null=True,
        blank=True,
    )
    provider = models.CharField(max_length=30, db_index=True)
    provider_event_id = models.CharField(max_length=200)
    payload_sha256 = models.CharField(max_length=64)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_RECEIVED, db_index=True
    )
    error_code = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["integration", "provider_event_id"],
                name="uniq_tracking_hook_integration_event",
            )
        ]
        indexes = [
            models.Index(fields=["company", "status"], name="thook_comp_status_idx"),
            models.Index(fields=["company", "received_at"], name="thook_comp_date_idx"),
        ]

    def clean(self):
        super().clean()
        if self.integration_id and self.integration.company_id != self.company_id:
            raise ValidationError({"integration": "La integración pertenece a otra empresa."})
        if self.carrier_shipment_id and self.carrier_shipment.company_id != self.company_id:
            raise ValidationError({"carrier_shipment": "El envío pertenece a otra empresa."})


class TrackingSyncAttempt(CompanyScopedModel):
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_SKIPPED = "skipped"
    STATUS_CHOICES = [
        (STATUS_COMPLETED, "Completada"),
        (STATUS_FAILED, "Fallida"),
        (STATUS_SKIPPED, "Omitida"),
    ]

    carrier_shipment = models.ForeignKey(
        CarrierShipment, on_delete=models.CASCADE, related_name="tracking_sync_attempts"
    )
    integration = models.ForeignKey(
        CompanyShippingIntegration,
        on_delete=models.PROTECT,
        related_name="tracking_sync_attempts",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="requested_tracking_syncs",
        null=True,
        blank=True,
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, db_index=True)
    events_created = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["company", "started_at"], name="tsync_comp_date_idx")
        ]

    def clean(self):
        super().clean()
        if self.carrier_shipment_id and self.carrier_shipment.company_id != self.company_id:
            raise ValidationError({"carrier_shipment": "El envío pertenece a otra empresa."})
        if self.integration_id and self.integration.company_id != self.company_id:
            raise ValidationError({"integration": "La integración pertenece a otra empresa."})


# ============================================================
# DIRECCIÓN LOGÍSTICA DE ORIGEN POR BODEGA
# ============================================================


class ShippingOriginAddress(CompanyScopedModel):
    warehouse = models.OneToOneField(
        "inventory.Warehouse",
        on_delete=models.CASCADE,
        related_name="shipping_origin_address",
    )

    contact_name = models.CharField(
        "Persona de contacto",
        max_length=150,
    )

    company_name = models.CharField(
        "Empresa remitente",
        max_length=200,
    )

    phone = models.CharField(
        "Teléfono",
        max_length=30,
    )

    email = models.EmailField(
        "Correo electrónico",
        blank=True,
    )

    street = models.CharField(
        "Calle",
        max_length=200,
    )

    exterior_number = models.CharField(
        "Número exterior",
        max_length=50,
    )

    interior_number = models.CharField(
        "Número interior",
        max_length=50,
        blank=True,
    )

    neighborhood = models.CharField(
        "Colonia",
        max_length=150,
    )

    city = models.CharField(
        "Ciudad",
        max_length=150,
    )

    state = models.CharField(
        "Estado",
        max_length=150,
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
    )

    zip_code = models.CharField(
        "Código postal",
        max_length=20,
    )

    reference = models.TextField(
        "Referencias",
        blank=True,
    )

    is_active = models.BooleanField(
        "Activa",
        default=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        verbose_name = "Dirección logística de origen"
        verbose_name_plural = "Direcciones logísticas de origen"

        ordering = [
            "warehouse__name",
        ]

        indexes = [
            models.Index(
                fields=[
                    "company",
                    "is_active",
                ],
                name="origin_comp_active_idx",
            ),
        ]

    def clean(self):
        super().clean()

        # Validación multiempresa.
        # Solo se ejecuta cuando ambos IDs ya están asignados.
        if self.warehouse_id and self.company_id:
            if self.warehouse.company_id != self.company_id:
                raise ValidationError(
                    {
                        "warehouse": (
                            "La bodega seleccionada pertenece " "a otra empresa."
                        )
                    }
                )

        self.contact_name = (self.contact_name or "").strip()

        self.company_name = (self.company_name or "").strip()

        self.phone = (self.phone or "").strip()

        self.street = (self.street or "").strip()

        self.exterior_number = (self.exterior_number or "").strip()

        self.interior_number = (self.interior_number or "").strip()

        self.neighborhood = (self.neighborhood or "").strip()

        self.city = (self.city or "").strip()

        self.state = (self.state or "").strip()

        self.country = (self.country or "México").strip()

        self.country_code = (self.country_code or "MX").strip().upper()

        self.zip_code = (self.zip_code or "").strip()

        if len(self.country_code) != 2:
            raise ValidationError(
                {
                    "country_code": (
                        "El código de país debe tener dos letras. " "Ejemplo: MX."
                    )
                }
            )

        if self.country_code == "MX" and (
            not self.zip_code.isdigit() or len(self.zip_code) != 5
        ):
            raise ValidationError(
                {
                    "zip_code": (
                        "El código postal de México debe tener "
                        "exactamente 5 números."
                    )
                }
            )

        normalized_phone = (
            self.phone.replace(" ", "")
            .replace("-", "")
            .replace("(", "")
            .replace(")", "")
        )

        if len(normalized_phone) < 10:
            raise ValidationError(
                {"phone": ("Escribe un teléfono válido de al menos " "10 dígitos.")}
            )

        self.phone = normalized_phone

    @property
    def complete_street(self):
        parts = [
            self.street,
            self.exterior_number,
        ]

        if self.interior_number:
            parts.append(f"Int. {self.interior_number}")

        return " ".join(str(part).strip() for part in parts if part)

    def __str__(self):
        return f"{self.warehouse.name} - " f"{self.city}, {self.state}"


class DeliveryEvidence(CompanyScopedModel):
    RESULT_DELIVERED, RESULT_PARTIAL, RESULT_REJECTED = "delivered", "partial", "rejected"
    RESULT_CHOICES = [(RESULT_DELIVERED, "Entrega completa"), (RESULT_PARTIAL, "Entrega parcial"), (RESULT_REJECTED, "Entrega rechazada")]
    carrier_shipment = models.ForeignKey(CarrierShipment, on_delete=models.PROTECT, related_name="delivery_evidences")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="shipping_delivery_evidences")
    result = models.CharField(max_length=20, choices=RESULT_CHOICES, db_index=True)
    receiver_name = models.CharField(max_length=180, blank=True)
    receiver_relationship = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    gps_accuracy_m = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)
    location_consent = models.BooleanField(default=False)
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    recorded_at = models.DateTimeField(auto_now_add=True, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="shipping_delivery_evidences_created", null=True, blank=True)
    class Meta:
        ordering = ["-recorded_at", "-pk"]
        indexes = [models.Index(fields=["company", "warehouse", "recorded_at"], name="devid_comp_wh_date_idx"), models.Index(fields=["company", "carrier_shipment", "result"], name="devid_comp_ship_res_idx")]
    def clean(self):
        super().clean(); errors = {}
        if self.carrier_shipment_id:
            if self.carrier_shipment.company_id != self.company_id: errors["carrier_shipment"] = "El envío pertenece a otra empresa."
            if self.warehouse_id and self.carrier_shipment.shipping_order.packing_order.picking_order.warehouse_id != self.warehouse_id: errors["warehouse"] = "La evidencia no corresponde a la bodega del envío."
        if self.warehouse_id and self.warehouse.company_id != self.company_id: errors["warehouse"] = "La bodega pertenece a otra empresa."
        if (self.latitude is None) != (self.longitude is None): errors["latitude"] = "La latitud y longitud deben registrarse juntas."
        if self.latitude is not None and not self.location_consent: errors["location_consent"] = "Se requiere consentimiento para guardar GPS."
        if self.result == self.RESULT_DELIVERED and not self.receiver_name.strip(): errors["receiver_name"] = "Indica quién recibió la entrega."
        if self.result == self.RESULT_REJECTED and not self.notes.strip(): errors["notes"] = "Explica el motivo del rechazo."
        if errors: raise ValidationError(errors)


class DeliveryEvidencePackage(CompanyScopedModel):
    evidence = models.ForeignKey(DeliveryEvidence, on_delete=models.CASCADE, related_name="package_items")
    package = models.ForeignKey(ShipmentPackage, on_delete=models.PROTECT, related_name="delivery_evidence_items")
    class Meta: constraints = [models.UniqueConstraint(fields=["evidence", "package"], name="uniq_delivery_evidence_package")]
    def clean(self):
        super().clean(); errors = {}
        if self.evidence_id and self.evidence.company_id != self.company_id: errors["evidence"] = "La evidencia pertenece a otra empresa."
        if self.package_id and self.package.company_id != self.company_id: errors["package"] = "El bulto pertenece a otra empresa."
        if self.package_id and self.evidence_id and self.package.carrier_shipment_id != self.evidence.carrier_shipment_id: errors["package"] = "El bulto no pertenece al envío de la evidencia."
        if errors: raise ValidationError(errors)


class DeliveryEvidenceAsset(CompanyScopedModel):
    TYPE_PHOTO, TYPE_SIGNATURE, TYPE_DOCUMENT = "photo", "signature", "document"
    TYPE_CHOICES = [(TYPE_PHOTO, "Fotografía"), (TYPE_SIGNATURE, "Firma"), (TYPE_DOCUMENT, "Documento")]
    evidence = models.ForeignKey(DeliveryEvidence, on_delete=models.CASCADE, related_name="assets")
    asset_type = models.CharField(max_length=20, choices=TYPE_CHOICES, db_index=True)
    file = models.FileField(upload_to="private/delivery_evidence/%Y/%m/")
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size_bytes = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [models.UniqueConstraint(fields=["evidence", "sha256"], name="uniq_delivery_evidence_asset_hash")]
        indexes = [models.Index(fields=["company", "evidence", "asset_type"], name="devas_comp_ev_type_idx")]
    def clean(self):
        super().clean()
        if self.evidence_id and self.evidence.company_id != self.company_id: raise ValidationError({"evidence": "La evidencia pertenece a otra empresa."})


# ============================================================
# FASE 9 · INCIDENCIAS, DEVOLUCIONES Y CONCILIACIÓN
# ============================================================

class LogisticsIncident(CompanyScopedModel):
    TYPE_LOST, TYPE_DAMAGED, TYPE_HELD = "lost", "damaged", "held"
    TYPE_FAILED_DELIVERY, TYPE_DELAY, TYPE_OTHER = "failed_delivery", "delay", "other"
    TYPE_CHOICES = [
        (TYPE_LOST, "Extraviado"), (TYPE_DAMAGED, "Dañado"),
        (TYPE_HELD, "Retenido"), (TYPE_FAILED_DELIVERY, "Entrega fallida"),
        (TYPE_DELAY, "Retraso"), (TYPE_OTHER, "Otro"),
    ]
    STATUS_OPEN, STATUS_INVESTIGATING = "open", "investigating"
    STATUS_ESCALATED, STATUS_RESOLVED, STATUS_CANCELLED = "escalated", "resolved", "cancelled"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Abierta"), (STATUS_INVESTIGATING, "En investigación"),
        (STATUS_ESCALATED, "Escalada"), (STATUS_RESOLVED, "Resuelta"),
        (STATUS_CANCELLED, "Cancelada"),
    ]
    SEVERITY_LOW, SEVERITY_MEDIUM, SEVERITY_HIGH, SEVERITY_CRITICAL = "low", "medium", "high", "critical"
    SEVERITY_CHOICES = [(SEVERITY_LOW, "Baja"), (SEVERITY_MEDIUM, "Media"), (SEVERITY_HIGH, "Alta"), (SEVERITY_CRITICAL, "Crítica")]

    carrier_shipment = models.ForeignKey(CarrierShipment, on_delete=models.PROTECT, related_name="logistics_incidents")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="shipping_incidents")
    package = models.ForeignKey(ShipmentPackage, on_delete=models.PROTECT, related_name="logistics_incidents", null=True, blank=True)
    incident_type = models.CharField(max_length=30, choices=TYPE_CHOICES, db_index=True)
    severity = models.CharField(max_length=12, choices=SEVERITY_CHOICES, default=SEVERITY_MEDIUM, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)
    title = models.CharField(max_length=180)
    description = models.TextField()
    provider_reference = models.CharField(max_length=200, blank=True)
    resolution = models.TextField(blank=True)
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="shipping_incidents_opened", null=True, blank=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="shipping_incidents_assigned", null=True, blank=True)
    opened_at = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-opened_at", "-pk"]
        indexes = [
            models.Index(fields=["company", "warehouse", "status"], name="linc_comp_wh_status_idx"),
            models.Index(fields=["company", "carrier_shipment", "status"], name="linc_comp_ship_stat_idx"),
        ]

    def clean(self):
        super().clean(); errors = {}
        if self.carrier_shipment_id and self.carrier_shipment.company_id != self.company_id: errors["carrier_shipment"] = "El envío pertenece a otra empresa."
        if self.warehouse_id and self.warehouse.company_id != self.company_id: errors["warehouse"] = "La bodega pertenece a otra empresa."
        if self.carrier_shipment_id and self.warehouse_id and self.carrier_shipment.shipping_order.packing_order.picking_order.warehouse_id != self.warehouse_id: errors["warehouse"] = "La incidencia no corresponde a la bodega del envío."
        if self.package_id and (self.package.company_id != self.company_id or self.package.carrier_shipment_id != self.carrier_shipment_id): errors["package"] = "El bulto no pertenece a este envío y empresa."
        if self.status == self.STATUS_RESOLVED and not self.resolution.strip(): errors["resolution"] = "Indica cómo se resolvió la incidencia."
        if errors: raise ValidationError(errors)


class LogisticsIncidentEvent(CompanyScopedModel):
    incident = models.ForeignKey(LogisticsIncident, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=40)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="shipping_incident_events", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    class Meta: ordering = ["-created_at", "-pk"]
    def clean(self):
        super().clean()
        if self.incident_id and self.incident.company_id != self.company_id: raise ValidationError({"incident": "La incidencia pertenece a otra empresa."})


class IncidentExpenseReceipt(CompanyScopedModel):
    """
    Comprobante de gasto reportado en sitio (ej. Héctor paga una guía
    nueva en el mostrador de la paquetería). Requiere evidencia
    fotográfica obligatoria y queda pendiente de conciliar por finanzas
    hasta que alguien lo revise.
    """

    STATUS_PENDING = "pending_reconciliation"
    STATUS_RECONCILED = "reconciled"
    STATUS_DISPUTED = "disputed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pendiente de conciliar"),
        (STATUS_RECONCILED, "Conciliado"),
        (STATUS_DISPUTED, "En disputa"),
    ]

    incident = models.ForeignKey(
        LogisticsIncident,
        on_delete=models.PROTECT,
        related_name="expense_receipts",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default="MXN")
    carrier_name = models.CharField(max_length=150, blank=True)
    new_tracking_number = models.CharField(max_length=200, blank=True)

    receipt_file = models.FileField(
        upload_to="private/incident_expense_receipts/%Y/%m/"
    )
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size_bytes = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)

    status = models.CharField(
        max_length=25,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="incident_expense_receipts_reported",
        null=True,
        blank=True,
    )
    reconciled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="incident_expense_receipts_reconciled",
        null=True,
        blank=True,
    )
    reconciliation_notes = models.TextField(blank=True)
    reported_at = models.DateTimeField(auto_now_add=True, db_index=True)
    reconciled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-reported_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0), name="expense_amount_positive"
            ),
        ]
        indexes = [
            models.Index(fields=["company", "status"], name="iexp_comp_status_idx"),
            models.Index(fields=["company", "incident"], name="iexp_comp_incident_idx"),
        ]

    def clean(self):
        super().clean()
        if self.incident_id and self.incident.company_id != self.company_id:
            raise ValidationError(
                {"incident": "La incidencia pertenece a otra empresa."}
            )

    def __str__(self):
        return f"Gasto ${self.amount} · incidencia {self.incident_id}"


class ShipmentReturn(CompanyScopedModel):
    TYPE_TOTAL, TYPE_PARTIAL = "total", "partial"
    TYPE_CHOICES = [(TYPE_TOTAL, "Devolución total"), (TYPE_PARTIAL, "Devolución parcial")]
    STATUS_REQUESTED, STATUS_AUTHORIZED, STATUS_IN_TRANSIT = "requested", "authorized", "in_transit"
    STATUS_RECEIVED, STATUS_REJECTED, STATUS_CANCELLED = "received", "rejected", "cancelled"
    STATUS_CHOICES = [(STATUS_REQUESTED, "Solicitada"), (STATUS_AUTHORIZED, "Autorizada"), (STATUS_IN_TRANSIT, "En tránsito"), (STATUS_RECEIVED, "Recibida"), (STATUS_REJECTED, "Rechazada"), (STATUS_CANCELLED, "Cancelada")]
    carrier_shipment = models.ForeignKey(CarrierShipment, on_delete=models.PROTECT, related_name="returns")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="shipping_returns")
    incident = models.ForeignKey(LogisticsIncident, on_delete=models.SET_NULL, related_name="returns", null=True, blank=True)
    return_type = models.CharField(max_length=12, choices=TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_REQUESTED, db_index=True)
    reason = models.TextField()
    package_ids = models.JSONField(default=list, blank=True)
    provider_reference = models.CharField(max_length=200, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="shipping_returns_requested", null=True, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True, db_index=True)
    received_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ["-requested_at", "-pk"]
        indexes = [models.Index(fields=["company", "warehouse", "status"], name="sret_comp_wh_status_idx")]
    def clean(self):
        super().clean(); errors = {}
        if self.carrier_shipment_id and self.carrier_shipment.company_id != self.company_id: errors["carrier_shipment"] = "El envío pertenece a otra empresa."
        if self.warehouse_id and self.warehouse.company_id != self.company_id: errors["warehouse"] = "La bodega pertenece a otra empresa."
        if self.incident_id and (self.incident.company_id != self.company_id or self.incident.carrier_shipment_id != self.carrier_shipment_id): errors["incident"] = "La incidencia no corresponde al envío."
        if self.return_type == self.TYPE_PARTIAL and not self.package_ids: errors["package_ids"] = "Selecciona al menos un bulto para la devolución parcial."
        if errors: raise ValidationError(errors)


class DeliveryRetry(CompanyScopedModel):
    STATUS_SCHEDULED, STATUS_DISPATCHED, STATUS_DELIVERED, STATUS_FAILED, STATUS_CANCELLED = "scheduled", "dispatched", "delivered", "failed", "cancelled"
    STATUS_CHOICES = [(STATUS_SCHEDULED, "Programado"), (STATUS_DISPATCHED, "En reparto"), (STATUS_DELIVERED, "Entregado"), (STATUS_FAILED, "Fallido"), (STATUS_CANCELLED, "Cancelado")]
    carrier_shipment = models.ForeignKey(CarrierShipment, on_delete=models.PROTECT, related_name="delivery_retries")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="shipping_delivery_retries")
    incident = models.ForeignKey(LogisticsIncident, on_delete=models.PROTECT, related_name="delivery_retries")
    attempt_number = models.PositiveIntegerField()
    scheduled_for = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_SCHEDULED, db_index=True)
    instructions = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="shipping_delivery_retries_created", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ["-scheduled_for", "-pk"]
        constraints = [models.UniqueConstraint(fields=["carrier_shipment", "attempt_number"], name="uniq_delivery_retry_attempt")]
    def clean(self):
        super().clean(); errors = {}
        if self.carrier_shipment_id and self.carrier_shipment.company_id != self.company_id: errors["carrier_shipment"] = "El envío pertenece a otra empresa."
        if self.incident_id and (self.incident.company_id != self.company_id or self.incident.carrier_shipment_id != self.carrier_shipment_id): errors["incident"] = "La incidencia no corresponde al envío."
        if self.warehouse_id and self.warehouse.company_id != self.company_id: errors["warehouse"] = "La bodega pertenece a otra empresa."
        if errors: raise ValidationError(errors)


class ShippingReconciliation(CompanyScopedModel):
    STATUS_PENDING, STATUS_MATCHED, STATUS_MISMATCH, STATUS_RESOLVED = "pending", "matched", "mismatch", "resolved"
    STATUS_CHOICES = [(STATUS_PENDING, "Pendiente"), (STATUS_MATCHED, "Conciliado"), (STATUS_MISMATCH, "Con diferencia"), (STATUS_RESOLVED, "Resuelto")]
    carrier_shipment = models.ForeignKey(CarrierShipment, on_delete=models.PROTECT, related_name="reconciliations")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="shipping_reconciliations")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    expected_status = models.CharField(max_length=30, blank=True)
    provider_status = models.CharField(max_length=100, blank=True)
    expected_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    provider_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=10, default="MXN")
    differences = models.JSONField(default=dict, blank=True)
    resolution_notes = models.TextField(blank=True)
    checked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="shipping_reconciliations_checked", null=True, blank=True)
    checked_at = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        ordering = ["-checked_at", "-pk"]
        indexes = [models.Index(fields=["company", "warehouse", "status"], name="srec_comp_wh_status_idx")]
    def clean(self):
        super().clean(); errors = {}
        if self.carrier_shipment_id and self.carrier_shipment.company_id != self.company_id: errors["carrier_shipment"] = "El envío pertenece a otra empresa."
        if self.warehouse_id and self.warehouse.company_id != self.company_id: errors["warehouse"] = "La bodega pertenece a otra empresa."
        if self.status == self.STATUS_RESOLVED and not self.resolution_notes.strip(): errors["resolution_notes"] = "Indica cómo se resolvió la diferencia."
        if errors: raise ValidationError(errors)


# ============================================================
# SHIPPING CORE ENTERPRISE — FASE 2
# Operaciones de compra separadas del envío logístico.
# ============================================================


class ShippingPurchase(CompanyScopedModel):
    STATUS_CREATED = "created"
    STATUS_QUEUED = "queued"
    STATUS_SENDING = "sending"
    STATUS_PROVIDER_PENDING = "provider_pending"
    STATUS_RECONCILING = "reconciling"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED_FINAL = "failed_final"
    STATUS_CANCELLED = "cancelled"
    STATUS_MANUAL_REVIEW = "manual_review"

    STATUS_CHOICES = [
        (STATUS_CREATED, "Creada"),
        (STATUS_QUEUED, "En cola"),
        (STATUS_SENDING, "Enviando al proveedor"),
        (STATUS_PROVIDER_PENDING, "Pendiente del proveedor"),
        (STATUS_RECONCILING, "Conciliando"),
        (STATUS_COMPLETED, "Completada"),
        (STATUS_FAILED_FINAL, "Fallo definitivo"),
        (STATUS_CANCELLED, "Cancelada"),
        (STATUS_MANUAL_REVIEW, "Revisión manual"),
    ]

    CHARGE_UNKNOWN = "unknown"
    CHARGE_NOT_CHARGED = "not_charged"
    CHARGE_POSSIBLE = "possible"
    CHARGE_CONFIRMED = "confirmed"
    CHARGE_REFUNDED = "refunded"
    CHARGE_CHOICES = [
        (CHARGE_UNKNOWN, "Desconocido"),
        (CHARGE_NOT_CHARGED, "No cobrado"),
        (CHARGE_POSSIBLE, "Posible cobro"),
        (CHARGE_CONFIRMED, "Cobro confirmado"),
        (CHARGE_REFUNDED, "Reembolsado"),
    ]

    carrier_shipment = models.ForeignKey(
        CarrierShipment, on_delete=models.PROTECT, related_name="purchases"
    )
    warehouse = models.ForeignKey(
        "inventory.Warehouse", on_delete=models.PROTECT, related_name="shipping_purchases"
    )
    integration = models.ForeignKey(
        CompanyShippingIntegration, on_delete=models.PROTECT, related_name="purchases"
    )
    selected_quote = models.ForeignKey(
        ShippingQuote, on_delete=models.PROTECT, related_name="purchases"
    )
    generation = models.PositiveIntegerField(default=1)
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    provider = models.CharField(max_length=30, db_index=True)
    environment = models.CharField(max_length=20, blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_CREATED, db_index=True)
    charge_status = models.CharField(max_length=20, choices=CHARGE_CHOICES, default=CHARGE_UNKNOWN, db_index=True)
    provider_request_id = models.CharField(max_length=200, blank=True, db_index=True)
    provider_purchase_id = models.CharField(max_length=200, blank=True, db_index=True)
    provider_reference = models.CharField(max_length=200, blank=True, db_index=True)
    quoted_amount = models.DecimalField(max_digits=12, decimal_places=2)
    charged_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=10, default="MXN")
    attempt_count = models.PositiveIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_error_code = models.CharField(max_length=100, blank=True)
    last_error_message = models.TextField(blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="shipping_purchases_requested", null=True, blank=True
    )
    requested_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    request_snapshot = models.JSONField(default=dict, blank=True)
    queued_at = models.DateTimeField(null=True, blank=True, db_index=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-requested_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "carrier_shipment", "generation"],
                name="uniq_ship_purchase_generation",
            ),
            models.UniqueConstraint(
                fields=["company", "carrier_shipment"],
                condition=Q(status__in=["created", "queued", "sending", "provider_pending", "reconciling", "manual_review"]),
                name="uniq_open_ship_purchase",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "warehouse", "status"], name="spurch_comp_wh_stat_idx"),
            models.Index(fields=["provider", "status", "next_retry_at"], name="spurch_prov_retry_idx"),
            models.Index(fields=["company", "carrier_shipment"], name="spurch_comp_ship_idx"),
            models.Index(fields=["status", "queued_at", "id"], name="spurch_queue_order_idx"),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.carrier_shipment_id and self.carrier_shipment.company_id != self.company_id:
            errors["carrier_shipment"] = "El envío pertenece a otra empresa."
        if self.warehouse_id and self.warehouse.company_id != self.company_id:
            errors["warehouse"] = "La bodega pertenece a otra empresa."
        if self.integration_id and self.integration.company_id != self.company_id:
            errors["integration"] = "La integración pertenece a otra empresa."
        if self.selected_quote_id:
            if self.selected_quote.company_id != self.company_id:
                errors["selected_quote"] = "La cotización pertenece a otra empresa."
            elif self.selected_quote.shipping_order_id != self.carrier_shipment.shipping_order_id:
                errors["selected_quote"] = "La cotización no corresponde al envío."
        if self.integration_id and self.provider and self.integration.provider != self.provider:
            errors["provider"] = "El proveedor no coincide con la integración."
        if self.quoted_amount is not None and self.quoted_amount < 0:
            errors["quoted_amount"] = "El importe cotizado no puede ser negativo."
        if self.charged_amount is not None and self.charged_amount < 0:
            errors["charged_amount"] = "El importe cobrado no puede ser negativo."
        if errors:
            raise ValidationError(errors)


class ShippingPurchaseAttempt(CompanyScopedModel):
    OUTCOME_COMPLETED = "completed"
    OUTCOME_PENDING = "pending"
    OUTCOME_UNCERTAIN = "uncertain"
    OUTCOME_FAILED_FINAL = "failed_final"
    OUTCOME_CHOICES = [
        (OUTCOME_COMPLETED, "Completado"),
        (OUTCOME_PENDING, "Pendiente"),
        (OUTCOME_UNCERTAIN, "Resultado incierto"),
        (OUTCOME_FAILED_FINAL, "Fallo definitivo"),
    ]

    purchase = models.ForeignKey(ShippingPurchase, on_delete=models.CASCADE, related_name="attempts")
    attempt_number = models.PositiveIntegerField()
    correlation_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    outcome = models.CharField(max_length=20, choices=OUTCOME_CHOICES, db_index=True)
    http_status = models.PositiveIntegerField(null=True, blank=True)
    provider_request_id = models.CharField(max_length=200, blank=True)
    provider_purchase_id = models.CharField(max_length=200, blank=True)
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["purchase", "attempt_number"], name="uniq_ship_purchase_attempt"),
        ]
        indexes = [
            models.Index(fields=["company", "purchase", "outcome"], name="spatt_comp_pur_out_idx"),
        ]

    def clean(self):
        super().clean()
        if self.purchase_id and self.purchase.company_id != self.company_id:
            raise ValidationError({"purchase": "La compra pertenece a otra empresa."})


class ShippingPurchaseEvent(CompanyScopedModel):
    purchase = models.ForeignKey(ShippingPurchase, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=50, db_index=True)
    from_status = models.CharField(max_length=30, blank=True)
    to_status = models.CharField(max_length=30, blank=True)
    message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="shipping_purchase_events", null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["company", "purchase", "created_at"], name="spevt_comp_pur_date_idx")]

    def clean(self):
        super().clean()
        if self.purchase_id and self.purchase.company_id != self.company_id:
            raise ValidationError({"purchase": "La compra pertenece a otra empresa."})

# ============================================================
# EVENT STORE UNIVERSAL DE PROVEEDORES LOGÍSTICOS
# ============================================================


class ShippingProviderEvent(CompanyScopedModel):
    """
    Evento entrante inmutable de cualquier proveedor logístico.

    Este modelo almacena primero el webhook y lo procesa después.
    El request HTTP nunca modifica directamente compras, envíos o bultos.
    """

    STATUS_RECEIVED = "received"
    STATUS_PROCESSING = "processing"
    STATUS_PROCESSED = "processed"
    STATUS_RETRY = "retry"
    STATUS_DEAD_LETTER = "dead_letter"
    STATUS_IGNORED = "ignored"

    STATUS_CHOICES = [
        (STATUS_RECEIVED, "Recibido"),
        (STATUS_PROCESSING, "Procesando"),
        (STATUS_PROCESSED, "Procesado"),
        (STATUS_RETRY, "Reintento pendiente"),
        (STATUS_DEAD_LETTER, "Revisión manual"),
        (STATUS_IGNORED, "Ignorado"),
    ]

    SIGNATURE_VALID = "valid"
    SIGNATURE_INVALID = "invalid"
    SIGNATURE_NOT_PROVIDED = "not_provided"
    SIGNATURE_NOT_SUPPORTED = "not_supported"

    SIGNATURE_CHOICES = [
        (SIGNATURE_VALID, "Firma válida"),
        (SIGNATURE_INVALID, "Firma inválida"),
        (SIGNATURE_NOT_PROVIDED, "Firma no proporcionada"),
        (SIGNATURE_NOT_SUPPORTED, "Proveedor sin firma compatible"),
    ]

    provider = models.CharField(max_length=50, db_index=True)
    environment = models.CharField(max_length=20, default="production", db_index=True)

    provider_event_id = models.CharField(max_length=255)
    deduplication_key = models.CharField(max_length=64)
    event_type = models.CharField(max_length=100, blank=True, db_index=True)

    integration = models.ForeignKey(
        "shipping_hub.CompanyShippingIntegration",
        on_delete=models.SET_NULL,
        related_name="provider_events",
        null=True,
        blank=True,
    )
    purchase = models.ForeignKey(
        "shipping_hub.ShippingPurchase",
        on_delete=models.SET_NULL,
        related_name="provider_events",
        null=True,
        blank=True,
    )
    carrier_shipment = models.ForeignKey(
        "shipping_hub.CarrierShipment",
        on_delete=models.SET_NULL,
        related_name="provider_events",
        null=True,
        blank=True,
    )

    provider_purchase_id = models.CharField(max_length=200, blank=True, db_index=True)
    tracking_number = models.CharField(max_length=100, blank=True, db_index=True)

    payload = models.JSONField(default=dict, blank=True)
    sanitized_headers = models.JSONField(default=dict, blank=True)
    payload_sha256 = models.CharField(max_length=64)

    signature_status = models.CharField(
        max_length=20,
        choices=SIGNATURE_CHOICES,
        default=SIGNATURE_NOT_PROVIDED,
        db_index=True,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_RECEIVED,
        db_index=True,
    )
    processing_attempts = models.PositiveIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    error_code = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)

    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["received_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "company",
                    "provider",
                    "environment",
                    "provider_event_id",
                ],
                name="uniq_ship_provider_event",
            ),
            models.UniqueConstraint(
                fields=[
                    "company",
                    "provider",
                    "environment",
                    "deduplication_key",
                ],
                name="uniq_ship_provider_dedupe",
            ),
        ]
        indexes = [
            models.Index(
                fields=["status", "next_retry_at", "id"],
                name="spevent_retry_queue_idx",
            ),
            models.Index(
                fields=["company", "provider", "received_at"],
                name="spevent_comp_prov_date_idx",
            ),
            models.Index(
                fields=["provider", "provider_purchase_id"],
                name="spevent_prov_purchase_idx",
            ),
            models.Index(
                fields=["provider", "tracking_number"],
                name="spevent_prov_track_idx",
            ),
            models.Index(
                fields=["status", "lease_expires_at"],
                name="spevent_status_lease_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}

        if self.integration_id and self.integration.company_id != self.company_id:
            errors["integration"] = "La integración pertenece a otra empresa."

        if self.purchase_id and self.purchase.company_id != self.company_id:
            errors["purchase"] = "La compra pertenece a otra empresa."

        if (
            self.carrier_shipment_id
            and self.carrier_shipment.company_id != self.company_id
        ):
            errors["carrier_shipment"] = "El envío pertenece a otra empresa."

        if (
            self.integration_id
            and self.provider
            and self.integration.provider != self.provider
        ):
            errors["provider"] = "El proveedor no coincide con la integración."

        if errors:
            raise ValidationError(errors)

    @property
    def is_terminal(self):
        return self.status in {
            self.STATUS_PROCESSED,
            self.STATUS_DEAD_LETTER,
            self.STATUS_IGNORED,
        }

# ============================================================
# WMS360 SECRET MANAGER
# ============================================================


class IntegrationSecret(CompanyScopedModel):
    """
    Identidad lógica de un secreto perteneciente a una integración.

    El valor nunca se almacena aquí. Cada rotación crea una nueva versión
    cifrada en IntegrationSecretVersion.
    """

    KIND_WEBHOOK_SECRET = "webhook_secret"
    KIND_API_KEY = "api_key"
    KIND_ACCESS_TOKEN = "access_token"
    KIND_REFRESH_TOKEN = "refresh_token"
    KIND_CLIENT_SECRET = "client_secret"
    KIND_PRIVATE_KEY = "private_key"
    KIND_CERTIFICATE = "certificate"
    KIND_OTHER = "other"

    KIND_CHOICES = [
        (KIND_WEBHOOK_SECRET, "Secreto de webhook"),
        (KIND_API_KEY, "API key"),
        (KIND_ACCESS_TOKEN, "Access token"),
        (KIND_REFRESH_TOKEN, "Refresh token"),
        (KIND_CLIENT_SECRET, "Client secret"),
        (KIND_PRIVATE_KEY, "Llave privada"),
        (KIND_CERTIFICATE, "Certificado"),
        (KIND_OTHER, "Otro"),
    ]

    integration = models.ForeignKey(
        "shipping_hub.CompanyShippingIntegration",
        on_delete=models.CASCADE,
        related_name="managed_secrets",
    )
    name = models.SlugField(max_length=100)
    kind = models.CharField(
        max_length=30,
        choices=KIND_CHOICES,
        db_index=True,
    )
    purpose = models.CharField(max_length=250, blank=True)
    is_enabled = models.BooleanField(default=True, db_index=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_shipping_secrets",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["company_id", "integration_id", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "integration", "name"],
                name="uniq_integration_secret_name",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "integration", "kind"],
                name="intsecret_comp_int_kind_idx",
            ),
            models.Index(
                fields=["is_enabled", "kind"],
                name="intsecret_enabled_kind_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if (
            self.integration_id
            and self.integration.company_id != self.company_id
        ):
            raise ValidationError(
                {"integration": "La integración pertenece a otra empresa."}
            )

    def __str__(self):
        return f"{self.integration_id}:{self.name}"


class IntegrationSecretVersion(CompanyScopedModel):
    """
    Versión cifrada e inmutable de un secreto.

    Solo una versión puede estar activa por identidad lógica.
    """

    STATUS_ACTIVE = "active"
    STATUS_REVOKED = "revoked"
    STATUS_SUPERSEDED = "superseded"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Activa"),
        (STATUS_REVOKED, "Revocada"),
        (STATUS_SUPERSEDED, "Sustituida"),
    ]

    secret = models.ForeignKey(
        "shipping_hub.IntegrationSecret",
        on_delete=models.CASCADE,
        related_name="versions",
    )
    version = models.PositiveIntegerField()
    encrypted_value = models.TextField()
    fingerprint_sha256 = models.CharField(max_length=64, db_index=True)
    masked_hint = models.CharField(max_length=32, blank=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        db_index=True,
    )
    rotation_reason = models.CharField(max_length=500, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_shipping_secret_versions",
        null=True,
        blank=True,
    )
    activated_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="revoked_shipping_secret_versions",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["secret_id", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["secret", "version"],
                name="uniq_integration_secret_version",
            ),
            models.UniqueConstraint(
                fields=["secret"],
                condition=Q(status="active"),
                name="uniq_active_integration_secret",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "secret", "status"],
                name="intsecver_comp_sec_st_idx",
            ),
            models.Index(
                fields=["status", "activated_at"],
                name="intsecver_status_date_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if self.secret_id and self.secret.company_id != self.company_id:
            raise ValidationError(
                {"secret": "El secreto pertenece a otra empresa."}
            )

    def __str__(self):
        return f"{self.secret_id}:v{self.version}:{self.status}"


class IntegrationSecretAuditEvent(CompanyScopedModel):
    """Auditoría sin valores sensibles del ciclo de vida de secretos."""

    ACTION_CREATED = "created"
    ACTION_ROTATED = "rotated"
    ACTION_REVOKED = "revoked"
    ACTION_READ = "read"
    ACTION_LEGACY_SYNCED = "legacy_synced"

    ACTION_CHOICES = [
        (ACTION_CREATED, "Creado"),
        (ACTION_ROTATED, "Rotado"),
        (ACTION_REVOKED, "Revocado"),
        (ACTION_READ, "Consultado"),
        (ACTION_LEGACY_SYNCED, "Sincronizado con campo heredado"),
    ]

    secret = models.ForeignKey(
        "shipping_hub.IntegrationSecret",
        on_delete=models.CASCADE,
        related_name="audit_events",
    )
    version = models.ForeignKey(
        "shipping_hub.IntegrationSecretVersion",
        on_delete=models.SET_NULL,
        related_name="audit_events",
        null=True,
        blank=True,
    )
    action = models.CharField(
        max_length=30,
        choices=ACTION_CHOICES,
        db_index=True,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="shipping_secret_audit_events",
        null=True,
        blank=True,
    )
    reason = models.CharField(max_length=500, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-occurred_at", "-pk"]
        indexes = [
            models.Index(
                fields=["company", "secret", "occurred_at"],
                name="intsecaudit_comp_secret_idx",
            ),
            models.Index(
                fields=["action", "occurred_at"],
                name="intsecaudit_action_date_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if self.secret_id and self.secret.company_id != self.company_id:
            raise ValidationError(
                {"secret": "El secreto pertenece a otra empresa."}
            )
        if self.version_id and self.version.company_id != self.company_id:
            raise ValidationError(
                {"version": "La versión pertenece a otra empresa."}
            )

# ============================================================
# AUDITORÍA UNIVERSAL DEL LIFECYCLE ENGINE
# ============================================================


class ShippingLifecycleTransition(CompanyScopedModel):
    """
    Registro inmutable de una decisión o transición aplicada por el
    ShippingLifecycleService.

    Nunca almacena secretos, payloads completos ni datos personales.
    """

    ENTITY_PURCHASE = "purchase"
    ENTITY_SHIPMENT = "shipment"
    ENTITY_PACKAGE = "package"
    ENTITY_EVENT = "event"

    ENTITY_CHOICES = [
        (ENTITY_PURCHASE, "Compra de guía"),
        (ENTITY_SHIPMENT, "Envío"),
        (ENTITY_PACKAGE, "Bulto"),
        (ENTITY_EVENT, "Evento de proveedor"),
    ]

    entity_type = models.CharField(
        max_length=20,
        choices=ENTITY_CHOICES,
        db_index=True,
    )
    entity_id = models.PositiveBigIntegerField(db_index=True)

    purchase = models.ForeignKey(
        "shipping_hub.ShippingPurchase",
        on_delete=models.SET_NULL,
        related_name="lifecycle_transitions",
        null=True,
        blank=True,
    )
    carrier_shipment = models.ForeignKey(
        "shipping_hub.CarrierShipment",
        on_delete=models.SET_NULL,
        related_name="lifecycle_transitions",
        null=True,
        blank=True,
    )
    shipment_package = models.ForeignKey(
        "shipping_hub.ShipmentPackage",
        on_delete=models.SET_NULL,
        related_name="lifecycle_transitions",
        null=True,
        blank=True,
    )
    provider_event = models.ForeignKey(
        "shipping_hub.ShippingProviderEvent",
        on_delete=models.SET_NULL,
        related_name="lifecycle_transitions",
        null=True,
        blank=True,
    )

    from_state = models.CharField(max_length=30)
    to_state = models.CharField(max_length=30)
    source = models.CharField(max_length=50, db_index=True)
    reason = models.CharField(max_length=500, blank=True)
    correlation_id = models.UUIDField(
        default=uuid.uuid4,
        db_index=True,
        editable=False,
    )
    idempotent = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True)

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="shipping_lifecycle_transitions",
        null=True,
        blank=True,
    )
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-occurred_at", "-pk"]
        indexes = [
            models.Index(
                fields=["company", "entity_type", "entity_id"],
                name="slife_comp_entity_idx",
            ),
            models.Index(
                fields=["source", "occurred_at"],
                name="slife_source_date_idx",
            ),
            models.Index(
                fields=["company", "occurred_at"],
                name="slife_comp_date_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        relationships = {
            self.ENTITY_PURCHASE: self.purchase,
            self.ENTITY_SHIPMENT: self.carrier_shipment,
            self.ENTITY_PACKAGE: self.shipment_package,
            self.ENTITY_EVENT: self.provider_event,
        }

        related = relationships.get(self.entity_type)
        if related is None:
            errors["entity_type"] = (
                "La transición debe estar vinculada a su entidad concreta."
            )
        else:
            if related.company_id != self.company_id:
                errors["company"] = "La entidad pertenece a otra empresa."
            if related.pk != self.entity_id:
                errors["entity_id"] = (
                    "El identificador no coincide con la entidad vinculada."
                )

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return (
            f"{self.entity_type}:{self.entity_id} "
            f"{self.from_state}->{self.to_state}"
        )


# ============================================================
# LOTES DE ENVÍO Y PUNTOS DE RECOLECCIÓN (QR)
# ============================================================


class ShipmentCollectionPoint(CompanyScopedModel):
    """
    Punto físico fijo de recolección (ej. "Área de empaquetado, salida A").

    Representa el QR que se imprime UNA sola vez y se queda pegado en el
    lugar físico. No representa un lote: cada vez que se escanea, el
    recolector ve el lote (ShipmentBatch) más reciente sin confirmar
    asociado a este punto.
    """

    warehouse = models.ForeignKey(
        "inventory.Warehouse",
        on_delete=models.PROTECT,
        related_name="shipping_collection_points",
    )

    name = models.CharField(max_length=150)

    qr_token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        help_text="Identificador codificado en el QR impreso. No cambia nunca.",
    )

    is_active = models.BooleanField(default=True, db_index=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_collection_points",
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["warehouse", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "warehouse", "name"],
                name="uniq_collection_point_wh_name",
            )
        ]

    def clean(self):
        super().clean()
        if self.warehouse_id and self.warehouse.company_id != self.company_id:
            raise ValidationError({"warehouse": "La bodega pertenece a otra empresa."})

    def __str__(self):
        return f"{self.name} · {self.warehouse}"


class ShipmentBatch(CompanyScopedModel):
    """
    Lote de paquetes armado por empaquetado (ej. Camila) y asignado a un
    recolector (ej. Héctor) para llevar a las paqueterías correspondientes.

    Siempre está ligado a un ShipmentCollectionPoint fijo; el QR nunca
    cambia, pero cada lote es un registro propio con su fecha, hora y
    estado independientes.
    """

    STATUS_OPEN = "open"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_OPEN, "Abierto"),
        (STATUS_COMPLETED, "Completado"),
        (STATUS_CANCELLED, "Cancelado"),
    ]

    collection_point = models.ForeignKey(
        ShipmentCollectionPoint,
        on_delete=models.PROTECT,
        related_name="batches",
    )

    status = models.CharField(
        max_length=15,
        choices=STATUS_CHOICES,
        default=STATUS_OPEN,
        db_index=True,
    )

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="assigned_shipment_batches",
        help_text="Recolector autorizado para este lote.",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_shipment_batches",
        null=True,
        blank=True,
    )

    first_scanned_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(
                fields=["company", "collection_point", "status"],
                name="sbatch_comp_point_status_idx",
            ),
            models.Index(
                fields=["company", "assigned_to", "status"],
                name="sbatch_comp_assign_stat_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if (
            self.collection_point_id
            and self.collection_point.company_id != self.company_id
        ):
            errors["collection_point"] = (
                "El punto de recolección pertenece a otra empresa."
            )
        if errors:
            raise ValidationError(errors)

    @property
    def is_fully_resolved(self):
        return not self.items.filter(status=ShipmentBatchItem.STATUS_PENDING).exists()

    def __str__(self):
        return f"Lote {self.pk or 'nuevo'} · {self.collection_point}"


class ShipmentBatchItem(CompanyScopedModel):
    """
    Un paquete (ShipmentPackage) dentro de un lote de recolección.

    El estado aquí NO reemplaza el status de ShipmentPackage: lo
    complementa para el flujo operativo de recolección/entrega. Cuando un
    paquete se reporta faltante o rechazado, se crea (o liga) un
    LogisticsIncident — ese es el mecanismo de investigación y resolución,
    no un estado nuevo inventado aquí.
    """

    STATUS_PENDING = "pending"
    STATUS_DELIVERED = "delivered_to_carrier"
    STATUS_MISSING = "missing_in_collection"
    STATUS_REJECTED = "rejected_by_carrier"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pendiente"),
        (STATUS_DELIVERED, "Entregado a paquetería"),
        (STATUS_MISSING, "Faltante en recolección"),
        (STATUS_REJECTED, "Rechazado por paquetería"),
    ]

    batch = models.ForeignKey(
        ShipmentBatch,
        on_delete=models.CASCADE,
        related_name="items",
    )

    package = models.ForeignKey(
        ShipmentPackage,
        on_delete=models.PROTECT,
        related_name="batch_items",
    )

    carrier_name_snapshot = models.CharField(
        max_length=150,
        blank=True,
        help_text="Paquetería del paquete al momento de armar el lote.",
    )

    status = models.CharField(
        max_length=25,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )

    incident = models.ForeignKey(
        LogisticsIncident,
        on_delete=models.SET_NULL,
        related_name="batch_items",
        null=True,
        blank=True,
    )

    delivered_at = models.DateTimeField(null=True, blank=True)

    delivered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="delivered_batch_items",
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["package__package_number", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "package"],
                name="uniq_batch_package",
            )
        ]
        indexes = [
            models.Index(
                fields=["company", "batch", "status"],
                name="sbitem_comp_batch_status_idx",
            ),
            models.Index(
                fields=["company", "package"],
                name="sbitem_comp_pkg_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.batch_id and self.batch.company_id != self.company_id:
            errors["batch"] = "El lote pertenece a otra empresa."
        if self.package_id and self.package.company_id != self.company_id:
            errors["package"] = "El bulto pertenece a otra empresa."
        if self.incident_id and self.incident.company_id != self.company_id:
            errors["incident"] = "La incidencia pertenece a otra empresa."

        if self.package_id:
            conflict = (
                ShipmentBatchItem.objects.filter(
                    company_id=self.company_id,
                    package_id=self.package_id,
                    batch__status=ShipmentBatch.STATUS_OPEN,
                )
                .exclude(pk=self.pk)
                .exclude(batch_id=self.batch_id)
            )
            if conflict.exists():
                errors["package"] = "Este bulto ya está en otro lote abierto."

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"Lote {self.batch_id} · bulto {self.package_id} · {self.status}"
