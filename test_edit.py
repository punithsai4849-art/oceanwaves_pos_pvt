import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'oceanwaves_project.settings')
django.setup()

from django.test import RequestFactory
from django.contrib.auth.models import User
from pos.models import WholesaleCustomer, Store, UserProfile
from pos.views import wholesale_customer_edit
from django.contrib.messages.storage.fallback import FallbackStorage

# Create user & profile
user, _ = User.objects.get_or_create(username='testadmin')
profile, _ = UserProfile.objects.get_or_create(user=user, role='SUPERADMIN')

# Create store & customer
store, _ = Store.objects.get_or_create(name='Test Store')
wc, created = WholesaleCustomer.objects.get_or_create(name='Test Cust', defaults={'store': store})

print("Before:", wc.phone)

factory = RequestFactory()
request = factory.post(f'/wholesale-customers/{wc.id}/edit/', {
    'name': 'Test Cust',
    'phone': '1234567890',
    'customer_code': wc.customer_code,
    'email': 'test@test.com',
    'gst': '123',
    'credit_duration_days': '15',
    'is_credit_enabled': 'on',
})
request.user = user
setattr(request, 'session', 'session')
messages = FallbackStorage(request)
setattr(request, '_messages', messages)

response = wholesale_customer_edit(request, wc.id)
print("Response status:", response.status_code)
print("Response url:", response.url)

wc.refresh_from_db()
print("After:", wc.phone)

