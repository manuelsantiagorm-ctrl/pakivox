# Guía de demostración · 5 minutos

Para mostrar PAKIVOX a un mayorista (ropa u otro producto) sin datos reales de nadie.

## Antes de la cita (una sola vez)

```bash
python manage.py init_plans                      # si la base no tiene planes
python manage.py crear_demo_pakivox --clave "TuClaveDeDemo2026!"
```

- Se crea la empresa **"Moda Mayorista Norte (DEMO)"** con 2 bodegas, catálogo de ropa por tallas, clientes, pedidos en todas las etapas, compras y movimientos.
- Todo está separado de las empresas reales. Los folios empiezan con `DEMO-` y los correos terminan en `.demo`, así que nada le llega a una persona real.
- Entra con `demo@pakivox.demo` y la clave que pusiste.
- En producción el comando se niega a correr salvo con `--permitir-produccion`. Lo ideal es hacer la demo en un entorno de pruebas.
- Para una demo nueva usa `--reemplazar`: la anterior se archiva, no se borra.

Abre el sistema en una laptop y ten a la mano un celular con la misma sesión.

## El recorrido

| Min | Qué mostrar | Qué decir (beneficio concreto) |
|---|---|---|
| 0:00 | **Inicio de sesión** | "Cada empresa ve solo su información; si olvidas la contraseña la recuperas desde aquí." |
| 0:20 | **Dashboard CEO → Resumen** (salud del negocio 0–100) | "En 5 segundos sabes cómo va tu negocio y qué es lo más débil hoy, con una frase en palabras simples." |
| 0:50 | **Río "De la compra al cobro"** | "Ves por dónde va tu mercancía y tu dinero, de la compra al cobro. La etapa en ámbar es donde se están juntando pedidos." Toca la etapa para abrir esos pedidos. |
| 1:30 | **Pestaña Finanzas** | "Utilidad del mes al frente; de cada $100 que vendes sabes cuánto te queda; cuánto te deben y cuánto ya cobraste." |
| 2:10 | **Pestaña Operación** | "Pedidos por surtir, empacar y enviar, con el avance de la bodega." |
| 2:40 | **Inventario → Productos** → buscar "Blusa" → abrir → **Editar** | "Buscas entre miles de productos al instante. Cambias precio o marca y el sistema no te deja poner un mayoreo más caro que la venta." Pulsa **Volver** para regresar al listado con la búsqueda intacta. |
| 3:30 | **CRM → Nuevo cliente** | "Das de alta a un cliente sin salir del CRM; si el teléfono ya existe, te avisa." |
| 4:00 | **Ventas → Pedidos** | "Cada pedido con su estado, su margen y su cobro." |
| 4:30 | **El mismo sistema en el celular** | "Todo funciona igual en el celular o la tablet: para el dueño fuera de la oficina y para el almacén." |

## Qué NO mostrar en la demo

- **WhatsApp** en la empresa demo. Ese módulo solo se activa con un pago confirmado en Stripe y en la demo aparece "Esta función no está activa en tu plan". Si te preguntan, explica que va incluido en los planes con WhatsApp y que los mensajes se pagan según su uso.
- Pantallas de administración técnica (`/admin/`).
- No prometas cifras de capacidad: lo medido está en `PRUEBAS_DE_CARGA.md`.
