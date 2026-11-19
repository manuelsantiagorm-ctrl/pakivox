# Matriz de permisos por pantalla — auditoría oct 2026

**Rama:** `mejora-demo-clientes` · **Estado:** probado en local (PostgreSQL 16 + copia de la BD de desarrollo). No está desplegado.

## Cómo se revisó

1. **Inventario de rutas.** Se recorrieron las 386 rutas de la app (sin contar el admin de Django) y se anotó qué candado tiene cada vista: `login_required`, `capability_required` o ninguno.
   - 47 rutas no tienen candado. Todas son públicas a propósito: landing, login y registro, Google, recuperación de contraseña, documentos legales, *health checks* y webhooks.
   - Los webhooks validan firma: Twilio, Meta (X-Hub-Signature-256), YCloud, Stripe (`construct_event`) y paqueterías (`verify_signature`).
2. **Prueba real, no de lectura.** Se hizo una petición a cada ruta como cada tipo de usuario, con objetos reales de la empresa A (el folio de un pedido, un pago, un surtido…). Las acciones que solo aceptan POST se probaron con POST vacío.

| Persona | Qué representa |
|---|---|
| Anónimo | Nadie ha iniciado sesión |
| Sin empresa | Cuenta registrada sin empresa (p. ej. alta con Google) |
| Dado de baja | Ex trabajador con TODOS los permisos asignados, pero con baja laboral |
| Trabajador sin permisos | Rol "Ventas" sin ningún permiso |
| Trabajador con bodegas pero sin permisos | Ve las bodegas pero no tiene ningún permiso |
| Rol "Administrador" sin permisos | Etiqueta *admin* sin permisos asignados |
| 64 trabajadores de un solo permiso | Uno por cada permiso del catálogo, con acceso a todas las bodegas |
| Trabajador de una sola bodega | Todos los permisos, pero solo en la bodega 1 |
| Dueño de OTRA empresa | Pide los ids de la empresa A (prueba de IDOR) |

Son unas 27,000 peticiones en total.

## Resultado

- **Entre empresas no se filtró nada.** El dueño de otra empresa no obtuvo ningún 200 sobre objetos ajenos.
- **Ningún permiso abre una pantalla de otro permiso.** Las 64 personas de un solo permiso solo entraron a las rutas cuyo candado es justo ese permiso.
- **Un anónimo solo llega a pantallas públicas.**
- Un trabajador sin permisos solo ve:
  - su **Inicio**;
  - **Notificaciones**;
  - el **Perfil de la empresa** (ahora sin el expediente);
  - el aviso de suscripción.

## Huecos encontrados y corregidos (cada uno con prueba)

| # | Hueco | Gravedad | Corrección |
|---|---|---|---|
| 1 | **Modo pickeo** abría y **confirmaba piezas** de surtidos de cualquier bodega. Bastaba cambiar el número en la dirección. | Alta | Ver el surtido exige acceso a esa bodega. Confirmar una pieza exige la bodega activa con nivel *Operar*, igual que el surtido normal. |
| 2 | **Expediente de la empresa** (constancia fiscal, identificaciones, contratos): cualquier trabajador podía descargarlo. | Alta | Solo dueño, administrador o contabilidad, o quien tenga *Configurar empresa* o *Consultar contabilidad*. Los demás ni siquiera ven los títulos. |
| 3 | **Editar ubicación** y **asignar existencias a ubicación** aceptaban ubicaciones de bodegas sin acceso. | Media | Solo ubicaciones de bodegas con nivel *Administrar*, como dice el contrato de la ruta. |
| 4 | **Lista y mapa de ubicaciones** mostraban todas las bodegas, y el filtro se saltaba si no había empresa (*fail-open*). | Media | Solo las bodegas que el usuario puede consultar. Sin empresa no se muestra nada. |
| 5 | **Trabajador dado de baja o perfil desactivado:** si conservaba o reabría sesión, seguía viendo pantallas que solo pedían "estar logueado". | Media | El middleware de acceso cierra la sesión de cualquier perfil que no esté activo. |
| 6 | **Configuración inicial y Bienvenida** (estado de la empresa, plan y suscripción) visibles para cualquier trabajador. | Baja | Solo dueño, administrador o quien tenga *Configurar empresa*. Los demás vuelven a su Inicio. |
| 7 | **Pagos de un pedido** (`/pagos/pedido/<id>/`) daba **error 500 a todos**, el dueño incluido, si no venía `?conversation_id`. | Error | Corregida la variable sin asignar. |
| 8 | Resolver incidencias, reparto local y cubicar con un id ajeno o inexistente daban **500**. | Error | Ahora responden 404 o un aviso 400. No había fuga de datos. |

## Decisiones que son tuyas (no se cambiaron)

- **Rol "Administrador" sin permisos.** Aun sin permisos puede:
  - autorizar el **excedente de WhatsApp** (cargo extra);
  - recibir notificaciones de toda la empresa;
  - entrar a Configuración inicial.

  Así está diseñado ("propietario o administrador"). Si quieres que solo el dueño autorice cargos, es un cambio de una línea.
- ***Configurar empresa y bodegas* no basta para administrar bodegas ni editar el perfil.** Esas vistas exigen además el rol dueño o administrador. Es más restrictivo, no menos, así que es seguro. Pero un trabajador con ese permiso verá un 403 donde no lo espera.
- **Permisos sin pantalla propia** (p. ej. `payments.create`, `sales.cancel_order`, `purchases.costs.view`, `quality.approve`). Activan botones o acciones dentro de pantallas que ya tienen otro candado. En el barrido aparecen con 0 rutas: no hay una pantalla que se abra solo con ellos.
- **Huecos conocidos en `KNOWN_ENFORCEMENT_GAPS`** (nivel de bodega en pagos y transiciones de ventas). Siguen documentados y no se tocaron.
- **Diseñador de layout:** filtra por empresa, no por bodega. Está pendiente, es de bajo riesgo (solo muestra la distribución).

## Qué desbloquea cada permiso (resultado medido)

Pantallas y acciones a las que entró un trabajador que solo tenía ese permiso y acceso a todas las bodegas. Además, todos ven Inicio, Notificaciones y el Perfil de la empresa.

| Área | Permiso | Qué desbloquea (pantallas y acciones) |
|---|---|---|
| accounting_finance | **Registrar y cerrar contabilidad** (`accounting.manage`) | 2: `accounting:expense_create`, `accounting:reconcile_balance` |
| accounting_finance | **Consultar contabilidad** (`accounting.view`) | 6: `accounting:balance_sheet`, `accounting:expense_list`, `accounting:export_financial_excel`, `accounting:financial_ai_advisor`, `accounting:financial_dashboard`, `accounting:income_statement` |
| accounting_finance | **Consultar costos, margen y utilidad** (`finance.costs.view`) | 0:  |
| accounting_finance | **Aprobar pagos a proveedores** (`supplier_payments.approve`) | 0:  |
| accounting_finance | **Cancelar pagos a proveedores** (`supplier_payments.cancel`) | 0:  |
| accounting_finance | **Registrar pagos a proveedores** (`supplier_payments.create`) | 1: `purchases:registrar_pago_proveedor` |
| accounting_finance | **Consultar comprobantes de pagos a proveedores** (`supplier_payments.proof.view`) | 0:  |
| accounting_finance | **Rechazar pagos a proveedores** (`supplier_payments.reject`) | 0:  |
| accounting_finance | **Consultar pagos a proveedores** (`supplier_payments.view`) | 0:  |
| collections_payments | **Gestionar cobranza y saldos** (`collections.manage`) | 0:  |
| collections_payments | **Aprobar o rechazar pagos** (`payments.approve`) | 5: `payments:aprobar_pago`, `payments:rechazar_pago`, `sales:actualizar_pago_pedido`, `sales:marcar_pedido_pagado`, `sales:regresar_pedido_a_captura` |
| collections_payments | **Cancelar pagos** (`payments.cancel`) | 1: `payments:cancelar_pago` |
| collections_payments | **Registrar pagos** (`payments.create`) | 0:  |
| collections_payments | **Aplicar saldo a favor a pedidos** (`payments.customer_credit.apply`) | 1: `payments:aplicar_saldo_favor_pedido` |
| collections_payments | **Gestionar saldo a favor de clientes** (`payments.customer_credit.manage`) | 0:  |
| collections_payments | **Revisar comprobantes y chat asociado** (`payments.proof.view`) | 1: `payments:abrir_chat_flotante_pago` |
| collections_payments | **Reembolsar pagos** (`payments.refund`) | 1: `payments:reembolsar_pago` |
| collections_payments | **Confirmar devolución financiera** (`payments.refund.confirm`) | 0:  |
| collections_payments | **Consultar pagos** (`payments.view`) | 3: `payments:detalle_pago`, `payments:lista_pagos`, `sales:pedidos_en_revision_pago` |
| company_admin | **Configurar empresa y bodegas** (`company.configure`) | 1: `companies:branch_photo_upload` |
| company_admin | **Ejecutar importaciones** (`imports.manage`) | 9: `data_imports:dashboard`, `data_imports:download_customer_template`, `data_imports:download_inventory_template`, `data_imports:download_product_template`, `data_imports:download_supplier_template`, `data_imports:upload_customers`, `data_imports:upload_inventory`, `data_imports:upload_products`, `data_imports:upload_suppliers` |
| company_admin | **Configurar integraciones** (`integrations.configure`) | 0:  |
| company_admin | **Configurar canales y agentes** (`messaging.configure`) | 0:  |
| company_admin | **Configurar API propia de envíos** (`shipping.configure`) | 6: `shipping_hub:custom_carriers`, `shipping_hub:integration_create`, `shipping_hub:integrations_list`, `shipping_hub:local_delivery_resources`, `shipping_hub:origin_create`, `shipping_hub:origins_list` |
| company_admin | **Administrar equipo** (`team.manage`) | 2: `accounts:employee_invitation_revoke`, `accounts:employee_jobs_update` |
| direction | **Administrar campos personalizados** (`custom_fields.manage`) | 1: `custom_fields:field_create` |
| direction | **Consultar campos personalizados** (`custom_fields.view`) | 1: `custom_fields:field_list` |
| executive_direction | **Consultar Mesa de Control** (`control_tower.view`) | 0:  |
| executive_direction | **Consultar dashboard CEO** (`dashboard.view`) | 4: `dashboard:ceo_dashboard`, `dashboard:control_flow_dashboard`, `dashboard_legacy:ceo_dashboard`, `dashboard_legacy:control_flow_dashboard` |
| inventory_warehouse | **Administrar inventario** (`inventory.manage`) | 11: `inventory:adoptar_control_lotes`, `inventory:agregar_variante`, `inventory:ajustar_stock`, `inventory:asignar_ubicacion_variante`, `inventory:crear_producto`, `inventory:editar_producto`, `inventory:lot_expiry_alert_sync`, `warehouse:asignar_stock_ubicacion`, `warehouse:auditoria_warehouse`, `warehouse:crear_ubicacion`, `warehouse:transfer_list` |
| inventory_warehouse | **Consultar inventario** (`inventory.view`) | 7: `inventory:detalle_producto`, `inventory:lista_productos`, `inventory:lista_stock`, `inventory:lot_expiry_alerts`, `inventory:movimientos_inventario`, `inventory:stock_critical_alerts`, `warehouse:lot_traceability_list` |
| inventory_warehouse | **Empacar pedidos** (`packing.execute`) | 0:  |
| inventory_warehouse | **Consultar packing** (`packing.view`) | 2: `warehouse:detalle_packing`, `warehouse:lista_packing` |
| inventory_warehouse | **Surtir pedidos** (`picking.execute`) | 3: `warehouse:generar_picking`, `warehouse:modo_pickeo`, `warehouse:modo_pickeo_confirmar_item` |
| inventory_warehouse | **Consultar picking** (`picking.view`) | 3: `warehouse:detalle_picking`, `warehouse:lista_picking`, `warehouse:pendientes_por_enviar` |
| inventory_warehouse | **Aprobar control de calidad** (`quality.approve`) | 0:  |
| inventory_warehouse | **Cancelar recepciones de mercancía** (`receiving.cancel`) | 0:  |
| inventory_warehouse | **Confirmar entrada de mercancía** (`receiving.confirm`) | 0:  |
| inventory_warehouse | **Capturar recepción de mercancía** (`receiving.execute`) | 1: `purchases:crear_recepcion` |
| inventory_warehouse | **Consultar recepciones de mercancía** (`receiving.view`) | 1: `purchases:lista_recepciones` |
| logistics_delivery | **Recolectar lotes de envío** (`shipping.collect`) | 4: `shipping_hub:my_pending_batches`, `shipping_hub:shipment_batch_confirm`, `shipping_hub:shipment_batch_report_missing`, `shipping_hub:shipment_batch_report_rejected` |
| logistics_delivery | **Despachar y confirmar entregas** (`shipping.execute`) | 1: `shipping_hub:incident_resolve_field_expense` |
| logistics_delivery | **Resolver incidencias de envío** (`shipping.incidents.resolve`) | 1: `shipping_hub:incident_resolve_new_label` |
| logistics_delivery | **Consultar tarifas de envío** (`shipping.quote`) | 0:  |
| logistics_delivery | **Consultar envíos y tracking** (`shipping.view`) | 5: `shipping_hub_dispatch:dispatch_center`, `warehouse:detalle_shipping`, `warehouse:lista_shipping`, `warehouse:listos_despacho`, `warehouse:pedidos_en_ruta` |
| purchasing_suppliers | **Autorizar y enviar órdenes de compra** (`purchases.approve`) | 1: `purchases:enviar_orden_compra` |
| purchasing_suppliers | **Cancelar órdenes de compra** (`purchases.cancel`) | 1: `purchases:cancelar_orden_compra` |
| purchasing_suppliers | **Modificar costos de compra** (`purchases.costs.edit`) | 0:  |
| purchasing_suppliers | **Consultar costos de compra** (`purchases.costs.view`) | 0:  |
| purchasing_suppliers | **Crear órdenes de compra** (`purchases.create`) | 1: `purchases:crear_orden_compra` |
| purchasing_suppliers | **Editar órdenes de compra** (`purchases.edit`) | 0:  |
| purchasing_suppliers | **Gestionar compras** (`purchases.manage`) | 0:  |
| purchasing_suppliers | **Consultar órdenes de compra** (`purchases.view`) | 2: `purchases:detalle_orden_compra`, `purchases:lista_compras` |
| purchasing_suppliers | **Gestionar proveedores** (`suppliers.manage`) | 1: `purchases:crear_proveedor` |
| purchasing_suppliers | **Consultar proveedores** (`suppliers.view`) | 1: `purchases:lista_proveedores` |
| sales_service | **Gestionar clientes y oportunidades** (`crm.manage`) | 17: `add_deal_comment`, `add_deal_followup`, `create_deal_modal`, `crm_agenda`, `crm_home`, `crm_kanban`, `crm_search_deals`, `customer_address_create`, `customer_create`, `customer_edit`, `customer_smart_profile`, `customer_smart_profile_by_phone`, `deal_detail_modal`, `duplicate_last_customer_order`, `mark_deal_lost`, `mark_deal_won`, `update_deal_quick` |
| sales_service | **Atender WhatsApp** (`messaging.operate`) | 1: `messaging:chat_inbox` |
| sales_service | **Supervisar y reasignar conversaciones** (`messaging.supervise`) | 0:  |
| sales_service | **Cancelar pedidos de forma segura** (`sales.cancel_order`) | 0:  |
| sales_service | **Cancelar comercialmente pedidos pagados** (`sales.commercial_cancel`) | 0:  |
| sales_service | **Crear pedidos** (`sales.create`) | 3: `sales:crear_cliente_rapido_desde_pedido`, `sales:crear_pedido`, `sales:customer_related_orders_api` |
| sales_service | **Recuperar pedidos por error humano** (`sales.recover_order`) | 0:  |
| sales_service | **Supervisar ventas del equipo** (`sales.supervise`) | 0:  |
| sales_service | **Consultar pedidos** (`sales.view`) | 8: `sales:abrir_chat_cliente_desde_pedido`, `sales:customer_shipping_addresses_api`, `sales:incidencias_pedidos`, `sales:lista_pedidos`, `sales:pedidos_aprobados`, `sales:pedidos_en_curso`, `sales:pedidos_sin_pagar`, `warehouse:timeline_pedido` |

## Cómo repetir el barrido

Los scripts de la auditoría (`barrido.py`, `analizar.py`, `matriz.py`) corren contra una **copia** de la BD de desarrollo. No se dejan en el repositorio. Las pruebas que sí quedan, y que corren con la suite normal, son:

- `companies/test_permisos_pantallas.py`
- `warehouse/test_permisos_por_bodega.py`
- `shipping_hub/test_ids_ajenos_404.py`
