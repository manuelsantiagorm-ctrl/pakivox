# Pruebas de carga · PAKIVOX

Fecha: 28 de octubre de 2026. Todo se midió **en una máquina local de pruebas**, nunca en producción.

## Resumen honesto

| Qué | Estado |
|---|---|
| Escenario de carga reproducible | **Listo** (`docs/mejora-demo/carga/locustfile.py`) |
| Capacidad medida en 2 vCPU | **≈ 12 peticiones por segundo**; tiempos buenos hasta **≈ 25–50 usuarios simultáneos** |
| 100,000 usuarios simultáneos | **No validado.** Requiere infraestructura y trabajo que se describen abajo |

## Entorno de la medición

- 1 máquina de **2 vCPU y 7 GB de RAM**, compartida por todo: la app, PostgreSQL 16 y el generador de carga (Locust). En producción estarían separados, así que esta medición es conservadora.
- Gunicorn: 3 procesos × 4 hilos (`gthread`), `DJANGO_DEBUG=False`, sin Nginx ni caché Redis.
- Datos: empresa de demostración con **10,023 SKUs**, 20,046 registros de existencias en 2 bodegas, 36 pedidos y 8 clientes.

## Escenario

Cada usuario virtual inicia sesión una vez y luego navega como un dueño o un encargado: Dashboard CEO, pedidos, catálogo (con y sin búsqueda, varias páginas), existencias, surtido y CRM. Entre clic y clic espera de 2 a 6 segundos, que es el tiempo de lectura de una persona.

## Resultados (medidos)

| Usuarios simultáneos | Peticiones/s | p50 general | p95 general | Fallos |
|---:|---:|---:|---:|---:|
| 10 | 2.7 | 150 ms | 810 ms | 0 |
| 25 | 6.5 | 180 ms | 950 ms | 0 |
| 50 | 11.1 | 460 ms | 2.8 s | 2 de 1,328 (0.15 %) |
| 100 | 12.2 | 2.9 s | 11 s | 7 de 1,456 (0.5 %) |
| 200 | 12.5 | 6.7 s | 27 s | 5 de 1,491 (0.3 %) |

Por pantalla, con 25 usuarios (p50 / p95): Dashboard CEO 460 ms / 1.1 s · Pedidos 78 ms / 270 ms · Catálogo 190 ms / 350 ms · Existencias 220 ms / 480 ms.

**Lectura:** a partir de unos 50 usuarios el procesador llega a su límite (≈ 12 peticiones por segundo) y el tiempo de respuesta crece. Los fallos fueron conexiones rechazadas por cola llena, no errores de la aplicación (en producción esto lo amortigua Nginx).

## Lo que ya se mejoró y se puede comprobar

- Dashboard CEO: el inventario se suma en la base de datos en lugar de recorrer todas las existencias en Python. Con 10,000 SKUs responde en ≈ 0.3–0.5 s sin carga.
- Catálogo de productos: antes hacía una consulta por cada SKU; ahora usa un número fijo de consultas y página de 50 (`inventory/test_lista_productos_rendimiento.py`).
- Pedidos por estado: una consulta agrupada en lugar de diez.
- Surtido paginado.

## Qué falta para 100,000 usuarios simultáneos

Con el mismo comportamiento (una petición cada ~4 s por usuario), 100,000 usuarios son unas **25,000 peticiones por segundo**. Con el costo actual por petición eso equivale, a grandes rasgos, a **unos 2,000 núcleos de aplicación**. Es una **estimación**, no una medición. Para acercarse de forma realista:

1. **Bajar el costo por petición.** Las cifras con más impacto:
   - El marco (`base.html`) es grande y consulta las bodegas 9 veces por página. Hay que memorizarlas por petición.
   - El Dashboard CEO hace unas 120 consultas. Hay que pasar sus agregados a vistas materializadas o a caché por empresa, invalidada por eventos.
   - Las existencias se ordenan por nombre de producto con joins. Con más de 100,000 SKUs se necesita una columna de orden o un índice de búsqueda.
2. **Escalar horizontalmente:**
   - Varias instancias de la app detrás de un balanceador.
   - Sesiones y caché en Redis compartido (`DJANGO_CACHE_URL` ya está preparado).
   - Archivos estáticos en CDN.
3. **Base de datos:**
   - PgBouncer para las conexiones.
   - Réplicas de lectura para tableros y reportes.
   - Revisión de índices con `pg_stat_statements` bajo carga real.
4. **Trabajo pesado fuera de la petición:** reportes, exportaciones y WhatsApp, con Celery (ya existe).
5. **Validar por etapas** en un entorno de pruebas del tamaño de producción: 1,000 → 10,000 → 100,000, midiendo cada vez.

## Cómo repetir la prueba

```bash
# 1. Base de pruebas (nunca producción) con la empresa demo y un catálogo grande
python manage.py migrate
python manage.py init_plans
python manage.py crear_demo_pakivox --clave "DemoPakivox2026!"

# 2. Servidor parecido a producción
DJANGO_DEBUG=False gunicorn config.wsgi:application -b 127.0.0.1:8001 -w 3 --threads 4 -k gthread

# 3. Carga (otra terminal)
pip install locust
locust -f docs/mejora-demo/carga/locustfile.py --host http://127.0.0.1:8001 \
  --headless -u 50 -r 10 -t 2m --csv resultados/u50
```

Salud del servicio para el balanceador: `GET /messaging/health/live/` (proceso vivo) y `GET /messaging/health/ready/` (base de datos accesible).
