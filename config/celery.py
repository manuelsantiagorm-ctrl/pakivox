import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("config")

# Lee la configuración de Celery desde settings.py, usando el
# prefijo CELERY_ (ej. CELERY_BROKER_URL en vez de BROKER_URL).
app.config_from_object("django.conf:settings", namespace="CELERY")

# Descubre automáticamente archivos tasks.py en cada app instalada.
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")