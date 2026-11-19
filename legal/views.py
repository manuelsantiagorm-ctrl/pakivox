from django.http import Http404
from django.shortcuts import render
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_GET

from legal.documents import DOCUMENTS


@require_GET
@cache_page(60 * 15)
def legal_document(request, slug):
    document = DOCUMENTS.get(slug)
    if document is None:
        raise Http404("Documento legal no encontrado")
    return render(
        request,
        document["template"],
        {"document": document, "document_slug": slug},
    )
