from django.urls import path
from . import views
from . import views_auth
from . import views_financials
from . import views_reports

urlpatterns = [
    # Auth
    path('login/',     views.login_view,  name='login'),
    path('logout/',    views.logout_view, name='logout'),
    
    # Password Reset OTP
    path('forgot-password/', views_auth.forgot_password_view, name='forgot_password'),
    path('verify-otp/', views_auth.verify_otp_view, name='verify_otp'),
    path('reset-password/', views_auth.reset_password_final_view, name='reset_password_final'),

    path('dashboard/', views.dashboard,   name='dashboard'),

    # Store management
    path('stores/',                       views.store_list,   name='store_list'),
    path('stores/create/',                views.store_create, name='store_create'),
    path('stores/<int:store_id>/edit/',   views.store_edit,   name='store_edit'),
    path('stores/<int:store_id>/',        views.store_detail, name='store_detail'),

    # User management
    path('users/',                        views.user_management, name='user_management'),
    path('users/create/',                 views.user_create,     name='user_create'),
    path('users/<int:user_id>/edit/',     views.user_edit,       name='user_edit'),
    path('users/<int:user_id>/delete/',   views.user_delete,     name='user_delete'),

    # Area Manager management
    path('area-managers/',                views.area_manager_list,   name='area_manager_list'),
    path('area-managers/assign/',         views.area_manager_assign, name='area_manager_assign'),
    path('area-managers/set-pin/',        views.set_manager_pin,     name='set_manager_pin'),
    path('area-managers/dashboard/',      views.am_dashboard,        name='am_dashboard'),

    # Billing
    path('billing/',                      views.billing,    name='billing'),
    path('billing/save/',                 views.save_bill,  name='save_bill'),
    path('billing/print/<int:bill_id>/',  views.bill_print, name='bill_print'),

    # Wholesale OTP approval
    path('billing/wholesale/managers/',    views.wholesale_managers,    name='wholesale_managers'),
    path('billing/wholesale/request-otp/', views.wholesale_request_otp, name='wholesale_request_otp'),
    path('billing/wholesale/verify-otp/',  views.wholesale_verify_otp,  name='wholesale_verify_otp'),

    # Inventory
    path('inventory/',                    views.inventory,       name='inventory'),
    path('inventory/add/',                views.product_add,     name='product_add'),
    path('inventory/<int:pid>/edit/',     views.product_edit,    name='product_edit'),
    path('inventory/<int:pid>/restock/',  views.product_restock, name='product_restock'),
    path('inventory/<int:pid>/delete/',   views.product_delete,  name='product_delete'),
    path('inventory/stock-log/',          views.stock_log,       name='stock_log'),

    # Stock Requests
    path('stock-requests/',                   views.stock_request_list,    name='stock_request_list'),
    path('stock-requests/create/',            views.stock_request_create,  name='stock_request_create'),
    path('stock-requests/<int:rid>/approve/', views.stock_request_approve, name='stock_request_approve'),
    path('stock-requests/<int:rid>/receive/', views.stock_request_receive, name='stock_request_receive'),
    path('notifications/mark-read/',          views.mark_notifications_read, name='mark_notifications_read'),

    # Reports (legacy hub → redirects to daily)
    path('reports/',                      views.reports,                       name='reports'),
    path('reports/export/',               views.export_excel,                  name='export_excel'),
    path('reports/approvals/',            views.approval_history,              name='approval_history'),

    # Daily & Monthly Reports
    path('reports/daily/',                views_reports.daily_report_view,     name='daily_report'),
    path('reports/monthly/',              views_reports.monthly_report_view,   name='monthly_report'),
    path('reports/daily/export/',         views_reports.export_daily_excel,    name='export_daily_excel'),
    path('reports/monthly/export/',       views_reports.export_monthly_excel,  name='export_monthly_excel'),

    # Expenses
    path('expenses/',                          views.expenses_page,   name='expenses_page'),
    path('expenses/add/',                      views.expense_add,     name='expense_add'),
    path('expenses/<int:expense_id>/delete/',  views.expense_delete,  name='expense_delete'),
    path('expenses/<int:expense_id>/mark-paid/', views.expense_mark_paid, name='expense_mark_paid'),

    # Employees
    path('employees/',                         views.employee_list,   name='employee_list'),
    path('employees/add/',                     views.employee_add,    name='employee_add'),
    path('employees/<int:emp_id>/',            views.employee_detail, name='employee_detail'),
    path('employees/<int:emp_id>/edit/',       views.employee_edit,   name='employee_edit'),
    path('employees/<int:emp_id>/delete/',     views.employee_delete,      name='employee_delete'),
    path('employees/<int:emp_id>/hard-delete/', views.employee_hard_delete, name='employee_hard_delete'),
    path('employees/<int:emp_id>/payslip/',    views.payslip_generate, name='payslip_generate'),
    path('payslips/<int:slip_id>/paid/',       views.payslip_mark_paid, name='payslip_mark_paid'),
    path('payslips/<int:slip_id>/delete/',     views.payslip_delete,   name='payslip_delete'),
    path('payslips/<int:slip_id>/print/',      views.payslip_print,    name='payslip_print'),
    
    # Wholesale & Credits
    path('wholesale-customers/',                    views.wholesale_customers,          name='wholesale_customers'),
    path('wholesale-customers/add/',                views.wholesale_customer_add,       name='wholesale_customer_add'),
    path('wholesale-customers/<int:cid>/edit/',     views.wholesale_customer_edit,      name='wholesale_customer_edit'),
    path('wholesale-customers/<int:cid>/delete/',   views.wholesale_customer_delete,    name='wholesale_customer_delete'),
    path('credits/',                                views.credits_list,                 name='credits_list'),
    path('credits/add-external/',                   views.credit_add_external,          name='credit_add_external'),
    path('credits/<int:cid>/pay/',                  views.credit_pay,                   name='credit_pay'),
    path('credits/customer/<int:customer_id>/',     views.customer_credit_detail,       name='customer_credit_detail'),
    path('credits/customer/<int:customer_id>/pay/', views.record_credit_payment,        name='record_credit_payment'),
    path('credits/<int:record_id>/delete/',         views.credit_delete,                name='credit_delete'),
    path('credits/payment/<int:payment_id>/delete/', views.credit_payment_delete,        name='credit_payment_delete'),

    # Ledger
    path('ledger/',                                       views_financials.ledger_books_list,  name='ledger_view'),
    path('ledger/book/<int:book_id>/',                    views_financials.ledger_book_detail, name='ledger_book_detail'),
    path('ledger/<int:entry_id>/delete/',                 views_financials.ledger_entry_delete, name='ledger_entry_delete'),

    # Assets
    path('assets/',                               views_financials.asset_list_view,   name='asset_list_view'),
    path('assets/<int:asset_id>/delete/',         views_financials.asset_delete_view, name='asset_delete_view'),

    # API
    path('api/product/<int:pid>/',        views.product_api, name='product_api'),
    path('api/phonepe/initiate/',         views.phonepe_initiate, name='phonepe_initiate'),
    path('api/phonepe/status/',           views.phonepe_status, name='phonepe_status'),
]
