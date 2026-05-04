from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import decimal, random, string
import uuid
from pathlib import Path
from datetime import timedelta

def _otp_default():
    return ''.join(random.choices(string.digits, k=6))


# ─────────────────────────────────────────────────────────────────────────────
#  STORE
# ─────────────────────────────────────────────────────────────────────────────
class Store(models.Model):
    name             = models.CharField(max_length=200)
    address          = models.TextField(blank=True)
    phone            = models.CharField(max_length=20, blank=True)
    whatsapp_number  = models.CharField(max_length=20, blank=True, help_text='Store WhatsApp number for sending bills (e.g. 919876543210)')
    email            = models.EmailField(blank=True)
    gstin            = models.CharField(max_length=20, blank=True, verbose_name="GSTIN")
    upi_id           = models.CharField(max_length=50, blank=True, help_text='Store UPI ID for receiving payments')
    code             = models.CharField(max_length=15, blank=True, null=True, unique=True, help_text='Short branch code, e.g., TNK')
    is_active        = models.BooleanField(default=True)
    created_at       = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


# ─────────────────────────────────────────────────────────────────────────────
#  USER PROFILE  (ties a user to one store + role)
# ─────────────────────────────────────────────────────────────────────────────
class UserProfile(models.Model):
    ROLE_CHOICES = [
        ('SUPERADMIN', 'Super Admin'),   # sees everything across all stores
        ('SUBADMIN',   'Sub-Admin'),     # restricted admin permissions
        ('OWNER',      'Store Owner'),   # full access within assigned store
        ('STAFF',      'Staff'),         # billing + inventory in assigned store
        ('AREAMANAGER','Area Manager'),   # approves wholesale bills via OTP
        ('WHOLESALE_EXEC', 'Wholesale Executive'), # manage 4-5 stores, stock + wholesale approval
    ]
    user      = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role      = models.CharField(max_length=20, choices=ROLE_CHOICES, default='STAFF')
    store     = models.ForeignKey(Store, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='staff')
    phone          = models.CharField(max_length=20, blank=True, help_text='Contact phone number')
    approval_pin   = models.CharField(max_length=128, blank=True, help_text='Hashed 4-6 digit approval PIN for wholesale approvals')
    is_active = models.BooleanField(default=True)
    
    # New fields
    permissions = models.JSONField(default=dict, blank=True, help_text='Restricted permissions for SUBADMIN')
    expires_at  = models.DateTimeField(null=True, blank=True, help_text='For temporary roles, access expires after this date')

    def __str__(self):
        return f"{self.user.username} ({self.role}) - {self.store}"

    @property
    def is_superadmin(self):
        return self.role == 'SUPERADMIN'

    @property
    def is_owner(self):
        return self.role in ('SUPERADMIN', 'OWNER')

    @property
    def is_staff_role(self):
        return self.role == 'STAFF'

    @property
    def is_area_manager(self):
        return self.role in ('AREAMANAGER', 'WHOLESALE_EXEC')

    @property
    def is_wholesale_exec(self):
        return self.role == 'WHOLESALE_EXEC'
        
    @property
    def is_subadmin(self):
        return self.role == 'SUBADMIN'
        
    @property
    def has_expired(self):
        if self.expires_at and timezone.now() > self.expires_at:
            return True
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  PRODUCT  (scoped to a store)
# ─────────────────────────────────────────────────────────────────────────────
class Product(models.Model):
    CATEGORY_CHOICES = [
        ('FISH',   'Fish'),
        ('PRAWNS', 'Prawns'),
        ('CRAB',   'Crab'),
        ('OTHER',  'Other'),
    ]
    store            = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='products')
    name             = models.CharField(max_length=200)
    barcode          = models.CharField(max_length=100, blank=True, help_text='Barcode number')
    category         = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='FISH', db_index=True)

    # Separate pricing for retail vs wholesale
    retail_price     = models.DecimalField(max_digits=10, decimal_places=2,
                                           help_text="Selling price for RETAIL (per kg)")
    wholesale_price  = models.DecimalField(max_digits=10, decimal_places=2,
                                           help_text="Selling price for WHOLESALE (per kg)")
    cost_price       = models.DecimalField(max_digits=10, decimal_places=2,
                                           help_text="Purchase / cost price (per kg)")

    stock_quantity   = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    low_stock_alert  = models.DecimalField(max_digits=10, decimal_places=3, default=5,
                                           help_text="Alert threshold in kg")
    is_active        = models.BooleanField(default=True, db_index=True)
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"[{self.store.name}] {self.name}"

    @property
    def is_low_stock(self):
        return 0 < self.stock_quantity <= self.low_stock_alert

    @property
    def is_out_of_stock(self):
        return self.stock_quantity <= 0

    class Meta:
        ordering = ['category', 'name']


# ─────────────────────────────────────────────────────────────────────────────
#  SALE
# ─────────────────────────────────────────────────────────────────────────────
class Sale(models.Model):
    BILL_TYPE_CHOICES = [
        ('RETAIL',     'Retail'),
        ('WHOLESALE',  'Wholesale'),
    ]
    PAYMENT_MODE_CHOICES = [
        ('CASH',   'Cash'),
        ('UPI',    'UPI'),
        ('ONLINE', 'Online'),
        ('CARD',   'Card'),
        ('CREDIT', 'Credit'),
    ]

    store          = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='sales')
    bill_number    = models.CharField(max_length=60, unique=True)
    bill_type      = models.CharField(max_length=20, choices=BILL_TYPE_CHOICES, default='RETAIL', db_index=True)

    # Customer (required for wholesale)
    wholesale_customer = models.ForeignKey('WholesaleCustomer', on_delete=models.SET_NULL, null=True, blank=True, related_name='sales')
    customer_name    = models.CharField(max_length=200, blank=True)
    customer_phone   = models.CharField(max_length=15,  blank=True)
    customer_gst     = models.CharField(max_length=20,  blank=True)
    customer_address = models.TextField(blank=True)

    # Financials
    subtotal       = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    gst_rate       = models.DecimalField(max_digits=5,  decimal_places=2, default=0)
    cgst_amount    = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sgst_amount    = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_gst      = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    grand_total    = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount       = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    payment_mode   = models.CharField(max_length=20, choices=PAYMENT_MODE_CHOICES, default='CASH')
    notes          = models.TextField(blank=True)
    created_by     = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at     = models.DateTimeField(default=timezone.now, db_index=True)

    def __str__(self):
        return f"#{self.bill_number} [{self.store.name}] ₹{self.grand_total}"

    def save(self, *args, **kwargs):
        if not self.bill_number:
            # Store-scoped sequential bill numbers
            last = Sale.objects.filter(store=self.store).order_by('-id').first()
            num  = (last.id + 1) if last else 1
            pfx  = 'WS' if self.bill_type == 'WHOLESALE' else 'RT'
            # Use store id prefix to keep cross-store unique
            self.bill_number = f"S{self.store_id}-{pfx}{str(num).zfill(5)}"
        super().save(*args, **kwargs)

    class Meta:
        ordering = ['-created_at']


class SaleItem(models.Model):
    sale           = models.ForeignKey(Sale, related_name='items', on_delete=models.CASCADE)
    product        = models.ForeignKey(Product, on_delete=models.PROTECT)
    product_name   = models.CharField(max_length=200)   # snapshot
    quantity       = models.DecimalField(max_digits=10, decimal_places=3)
    cost_price     = models.DecimalField(max_digits=10, decimal_places=2)
    selling_price  = models.DecimalField(max_digits=10, decimal_places=2)
    total_amount   = models.DecimalField(max_digits=12, decimal_places=2)
    total_cost     = models.DecimalField(max_digits=12, decimal_places=2)
    profit         = models.DecimalField(max_digits=12, decimal_places=2)

    def save(self, *args, **kwargs):
        self.total_amount = (self.quantity * self.selling_price).quantize(decimal.Decimal('0.01'))
        self.total_cost   = (self.quantity * self.cost_price).quantize(decimal.Decimal('0.01'))
        self.profit       = self.total_amount - self.total_cost
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product_name} × {self.quantity} kg"


# ─────────────────────────────────────────────────────────────────────────────
#  STOCK LOG  (audit trail for every stock movement)
# ─────────────────────────────────────────────────────────────────────────────
class StockLog(models.Model):
    MOVEMENT_CHOICES = [
        ('IN',    'Stock In'),
        ('OUT',   'Sale'),
        ('ADJ',   'Adjustment'),
        ('WASTE', 'Waste / Damage'),
    ]
    store       = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='stock_logs')
    product     = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='stock_logs')
    movement    = models.CharField(max_length=10, choices=MOVEMENT_CHOICES)
    quantity    = models.DecimalField(max_digits=10, decimal_places=3)
    balance     = models.DecimalField(max_digits=10, decimal_places=3)
    reference   = models.CharField(max_length=100, blank=True)   # bill number or note
    created_by  = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at  = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.product.name} {self.movement} {self.quantity} kg"


# ─────────────────────────────────────────────────────────────────────────────
#  EXPENSE  (track store operational costs)
# ─────────────────────────────────────────────────────────────────────────────
class Expense(models.Model):
    CATEGORY_CHOICES = [
        # Daily expenses
        ('TRANSPORT',    'Transport'),
        ('MAINTENANCE',  'Maintenance'),
        ('MISC',         'Miscellaneous'),
        # Monthly expenses
        ('RENT',         'Rent'),
        ('ELECTRICITY',  'Electricity Bill'),
        ('SALARY',       'Salary'),
        ('PURCHASE',     'Stock Purchase'),
        ('OTHER',        'Other'),
    ]
    EXPENSE_TYPE_CHOICES = [
        ('DAILY',   'Daily Expense'),
        ('MONTHLY', 'Monthly Expense'),
    ]
    # Category → type auto-mapping
    DAILY_CATEGORIES   = {'TRANSPORT', 'MAINTENANCE', 'MISC'}
    MONTHLY_CATEGORIES = {'RENT', 'ELECTRICITY', 'SALARY', 'PURCHASE', 'OTHER'}

    def expense_upload_path(instance, filename):
        ext = Path(filename).suffix.lower()
        return f"expense_bills/{uuid.uuid4().hex}{ext}"

    store        = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='expenses')
    category     = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    expense_type = models.CharField(max_length=10, choices=EXPENSE_TYPE_CHOICES, default='DAILY', db_index=True)
    description  = models.CharField(max_length=300)
    amount       = models.DecimalField(max_digits=12, decimal_places=2)
    bill_pdf     = models.FileField(upload_to=expense_upload_path, blank=True, null=True)
    date         = models.DateField(default=timezone.now, db_index=True)
    created_by   = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # Auto-set expense_type based on category if not explicitly set
        if self.category in self.DAILY_CATEGORIES:
            self.expense_type = 'DAILY'
        elif self.category in self.MONTHLY_CATEGORIES:
            self.expense_type = 'MONTHLY'
        super().save(*args, **kwargs)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"[{self.store.name}] {self.category} ₹{self.amount}"


# ─────────────────────────────────────────────────────────────────────────────
#  AREA MANAGER ↔ STORE  (ManyToMany, managed by admin)
# ─────────────────────────────────────────────────────────────────────────────
class AreaManagerStore(models.Model):
    """Links an Area Manager (UserProfile with role=AREAMANAGER) to one or many stores."""
    manager    = models.ForeignKey(
        UserProfile, on_delete=models.CASCADE,
        related_name='managed_stores',
        limit_choices_to=models.Q(role='AREAMANAGER') | models.Q(role='WHOLESALE_EXEC')
    )
    store      = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='area_managers')
    assigned_at = models.DateTimeField(auto_now_add=True)
    assigned_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')

    class Meta:
        unique_together = ('manager', 'store')
        ordering = ['store__name', 'manager__user__username']

    def __str__(self):
        return f"{self.manager.user.username} → {self.store.name}"


# ─────────────────────────────────────────────────────────────────────────────
#  WHOLESALE APPROVAL LOG  (audit trail of every wholesale approval)
# ─────────────────────────────────────────────────────────────────────────────
class WholesaleApproval(models.Model):
    """
    Records every wholesale bill approval.
    """
    store        = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='wholesale_approvals')
    area_manager = models.ForeignKey(
        UserProfile, on_delete=models.CASCADE,
        related_name='approvals_given',
        limit_choices_to=models.Q(role='AREAMANAGER') | models.Q(role='WHOLESALE_EXEC')
    )
    sale         = models.OneToOneField('Sale', on_delete=models.CASCADE,
                                        related_name='approval', null=True, blank=True)
    bill_snapshot = models.JSONField(default=dict)   # snapshot at time of approval
    approved_by_name = models.CharField(max_length=200)  # denormalized for history
    created_by   = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at   = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Approved by {self.approved_by_name} @ {self.store.name} on {self.created_at:%d/%m/%Y %H:%M}"


# ─────────────────────────────────────────────────────────────────────────────
#  EXPENSE v2  (with PDF bill upload + delete support)
# ─────────────────────────────────────────────────────────────────────────────
# We extend Expense by adding bill_pdf field via migration
# The Expense model above will get bill_pdf added in migration 0004

# ─────────────────────────────────────────────────────────────────────────────
#  EMPLOYEE
# ─────────────────────────────────────────────────────────────────────────────
class Employee(models.Model):
    EMPLOYMENT_TYPE = [
        ('FULLTIME',  'Full Time'),
        ('PARTTIME',  'Part Time'),
        ('CONTRACT',  'Contract'),
        ('DAILY',     'Daily Wage'),
    ]
    PAY_CYCLE = [
        ('MONTHLY',   'Monthly'),
        ('WEEKLY',    'Weekly'),
        ('DAILY',     'Daily'),
    ]

    store           = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='employees', null=True, blank=True)
    user_profile    = models.OneToOneField(UserProfile, on_delete=models.SET_NULL,
                                           null=True, blank=True, related_name='employee')
    employee_id     = models.CharField(max_length=30, unique=True)
    full_name       = models.CharField(max_length=200)
    phone           = models.CharField(max_length=20, blank=True)
    email           = models.EmailField(blank=True)
    designation     = models.CharField(max_length=100, blank=True)
    employment_type = models.CharField(max_length=20, choices=EMPLOYMENT_TYPE, default='FULLTIME')
    pay_cycle       = models.CharField(max_length=20, choices=PAY_CYCLE, default='MONTHLY')
    basic_salary    = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    allowances      = models.DecimalField(max_digits=12, decimal_places=2, default=0,
                                          help_text='HRA, travel, food etc.')
    deductions      = models.DecimalField(max_digits=12, decimal_places=2, default=0,
                                          help_text='PF, ESI, tax etc.')
    date_joined     = models.DateField(default=timezone.now)
    is_active       = models.BooleanField(default=True)
    notes           = models.TextField(blank=True)
    created_by      = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at      = models.DateTimeField(auto_now_add=True)

    @property
    def net_salary(self):
        return self.basic_salary + self.allowances - self.deductions

    def __str__(self):
        return f"{self.employee_id} — {self.full_name} ({self.store.name})"

    class Meta:
        ordering = ['store', 'full_name']


# ─────────────────────────────────────────────────────────────────────────────
#  PAY SLIP
# ─────────────────────────────────────────────────────────────────────────────
class PaySlip(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('PAID',    'Paid'),
    ]
    MONTH_CHOICES = [
        (1,'January'),(2,'February'),(3,'March'),(4,'April'),
        (5,'May'),(6,'June'),(7,'July'),(8,'August'),
        (9,'September'),(10,'October'),(11,'November'),(12,'December'),
    ]

    employee        = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='payslips')
    store           = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='payslips')
    month           = models.PositiveSmallIntegerField(choices=MONTH_CHOICES)
    year            = models.PositiveIntegerField()
    basic_salary    = models.DecimalField(max_digits=12, decimal_places=2)
    allowances      = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    deductions      = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    bonus           = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    net_pay         = models.DecimalField(max_digits=12, decimal_places=2)
    status          = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    payment_date    = models.DateField(null=True, blank=True)
    payment_mode    = models.CharField(max_length=30, blank=True)
    notes           = models.TextField(blank=True)
    created_by      = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at      = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        self.net_pay = self.basic_salary + self.allowances + self.bonus - self.deductions
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee.full_name} — {self.get_month_display()} {self.year} ₹{self.net_pay}"

    class Meta:
        ordering = ['-year', '-month']
        unique_together = ('employee', 'month', 'year')

# ─────────────────────────────────────────────────────────────────────────────
#  WHOLESALE CUSTOMERS & CREDIT TRACKING
# ─────────────────────────────────────────────────────────────────────────────
class WholesaleCustomer(models.Model):
    name                  = models.CharField(max_length=200, unique=True)
    phone                 = models.CharField(max_length=20, blank=True)
    email                 = models.EmailField(blank=True)
    gst                   = models.CharField(max_length=20, blank=True)
    address               = models.TextField(blank=True, help_text='Delivery / billing address')
    store                 = models.ForeignKey(Store, on_delete=models.SET_NULL, null=True, blank=True, related_name='wholesale_customers')
    customer_code         = models.CharField(max_length=50, blank=True, unique=True, db_index=True)
    is_credit_enabled     = models.BooleanField(default=True)
    credit_duration_days  = models.PositiveIntegerField(default=7, help_text="Default days to pay")
    created_by            = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at            = models.DateTimeField(auto_now_add=True)
    
    @property
    def total_credit_amount(self):
        records = self.credit_records.all()
        return sum(r.total_due for r in records)

    @property
    def total_paid_amount(self):
        payments = self.payments.all()
        return sum(p.amount for p in payments)

    @property
    def balance(self):
        return self.total_credit_amount - self.total_paid_amount

    @property
    def has_unpaid_credit(self):
        return self.balance > 0

    @property
    def last_credit_date(self):
        last = self.credit_records.order_by('-created_at').first()
        return last.created_at if last else None

    @property
    def credit_since(self):
        first = self.credit_records.order_by('created_at').first()
        return first.created_at if first else None

    def __str__(self):
        return f"{self.name} ({self.customer_code}) - Bal: ₹{self.balance}"

    def save(self, *args, **kwargs):
        if not self.customer_code:
            # Logic for OW-BRANCH-001
            branch_pfx = (self.store.code or "MAIN").upper() if self.store else "GLOBAL"
            # Get next sequential number for this branch
            from django.db.models import Max
            from django.db import transaction
            
            with transaction.atomic():
                last_code = WholesaleCustomer.objects.filter(customer_code__startswith=f"OW-{branch_pfx}-").aggregate(Max('customer_code'))['customer_code__max']
                if last_code:
                    try:
                        last_num = int(last_code.split('-')[-1])
                        next_num = last_num + 1
                    except (ValueError, IndexError):
                        next_num = 1
                else:
                    next_num = 1
                
                self.customer_code = f"OW-{branch_pfx}-{str(next_num).zfill(3)}"
        super().save(*args, **kwargs)

class CreditRecord(models.Model):
    customer   = models.ForeignKey(WholesaleCustomer, on_delete=models.CASCADE, related_name='credit_records')
    sale       = models.OneToOneField(Sale, on_delete=models.CASCADE, related_name='credit_record', null=True, blank=True)
    
    amount     = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_external = models.BooleanField(default=False)
    external_reference = models.CharField(max_length=150, blank=True)
    
    due_date   = models.DateField(db_index=True)
    is_paid    = models.BooleanField(default=False, db_index=True)
    paid_on    = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def total_due(self):
        if self.is_external or not self.sale:
            return self.amount
        return self.sale.grand_total

    def __str__(self):
        ref = self.sale.bill_number if self.sale else self.external_reference
        return f"Credit for {self.customer.name} - #{ref} (Paid: {self.is_paid})"

class CreditPayment(models.Model):
    PAYMENT_MODE_CHOICES = [
        ('CASH',   'Cash'),
        ('UPI',    'UPI'),
        ('ONLINE', 'Online'),
        ('CARD',   'Card'),
    ]
    customer     = models.ForeignKey(WholesaleCustomer, on_delete=models.CASCADE, related_name='payments')
    amount       = models.DecimalField(max_digits=12, decimal_places=2)
    payment_mode = models.CharField(max_length=20, choices=PAYMENT_MODE_CHOICES, default='CASH')
    date         = models.DateField(default=timezone.now)
    note         = models.CharField(max_length=255, blank=True)
    created_by   = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f"Payment ₹{self.amount} - {self.customer.name} ({self.date})"


# ─────────────────────────────────────────────────────────────────────────────
#  FINANCIAL SUITE: ASSETS, LEDGER, REPORTS & SESSIONS
# ─────────────────────────────────────────────────────────────────────────────
class StoreSession(models.Model):
    store      = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='sessions')
    date       = models.DateField(default=timezone.now, db_index=True)
    opened_at  = models.DateTimeField(null=True, blank=True)
    closed_at  = models.DateTimeField(null=True, blank=True)
    opened_by  = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='opened_sessions')
    closed_by  = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='closed_sessions')

    class Meta:
        ordering = ['-date', '-opened_at']

    @property
    def is_open(self):
        return self.opened_at is not None and self.closed_at is None

    def __str__(self):
        return f"[{self.store.name}] Session {self.date}"


def asset_upload_path(instance, filename):
    ext = Path(filename).suffix.lower()
    return f"asset_bills/{uuid.uuid4().hex}{ext}"

class StoreAsset(models.Model):
    store         = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='assets')
    name          = models.CharField(max_length=200)
    purchase_date = models.DateField(default=timezone.now)
    cost          = models.DecimalField(max_digits=12, decimal_places=2)
    notes         = models.TextField(blank=True)
    bill_pdf      = models.FileField(upload_to=asset_upload_path, blank=True, null=True)
    created_at    = models.DateTimeField(auto_now_add=True)
    created_by    = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    class Meta:
        ordering = ['-purchase_date']

    def __str__(self):
        return f"{self.name} ({self.store.name})"


# ─────────────────────────────────────────────────────────────────────────────
#  LEDGER BOOK  (each book = one notebook on the shelf)
# ─────────────────────────────────────────────────────────────────────────────
COLOR_CHOICES = [
    ('#27a4d1', 'Ocean Blue'),
    ('#16a34a', 'Forest Green'),
    ('#dc2626', 'Ruby Red'),
    ('#7c3aed', 'Violet'),
    ('#d97706', 'Amber'),
    ('#0f172a', 'Midnight'),
    ('#db2777', 'Rose'),
    ('#0891b2', 'Cyan'),
]

class LedgerBook(models.Model):
    store       = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='ledger_books')
    name        = models.CharField(max_length=150, help_text='e.g. Cash Ledger, Bank Account, Supplier Dues')
    description = models.TextField(blank=True)
    color       = models.CharField(max_length=10, default='#27a4d1', choices=COLOR_CHOICES)
    icon        = models.CharField(max_length=50, default='fa-book', help_text='FontAwesome icon class e.g. fa-book')
    created_by  = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at  = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"[{self.store.name}] {self.name}"

    @property
    def entry_count(self):
        return self.entries.count()

    @property
    def current_balance(self):
        agg = self.entries.aggregate(
            given=models.Sum('amount_given'),
            spent=models.Sum('amount_spent')
        )
        return (agg['given'] or 0) - (agg['spent'] or 0)

    class Meta:
        ordering = ['name']


# ─────────────────────────────────────────────────────────────────────────────
#  LEDGER ENTRY
# ─────────────────────────────────────────────────────────────────────────────
class LedgerEntry(models.Model):
    store         = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='ledger_entries')
    ledger_book   = models.ForeignKey(
        'LedgerBook', on_delete=models.CASCADE, related_name='entries',
        null=True, blank=True
    )
    date          = models.DateField(default=timezone.now)
    description   = models.CharField(max_length=300)
    amount_given  = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Credit (Given)")
    amount_spent  = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Debit (Spent)")
    balance       = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at    = models.DateTimeField(auto_now_add=True)
    created_by    = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    class Meta:
        ordering = ['date', 'created_at']

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.amount_spent > 0 and not self.description:
            raise ValidationError("Explanation is mandatory for debit entries.")

    def __str__(self):
        return f"[{self.store.name}] {self.date} - {self.description[:30]}"


class DailyStockSnapshot(models.Model):
    store          = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='daily_snapshots')
    product        = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='daily_snapshots')
    date           = models.DateField(default=timezone.now)
    
    opening_qty    = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    purchased_qty  = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    sold_qty       = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    closing_qty    = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('store', 'product', 'date')
        ordering = ['-date', 'product__name']

    def __str__(self):
        return f"[{self.store.name}] {self.product.name} Snapshot - {self.date}"


# ─────────────────────────────────────────────────────────────────────────────
#  LOGIN ATTENDANCE
# ─────────────────────────────────────────────────────────────────────────────
class LoginAttendance(models.Model):
    """Records every successful login as an attendance event."""
    STATUS_CHOICES = [
        ('PRESENT', 'Present'),
        ('ABSENT',  'Absent'),
    ]
    store      = models.ForeignKey(Store, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='attendance_records')
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='attendance_records')
    login_date = models.DateField(default=timezone.now)
    login_time = models.TimeField(null=True, blank=True)
    status     = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PRESENT')
    ip_address = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-login_date', '-login_time']
        indexes = [
            models.Index(fields=['login_date', 'store']),
        ]

    def __str__(self):
        store_name = self.store.name if self.store else 'No Store'
        return f"{self.user.get_full_name() or self.user.username} @ {store_name} — {self.login_date}"


# ─────────────────────────────────────────────────────────────────────────────
#  STOCK REQUEST & VERIFICATION
# ─────────────────────────────────────────────────────────────────────────────
class StockRequest(models.Model):
    STATUS_CHOICES = [
        ('PENDING',    'Pending Approval'),
        ('APPROVED',   'Approved & Shipped'),
        ('RECEIVED',   'Received'),
        ('DISCREPANCY','Quantity Discrepancy'),
    ]
    store           = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='stock_requests')
    product         = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='stock_requests')
    requested_qty   = models.DecimalField(max_digits=10, decimal_places=3)
    sent_qty        = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    received_qty    = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    status          = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    requested_by    = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='stock_requests_made')
    approved_by     = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='stock_requests_approved')
    requested_at    = models.DateTimeField(auto_now_add=True)
    shipped_at      = models.DateTimeField(null=True, blank=True)
    received_at     = models.DateTimeField(null=True, blank=True)
    notes           = models.TextField(blank=True)

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f"Request: {self.product.name} ({self.requested_qty}kg) for {self.store.name}"


# ─────────────────────────────────────────────────────────────────────────────
#  NOTIFICATIONS / DASHBOARD ALERTS
# ─────────────────────────────────────────────────────────────────────────────
class Notification(models.Model):
    LEVEL_CHOICES = [
        ('INFO',    'Info'),
        ('SUCCESS', 'Success'),
        ('WARNING', 'Warning'),
        ('DANGER',  'Critical'),
    ]
    user        = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    title       = models.CharField(max_length=200)
    message     = models.TextField()
    level       = models.CharField(max_length=10, choices=LEVEL_CHOICES, default='INFO')
    is_read     = models.BooleanField(default=False)
    link        = models.CharField(max_length=255, blank=True, null=True)
    created_at  = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.level}] {self.title} for {self.user.username}"
