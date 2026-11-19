from messaging.provider_policy import meta_direct_new_connections_enabled


def whatsapp_providers(request):
    """
    Banderas de proveedores de WhatsApp para las plantillas (menu).
    Solo booleanos: nunca credenciales ni configuracion interna.
    """
    return {
        "whatsapp_meta_direct_enabled": meta_direct_new_connections_enabled(),
    }
