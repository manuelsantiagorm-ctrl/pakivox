from django.urls import path

from legal.views import legal_document

app_name = "legal"

urlpatterns = [
    path("<slug:slug>/", legal_document, name="document"),
]
