from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import date
from decimal import Decimal
from pos.models import Store, UserProfile, Product, Sale, SaleItem

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

