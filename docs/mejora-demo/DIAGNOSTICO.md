# Diagnóstico y plan · mejora para demostración a clientes

Fecha: 28 de octubre de 2026 · Rama: `mejora-demo-clientes` (sale de `main` en `ec049a6`).

## Cómo está el sistema

- Django 5.2 + PostgreSQL (producción) / SQLite (desarrollo), Celery + Redis, Gunicorn + Nginx en AWS Lightsail.
- 19 apps, 170 plantillas, 1,963 pruebas automatizadas.
- Multiempresa con aislamiento por `company`; permisos por capacidades (`capability_required` + `ROUTE_ACTION_CONTRACTS`).
- Ya existía navegación de regreso determinística (`accounts/navigation.py`) con breadcrumbs y ruta de respaldo por módulo.

## Paleta

Se usa la paleta que Manuel eligió el 27 de octubre de 2026 (está en la conversación y ya aplicada a inicio, login, registro, marco del sistema y Dashboard CEO):
`#021024` `#052659` `#5483B3` `#7DA0CA` `#C1E8FF`. Sobre fondo oscuro: bien `#8EB69B`, aviso `#F0B856`, problema `#F17882`.

## Hallazgos (ordenados por prioridad)

### P0 · seguridad y flujos rotos

| # | Hallazgo | Riesgo | Estado |
|---|---|---|---|
| 1 | El inicio de sesión no tenía freno contra fuerza bruta. | Prueba ilimitada de contraseñas. | Corregido: freno por correo (5) y por IP (20) en 15 min. |
| 2 | `/accounts/signup/` (alta local de allauth) estaba abierta. Creaba usuarios sin verificar el correo que después podían crear empresa y prueba gratis desde el onboarding. | Saltarse la verificación del registro oficial; empresas basura. | Corregido: alta local cerrada (adapter) y la ruta redirige al registro oficial. Google sigue abierto. |
| 3 | Un usuario sin empresa que iniciaba sesión era mandado a `logout`, que solo acepta POST: veía un error 405. | Flujo roto (alta con Google). | Corregido: va al onboarding para crear su empresa. |
| 4 | No existían páginas 404 ni 500 propias; la 403 tenía estilos en línea y fondo blanco. | Mala impresión; textos genéricos en inglés. | Corregido: 403/404/500 con la paleta, en español y con salida segura. |
| 5 | El login fallaba (500) si la app de Google no estaba dada de alta en la base. | Entornos nuevos y demos sin Google se caen al mostrar un error de contraseña. | Corregido: el botón de Google se oculta si no está configurado. |
| 6 | No había forma de recuperar la contraseña desde el login. | Usuarios bloqueados, más soporte. | Corregido: enlace "¿Olvidaste tu contraseña?" y páginas de allauth con la marca. |
| 7 | El modal "Premium" mostraba un precio fijo ($4,500/bodega) que no coincide con el catálogo de planes, y promesas no verificables (soporte 24/7, respaldos diarios). | Cifras y capacidades inventadas frente a clientes. | Corregido: textos verificables y el precio real se ve en Stripe. |
| 8 | `base.html` tenía tres `</body>` y scripts después del cierre. | HTML inválido. | Corregido. |

Revisión de rutas: se probaron todas las rutas del sistema sin sesión. Solo responden las públicas (inicio, login, registro, recuperación de contraseña, salud). Quedó como prueba automática (`accounts/test_seguridad_acceso.py`).

### P0 encontrados en la segunda ronda (barrido de 135 pantallas)

Se abrieron todas las pantallas sin parámetros como dueño de la empresa demo, en celular (390 px), tablet (820 y 1024 px) y celular horizontal (844 px).

| # | Hallazgo | Estado |
|---|---|---|
| 9 | La bienvenida después de crear empresa (`/empresa/bienvenida/`) daba error 500: la plantilla tenía `{% load static %}` antes de `{% extends %}`. Todo cliente nuevo la veía. | Corregido + prueba que compila todas las plantillas. |
| 10 | Activar cuenta por invitación daba error 500: la plantilla estaba cortada a la mitad desde que entró al repositorio. | Corregido (formulario completo y mensajes de invitación inválida/vencida). |
| 11 | "Editar producto" solo mostraba "pendiente de conectar" y regresaba. La plantilla además pedía elegir **empresa**. | Implementado: edición acotada a la empresa, con bloqueo de fila; SKU base y unidades no se cambian. |
| 12 | "Nuevo cliente" del CRM mandaba al admin de Django (el dueño no puede entrar). El Kanban de una empresa nueva pedía crear etapas en el admin. | Alta de cliente dentro del CRM; etapas iniciales automáticas; enlaces al admin solo para staff. |
| 13 | Creación rápida de categoría/marca daba error 500 (y respondía "ok" sin crear nada). | Ahora responde 501 honesto. Ninguna pantalla la usa. |
| 14 | Al abrir WhatsApp sin plan con WhatsApp se mostraba "Suscripción vencida, tu operación está en pausa", un precio fijo de $4,500 que no coincide con el catálogo y un botón que crearía **un segundo cobro**. | La pantalla distingue suscripción vencida de función no incluida; sin precio inventado ni botón de cobro cuando la suscripción está vigente. |
| 15 | Compras, CRM, pagos, finanzas, empaque, envíos, importaciones y la mesa de control se salían del ancho del celular (contenido cortado a la derecha). | Corregido; el barrido ya no encuentra desbordes en 390, 820, 844 y 1024 px. |

Sigue igual a propósito: `/warehouse/traspasos/nuevo/`, registrar entrada y ubicar mercancía responden 403 en "Todas las bodegas" porque piden elegir una bodega específica (regla de seguridad existente; la página 403 lo explica).

### P1 · experiencia, móvil y navegación

- Términos técnicos en inglés en la interfaz (picking, packing, shipping, tracking, "Active system").
- El regreso no conservaba filtros, búsqueda ni posición del listado, y no respetaba si se llegó desde el dashboard.
- No había protección global contra doble envío de formularios ni aviso al salir con cambios sin guardar.
- En celular la barra inferior podía tapar el final del contenido.
- 111 de 170 plantillas todavía tienen `<style>` en línea y 36 tienen `<script>` en línea (deuda histórica; se ataca por pantallas, no de golpe).

### P2 · rendimiento

- El Dashboard CEO recorre en Python todas las existencias (`for stock in stock_qs`) en cada carga.
- El cache es memoria local por proceso: los tokens de exportación del copiloto y el freno de login no se comparten entre procesos de Gunicorn.

## Plan

1. P0 (hecho arriba) con pruebas.
2. Navegación: regreso con contexto (dashboard / listado con filtros / posición), aviso de cambios sin guardar y prevención de doble envío para todo el sistema.
3. Lenguaje claro: menús y pantallas principales en español de mayorista.
4. Móvil: barra inferior sin tapar contenido, tablas con desplazamiento controlado, objetivos táctiles de 44 px y foco visible.
5. Datos de demostración en una empresa separada y marcada.
6. Rendimiento del dashboard y prueba de carga local reproducible.
7. Reporte, guía de demo de 5 minutos.

El menú del dueño por grupos (Mi empresa, Ventas, Operación…) NO se toca: Manuel decidió el 14 de septiembre de 2026 construirlo junto con los candados por plan (Fase 4).
