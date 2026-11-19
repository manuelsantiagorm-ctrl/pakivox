from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Mapping

from django.urls import NoReverseMatch, reverse


@dataclass(frozen=True)
class ParentSpec:
    view_name: str
    label: str
    kwargs_map: Mapping[str, str] | None = None


# ---------------------------------------------------------------------
# Raíces de navegación por namespace.
# Solo se usan nombres de URL internos de Django; nunca URLs externas.
# ---------------------------------------------------------------------
NAMESPACE_ROOTS = {
    "accounts": ("accounts:employee_home", "Inicio"),
    "dashboard": ("dashboard:ceo_dashboard", "Dashboard CEO"),
    "inventory": ("inventory:lista_productos", "Inventario"),
    "sales": ("sales:lista_pedidos", "Pedidos"),
    "payments": ("payments:lista_pagos", "Pagos"),
    "purchases": ("purchases:lista_compras", "Compras"),
    "warehouse": ("warehouse:lista_picking", "Almacén"),
    "shipping_hub": ("shipping_hub:logistics_dashboard", "Guías de envío"),
    "messaging": ("messaging:home", "Mensajería"),
    "accounting": ("accounting:financial_dashboard", "Finanzas"),
    "companies": ("companies:profile", "Empresa"),
    "subscriptions": ("subscriptions:subscription_expired", "Suscripción"),
    "owner": ("owner:console", "Owner SaaS"),
    "notifications": ("notifications:center", "Notificaciones"),
    "data_imports": ("data_imports:dashboard", "Importaciones"),
    "custom_fields": ("custom_fields:field_list", "Campos personalizados"),
}

# Vistas raíz donde no debe mostrarse botón global de regreso.
ROOT_VIEWS = {
    "accounts:employee_home",
    "dashboard:ceo_dashboard",
    "inventory:lista_productos",
    "sales:lista_pedidos",
    "payments:lista_pagos",
    "purchases:lista_compras",
    "warehouse:lista_picking",
    "shipping_hub:logistics_dashboard",
    "messaging:home",
    "accounting:financial_dashboard",
    "companies:profile",
    "owner:console",
    "notifications:center",
    "data_imports:dashboard",
    "custom_fields:field_list",
    "crm_home",
    "landing",
}

# Vistas que no representan una pantalla navegable completa.
# Aunque alguna llegue a renderizar error/fragmento, no debe generar navegación.
NON_PAGE_VIEWS = {
    "accounts:logout",
    "accounts:seleccionar_bodega_activa",
    "dashboard:ceo_ai_query",
    "dashboard:ceo_ai_export_excel",
    "subscriptions:stripe_webhook",
    "subscriptions:stripe_webhook_legacy",
    "messaging:twilio_inbound_webhook",
    "messaging:meta_whatsapp_webhook",
    "messaging:ycloud_whatsapp_webhook",
    "shipping_hub:tracking_webhook",
    "shipping_hub:provider_event_webhook",
    "shipping_hub:skydropx_event_webhook",
    "shipping_hub:envia_event_webhook",
}

# ---------------------------------------------------------------------
# Destinos explícitos.
# kwargs_map = {kwarg_destino: kwarg_vista_actual}
# No se consulta la base de datos para construir la navegación.
# ---------------------------------------------------------------------
EXACT_PARENTS = {
    # Inventario
    "inventory:crear_producto": ParentSpec("inventory:lista_productos", "Inventario"),
    "inventory:detalle_producto": ParentSpec("inventory:lista_productos", "Inventario"),
    "inventory:editar_producto": ParentSpec(
        "inventory:detalle_producto",
        "Detalle del producto",
        {"product_id": "product_id"},
    ),
    "inventory:agregar_variante": ParentSpec(
        "inventory:detalle_producto",
        "Detalle del producto",
        {"product_id": "product_id"},
    ),
    "inventory:agregar_stock": ParentSpec(
        "inventory:detalle_producto",
        "Detalle del producto",
        {"product_id": "product_id"},
    ),
    "inventory:asignar_ubicacion_variante": ParentSpec(
        "inventory:detalle_producto",
        "Detalle del producto",
        {"product_id": "product_id"},
    ),
    "inventory:ajustar_stock": ParentSpec("inventory:lista_stock", "Existencias"),
    "inventory:movimientos_inventario": ParentSpec("inventory:lista_stock", "Existencias"),
    "inventory:stock_critical_alerts": ParentSpec("inventory:lista_stock", "Existencias"),
    "inventory:bundle_create": ParentSpec("inventory:lista_productos", "Inventario"),

    # Ventas
    "sales:pedidos_en_curso": ParentSpec("sales:lista_pedidos", "Pedidos"),
    "sales:incidencias_pedidos": ParentSpec("sales:pedidos_en_curso", "Pedidos en curso"),
    "sales:crear_pedido": ParentSpec("sales:lista_pedidos", "Pedidos"),
    "sales:detalle_pedido": ParentSpec("sales:lista_pedidos", "Pedidos"),
    "sales:pedidos_sin_pagar": ParentSpec("sales:lista_pedidos", "Pedidos"),
    "sales:pedidos_en_revision_pago": ParentSpec("sales:lista_pedidos", "Pedidos"),
    "sales:pedidos_aprobados": ParentSpec("sales:lista_pedidos", "Pedidos"),

    # Pagos
    "payments:registrar_pago": ParentSpec("payments:lista_pagos", "Pagos"),
    "payments:detalle_pago": ParentSpec("payments:lista_pagos", "Pagos"),
    "payments:pagos_pedido": ParentSpec(
        "sales:detalle_pedido",
        "Pedido",
        {"order_id": "order_id"},
    ),

    # Compras
    "purchases:lista_proveedores": ParentSpec("purchases:lista_compras", "Compras"),
    "purchases:crear_proveedor": ParentSpec("purchases:lista_proveedores", "Proveedores"),
    "purchases:crear_orden_compra": ParentSpec("purchases:lista_compras", "Compras"),
    "purchases:detalle_orden_compra": ParentSpec("purchases:lista_compras", "Compras"),
    "purchases:detalle_recepcion": ParentSpec("purchases:lista_compras", "Compras"),

    # Almacén
    "warehouse:transfer_create": ParentSpec("warehouse:transfer_list", "Transferencias"),
    "warehouse:transfer_detail": ParentSpec("warehouse:transfer_list", "Transferencias"),
    "warehouse:transfer_dispatch_page": ParentSpec(
        "warehouse:transfer_detail",
        "Detalle de transferencia",
        {"pk": "pk"},
    ),
    "warehouse:lista_ubicaciones": ParentSpec("warehouse:mapa_ubicaciones_qr", "Mapa de ubicaciones"),
    "warehouse:crear_ubicacion": ParentSpec("warehouse:lista_ubicaciones", "Ubicaciones"),
    "warehouse:editar_ubicacion": ParentSpec("warehouse:lista_ubicaciones", "Ubicaciones"),
    "warehouse:ubicar_mercancia": ParentSpec("warehouse:lista_ubicaciones", "Ubicaciones"),
    "warehouse:detalle_picking": ParentSpec("warehouse:lista_picking", "Surtido"),
    "warehouse:modo_pickeo": ParentSpec(
        "warehouse:detalle_picking",
        "Detalle del surtido",
        {"picking_id": "picking_id"},
    ),
    "warehouse:lista_packing": ParentSpec("warehouse:lista_picking", "Surtido"),
    "warehouse:detalle_packing": ParentSpec("warehouse:lista_packing", "Empaque"),
    "warehouse:lista_shipping": ParentSpec("warehouse:lista_packing", "Empaque"),
    "warehouse:detalle_shipping": ParentSpec("warehouse:lista_shipping", "Envíos"),
    "warehouse:timeline_pedido": ParentSpec(
        "sales:detalle_pedido",
        "Pedido",
        {"order_id": "order_id"},
    ),
    "warehouse:layout_designer": ParentSpec("warehouse:mapa_ubicaciones_qr", "Mapa de ubicaciones"),
    "warehouse:auditoria_warehouse": ParentSpec("warehouse:lista_picking", "Almacén"),

    # Shipping Hub
    "shipping_hub:incident_center": ParentSpec("shipping_hub:logistics_dashboard", "Guías de envío"),
    "shipping_hub:incident_detail": ParentSpec("shipping_hub:incident_center", "Incidencias"),
    "shipping_hub:tracking_center": ParentSpec("shipping_hub:logistics_dashboard", "Guías de envío"),
    "shipping_hub:tracking_detail": ParentSpec("shipping_hub:tracking_center", "Rastreo"),
    "shipping_hub:print_center": ParentSpec("shipping_hub:logistics_dashboard", "Guías de envío"),
    "shipping_hub:integrations_list": ParentSpec("shipping_hub:logistics_dashboard", "Guías de envío"),
    "shipping_hub:integration_create": ParentSpec("shipping_hub:integrations_list", "Integraciones"),
    "shipping_hub:integration_edit": ParentSpec("shipping_hub:integrations_list", "Integraciones"),
    "shipping_hub:origins_list": ParentSpec("shipping_hub:logistics_dashboard", "Guías de envío"),
    "shipping_hub:origin_create": ParentSpec("shipping_hub:origins_list", "Orígenes"),
    "shipping_hub:origin_edit": ParentSpec("shipping_hub:origins_list", "Orígenes"),
    "shipping_hub:local_delivery_resources": ParentSpec("shipping_hub:logistics_dashboard", "Guías de envío"),
    "shipping_hub:custom_carriers": ParentSpec("shipping_hub:logistics_dashboard", "Guías de envío"),

    # Mensajería
    "messaging:channels_list": ParentSpec("messaging:home", "Mensajería"),
    "messaging:channel_create": ParentSpec("messaging:channels_list", "Canales"),
    "messaging:channel_detail": ParentSpec("messaging:channels_list", "Canales"),
    "messaging:chat_inbox": ParentSpec("messaging:home", "Mensajería"),
    "messaging:chat_detail": ParentSpec("messaging:chat_inbox", "Bandeja de chats"),
    "messaging:ai_offer_draft_list": ParentSpec("messaging:home", "Mensajería"),
    "messaging:ai_offer_draft_detail": ParentSpec("messaging:ai_offer_draft_list", "Borradores IA"),
    "messaging:owner_saas_console": ParentSpec("owner:console", "Owner SaaS"),
    "messaging:owner_notification_logs": ParentSpec("messaging:owner_saas_console", "WhatsApp Owner"),

    # Empresa
    "companies:profile_edit": ParentSpec("companies:profile", "Empresa"),
    "companies:warehouse_list": ParentSpec("companies:profile", "Empresa"),
    "companies:warehouse_create": ParentSpec("companies:warehouse_list", "Bodegas"),
    "companies:warehouse_update": ParentSpec("companies:warehouse_list", "Bodegas"),

    # Finanzas
    "accounting:income_statement": ParentSpec("accounting:financial_dashboard", "Finanzas"),
    "accounting:balance_sheet": ParentSpec("accounting:financial_dashboard", "Finanzas"),
    "accounting:expense_list": ParentSpec("accounting:financial_dashboard", "Finanzas"),
    "accounting:expense_create": ParentSpec("accounting:expense_list", "Gastos"),
    "accounting:financial_ai_advisor": ParentSpec("accounting:financial_dashboard", "Finanzas"),

    # Dashboard
    "dashboard:control_flow_dashboard": ParentSpec("dashboard:ceo_dashboard", "Dashboard CEO"),

    # Owner
    "owner:whatsapp_requests_list": ParentSpec("owner:console", "Owner SaaS"),
    "owner:whatsapp_request_detail": ParentSpec("owner:whatsapp_requests_list", "Solicitudes WhatsApp"),
    "owner:company_profile": ParentSpec("owner:console", "Owner SaaS"),
    "owner:company_edit": ParentSpec("owner:company_profile", "Empresa"),

    # Notificaciones
    "notifications:open": ParentSpec("notifications:center", "Notificaciones"),

    # Campos personalizados
    "custom_fields:field_create": ParentSpec("custom_fields:field_list", "Campos personalizados"),
    "custom_fields:field_edit": ParentSpec("custom_fields:field_list", "Campos personalizados"),

    # CRM sin namespace en config.urls
    "crm_kanban": ParentSpec("crm_home", "CRM"),
    "crm_agenda": ParentSpec("crm_home", "CRM"),
    "customer_smart_profile": ParentSpec("crm_home", "CRM"),
    "customer_smart_profile_by_phone": ParentSpec("crm_home", "CRM"),
    "customer_smart_profile_by_phone_direct": ParentSpec("crm_home", "CRM"),
    "customer_edit": ParentSpec("crm_home", "CRM"),
    "customer_create": ParentSpec("crm_home", "CRM"),
}


VIEW_LABELS = {
    "dashboard:ceo_dashboard": "Dashboard CEO",
    "dashboard:control_flow_dashboard": "Mesa de Control",
    "inventory:lista_productos": "Inventario",
    "inventory:lista_stock": "Existencias",
    "inventory:detalle_producto": "Detalle del producto",
    "sales:lista_pedidos": "Pedidos",
    "sales:detalle_pedido": "Detalle del pedido",
    "sales:pedidos_en_curso": "Pedidos en curso",
    "payments:lista_pagos": "Pagos",
    "purchases:lista_compras": "Compras",
    "warehouse:lista_picking": "Surtido",
    "warehouse:lista_packing": "Empaque",
    "warehouse:lista_shipping": "Envíos",
    "warehouse:transfer_list": "Transferencias",
    "shipping_hub:logistics_dashboard": "Guías de envío",
    "shipping_hub:incident_center": "Incidencias",
    "shipping_hub:tracking_center": "Rastreo",
    "messaging:home": "Mensajería",
    "messaging:chat_inbox": "Bandeja de chats",
    "accounting:financial_dashboard": "Finanzas",
    "companies:profile": "Empresa",
    "notifications:center": "Notificaciones",
    "data_imports:dashboard": "Importaciones",
    "custom_fields:field_list": "Campos personalizados",
    "crm_home": "CRM",
    "crm_kanban": "Embudo de ventas",
    "crm_agenda": "Agenda",
    "customer_create": "Nuevo cliente",
    "customer_edit": "Editar cliente",
    "customer_smart_profile": "Perfil del cliente",
    "inventory:editar_producto": "Editar producto",
    # Pantallas cuyo nombre interno está en inglés (migas en español).
    "accounting:expense_create": "Nuevo gasto",
    "accounting:expense_list": "Gastos",
    "accounts:employee_invitation_create": "Invitar empleado",
    "accounts:employee_activities_update": "Áreas del empleado",
    "accounts:employee_jobs_update": "Puestos del empleado",
    "companies:profile_edit": "Editar empresa",
    "companies:warehouse_create": "Nueva bodega",
    "companies:warehouse_list": "Bodegas",
    "companies:warehouse_update": "Editar bodega",
    "custom_fields:field_create": "Nuevo campo",
    "custom_fields:field_edit": "Editar campo",
    "customer_address_create": "Nueva dirección",
    "customer_address_edit": "Editar dirección",
    "customer_smart_profile_by_phone": "Perfil del cliente",
    "customer_smart_profile_by_phone_direct": "Perfil del cliente",
    "data_imports:job_detail": "Detalle de la importación",
    "data_imports:edit_pending_customer": "Revisar cliente",
    "data_imports:supplier_mapping": "Columnas de proveedores",
    "data_imports:supplier_duplicates": "Proveedores repetidos",
    "inventory:bundle_create": "Nuevo combo",
    "messaging:ai_offer_draft_create": "Nuevo borrador",
    "messaging:ai_offer_draft_detail": "Borrador",
    "messaging:ai_offer_draft_list": "Borradores con IA",
    "messaging:channel_create": "Nuevo canal",
    "messaging:channel_detail": "Canal",
    "messaging:channels_list": "Canales",
    "messaging:chat_detail": "Conversación",
    "purchases:replenishment_alert_detail": "Alerta de reabasto",
    "shipping_hub:delivery_evidence_create": "Nueva evidencia de entrega",
    "shipping_hub:delivery_evidence_detail": "Evidencia de entrega",
    "shipping_hub:incident_create": "Nueva incidencia",
    "shipping_hub:incident_detail": "Incidencia",
    "shipping_hub:integration_create": "Nueva paquetería",
    "shipping_hub:integration_edit": "Editar paquetería",
    "shipping_hub:integrations_list": "Paqueterías",
    "shipping_hub:origin_create": "Nuevo origen",
    "shipping_hub:origin_edit": "Editar origen",
    "shipping_hub:origins_list": "Orígenes de envío",
    "shipping_hub:print_center": "Impresión de guías",
    "shipping_hub:print_job_preview": "Vista previa de impresión",
    "shipping_hub:shipment_batch_create": "Nuevo lote de envío",
    "shipping_hub:shipment_batch_scan": "Escanear lote",
    "shipping_hub:tracking_detail": "Rastreo del envío",
    "shipping_hub_dispatch:dispatch_center": "Centro de despacho",
    "shipping_hub_dispatch:shipment_tracking_timeline": "Historial del envío",
    "warehouse:auditoria_warehouse": "Bitácora del almacén",
    "warehouse:lot_traceability_detail": "Trazabilidad del lote",
    "warehouse:lot_traceability_list": "Trazabilidad de lotes",
    "warehouse:transfer_create": "Nueva transferencia",
    "warehouse:transfer_detail": "Detalle de la transferencia",
    "warehouse:transfer_dispatch_page": "Despachar transferencia",
}


def _humanize(value: str) -> str:
    value = (value or "").split(":")[-1]
    value = value.replace("_", " ").replace("-", " ").strip()
    return value[:1].upper() + value[1:] if value else "Vista"


def _safe_reverse(view_name: str, kwargs: dict | None = None) -> str | None:
    try:
        url = reverse(view_name, kwargs=kwargs or None)
    except NoReverseMatch:
        return None

    # reverse() debe generar URL local. Defensa adicional por contrato.
    if not url.startswith("/") or url.startswith("//"):
        return None
    return url


def _mapped_kwargs(spec: ParentSpec, current_kwargs: Mapping[str, object]) -> dict | None:
    if not spec.kwargs_map:
        return {}

    mapped = {}
    for destination_name, current_name in spec.kwargs_map.items():
        if current_name not in current_kwargs:
            return None
        mapped[destination_name] = current_kwargs[current_name]
    return mapped


@lru_cache(maxsize=1)
def _contextos_json() -> str:
    """
    Pantallas "de inicio" desde las que se suele abrir otra pantalla.

    El navegador las usa para que "Volver" regrese a ese contexto (por
    ejemplo, al Dashboard CEO) cuando el usuario llegó desde ahí, en vez de
    mandarlo siempre al listado del módulo. Son rutas fijas: se calculan una
    vez por proceso.
    """
    contextos = {}
    vistas = (ROOT_VIEWS - {"landing"}) | {
        "dashboard:control_flow_dashboard",
        "inventory:lista_stock",
        "sales:pedidos_en_curso",
    }
    for view_name in sorted(vistas):
        url = _safe_reverse(view_name)
        if url:
            contextos[url] = VIEW_LABELS.get(view_name) or NAMESPACE_ROOTS.get(
                view_name.split(":", 1)[0], (None, _humanize(view_name))
            )[1]
    return json.dumps(contextos, ensure_ascii=False, sort_keys=True)


def build_navigation(request) -> dict:
    """
    Construye navegación determinística sin consultar DB y sin usar Referer/history.

    La seguridad del recurso destino sigue perteneciendo a su vista/decoradores.
    Este helper nunca concede permisos ni recupera objetos de otro tenant.
    """
    hidden = {
        "show": False,
        "back_url": "",
        "back_label": "",
        "breadcrumbs": (),
        "view_name": "",
    }

    if request is None:
        return hidden

    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return hidden

    resolver = getattr(request, "resolver_match", None)
    if resolver is None:
        return hidden

    view_name = getattr(resolver, "view_name", "") or ""
    if not view_name or view_name in ROOT_VIEWS or view_name in NON_PAGE_VIEWS:
        return {**hidden, "view_name": view_name}

    current_kwargs = getattr(resolver, "kwargs", None) or {}

    spec = EXACT_PARENTS.get(view_name)

    namespace = view_name.split(":", 1)[0] if ":" in view_name else ""
    root = NAMESPACE_ROOTS.get(namespace)

    # Fallback determinístico al inicio del módulo.
    if spec is None and root:
        spec = ParentSpec(root[0], root[1])

    # CRM es actualmente un conjunto de rutas sin namespace.
    if spec is None and view_name.startswith(("crm_", "deal_", "customer_", "add_deal_", "mark_deal_", "update_deal_")):
        spec = ParentSpec("crm_home", "CRM")

    if spec is None:
        return {**hidden, "view_name": view_name}

    mapped_kwargs = _mapped_kwargs(spec, current_kwargs)
    if mapped_kwargs is None:
        # Si el parent específico requiere kwargs ausentes, degradar al root
        # del namespace; nunca fabricar IDs.
        if root:
            spec = ParentSpec(root[0], root[1])
            mapped_kwargs = {}
        else:
            return {**hidden, "view_name": view_name}

    back_url = _safe_reverse(spec.view_name, mapped_kwargs)
    if not back_url:
        if root and spec.view_name != root[0]:
            back_url = _safe_reverse(root[0])
            spec = ParentSpec(root[0], root[1])
        if not back_url:
            return {**hidden, "view_name": view_name}

    current_label = VIEW_LABELS.get(view_name, _humanize(view_name))
    breadcrumbs = []

    if root:
        root_url = _safe_reverse(root[0])
        if root_url:
            breadcrumbs.append({
                "label": root[1],
                "url": root_url,
                "current": view_name == root[0],
            })

    if spec.view_name != (root[0] if root else None):
        parent_url = _safe_reverse(spec.view_name, mapped_kwargs)
        if parent_url and not any(item["url"] == parent_url for item in breadcrumbs):
            breadcrumbs.append({
                "label": spec.label,
                "url": parent_url,
                "current": False,
            })

    breadcrumbs.append({
        "label": current_label,
        "url": "",
        "current": True,
    })

    return {
        "show": True,
        "back_url": back_url,
        "back_label": f"Volver a {spec.label}",
        "breadcrumbs": tuple(breadcrumbs),
        "view_name": view_name,
        "contextos_json": _contextos_json(),
    }
