from django.urls import path
from accounts.capability_access import capability_required

from . import pending_views
from . import views

app_name = "data_imports"


urlpatterns = [
    # ============================================================
    # DASHBOARD
    # ============================================================
    path(
        "",
        views.dashboard,
        name="dashboard",
    ),
    # ============================================================
    # PRODUCTOS
    # ============================================================
    path(
        "productos/nueva/",
        views.upload_products,
        name="upload_products",
    ),
    path(
        "productos/plantilla/",
        views.download_product_template,
        name="download_product_template",
    ),
    path(
        "productos/<int:pk>/columnas/",
        views.product_mapping,
        name="product_mapping",
    ),
    path(
        "productos/<int:pk>/validar/",
        views.validate_products,
        name="validate_products",
    ),
    path(
        "productos/<int:pk>/importar/",
        views.confirm_product_import,
        name="confirm_product_import",
    ),
    # ============================================================
    # CLIENTES
    # ============================================================
    path(
        "clientes/nueva/",
        views.upload_customers,
        name="upload_customers",
    ),
    path(
        "clientes/plantilla/",
        views.download_customer_template,
        name="download_customer_template",
    ),
    path(
        "<int:pk>/columnas/",
        views.customer_mapping,
        name="customer_mapping",
    ),
    path(
        "<int:pk>/validar/",
        views.validate_customers,
        name="validate_customers",
    ),
    path(
        "<int:pk>/duplicados/",
        views.resolve_duplicates,
        name="resolve_duplicates",
    ),
    path(
        "<int:pk>/duplicados/guardar/",
        views.save_duplicates,
        name="save_duplicates",
    ),
    path(
        "<int:pk>/duplicados/resolver-bloque/",
        views.bulk_resolve_duplicates,
        name="bulk_resolve_duplicates",
    ),
    path(
        "<int:pk>/importar/",
        views.confirm_customer_import,
        name="confirm_customer_import",
    ),
    path(
        "<int:pk>/estado/",
        views.job_status,
        name="job_status",
    ),
    # ============================================================
    # PROVEEDORES
    # ============================================================
    path(
        "proveedores/nueva/",
        views.upload_suppliers,
        name="upload_suppliers",
    ),
    path(
        "proveedores/plantilla/",
        views.download_supplier_template,
        name="download_supplier_template",
    ),
    path(
        "proveedores/<int:pk>/columnas/",
        views.supplier_mapping,
        name="supplier_mapping",
    ),
    path(
        "proveedores/<int:pk>/validar/",
        views.validate_suppliers,
        name="validate_suppliers",
    ),
    path(
        "proveedores/<int:pk>/duplicados/",
        views.supplier_duplicates,
        name="supplier_duplicates",
    ),
    path(
        "proveedores/<int:pk>/duplicados/guardar/",
        views.save_supplier_duplicates,
        name="save_supplier_duplicates",
    ),
    path(
        "proveedores/<int:pk>/importar/",
        views.confirm_supplier_import,
        name="confirm_supplier_import",
    ),
    # ============================================================
    # DETALLE GENERAL DEL LOTE
    # ============================================================
    path(
        "<int:pk>/",
        views.job_detail,
        name="job_detail",
    ),
    path(
        "<int:pk>/filas/<int:row_pk>/resolver/",
        views.resolve_row_issue,
        name="resolve_row_issue",
    ),
    # ============================================================
    # PASO 5 - CLIENTES NO CREADOS
    # ============================================================
    path(
        "<int:pk>/pendientes/",
        pending_views.pending_customers,
        name="pending_customers",
    ),
    path(
        "<int:pk>/pendientes/reporte/",
        pending_views.download_pending_report,
        name="download_pending_report",
    ),
    path(
        "<int:pk>/pendientes/<int:row_pk>/editar/",
        pending_views.edit_pending_customer,
        name="edit_pending_customer",
    ),
    path(
        "<int:pk>/pendientes/<int:row_pk>/importar/",
        pending_views.import_pending_customer,
        name="import_pending_customer",
    ),
    # ============================================================
    # INVENTARIO INICIAL
    # ============================================================
    path(
        "inventario/nueva/",
        views.upload_inventory,
        name="upload_inventory",
    ),
    path(
        "inventario/plantilla/",
        views.download_inventory_template,
        name="download_inventory_template",
    ),
    path(
        "inventario/<int:pk>/columnas/",
        views.inventory_mapping,
        name="inventory_mapping",
    ),
    path(
        "inventario/<int:pk>/validar/",
        views.validate_inventory,
        name="validate_inventory",
    ),
    path(
        "inventario/<int:pk>/importar/",
        views.confirm_inventory_import,
        name="confirm_inventory_import",
    ),
]

# El menú no es una frontera de seguridad: todas las rutas de importación,
# incluidas descargas y correcciones por POST, exigen la misma capacidad.
for pattern in urlpatterns:
    pattern.callback = capability_required("imports.manage")(pattern.callback)
