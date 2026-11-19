# En el archivo principal config/urls.py:

from django.urls import include, path

urlpatterns = [
    # ... tus rutas actuales ...

    path(
        "shipping-hub/",
        include("shipping_hub.urls"),
    ),
]
