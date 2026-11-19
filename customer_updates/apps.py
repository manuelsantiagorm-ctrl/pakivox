from django.apps import AppConfig


class CustomerUpdatesConfig(AppConfig):
    """
    Avisos al cliente sin API de WhatsApp.

    Tres piezas que no cuestan por mensaje:
    - link público de seguimiento del pedido (token secreto, sin sesión);
    - borradores de WhatsApp que se abren con wa.me desde el celular o
      WhatsApp Web del vendedor;
    - correos automáticos al comprador en los hitos del pedido.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "customer_updates"
    verbose_name = "Avisos al cliente"

    def ready(self):
        from . import signals  # noqa: F401
