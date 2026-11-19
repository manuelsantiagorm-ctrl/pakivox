"""
Politica de proveedores de WhatsApp (migracion a Twilio, 25-sep-2026).

Decision de Manuel: todo WhatsApp se migra a Twilio. La conexion DIRECTA
con Meta (WhatsApp Cloud API + Embedded Signup) NO se borra: queda en el
codigo, APAGADA, por si se vuelve a ocupar.

"Apagada" significa:
  - Ningun numero NUEVO puede quedar con provider="meta" (ni por el alta
    asistida, ni por Embedded Signup, ni por el admin, ni por shell).
  - Un numero que ya era de Meta directo (Jazmin) SIGUE funcionando igual
    -- envia, recibe y se puede editar -- hasta el dia de su migracion a
    Twilio. Apagar Meta para los existentes le cortaria el WhatsApp.
  - La opcion "Conectar WhatsApp con Meta" no se muestra en el menu.

Para volver a encenderla: WHATSAPP_META_DIRECT_ENABLED=1 en el entorno.
Ver claude/twilio-migracion-checklist.md.
"""

from django.conf import settings

META_DIRECT_DISABLED_MESSAGE = (
    "La conexión directa con Meta está deshabilitada: los números nuevos "
    "de WhatsApp se conectan por Twilio."
)


def meta_direct_new_connections_enabled():
    """True solo si se permite dar de alta numeros NUEVOS por Meta directo."""
    return bool(getattr(settings, "WHATSAPP_META_DIRECT_ENABLED", False))


def is_new_meta_direct_connection(*, provider, previous_provider):
    """
    True si el cambio convierte a un numero en Meta directo: un numero
    nuevo (previous_provider None) o uno que antes era de otro proveedor.
    Un numero que ya era de Meta directo no es una conexion nueva.
    """
    return provider == "meta" and previous_provider != "meta"


def meta_direct_connection_blocked(*, provider, previous_provider):
    """True si esta alta/cambio a Meta directo debe rechazarse."""
    return (
        is_new_meta_direct_connection(
            provider=provider,
            previous_provider=previous_provider,
        )
        and not meta_direct_new_connections_enabled()
    )
