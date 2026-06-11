from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import date
from decimal import Decimal
from pos.models import Store, UserProfile, Product, Sale, SaleItem, WholesaleCustomer, CreditRecord, CreditPayment

class ReportsViewTestCase(TestCase):
    def setUp(self):
        # Create user
        self.user = User.objects.create_user(username='testowner', password='password123')
        
        # Create store
        self.store = Store.objects.create(name='Test Store', is_active=True)
        
        # Create user profile with OWNER role
        self.profile = UserProfile.objects.create(
            user=self.user,
            role='OWNER',
            store=self.store,
            is_active=True
        )
        
        # Create a product
        self.product = Product.objects.create(
            store=self.store,
            name='Test Fish',
            category='FISH',
            retail_price=Decimal('100.00'),
            wholesale_price=Decimal('80.00'),
            cost_price=Decimal('50.00'),
            stock_quantity=Decimal('100.00'),
            is_active=True
        )
        
        # Create a retail sale
        self.retail_sale = Sale.objects.create(
            store=self.store,
            bill_type='RETAIL',
            subtotal=Decimal('200.00'),
            grand_total=Decimal('200.00'),
            created_by=self.user,
            created_at=timezone.now()
        )
        self.retail_item = SaleItem.objects.create(
            sale=self.retail_sale,
            product=self.product,
            product_name=self.product.name,
            quantity=Decimal('2.00'),
            cost_price=Decimal('50.00'),
            selling_price=Decimal('100.00'),
            total_amount=Decimal('200.00'),
            total_cost=Decimal('100.00'),
            profit=Decimal('100.00')
        )
        
        # Create a wholesale sale
        self.wholesale_sale = Sale.objects.create(
            store=self.store,
            bill_type='WHOLESALE',
            subtotal=Decimal('400.00'),
            grand_total=Decimal('400.00'),
            created_by=self.user,
            created_at=timezone.now()
        )
        self.wholesale_item = SaleItem.objects.create(
            sale=self.wholesale_sale,
            product=self.product,
            product_name=self.product.name,
            quantity=Decimal('5.00'),
            cost_price=Decimal('50.00'),
            selling_price=Decimal('80.00'),
            total_amount=Decimal('400.00'),
            total_cost=Decimal('250.00'),
            profit=Decimal('150.00')
        )

        self.client = Client()

    def test_reports_view_data(self):
        # Log in the user
        self.client.login(username='testowner', password='password123')
        
        # Call the reports page
        response = self.client.get('/reports/')
        
        # Assert OK status code
        self.assertEqual(response.status_code, 200)
        
        # Verify context variables
        self.assertIn('total_retail_qty', response.context)
        self.assertIn('total_wholesale_qty', response.context)
        self.assertIn('total_qty', response.context)
        self.assertIn('product_summary', response.context)
        self.assertIn('type_breakdown', response.context)
        
        # Verify specific aggregated values
        self.assertEqual(response.context['total_retail_qty'], 2.0)
        self.assertEqual(response.context['total_wholesale_qty'], 5.0)
        self.assertEqual(response.context['total_qty'], 7.0)
        
        # Check product summary details
        prod_sum = response.context['product_summary']
        self.assertEqual(len(prod_sum), 1)
        self.assertEqual(prod_sum[0]['product_name'], 'Test Fish')
        self.assertEqual(prod_sum[0]['retail_qty'], Decimal('2.00'))
        self.assertEqual(prod_sum[0]['wholesale_qty'], Decimal('5.00'))
        self.assertEqual(prod_sum[0]['qty'], Decimal('7.00'))
        
        # Check type breakdown details
        type_breakdown = response.context['type_breakdown']
        wholesale_breakdown = next(t for t in type_breakdown if t['bill_type'] == 'WHOLESALE')
        retail_breakdown = next(t for t in type_breakdown if t['bill_type'] == 'RETAIL')
        
        self.assertEqual(wholesale_breakdown['total_qty'], Decimal('5.00'))
        self.assertEqual(wholesale_breakdown['count'], 1)
        
        self.assertEqual(retail_breakdown['total_qty'], Decimal('2.00'))
        self.assertEqual(retail_breakdown['count'], 1)
        
        # Verify content renders in HTML
        html_content = response.content.decode('utf-8')
        self.assertIn('Retail Qty (kg)', html_content)
        self.assertIn('Wholesale Qty (kg)', html_content)
        self.assertIn('Total Qty (kg)', html_content)
        self.assertIn('2.000', html_content)
        self.assertIn('5.000', html_content)
        self.assertIn('7.000', html_content)


class CustomerAndCreditsTestCase(TestCase):
    def setUp(self):
        from pos.models import WholesaleCustomer, CreditRecord
        
        # Create two stores
        self.store_tanuku = Store.objects.create(name='Tanuku Store', is_active=True, code='TNK')
        self.store_eluru = Store.objects.create(name='Eluru Store', is_active=True, code='ELR')
        
        # Create users
        self.superadmin = User.objects.create_user(username='superadmin', password='password123')
        UserProfile.objects.create(user=self.superadmin, role='SUPERADMIN', is_active=True)
        
        self.owner_tanuku = User.objects.create_user(username='ownertanuku', password='password123')
        UserProfile.objects.create(user=self.owner_tanuku, role='OWNER', store=self.store_tanuku, is_active=True)
        
        self.owner_eluru = User.objects.create_user(username='ownereluru', password='password123')
        UserProfile.objects.create(user=self.owner_eluru, role='OWNER', store=self.store_eluru, is_active=True)
        
        # Create products
        self.prod_tanuku = Product.objects.create(
            store=self.store_tanuku, name='TNK Fish', category='FISH',
            retail_price=Decimal('10.00'), wholesale_price=Decimal('8.00'), cost_price=Decimal('5.00')
        )
        self.prod_eluru = Product.objects.create(
            store=self.store_eluru, name='ELR Fish', category='FISH',
            retail_price=Decimal('10.00'), wholesale_price=Decimal('8.00'), cost_price=Decimal('5.00')
        )
        
        # Create customers
        self.cust_tanuku = WholesaleCustomer.objects.create(
            name='Tanuku Client', store=self.store_tanuku, customer_code='OW-TNK-001', is_credit_enabled=True
        )
        self.cust_eluru = WholesaleCustomer.objects.create(
            name='Eluru Client', store=self.store_eluru, customer_code='OW-ELR-001', is_credit_enabled=True
        )
        
        # Create credit sales
        self.sale_tanuku = Sale.objects.create(
            store=self.store_tanuku, bill_type='WHOLESALE', subtotal=Decimal('100.00'), grand_total=Decimal('100.00'),
            wholesale_customer=self.cust_tanuku, created_at=timezone.now()
        )
        CreditRecord.objects.create(
            customer=self.cust_tanuku, sale=self.sale_tanuku, amount=Decimal('100.00'), due_date=timezone.now().date()
        )
        
        self.sale_eluru = Sale.objects.create(
            store=self.store_eluru, bill_type='WHOLESALE', subtotal=Decimal('200.00'), grand_total=Decimal('200.00'),
            wholesale_customer=self.cust_eluru, created_at=timezone.now()
        )
        CreditRecord.objects.create(
            customer=self.cust_eluru, sale=self.sale_eluru, amount=Decimal('200.00'), due_date=timezone.now().date()
        )
        
        self.client = Client()

    def test_wholesale_customer_list_scoping(self):
        # 1. Superadmin sees all customers
        self.client.login(username='superadmin', password='password123')
        response = self.client.get('/wholesale-customers/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['customers']), 2)
        
        # 2. Tanuku owner only sees Tanuku client
        self.client.login(username='ownertanuku', password='password123')
        response = self.client.get('/wholesale-customers/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['customers']), 1)
        self.assertEqual(response.context['customers'][0].name, 'Tanuku Client')
        
        # 3. Eluru owner only sees Eluru client
        self.client.login(username='ownereluru', password='password123')
        response = self.client.get('/wholesale-customers/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['customers']), 1)
        self.assertEqual(response.context['customers'][0].name, 'Eluru Client')

    def test_credits_list_filtering(self):
        # 1. Superadmin sees all credits
        self.client.login(username='superadmin', password='password123')
        response = self.client.get('/credits/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['summaries']), 2)
        
        # 2. Superadmin filters credits by Tanuku store
        response = self.client.get('/credits/', {'store_filter': self.store_tanuku.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['summaries']), 1)
        self.assertEqual(response.context['summaries'][0]['customer'].name, 'Tanuku Client')
        
        # 3. Superadmin filters credits by Eluru store
        response = self.client.get('/credits/', {'store_filter': self.store_eluru.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['summaries']), 1)
        self.assertEqual(response.context['summaries'][0]['customer'].name, 'Eluru Client')
        
        # 4. Tanuku owner only sees Tanuku credits, even without filter
        self.client.login(username='ownertanuku', password='password123')
        response = self.client.get('/credits/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['summaries']), 1)
        self.assertEqual(response.context['summaries'][0]['customer'].name, 'Tanuku Client')


class RBACPermissionTestCase(TestCase):
    def setUp(self):
        self.store = Store.objects.create(name='TNK Store', is_active=True, code='TNK')
        
        # Cashier
        self.cashier_user = User.objects.create_user(username='cashier', password='password123')
        self.cashier_profile = UserProfile.objects.create(
            user=self.cashier_user, role='STAFF', store=self.store, is_active=True
        )
        
        # Owner
        self.owner_user = User.objects.create_user(username='owner', password='password123')
        self.owner_profile = UserProfile.objects.create(
            user=self.owner_user, role='OWNER', store=self.store, is_active=True
        )

        # Product
        self.product = Product.objects.create(
            store=self.store, name='Rohu Fish', category='FISH',
            retail_price=Decimal('100.00'), wholesale_price=Decimal('80.00'), cost_price=Decimal('50.00')
        )
        self.client = Client()

    def test_cashier_dashboard_hides_profit_cost(self):
        # Cashier login
        self.client.login(username='cashier', password='password123')
        response = self.client.get('/dashboard/')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('today_profit', response.context)
        self.assertNotIn('today_cost', response.context)
        self.assertNotIn('week_profit', response.context)

        # Owner login
        self.client.login(username='owner', password='password123')
        response = self.client.get('/dashboard/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('today_profit', response.context)
        self.assertIn('today_cost', response.context)
        self.assertIn('week_profit', response.context)

    def test_cashier_blocked_from_product_actions(self):
        self.client.login(username='cashier', password='password123')
        
        # Add product
        response = self.client.post('/inventory/add/', {'name': 'New Fish', 'category': 'FISH', 'cost_price': 10, 'retail_price': 12, 'wholesale_price': 11})
        self.assertRedirects(response, '/dashboard/')
        
        # Edit product
        response = self.client.post(f'/inventory/{self.product.id}/edit/', {'name': 'Updated Fish'})
        self.assertRedirects(response, '/dashboard/')

        # Restock product
        response = self.client.post(f'/inventory/{self.product.id}/restock/', {'add_quantity': 10})
        self.assertRedirects(response, '/dashboard/')

        # Delete product
        response = self.client.post(f'/inventory/{self.product.id}/delete/')
        self.assertRedirects(response, '/dashboard/')

    def test_cashier_blocked_from_expenses(self):
        self.client.login(username='cashier', password='password123')
        
        # Expenses page
        response = self.client.get('/expenses/')
        self.assertRedirects(response, '/dashboard/')
        
        # Add expense
        response = self.client.post('/expenses/add/', {'category': 'MISC', 'amount': 100, 'description': 'test'})
        self.assertRedirects(response, '/dashboard/')


class CreditDeletionTestCase(TestCase):
    def setUp(self):
        from pos.models import WholesaleCustomer, CreditRecord, CreditPayment
        self.store = Store.objects.create(name='Tanuku Store', is_active=True, code='TNK')
        
        # Admin
        self.admin = User.objects.create_user(username='admin', password='password123')
        UserProfile.objects.create(user=self.admin, role='SUPERADMIN', is_active=True)

        # Owner
        self.owner = User.objects.create_user(username='owner', password='password123')
        UserProfile.objects.create(user=self.owner, role='OWNER', store=self.store, is_active=True)

        self.customer = WholesaleCustomer.objects.create(
            name='Test Client', store=self.store, customer_code='OW-TNK-001', is_credit_enabled=True
        )
        self.credit = CreditRecord.objects.create(
            customer=self.customer, amount=Decimal('100.00'), due_date=timezone.now().date()
        )
        self.payment = CreditPayment.objects.create(
            customer=self.customer, amount=Decimal('50.00')
        )
        self.client = Client()

    def test_credit_and_payment_deletion_by_admin(self):
        # 1. Non-admin fails to delete credit
        self.client.login(username='owner', password='password123')
        response = self.client.post(f'/credits/{self.credit.id}/delete/')
        self.assertRedirects(response, '/credits/')
        self.assertTrue(CreditRecord.objects.filter(id=self.credit.id).exists())

        # 2. Non-admin fails to delete payment
        response = self.client.post(f'/credits/payment/{self.payment.id}/delete/')
        self.assertRedirects(response, '/credits/')
        self.assertTrue(CreditPayment.objects.filter(id=self.payment.id).exists())

        # 3. Admin successfully deletes credit
        self.client.login(username='admin', password='password123')
        response = self.client.post(f'/credits/{self.credit.id}/delete/')
        self.assertRedirects(response, f'/credits/customer/{self.customer.id}/')
        self.assertFalse(CreditRecord.objects.filter(id=self.credit.id).exists())

        # 4. Admin successfully deletes payment
        response = self.client.post(f'/credits/payment/{self.payment.id}/delete/')
        self.assertRedirects(response, f'/credits/customer/{self.customer.id}/')
        self.assertFalse(CreditPayment.objects.filter(id=self.payment.id).exists())


class CrossStoreIsolationTestCase(TestCase):
    def setUp(self):
        from pos.models import WholesaleCustomer
        self.store_t = Store.objects.create(name='Tanuku', is_active=True, code='TNK')
        self.store_e = Store.objects.create(name='Eluru', is_active=True, code='ELR')

        # Owner Tanuku
        self.user_t = User.objects.create_user(username='ownert', password='password123')
        UserProfile.objects.create(user=self.user_t, role='OWNER', store=self.store_t, is_active=True)

        self.cust_t = WholesaleCustomer.objects.create(name='TNK Cust', store=self.store_t, is_credit_enabled=True)
        self.cust_e = WholesaleCustomer.objects.create(name='ELR Cust', store=self.store_e, is_credit_enabled=True)
        
        self.client = Client()

    def test_cross_store_access_denied(self):
        self.client.login(username='ownert', password='password123')

        # Attempt to view Eluru customer details
        response = self.client.get(f'/credits/customer/{self.cust_e.id}/')
        self.assertRedirects(response, '/credits/')

        # Attempt to edit Eluru customer
        response = self.client.post(f'/wholesale-customers/{self.cust_e.id}/edit/', {'name': 'Hacked Name'})
        self.assertRedirects(response, '/wholesale-customers/')
        
        # Verify name did not change
        self.cust_e.refresh_from_db()
        self.assertEqual(self.cust_e.name, 'ELR Cust')


