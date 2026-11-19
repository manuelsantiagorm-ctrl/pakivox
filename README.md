<div align="center">

# PAKIVOX
### Plataforma SaaS de Gestión de Almacenes y Operación Comercial Mayorista

*Sistema multi-tenant B2B construido para operar a escala real: 100,000+ productos, cientos de empresas cliente, miles de bodegas.*

![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django-5.2-092E20?style=flat-square&logo=django&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Production-4169E1?style=flat-square&logo=postgresql&logoColor=white)
![Celery](https://img.shields.io/badge/Celery-Async%20Tasks-37814A?style=flat-square)
![Status](https://img.shields.io/badge/Status-En%20producción-brightgreen?style=flat-square)
![Tests](https://img.shields.io/badge/Tests-380%2B%20passing-success?style=flat-square)

</div>

---

## 📖 ¿Qué es PAKIVOX?

PAKIVOX es una plataforma SaaS que centraliza la operación completa de un negocio mayorista: inventario, ventas, compras, envíos, CRM, cobros y mensajería comercial vía WhatsApp — todo en un solo sistema, con aislamiento total de datos entre cada empresa cliente.

No es un proyecto de demostración: es un sistema en producción activa, sirviendo operación real de almacén y ventas, con integración de pagos, mensajería empresarial de WhatsApp, y una arquitectura pensada desde el diseño para escalar sin comprometer seguridad ni rendimiento.

---

## 🧠 Retos técnicos que resuelve

- **Aislamiento multi-tenant a prueba de fallos.** Cada empresa cliente opera de forma completamente aislada.

- **Mensajería empresarial a escala.** Integración con Meta Cloud API para WhatsApp Business, con procesamiento asíncrono vía Celery.

- **Búsqueda y catálogos que no dependen del tamaño de los datos.** Los catálogos se consultan bajo demanda.

- **Prevención activa de errores operativos costosos.** Detección de posibles pedidos duplicados y recuperación segura de operaciones.

- **Concurrencia segura en operaciones críticas.** Uso de mecanismos de PostgreSQL y Django para proteger operaciones sensibles.

---

## 🛠️ Stack tecnológico

| Capa | Tecnología |
|---|---|
| Backend | Python 3.11 · Django 5.2 |
| Base de datos | PostgreSQL |
| Cola de tareas / async | Celery + Redis |
| Servidor | Gunicorn + Nginx |
| Frontend | HTMX + JavaScript · CSS propio |
| Mensajería | Meta Cloud API — WhatsApp Business |
| Pagos | Stripe |
| Infraestructura | AWS Lightsail |
| Monitoreo | Sentry |

---

## 📁 Arquitectura

El sistema está organizado en múltiples aplicaciones Django, cada una especializada en una responsabilidad de negocio.

<!-- AQUÍ PUEDES SEGUIR PEGANDO EL RESTO DE TU README -->
