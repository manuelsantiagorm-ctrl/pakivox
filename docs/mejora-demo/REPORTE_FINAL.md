# Reporte final · mejora de PAKIVOX para demostración a clientes

Fecha: 28 de octubre de 2026. Rama: `mejora-demo-clientes`, que sale de `main` en `ec049a6`. No hay migraciones nuevas y no se tocó producción.

## Qué se mejoró y para qué le sirve al cliente

**Seguridad y acceso (P0)**
- **Freno a intentos de contraseña:** 5 por correo y 20 por IP cada 15 minutos.
- **Alta local de allauth cerrada.** Ya no se crean cuentas sin verificar el correo.
- **Recuperar contraseña** desde el inicio de sesión.
- **Usuario sin empresa:** va a crearla en lugar de ver un error 405.
- **Prueba automática:** confirma que ninguna ruta privada responde sin sesión.

**Pantallas que estaban rotas**
- **Bienvenida tras crear la empresa** daba error 500, y la veía todo cliente nuevo.
- **Activar cuenta por invitación** daba error 500: la plantilla estaba cortada.
- **Editar producto** solo decía "pendiente de conectar". Ahora edita de verdad: nombre, categoría, marca, código de barras, precios, imagen y estado.
- **CRM:**
  - "Nuevo cliente" llevaba al admin de Django; ahora el alta se hace dentro del CRM.
  - El Kanban de una empresa nueva ya trae sus etapas iniciales.
- **Pantalla de suscripción:**
  - Ya no le dice "vencida" a quien tiene su suscripción vigente.
  - Ya no muestra un precio inventado.
  - Ya no ofrece un botón que generaría un segundo cobro.
- **Páginas 403, 404 y 500** en español, con la marca. La 403 ahora explica el motivo real, por ejemplo "Primero elige una bodega".

**Navegación**
- **"Volver"** regresa a donde estabas: el tablero, o el listado con la misma búsqueda, filtros y posición. Si entraste por un enlace directo, usa una ruta segura de respaldo.
- **Migas de pan** en español.
- **Aviso** antes de salir con cambios sin guardar.
- **Freno global al doble envío** de formularios.

**Celular y tablet**
- Ninguna pantalla principal se sale del ancho. Probado en 390, 820, 844 y 1024 px sobre 135 pantallas.
- Listados de empaque y envíos como tarjetas.
- Encabezado sin desbordes en tablet.
- Enlace "Saltar al contenido" y foco visible para quien usa teclado.

**Diseño:** paleta azul de marca (#021024, #052659, #5483B3, #7DA0CA, #C1E8FF) en inicio, login, registro, marco del sistema, Dashboard CEO y las pantallas tocadas.

**Dashboard CEO**
- Centro de mando con salud del negocio de 0 a 100 y el río "De la compra al cobro".
- Pestañas de Operación, Finanzas y Actividad rediseñadas.
- Todo con datos reales del libro financiero.

**Rendimiento**
- El inventario del dashboard se suma en la base de datos.
- Catálogo y surtido paginados; el catálogo ya no hace una consulta por cada SKU.
- Los pedidos por estado se cuentan en una sola consulta en lugar de diez.

**Demostración:** `python manage.py crear_demo_pakivox` crea una empresa "(DEMO)" aislada, con datos ficticios marcados como tales.

**Código:** el CSS y el JS de las pantallas tocadas ya no van dentro de las plantillas; están en `static/style/<nombre>.css` y `static/js/<nombre>.js`.

## Pantallas y flujos que quedaron completos

- **Acceso:** inicio de sesión, registro, recuperar contraseña y activar invitación.
- **Bienvenida** después de crear la empresa.
- **Dashboard CEO** con sus cuatro pestañas.
- **Inventario:** productos (buscar, paginar, ver y editar), existencias y movimientos.
- **CRM:** inicio, nuevo cliente y Kanban de una empresa nueva.
- **Compras:** listado.
- **Almacén:** surtido, empaque y envíos, en celular y tablet.
- **Mensajes de acceso:** suscripción y función no incluida, y páginas de error.

## Cómo iniciar y probar

```bash
python manage.py migrate
python manage.py init_plans                     # si la base no tiene planes
python manage.py crear_demo_pakivox --clave "TuClave2026!"
python manage.py runserver
# Entra con demo@pakivox.demo y esa clave
```

Pruebas, siempre indicando los módulos:

```bash
python manage.py test companies accounts subscriptions inventory purchases sales payments crm \
  warehouse shipping_hub messaging accounting dashboard analytics data_imports custom_fields \
  notifications legal owner
```

Para desplegar: `git push` de la rama, revisarla, unirla a `main` y luego `./deploy.sh` con `collectstatic` (hay CSS y JS nuevos).

## Pruebas ejecutadas (resultados reales, PostgreSQL 16)

| Corrida | Pruebas | Resultado |
|---|---:|---|
| Línea base (`main` en `ec049a6`) | 1,963 | 4 errores |
| Esta rama (corrida final) | 2,003 | 4 errores + 1 falla intermitente |

- **Los 4 errores ya estaban en la línea base:** `shipping_hub.tests_primary_integration_switch`, una prueba que exige PostgreSQL con integraciones primarias. No se tocaron.
- **La falla intermitente también es anterior:** `inventory.tests_lot_reconciliation.LotConcurrencyLT1GTests`, una prueba de concurrencia con hilos.
  - En 9 corridas seguidas pasó **6 veces en la línea base** (sin ningún cambio de esta rama) y **5 veces en esta rama**: la misma inestabilidad en ambas.
  - Esta rama no toca reservas ni lotes. Conviene revisar esa prueba aparte.
- **Pruebas nuevas, 43 en total** (2,003 en la corrida final más las 3 de la página 403, que se corrieron aparte):
  - Alta de cliente y su aislamiento entre empresas.
  - Etapas iniciales del Kanban.
  - Editar producto: 404 para otra empresa, SKU base inmutable y validaciones.
  - Todas las plantillas compilan.
  - Pantalla de suscripción.
  - Página 403.
  - Catálogo paginado con número fijo de consultas.
  - Inventario del dashboard.
  - Datos de demostración.
  - Rutas privadas sin sesión.
- **Revisión visual con navegador automático** de 135 pantallas en celular (390 px), tablet (820 y 1024 px) y celular horizontal (844 px): sin desbordes y sin errores de JavaScript.

## Pendiente, riesgos y configuración externa

- **Menú del dueño por grupos:** no se tocó. Manuel decidió construirlo junto con los candados por plan (Fase 4).
- **Estilos en línea:** quedan plantillas antiguas con `<style>` en línea; se limpian pantalla por pantalla.
- **WhatsApp en la empresa demo:** no se puede mostrar. Exige un pago confirmado en Stripe y no se simula.
- **Producción:**
  - `DJANGO_CACHE_URL` apuntando a Redis, para que el freno de login y los tokens se compartan entre procesos.
  - Revisar `DJANGO_ALLOWED_HOSTS`: el valor por defecto en el código menciona `wms360app.com`.
- **Pantallas que siguen pidiendo elegir una bodega** en "Todas las bodegas": traspasos y registrar entrada. Es una regla de seguridad existente; ahora la 403 lo explica.
- **Plantilla sin uso:** `templates/crm/home..html` es una copia vieja que no compila. Se puede borrar.

## Capacidad de carga

- **Medido** en 2 vCPU con todo en la misma máquina (detalle en `PRUEBAS_DE_CARGA.md`):
  - Hasta unos 12 peticiones por segundo.
  - Tiempos buenos con 25 a 50 usuarios simultáneos.
  - Catálogo de 10,000 SKUs.
- **100,000 usuarios simultáneos no está validado.** Se estima que hacen falta unas 25,000 peticiones por segundo. Para llegar se necesita:
  - Bajar el costo por página.
  - Escalar en horizontal con Redis compartido, CDN y PgBouncer.
  - Réplicas de lectura.
  - Medir por etapas en un entorno de pruebas del tamaño de producción.

## Guía de demostración

Ver `GUIA_DEMO.md`: recorrido de 5 minutos con qué mostrar, en qué orden y qué beneficio decir en cada paso.
