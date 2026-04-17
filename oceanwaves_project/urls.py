from django.contrib import admin
from django.urls import path, include
from django.http import HttpResponse
from django.conf import settings
from django.conf.urls.static import static

def home(request):
    return HttpResponse("App Live ✅")

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', home),   # IMPORTANT: root route
    path('', include('pos.urls')),  # keep your app routes
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
