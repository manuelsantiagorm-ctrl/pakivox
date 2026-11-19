# Cerca de tus demás variables de entorno en settings.py:

import os

SHIPPING_HUB_ENCRYPTION_KEY = os.getenv(
    "SHIPPING_HUB_ENCRYPTION_KEY",
    "",
)
