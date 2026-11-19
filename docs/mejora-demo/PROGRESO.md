# Progreso · mejora para demostración

Archivo vivo para continuar sin repetir el diagnóstico. Lo más nuevo va arriba.

## Estado: entregado (28 oct 2026)

Ver `REPORTE_FINAL.md` (qué se hizo, pruebas y pendientes), `GUIA_DEMO.md` (recorrido de 5 minutos) y `PRUEBAS_DE_CARGA.md` (capacidad medida).

## Hecho

- [x] Diagnóstico y plan (`DIAGNOSTICO.md`), incluida la segunda ronda (barrido de 135 pantallas).
- [x] P0: freno de login, alta local cerrada, usuario sin empresa → onboarding, páginas 403/404/500, botón de Google tolerante, recuperar contraseña, modal Premium honesto, HTML de `base.html` válido.
- [x] Prueba automática de rutas privadas sin sesión.
- [x] Navegación: "Volver" con contexto, aviso de cambios sin guardar y freno a doble envío (`static/js/navegacion.js`).
- [x] Lenguaje: menú, migas y títulos en español claro.
- [x] Datos de demostración: `python manage.py crear_demo_pakivox`.
- [x] Flujos reparados: bienvenida (500), activar invitación (500), editar producto, alta de cliente en CRM, etapas del Kanban, creación rápida (500), pantalla de suscripción honesta, 403 con motivo real.
- [x] Celular y tablet: sin desbordes en 390/820/844/1024 px; tarjetas en empaque/envíos; saltar al contenido y foco visible.
- [x] Rendimiento: inventario del dashboard en BD, catálogo y surtido paginados, conteo de pedidos por estado en una consulta.
- [x] Prueba de carga reproducible (`carga/locustfile.py`) y resultados medidos.
- [x] Reporte final y guía de demo.

## Siguiente (si se continúa)

- [ ] Memorizar por petición las bodegas accesibles (`base.html` las consulta 9 veces por página).
- [ ] Caché por empresa de los agregados del Dashboard CEO, invalidada por eventos.
- [ ] Seguir sacando `<style>`/`<script>` en línea de las plantillas antiguas, pantalla por pantalla.
- [ ] Menú del dueño por grupos, junto con los candados por plan (Fase 4).

## Cómo probar

```bash
python manage.py test accounts.test_seguridad_acceso accounts.test_pagina_403 accounts.test_plantillas_compilan \
  crm.test_alta_cliente inventory.test_editar_producto inventory.test_lista_productos_rendimiento \
  subscriptions.test_pantalla_acceso companies.test_crear_demo dashboard.test_inventario_resumen
```
