from django.urls import path

from . import views

urlpatterns = [
    path('', views.dashboard.dashboard, name='dashboard'),
    path('account/', views.account.account_details, name='account_details'),
    path('account/export/', views.dashboard.statement_export, name='statement_export'),
    path('top_up/', views.topup.top_up, name='top_up'),
    path('top_up_bank_transfer/', views.topup.top_up_bank_transfer, name='top_up_bank_transfer'),
    path('top_up_bank_transfer/<str:country>/', views.topup.top_up_bank_transfer_local, name='top_up_bank_transfer_local'),
    path('top_up_bank_details/<str:currency>/', views.topup.top_up_bank_details, name='top_up_bank_details'),
    path('complete_top_up_card/<str:item_id>/', views.topup.complete_top_up_card, name='complete_top_up_card'),
    path('complete_top_up_bank_transfer/<str:item_id>/', views.topup.complete_top_up_bank_transfer, name='complete_top_up_bank_transfer'),
    path('complete_top_up_bank_transfer/<str:item_id>/<str:currency>/', views.topup.complete_top_up_bank_details,
         name='complete_top_up_bank_details'),
    # path('complete_top_up_sepa_direct_debit/<str:item_id>/', views.topup.complete_top_up_sepa_direct_debit,
    #      name='complete_top_up_sepa_direct_debit'),
    # path('complete_top_up_checkout/<str:item_id>/', views.topup.complete_top_up_checkout, name='complete_top_up_checkout'),
    path('complete_top_up_sources/<str:item_id>/', views.topup.complete_top_up_sources, name='complete_top_up_sources'),
    path('complete_order/<str:charge_id>/', views.complete_order, name='complete_order'),
    path('order_details/<str:charge_id>/', views.order_details, name='order_details'),
    path('toup_up_details/<str:item_id>/', views.top_up_details, name='toup_up_details'),
    path('fail_top_up/<str:item_id>/', views.dashboard.fail_top_up, name='fail_top_up'),
    path('fail_charge/<str:charge_id>/', views.dashboard.fail_charge, name='fail_charge'),
    path('add_billing_adddress/', views.account.add_billing_address, name='add_billing_address'),
    path('edit_billing_adddress/<str:address_id>/', views.account.edit_billing_address, name='edit_billing_address'),
    path('add_card/', views.account.add_card, name='add_card'),
    path('edit_card/<pm_id>/', views.account.edit_card, name='edit_card'),
    path('view_ach_mandate/<str:m_id>/', views.account.view_ach_mandate, name='view_ach_mandate'),
    path('setup_new_ach/', views.account.setup_new_ach, name='setup_new_ach'),
    path('setup_new_ach_complete/', views.account.setup_new_ach_complete, name='setup_new_ach_complete'),
    path('edit_ach_mandate/<str:m_id>/', views.account.edit_ach_mandate, name='edit_ach_mandate'),
    path('view_autogiro_mandate/<str:m_id>/', views.account.view_autogiro_mandate, name='view_autogiro_mandate'),
    path('setup_new_autogiro/', views.account.setup_new_autogiro, name='setup_new_autogiro'),
    path('setup_new_autogiro_complete/', views.account.setup_new_autogiro_complete, name='setup_new_autogiro_complete'),
    path('edit_autogiro_mandate/<str:m_id>/', views.account.edit_autogiro_mandate, name='edit_autogiro_mandate'),
    path('view_bacs_mandate/<str:m_id>/', views.account.view_bacs_mandate, name='view_bacs_mandate'),
    path('setup_new_bacs/', views.account.setup_new_bacs, name='setup_new_bacs'),
    path('setup_new_bacs_complete/', views.account.setup_new_bacs_complete, name='setup_new_bacs_complete'),
    path('edit_bacs_mandate/<str:m_id>/', views.account.edit_bacs_mandate, name='edit_bacs_mandate'),
    path('view_becs_mandate/<str:m_id>/', views.account.view_becs_mandate, name='view_becs_mandate'),
    path('setup_new_becs/', views.account.setup_new_becs, name='setup_new_becs'),
    path('setup_new_becs_complete/', views.account.setup_new_becs_complete, name='setup_new_becs_complete'),
    path('edit_becs_mandate/<str:m_id>/', views.account.edit_becs_mandate, name='edit_becs_mandate'),
    path('view_becs_nz_mandate/<str:m_id>/', views.account.view_becs_nz_mandate, name='view_becs_nz_mandate'),
    path('setup_new_becs_nz/', views.account.setup_new_becs_nz, name='setup_new_becs_nz'),
    path('setup_new_becs_nz_complete/', views.account.setup_new_becs_nz_complete, name='setup_new_becs_nz_complete'),
    path('edit_becs_nz_mandate/<str:m_id>/', views.account.edit_becs_nz_mandate, name='edit_becs_nz_mandate'),
    path('view_betalingsservice_mandate/<str:m_id>/', views.account.view_betalingsservice_mandate,
         name='view_betalingsservice_mandate'),
    path('setup_new_betalingsservice/', views.account.setup_new_betalingsservice, name='setup_new_betalingsservice'),
    path('setup_new_betalingsservice_complete/', views.account.setup_new_betalingsservice_complete,
         name='setup_new_betalingsservice_complete'),
    path('edit_betalingsservice_mandate/<str:m_id>/', views.account.edit_betalingsservice_mandate,
         name='edit_betalingsservice_mandate'),
    path('view_pad_mandate/<str:m_id>/', views.account.view_pad_mandate, name='view_pad_mandate'),
    path('setup_new_pad/', views.account.setup_new_pad, name='setup_new_pad'),
    path('setup_new_pad_complete/', views.account.setup_new_pad_complete, name='setup_new_pad_complete'),
    path('edit_pad_mandate/<str:m_id>/', views.account.edit_pad_mandate, name='edit_pad_mandate'),
    path('view_sepa_mandate/<str:m_id>/', views.account.view_sepa_mandate, name='view_sepa_mandate'),
    path('top_up_new_sepa/', views.account.setup_new_sepa, name='setup_new_sepa'),
    path('top_up_new_sepa_complete/', views.account.setup_new_sepa_complete, name='setup_new_sepa_complete'),
    path('edit_sepa_mandate/<str:m_id>/', views.account.edit_sepa_mandate, name='edit_sepa_mandate'),
    # path('edit_subscription/<str:s_id>/', views.account.edit_subscription, name='edit_subscription'),
    path('stripe_webhook/', views.webhooks.stripe_webhook),
    path('gc_webhook/', views.webhooks.gc_webhook),
    path('xfw_webhook/', views.webhooks.xfw_webhook),
    path('monzo_webhook/<secret_key>/', views.webhooks.monzo_webhook),
    path('charge_user/<user_id>/', views.api.charge_user),
    path('get_charge_state/<str:charge_state_id>/', views.api.get_charge_state),
    path('reverse_charge/', views.api.reverse_charge),
    path('subscribe_user/<user_id>/', views.api.subscribe_user),
    path('log_usage/<subscription_id>/', views.api.log_usage),
    path('convert_currency/', views.api.convert_currency),
    path('save_subscription/', views.save_subscription),
    path('sw.js', views.sw),
    path('accounts/', views.admin.view_accounts, name='view_accounts'),
    path('accounts/<account_id>/', views.admin.view_account, name='view_account'),
    path('accounts/edit_item/<item_id>/', views.admin.edit_ledger_item, name='edit_ledger_item'),
    path('accounts/<account_id>/charge/', views.admin.charge_account, name='charge_account'),
    path('accounts/<account_id>/top_up/', views.admin.manual_top_up_account, name='manual_top_up_account'),
]
