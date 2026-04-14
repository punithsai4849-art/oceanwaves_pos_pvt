from django.contrib import admin
from .models import Store, UserProfile, Product, Sale, SaleItem, StockLog, Expense

admin.site.site_header  = "OCEANWAVES SEA FOODS — Admin"
admin.site.site_title   = "OCEANWAVES Admin"
admin.site.index_title  = "Multi-Store POS Administration"


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display  = ('name', 'phone', 'whatsapp_number', 'gstin', 'is_active', 'created_at')
    list_editable = ('is_active', 'whatsapp_number')
    search_fields = ('name', 'gstin')


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display  = ('user', 'role', 'store', 'is_active')
    list_filter   = ('role', 'store', 'is_active')
    search_fields = ('user__username',)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display  = ('name', 'store', 'category', 'cost_price', 'retail_price',
                      'wholesale_price', 'stock_quantity', 'is_active')
    list_filter   = ('store', 'category', 'is_active')
    search_fields = ('name',)
    list_editable = ('retail_price', 'wholesale_price', 'stock_quantity')


class SaleItemInline(admin.TabularInline):
    model         = SaleItem
    extra         = 0
    readonly_fields = ('total_amount', 'total_cost', 'profit')


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display  = ('bill_number', 'store', 'bill_type', 'customer_name',
                      'grand_total', 'payment_mode', 'created_at')
    list_filter   = ('store', 'bill_type', 'payment_mode', 'created_at')
    search_fields = ('bill_number', 'customer_name')
    readonly_fields = ('bill_number', 'subtotal', 'total_gst', 'grand_total')
    inlines       = [SaleItemInline]


@admin.register(StockLog)
class StockLogAdmin(admin.ModelAdmin):
    list_display  = ('created_at', 'store', 'product', 'movement', 'quantity', 'balance', 'reference')
    list_filter   = ('store', 'movement', 'created_at')
    search_fields = ('product__name', 'reference')


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display  = ('date', 'store', 'category', 'description', 'amount')
    list_filter   = ('store', 'category', 'date')
    search_fields = ('description',)

from .models import Employee, PaySlip

@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display  = ('employee_id', 'full_name', 'store', 'designation', 'employment_type', 'basic_salary', 'net_salary', 'is_active')
    list_filter   = ('store', 'employment_type', 'is_active')
    search_fields = ('employee_id', 'full_name', 'phone')
    list_editable = ('is_active',)

@admin.register(PaySlip)
class PaySlipAdmin(admin.ModelAdmin):
    list_display  = ('employee', 'month', 'year', 'net_pay', 'status', 'payment_date')
    list_filter   = ('store', 'status', 'year')
    search_fields = ('employee__full_name',)


from .models import LoginAttendance, LedgerEntry, StoreAsset, DailyStockSnapshot

@admin.register(LoginAttendance)
class LoginAttendanceAdmin(admin.ModelAdmin):
    list_display  = ('login_date', 'login_time', 'user', 'store', 'status', 'ip_address')
    list_filter   = ('login_date', 'store', 'status')
    search_fields = ('user__username', 'user__first_name', 'user__last_name')
    date_hierarchy = 'login_date'


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display  = ('date', 'store', 'description', 'amount_given', 'amount_spent', 'balance')
    list_filter   = ('store', 'date')
    search_fields = ('description',)


@admin.register(StoreAsset)
class StoreAssetAdmin(admin.ModelAdmin):
    list_display  = ('name', 'store', 'purchase_date', 'cost', 'notes')
    list_filter   = ('store', 'purchase_date')
    search_fields = ('name',)


@admin.register(DailyStockSnapshot)
class DailyStockSnapshotAdmin(admin.ModelAdmin):
    list_display  = ('date', 'store', 'product', 'opening_qty', 'purchased_qty', 'sold_qty', 'closing_qty')
    list_filter   = ('store', 'date')
    search_fields = ('product__name',)
    date_hierarchy = 'date'
